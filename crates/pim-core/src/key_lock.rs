//! In-process serialisation of the business-key uniqueness rule.
//!
//! `keys::normalize_key_attributes` asks whether another object of the same type
//! already carries a key value, and the caller writes the object afterwards. That
//! check-then-write window is not atomic: two writers (parallel MEF import chunks,
//! two editors) can both read "no conflict" and then either collide with a `409`
//! or — worse — leave **two live rows** with the same business key, which then
//! makes every later `PATCH` on that key fail (`plan/detail/demodaten-generator.md`
//! §4.4). A database guard is not available: SurrealDB cannot express a unique
//! index with `deleted_at IS NONE`, and a plain unique index would block
//! re-creating an object after a soft delete.
//!
//! The core therefore serialises the window per key **inside the process**. Keys
//! are mapped to a fixed set of shards, so two different keys almost never share
//! a mutex (and when they do, it only costs a little parallel throughput, never
//! correctness). This is sufficient because one core process owns a database
//! (`docker-compose.vps.yml`); a multi-instance deployment would need a
//! database-level guard instead.

use std::hash::{Hash, Hasher};
use std::sync::Arc;

use tokio::sync::{Mutex, OwnedMutexGuard};

/// Number of shards: high enough that two distinct keys rarely collide.
const SHARDS: usize = 4096;

/// Registry of per-key mutexes, shared through `AppState`.
pub struct KeyLocks {
    shards: Box<[Arc<Mutex<()>>]>,
}

impl Default for KeyLocks {
    fn default() -> Self {
        Self::new()
    }
}

impl KeyLocks {
    /// Builds the registry (one mutex per shard, no allocation per key).
    pub fn new() -> Self {
        let shards = (0..SHARDS).map(|_| Arc::new(Mutex::new(()))).collect();
        Self { shards }
    }

    /// Locks every given key and returns the guards; keep them until the write
    /// is done. Keys hashing to the same shard are locked once, and the shard
    /// order is sorted, so two writers can never deadlock.
    pub async fn lock_keys(&self, keys: &[String]) -> Vec<OwnedMutexGuard<()>> {
        let mut shards: Vec<usize> = keys.iter().map(|key| self.shard(key)).collect();
        shards.sort_unstable();
        shards.dedup();
        let mut guards = Vec::with_capacity(shards.len());
        for shard in shards {
            guards.push(self.shards[shard].clone().lock_owned().await);
        }
        guards
    }

    fn shard(&self, key: &str) -> usize {
        let mut hasher = std::collections::hash_map::DefaultHasher::new();
        key.hash(&mut hasher);
        (hasher.finish() as usize) % self.shards.len()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration;

    #[tokio::test]
    async fn different_keys_do_not_block_each_other() {
        let locks = KeyLocks::new();
        // Shards are chosen by hash; two arbitrary keys normally differ, so the
        // second lock is granted while the first is still held. Retry with other
        // keys if they happen to collide (1 in 4096).
        let first = locks.lock_keys(&["product:sku:A".to_owned()]).await;
        let second = tokio::time::timeout(
            Duration::from_millis(200),
            locks.lock_keys(&["product:sku:B".to_owned()]),
        )
        .await;
        assert!(second.is_ok(), "unrelated keys must not serialise");
        drop(first);
        drop(second);
    }

    #[tokio::test]
    async fn the_same_key_serialises() {
        let locks = KeyLocks::new();
        let held = locks.lock_keys(&["product:sku:A".to_owned()]).await;
        let blocked = tokio::time::timeout(
            Duration::from_millis(100),
            locks.lock_keys(&["product:sku:A".to_owned()]),
        )
        .await;
        assert!(
            blocked.is_err(),
            "the same key must block the second writer"
        );
        drop(held);
        let granted = tokio::time::timeout(
            Duration::from_millis(200),
            locks.lock_keys(&["product:sku:A".to_owned()]),
        )
        .await;
        assert!(
            granted.is_ok(),
            "the guard must be released with the holder"
        );
    }

    #[tokio::test]
    async fn repeated_keys_are_locked_once() {
        let locks = KeyLocks::new();
        let keys = vec!["product:sku:A".to_owned(), "product:sku:A".to_owned()];
        let guards = locks.lock_keys(&keys).await;
        assert_eq!(guards.len(), 1);
    }
}
