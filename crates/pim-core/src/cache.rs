//! In-memory cache for slow-changing metadata (Phase 3).
//!
//! Caches the attribute names marked `multilingual`/`inheritable`/`url` and the
//! language-tree fallback chains. These change only when attribute definitions
//! or the language tree change, so the cache is invalidated explicitly by the
//! corresponding write handlers. Redis remains optional for multi-instance
//! deployments (see plan Phase 3).
//!
//! Also holds the **dashboard aggregation** (`GET /objects/stats`): computing it
//! reads every object's attributes, the composition edges and the class features,
//! which cost ~1 s per call at demo scale (release build, 2026-09-16). The cached
//! payload is bound to a **monotonic data version** that every write path touching
//! objects, references, the data model or the language tree bumps
//! ([`MetaCache::invalidate_stats`]) — a stale payload is therefore never served,
//! the next call recomputes it. Because a miss under concurrency would let every
//! waiter recompute in parallel (measured 2026-09-16: 1.8 s cold **alone**, 8.2 s
//! mean for ten waiters), the recomputation is **single-flight**: the first caller
//! computes, the others wait on [`MetaCache::stats_lock`] and then read the stored
//! payload.

use std::collections::HashMap;
use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};

use serde_json::Value;
use tokio::sync::Mutex as AsyncMutex;
use tokio::sync::RwLock;

use crate::db::Db;
use crate::error::ApiError;

/// Caches frequently read, rarely written metadata.
#[derive(Default)]
pub struct MetaCache {
    multilingual_names: RwLock<Option<Arc<Vec<String>>>>,
    inheritable_names: RwLock<Option<Arc<Vec<String>>>>,
    url_attributes: RwLock<Option<Arc<Vec<UrlAttribute>>>>,
    feature_bindings: RwLock<Option<Arc<HashMap<String, String>>>>,
    attribute_vocabulary: RwLock<Option<Arc<String>>>,
    required_names: RwLock<HashMap<String, Arc<Vec<String>>>>,
    types_with_required: RwLock<Option<Arc<Vec<String>>>>,
    fallback_chains: RwLock<HashMap<String, Arc<Vec<String>>>>,
    /// Active rule specs per `(rule types, object type)`: the blocking write path
    /// evaluates the applicable rules for **every** save, so this lookup used to
    /// run once per write for data that only the rules administration changes.
    active_rules: RwLock<HashMap<String, Arc<Vec<crate::rules::RuleSpec>>>>,
    /// Rules whose computed value reads inherited values (the cascade questions).
    inherited_rules: RwLock<Option<Arc<Vec<crate::rules::InheritedComputedRule>>>>,
    /// Relation keys (`rel_…`) of every categorisation relation, asked on every
    /// class-feature and completeness lookup.
    category_relations: RwLock<Option<Arc<Vec<String>>>>,
    /// `GET /objects/stats` payloads per working language, bound to `stats_version`.
    stats: RwLock<HashMap<String, (u64, Arc<Value>)>>,
    /// One lock per `GET /objects/stats` cache key: makes the recomputation
    /// single-flight, so a miss under concurrency is computed once instead of
    /// once per waiting request.
    stats_locks: std::sync::Mutex<HashMap<String, Arc<AsyncMutex<()>>>>,
    /// Bumped by every write that can change the aggregation.
    stats_version: AtomicU64,
}

/// A `url` attribute: its name and whether it holds one value per language.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct UrlAttribute {
    pub name: String,
    pub multilingual: bool,
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

    /// `url` attributes (name + `multilingual`) of the data model (cached).
    pub async fn url_attributes(&self, db: &Db) -> Result<Arc<Vec<UrlAttribute>>, ApiError> {
        if let Some(attributes) = self.url_attributes.read().await.clone() {
            return Ok(attributes);
        }
        let attributes = Arc::new(load_url_attributes(db).await?);
        *self.url_attributes.write().await = Some(attributes.clone());
        Ok(attributes)
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

    /// Bindings from a standard's feature code to the attribute that carries
    /// its value (`attribute_definition.standard_codes`, e.g. `{"etim":
    /// "EF000123"}` → attribute name), cached.
    ///
    /// With a classification standard installed **every** feature definition
    /// carries a binding (17 397 definitions, ETIM 10.0), so loading them per
    /// call cost ~0.3 s on read paths that run per object (grid with `?locale=`,
    /// picker badges, rules).
    pub async fn feature_bindings(
        &self,
        db: &Db,
    ) -> Result<Arc<HashMap<String, String>>, ApiError> {
        if let Some(bindings) = self.feature_bindings.read().await.clone() {
            return Ok(bindings);
        }
        let bindings = Arc::new(load_feature_bindings(db).await?);
        *self.feature_bindings.write().await = Some(bindings.clone());
        Ok(bindings)
    }

    /// Projection of **all** attribute definitions as a **serialized JSON body**,
    /// cached.
    ///
    /// Consumers: the export wizard's field selection (`/export`), the catalog
    /// page (`/attribute-definitions`), the object grid's column picker
    /// (`/products`) and the product form (`/products/{id}`) — the last one needs
    /// **every** definition of the object's type, because `GET
    /// /attribute-definitions` is capped at 1000 rows and with a classification
    /// standard installed those are the standard's features, so a freshly created
    /// own attribute was unreachable in the form. The projection carries what such
    /// a list, selection or field renderer needs (`VOCABULARY_FIELDS`):
    /// 318 KB per 1000 rows, 6.49 MB without and 7.41 MB with the three fields the
    /// form renders additionally (`computed`, `enum_set_id`, `constraints`), one
    /// query either way.
    ///
    /// The body is cached **serialized** because this payload is megabytes:
    /// cloning 17 379 documents out of a cache and re-serialising them per
    /// request cost seconds in a debug build, the query ~3 s — the cache only
    /// pays off when a warm request just hands out the finished bytes. Cleared by
    /// the attribute-definition writes and by the MCF import.
    pub async fn attribute_vocabulary_json(&self, db: &Db) -> Result<Arc<String>, ApiError> {
        if let Some(json) = self.attribute_vocabulary.read().await.clone() {
            return Ok(json);
        }
        let json = Arc::new(load_attribute_vocabulary(db).await?);
        *self.attribute_vocabulary.write().await = Some(json.clone());
        Ok(json)
    }

    /// Invalidates cached attribute names after attribute-definition writes.
    pub async fn invalidate_attribute_definitions(&self) {
        *self.multilingual_names.write().await = None;
        *self.inheritable_names.write().await = None;
        *self.url_attributes.write().await = None;
        *self.feature_bindings.write().await = None;
        *self.attribute_vocabulary.write().await = None;
        // The per-type required names come from `attribute_definition.required_types`.
        self.required_names.write().await.clear();
        *self.types_with_required.write().await = None;
        self.invalidate_stats();
    }

    /// Attribute names an object of `object_type` must carry: the definitions with
    /// `required_types` for that type plus the type's declared `key_attributes`
    /// (cached **per type**).
    ///
    /// Both parts are two small queries, but the read paths ask for them **per
    /// object** (`list_objects`, picker, completeness), and with a classification
    /// standard installed (17 397 attribute definitions) that lookup dominated a
    /// page: 1000 rows spent ~2.8 s in it alone. The cache is cleared by the
    /// attribute-definition and object-type writes.
    pub async fn required_names(
        &self,
        db: &Db,
        object_type: &str,
    ) -> Result<Arc<Vec<String>>, ApiError> {
        if let Some(names) = self.required_names.read().await.get(object_type).cloned() {
            return Ok(names);
        }
        let names = Arc::new(load_required_names(db, object_type).await?);
        self.required_names
            .write()
            .await
            .insert(object_type.to_owned(), names.clone());
        Ok(names)
    }

    /// Object types that can be **incomplete**: every type an attribute definition
    /// declares as required plus the types that declare `key_attributes` (a declared
    /// key counts as mandatory). Cached because it is only needed to narrow a scan:
    /// objects of all other types have nothing to be incomplete about, so their
    /// attributes need not be loaded at all.
    ///
    /// Cleared by the attribute-definition and object-type writes.
    pub async fn types_with_required(&self, db: &Db) -> Result<Arc<Vec<String>>, ApiError> {
        if let Some(names) = self.types_with_required.read().await.clone() {
            return Ok(names);
        }
        let names = Arc::new(load_types_with_required(db).await?);
        *self.types_with_required.write().await = Some(names.clone());
        Ok(names)
    }

    /// Invalidates the cached per-type required names after object-type writes
    /// (`key_attributes` belongs to the type registry).
    pub async fn invalidate_object_types(&self) {
        self.required_names.write().await.clear();
        *self.types_with_required.write().await = None;
        self.invalidate_stats();
    }

    /// Current data version of the dashboard aggregation.
    pub fn stats_version(&self) -> u64 {
        self.stats_version.load(Ordering::Relaxed)
    }

    /// Invalidates every cached `GET /objects/stats` payload.
    ///
    /// Must be called by every write that can change the aggregation: objects and
    /// their attributes/status (`create`/`update`/`delete`/batch/computed),
    /// references (composition and categorisation edges), the data model
    /// (attribute definitions, object types, class features, relation
    /// definitions) and the language tree (locale fallback chains). The version
    /// is bumped, so a payload computed concurrently with a write is never served
    /// afterwards.
    pub fn invalidate_stats(&self) {
        self.stats_version.fetch_add(1, Ordering::Relaxed);
    }

    /// A cached aggregation for `key`, if it matches the current data version.
    pub async fn cached_stats(&self, key: &str, version: u64) -> Option<Arc<Value>> {
        self.stats
            .read()
            .await
            .get(key)
            .filter(|(stored, _)| *stored == version)
            .map(|(_, payload)| payload.clone())
    }

    /// The single-flight lock of a `GET /objects/stats` cache key.
    ///
    /// The caller that misses the cache holds it while computing, so the other
    /// waiters of the same key recompute nothing and take the payload the first
    /// caller stores. Measured 2026-09-16 (release build, 16 k objects): a cold
    /// computation costs 1.8 s **alone** but 8.2 s on average when ten requests
    /// miss together — the lock turns that herd back into one computation.
    pub fn stats_lock(&self, key: &str) -> Arc<AsyncMutex<()>> {
        let mut locks = self
            .stats_locks
            .lock()
            .expect("stats lock registry poisoned");
        locks.entry(key.to_owned()).or_default().clone()
    }

    /// Stores an aggregation for `key`, computed for `version`.
    pub async fn store_stats(&self, key: &str, version: u64, payload: Arc<Value>) {
        self.stats
            .write()
            .await
            .insert(key.to_owned(), (version, payload));
    }

    /// Invalidates cached fallback chains after language-tree changes.
    pub async fn invalidate_language_tree(&self) {
        self.fallback_chains.write().await.clear();
        self.invalidate_stats();
    }

    /// Active rules applicable to an object type, per `(rule types, object type)`.
    ///
    /// The blocking write path evaluates the applicable rules for **every** save,
    /// so this lookup ran once per write for data that only the rules
    /// administration changes. Cleared by [`Self::invalidate_rules`].
    pub(crate) async fn active_rules(
        &self,
        db: &Db,
        rule_types: &[&str],
        object_type: &str,
    ) -> Result<Arc<Vec<crate::rules::RuleSpec>>, ApiError> {
        let key = format!("{}|{object_type}", rule_types.join(","));
        if let Some(rules) = self.active_rules.read().await.get(&key).cloned() {
            return Ok(rules);
        }
        let rules = Arc::new(crate::rules::load_active_rules(db, rule_types, object_type).await?);
        self.active_rules.write().await.insert(key, rules.clone());
        Ok(rules)
    }

    /// Rules whose computed value reads inherited values (the inherited-computed
    /// cascade questions). Cached like [`Self::active_rules`].
    pub(crate) async fn inherited_computed_rules(
        &self,
        db: &Db,
    ) -> Result<Arc<Vec<crate::rules::InheritedComputedRule>>, ApiError> {
        if let Some(rules) = self.inherited_rules.read().await.clone() {
            return Ok(rules);
        }
        let rules = Arc::new(crate::rules::load_inherited_computed_rules(db).await?);
        *self.inherited_rules.write().await = Some(rules.clone());
        Ok(rules)
    }

    /// Relation keys of every categorisation relation (`role = category`).
    ///
    /// Needed for every class-feature and completeness lookup; cached because the
    /// answer only changes when a relation definition does.
    pub(crate) async fn category_relation_keys(
        &self,
        db: &Db,
    ) -> Result<Arc<Vec<String>>, ApiError> {
        if let Some(keys) = self.category_relations.read().await.clone() {
            return Ok(keys);
        }
        let keys = Arc::new(crate::class_features::load_category_relation_keys(db).await?);
        *self.category_relations.write().await = Some(keys.clone());
        Ok(keys)
    }

    /// Invalidates the cached rule lookups (create/update/delete/activate/disable).
    pub async fn invalidate_rules(&self) {
        self.active_rules.write().await.clear();
        *self.inherited_rules.write().await = None;
    }

    /// Invalidates the cached categorisation relation keys (relation-definition
    /// writes, including the MCF import).
    pub async fn invalidate_relation_definitions(&self) {
        *self.category_relations.write().await = None;
    }
}

/// Required attribute names of an object type (`attribute_definition.required_types`
/// plus the type's `key_attributes`).
async fn load_required_names(db: &Db, object_type: &str) -> Result<Vec<String>, ApiError> {
    let sql = "SELECT VALUE name FROM attribute_definition WHERE $type IN required_types";
    let mut response = db
        .inner()
        .query(sql)
        .bind(("type", object_type.to_owned()))
        .await?
        .check()?;
    let mut names: Vec<String> = response.take(0)?;
    let keys = crate::api::object_types::key_attributes_for(db, object_type).await?;
    for key in keys {
        if !names.contains(&key) {
            names.push(key);
        }
    }
    Ok(names)
}

/// Bindings from a standard's feature code to the attribute that carries its
/// value (`attribute_definition.standard_codes`, e.g. `{"etim": "EF000123"}` →
/// attribute name).
///
/// A code claimed twice keeps the first binding instead of flapping (the write
/// path rejects duplicates, but old data may still carry one).
async fn load_feature_bindings(db: &Db) -> Result<HashMap<String, String>, ApiError> {
    let sql =
        "SELECT name, standard_codes FROM attribute_definition WHERE standard_codes IS NOT NONE;";
    let mut response = db.inner().query(sql).await?.check()?;
    let rows: Vec<Value> = response.take(0)?;
    let mut bindings: HashMap<String, String> = HashMap::new();
    for row in rows {
        let Some(name) = row.get("name").and_then(Value::as_str) else {
            continue;
        };
        let Some(codes) = row.get("standard_codes").and_then(Value::as_object) else {
            continue;
        };
        for code in codes.values().filter_map(Value::as_str) {
            bindings
                .entry(code.to_owned())
                .or_insert_with(|| name.to_owned());
        }
    }
    Ok(bindings)
}

async fn load_multilingual_names(db: &Db) -> Result<Vec<String>, ApiError> {
    let sql = "SELECT VALUE name FROM attribute_definition WHERE multilingual = true";
    let mut response = db.inner().query(sql).await?.check()?;
    let names: Vec<String> = response.take(0)?;
    Ok(names)
}

/// Object types that can be incomplete: the union of every
/// `attribute_definition.required_types` plus the type names that declare
/// `key_attributes` (a declared key counts as mandatory for its type).
async fn load_types_with_required(db: &Db) -> Result<Vec<String>, ApiError> {
    let sql = "
        SELECT VALUE required_types FROM attribute_definition
          WHERE array::len(required_types) > 0;
        SELECT VALUE name FROM object_type
          WHERE array::len(key_attributes) > 0;
    ";
    let mut response = db.inner().query(sql).await?.check()?;
    let from_definitions: Vec<Vec<String>> = response.take(0)?;
    let from_types: Vec<String> = response.take(1)?;
    let mut names: Vec<String> = from_definitions.into_iter().flatten().collect();
    for name in from_types {
        if !names.contains(&name) {
            names.push(name);
        }
    }
    names.sort();
    names.dedup();
    Ok(names)
}

async fn load_inheritable_names(db: &Db) -> Result<Vec<String>, ApiError> {
    let sql = "SELECT VALUE name FROM attribute_definition WHERE inheritable = true";
    let mut response = db.inner().query(sql).await?.check()?;
    let names: Vec<String> = response.take(0)?;
    Ok(names)
}

async fn load_url_attributes(db: &Db) -> Result<Vec<UrlAttribute>, ApiError> {
    let sql = "SELECT name, multilingual FROM attribute_definition WHERE type = 'url'";
    let mut response = db.inner().query(sql).await?.check()?;
    let rows: Vec<Value> = response.take(0)?;
    Ok(rows
        .into_iter()
        .filter_map(|row| {
            let name = row.get("name")?.as_str()?.to_owned();
            let multilingual = row
                .get("multilingual")
                .and_then(Value::as_bool)
                .unwrap_or(false);
            Some(UrlAttribute { name, multilingual })
        })
        .collect())
}

/// Fields of the attribute catalog projection: everything an attribute
/// *selection* or a **form field renderer** needs (label, data type, group
/// membership, whether it applies to an object type, whether it is reference
/// data, which enum set supplies its options, the constraint values the UI shows)
/// plus the flags that drive the markers (`is_key`, `inheritable`, `computed`,
/// `required_types`) — and nothing else: `SELECT *` returned 640 KB per 1000 rows,
/// this projection 318 KB.
///
/// `constraints` holds only `min`, `max` and `target_types` in this system: enum
/// options live in `enum_set` (`enum_set_id`, and the core rejects an `enum`
/// attribute without one), and a standard import never writes constraints at all —
/// measured 2026-09-17 on the dev DB (17 463 definitions): 5 rows carry a
/// constraint, 0 rows carry the legacy inline `enum_values`, no reference-data row
/// carries one. It is projected anyway (+0.32 MB) because it is the only way the
/// form gets the reference-target and numeric bounds of an attribute without a
/// second read per definition; the *option list* stays out of the vocabulary.
const VOCABULARY_FIELDS: [&str; 14] = [
    "id",
    "name",
    "label",
    "type",
    "group_ids",
    "multilingual",
    "applicable_types",
    "reference_data",
    "is_key",
    "inheritable",
    "required_types",
    "computed",
    "enum_set_id",
    "constraints",
];

/// The vocabulary query: an explicit projection (never `SELECT *`), name order.
fn vocabulary_sql() -> String {
    format!(
        "SELECT {} FROM attribute_definition ORDER BY name;",
        VOCABULARY_FIELDS.join(", ")
    )
}

/// Reads the slim vocabulary of every attribute definition and serializes it
/// **once** (the cached body is handed out verbatim).
async fn load_attribute_vocabulary(db: &Db) -> Result<String, ApiError> {
    let mut response = db.inner().query(vocabulary_sql()).await?.check()?;
    let rows: Vec<Value> = response.take(0)?;
    serde_json::to_string(&rows).map_err(|error| {
        ApiError::internal(format!(
            "could not serialize the attribute vocabulary: {error}"
        ))
    })
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn vocabulary_query_projects_only_the_fields_the_ui_needs() {
        let sql = vocabulary_sql();
        // `SELECT *` on 17 463 definitions shipped the whole document per row.
        assert!(!sql.contains('*'), "the vocabulary must never select *");
        assert!(sql.contains("FROM attribute_definition"));
        assert!(sql.contains("ORDER BY name"));
        for field in VOCABULARY_FIELDS {
            assert!(
                sql.contains(field),
                "projection is missing `{field}`: {sql}"
            );
        }
        // The data-heavy fields are deliberately absent: a description, a default
        // value, the standard codes and — most importantly — the legacy inline
        // option list, which would multiply the body by the number of options per
        // attribute.
        for omitted in [
            "description",
            "enum_values",
            "default_value",
            "standard_codes",
        ] {
            assert!(
                !sql.contains(omitted),
                "`{omitted}` does not belong into the vocabulary projection"
            );
        }
    }

    #[tokio::test]
    async fn stats_lock_is_shared_per_key_and_serialises_waiters() {
        let cache = MetaCache::default();
        // The same key must hand out the same lock, otherwise the herd stays.
        assert!(Arc::ptr_eq(
            &cache.stats_lock("de"),
            &cache.stats_lock("de")
        ));
        assert!(!Arc::ptr_eq(
            &cache.stats_lock("de"),
            &cache.stats_lock("en")
        ));

        // A held lock blocks the second waiter until the payload is stored;
        // afterwards the payload is served without a second computation.
        let lock = cache.stats_lock("");
        let guard = lock.lock().await;
        let version = cache.stats_version();
        cache
            .store_stats("", version, Arc::new(serde_json::json!({"total": 42})))
            .await;
        let waiter = tokio::spawn({
            let cache = cache.stats_lock("");
            async move {
                let _guard = cache.lock().await;
                true
            }
        });
        assert!(!waiter.is_finished(), "the waiter must block on the lock");
        drop(guard);
        assert!(waiter.await.expect("waiter task panicked"));

        let cached = cache
            .cached_stats("", version)
            .await
            .expect("payload stored");
        assert_eq!(cached["total"], 42);
        // A version bump (every write) discards the payload — the version is what
        // makes a payload computed before a write unusable afterwards.
        cache.invalidate_stats();
        assert_eq!(cache.stats_version(), version + 1);
        assert!(
            cache
                .cached_stats("", cache.stats_version())
                .await
                .is_none()
        );
    }
}
