//! In-memory cache for slow-changing metadata (Phase 3).
//!
//! Caches the attribute names marked `multilingual`/`inheritable` and the
//! language-tree fallback chains. These change only when attribute definitions
//! or the language tree change, so the cache is invalidated explicitly by the
//! corresponding write handlers. Redis remains optional for multi-instance
//! deployments (see plan Phase 3).

use std::collections::HashMap;
use std::sync::Arc;

use serde_json::Value;
use tokio::sync::RwLock;

use crate::db::Db;
use crate::error::ApiError;

/// Caches frequently read, rarely written metadata.
#[derive(Default)]
pub struct MetaCache {
    multilingual_names: RwLock<Option<Arc<Vec<String>>>>,
    inheritable_names: RwLock<Option<Arc<Vec<String>>>>,
    fallback_chains: RwLock<HashMap<String, Arc<Vec<String>>>>,
}

impl MetaCache {
    /// Names of all `multilingual` attribute definitions (cached).
    pub async fn multilingual_names(&self, db: &Db) -> Result<Arc<Vec<String>>, ApiError> {
        if let Some(names) = self.multilingual_names.read().await.clone() {
            return Ok(names);
        }
        let names = Arc::new(load_multilingual_names(db).await?);
        *self.multilingual_names.write().await = Some(names.clone());
        Ok(names)
    }

    /// Names of all `inheritable` attribute definitions (cached).
    pub async fn inheritable_names(&self, db: &Db) -> Result<Arc<Vec<String>>, ApiError> {
        if let Some(names) = self.inheritable_names.read().await.clone() {
            return Ok(names);
        }
        let names = Arc::new(load_inheritable_names(db).await?);
        *self.inheritable_names.write().await = Some(names.clone());
        Ok(names)
    }

    /// Fallback chain (ancestor codes ending at `root`) for a locale (cached).
    pub async fn fallback_chain(
        &self,
        db: &Db,
        locale: &str,
    ) -> Result<Arc<Vec<String>>, ApiError> {
        if let Some(chain) = self.fallback_chains.read().await.get(locale).cloned() {
            return Ok(chain);
        }
        let chain = Arc::new(load_fallback_chain(db, locale).await?);
        self.fallback_chains
            .write()
            .await
            .insert(locale.to_owned(), chain.clone());
        Ok(chain)
    }

    /// Invalidates cached attribute names after attribute-definition writes.
    pub async fn invalidate_attribute_definitions(&self) {
        *self.multilingual_names.write().await = None;
        *self.inheritable_names.write().await = None;
    }

    /// Invalidates cached fallback chains after language-tree changes.
    pub async fn invalidate_language_tree(&self) {
        self.fallback_chains.write().await.clear();
    }
}

async fn load_multilingual_names(db: &Db) -> Result<Vec<String>, ApiError> {
    let sql = "SELECT VALUE name FROM attribute_definition WHERE multilingual = true";
    let mut response = db.inner().query(sql).await?.check()?;
    let names: Vec<String> = response.take(0)?;
    Ok(names)
}

async fn load_inheritable_names(db: &Db) -> Result<Vec<String>, ApiError> {
    let sql = "SELECT VALUE name FROM attribute_definition WHERE inheritable = true";
    let mut response = db.inner().query(sql).await?.check()?;
    let names: Vec<String> = response.take(0)?;
    Ok(names)
}

async fn load_fallback_chain(db: &Db, locale: &str) -> Result<Vec<String>, ApiError> {
    let sql = "SELECT fallback_order FROM language_tree WHERE code = $code LIMIT 1";
    let mut response = db
        .inner()
        .query(sql)
        .bind(("code", locale.to_owned()))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    let mut chain: Vec<String> = rows
        .first()
        .and_then(|row| row.get("fallback_order"))
        .and_then(Value::as_array)
        .map(|order| {
            order
                .iter()
                .filter_map(Value::as_str)
                .map(str::to_owned)
                .collect()
        })
        .unwrap_or_default();
    if !chain.iter().any(|code| code == "root") {
        chain.push("root".to_owned());
    }
    Ok(chain)
}
