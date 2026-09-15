//! In-memory cache for slow-changing metadata (Phase 3).
//!
//! Caches the attribute names marked `multilingual`/`inheritable`/`url` and the
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
    url_attributes: RwLock<Option<Arc<Vec<UrlAttribute>>>>,
    feature_bindings: RwLock<Option<Arc<HashMap<String, String>>>>,
    attribute_vocabulary: RwLock<Option<Arc<String>>>,
    required_names: RwLock<HashMap<String, Arc<Vec<String>>>>,
    fallback_chains: RwLock<HashMap<String, Arc<Vec<String>>>>,
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

    /// Slim projection of **all** attribute definitions as a **serialized JSON
    /// body**, cached.
    ///
    /// Two consumers: the export wizard offers an attribute selection over every
    /// definition (`/export`), and the catalog page (`/attribute-definitions`)
    /// lists them all — its paged list endpoint is capped at 1000 rows, and with a
    /// classification standard installed those are the standard's features, so own
    /// attributes were unreachable. The projection carries only what such a list or
    /// selection needs (`VOCABULARY_FIELDS`): 318 KB per 1000 rows and ~6 MB for the
    /// whole vocabulary in one query.
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

    /// Invalidates the cached per-type required names after object-type writes
    /// (`key_attributes` belongs to the type registry).
    pub async fn invalidate_object_types(&self) {
        self.required_names.write().await.clear();
    }

    /// Invalidates cached fallback chains after language-tree changes.
    pub async fn invalidate_language_tree(&self) {
        self.fallback_chains.write().await.clear();
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

/// Fields of the slim attribute catalog projection: everything an attribute
/// *selection* needs (label, data type, group membership, whether it applies to
/// an object type, whether it is reference data) plus the three flags the
/// catalog grid shows (`is_key`, `inheritable`, `required_types`) — and nothing
/// else: `SELECT *` returned 640 KB per 1000 rows, this projection 318 KB.
const VOCABULARY_FIELDS: [&str; 11] = [
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
    fn vocabulary_query_projects_only_the_slim_fields() {
        let sql = vocabulary_sql();
        // `SELECT *` on 17 379 definitions shipped the whole document per row.
        assert!(!sql.contains('*'), "the vocabulary must never select *");
        assert!(sql.contains("FROM attribute_definition"));
        assert!(sql.contains("ORDER BY name"));
        for field in VOCABULARY_FIELDS {
            assert!(
                sql.contains(field),
                "projection is missing `{field}`: {sql}"
            );
        }
        // The data-heavy fields are deliberately absent.
        for omitted in [
            "description",
            "constraints",
            "default_value",
            "standard_codes",
        ] {
            assert!(
                !sql.contains(omitted),
                "`{omitted}` does not belong into the vocabulary projection"
            );
        }
    }
}
