//! Class features (Stage B): the attributes a taxonomy node ("class") requires.
//!
//! A **class** is a taxonomy node (object type with `taxonomy: true`, see
//! `plan/detail/kategorisierung.md`). The `category_attribute` table attaches
//! attribute definitions to such a node: `required` marks the class's mandatory
//! features, everything else is an optional feature of the class.
//!
//! An object that is **assigned** to a class (via any categorisation relation)
//! gets that class's features — plus the features of the class's **ancestors**
//! (`depends_on` chain), so a taxonomy can define shared features high up and
//! specific ones on the leaves (ETIM/BMEcat style).
//!
//! Used by:
//!
//! - `completeness` — class-required names count towards an object's
//!   completeness and appear in `?missing_required`.
//! - `GET /objects/{id}/category-attributes` — the resolved feature list for an
//!   object (form section, rule context).
//!
//! The module keeps two paths: per-object resolution (few small indexed queries,
//! taxonomy depth is small) and a batch path that reuses an already loaded
//! `depends_on` parent map (see `completeness::compute_batch`).

use std::collections::{HashMap, HashSet};
use std::sync::Arc;

use serde_json::{Map, Value};

use crate::cache::MetaCache;
use crate::db::Db;
use crate::error::ApiError;

/// Maximum taxonomy depth walked when resolving a class's ancestors.
const MAX_DEPTH: usize = 64;

/// Categorisation edges: `object key → class node keys` (bare keys). Cached
/// version-bound in [`MetaCache::category_edges`].
pub(crate) type CategoryEdges = HashMap<String, Vec<String>>;

/// Bare key part of a record id (`table:key` → `key`).
pub(crate) fn key_of(record_id: &str) -> &str {
    record_id
        .split_once(':')
        .map(|(_, key)| key)
        .unwrap_or(record_id)
}

/// Relation keys (`rel_…`) of every categorisation relation, i.e. of every
/// relation type whose usage is `category` (any dimension).
///
/// Callers on a hot path use [`crate::cache::MetaCache::category_relation_keys`],
/// which caches this answer (it changes only when a relation definition does).
pub(crate) async fn load_category_relation_keys(db: &Db) -> Result<Vec<String>, ApiError> {
    let mut response = db
        .inner()
        .query("SELECT id FROM relation_definition WHERE role = $role;")
        .bind(("role", crate::api::references::ROLE_CATEGORY))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    Ok(rows
        .iter()
        .filter_map(|row| row.get("id").and_then(Value::as_str))
        .map(|id| key_of(id).to_owned())
        .collect())
}

/// Feature rows of one class node: `(attribute name, required)`.
pub(crate) async fn node_features(
    db: &Db,
    node_key: &str,
) -> Result<Vec<(String, bool)>, ApiError> {
    let mut response = db
        .inner()
        .query(
            // No `ORDER BY position` here: `position` is a reserved word in
            // SurrealDB's parser (`Missing order idiom`) — the resolved feature
            // lists are sorted in Rust instead.
            "SELECT attribute, required FROM category_attribute \
             WHERE category_id = type::record('object', $key);",
        )
        .bind(("key", node_key.to_owned()))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    Ok(rows
        .into_iter()
        .filter_map(|row| {
            let name = row.get("attribute").and_then(Value::as_str)?;
            let required = row
                .get("required")
                .and_then(Value::as_bool)
                .unwrap_or(false);
            Some((name.to_owned(), required))
        })
        .collect())
}

/// Class nodes an object is assigned to (through any categorisation relation).
///
/// The object is rendered as a **record literal** (see
/// `crate::api::keys::record_literal`): a bound record would make SurrealDB scan
/// the whole `reference` table, while the literal drives `idx_ref_in`; the
/// relation list stays bound and is applied as a filter on top of the index scan.
pub(crate) async fn assigned_node_keys(
    db: &Db,
    object_key: &str,
    relations: &[String],
) -> Result<Vec<String>, ApiError> {
    if relations.is_empty() {
        return Ok(Vec::new());
    }
    let Some(source) = crate::api::keys::record_literal("object", object_key) else {
        return Ok(Vec::new());
    };
    let mut response = db
        .inner()
        .query(format!(
            "SELECT VALUE out FROM reference \
             WHERE in = {source} AND definition_id IN $relations;"
        ))
        .bind(("relations", relations.to_vec()))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    Ok(rows
        .iter()
        .filter_map(Value::as_str)
        .map(|id| key_of(id).to_owned())
        .collect())
}

/// All categorisation edges as `object key → class node keys` (bare keys), in
/// **one** query — the input of [`MetaCache::category_edges`], which caches it
/// version-bound.
///
/// Without the cache every completeness page walked all category edges again
/// (7 324 rows in the demo data, +0,11 s quiet and multiples of that under load)
/// although only the page's objects are needed.
pub(crate) async fn load_category_edges(
    db: &Db,
    relations: &[String],
) -> Result<CategoryEdges, ApiError> {
    let mut edges: HashMap<String, Vec<String>> = HashMap::new();
    if relations.is_empty() {
        return Ok(edges);
    }
    let mut response = db
        .inner()
        .query(format!(
            "SELECT in AS object, out AS node FROM reference WHERE definition_id IN {};",
            crate::api::keys::record_list_literal("object", relations.iter().map(String::as_str))
        ))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    for row in rows {
        let object = row.get("object").and_then(Value::as_str).map(key_of);
        let node = row.get("node").and_then(Value::as_str).map(key_of);
        if let (Some(object), Some(node)) = (object, node) {
            edges
                .entry(object.to_owned())
                .or_default()
                .push(node.to_owned());
        }
    }
    Ok(edges)
}

/// Direct `depends_on` parent of an object key (`None` at the root).
pub(crate) async fn parent_key(db: &Db, object_key: &str) -> Result<Option<String>, ApiError> {
    let mut response = db
        .inner()
        .query(
            "SELECT VALUE out FROM reference \
             WHERE in = type::record('object', $key) AND definition_id = 'rel_depends_on' LIMIT 1;",
        )
        .bind(("key", object_key.to_owned()))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    Ok(rows
        .first()
        .and_then(Value::as_str)
        .map(|id| key_of(id).to_owned()))
}

/// Walks the `depends_on` chain upwards, returning `node_key` first and then its
/// ancestors (cycle-safe, bounded by [`MAX_DEPTH`]).
pub(crate) fn ancestor_chain(node_key: &str, parents: &HashMap<String, String>) -> Vec<String> {
    let mut chain = vec![node_key.to_owned()];
    let mut seen: HashSet<String> = HashSet::from([node_key.to_owned()]);
    let mut current = node_key.to_owned();
    for _ in 0..MAX_DEPTH {
        let Some(parent) = parents.get(&current) else {
            break;
        };
        if !seen.insert(parent.clone()) {
            break;
        }
        chain.push(parent.clone());
        current = parent.clone();
    }
    chain
}

/// Maps a standard's feature code to the attribute that carries its value.
///
/// `attribute_definition.standard_codes` binds an attribute to a standard's
/// feature code (`{"etim": "EF000123"}`), so an **own** attribute can serve a
/// class feature without being renamed after the standard
/// (`plan/detail/bmecat-export.md` §11.7). An attribute that is *named* like the
/// code needs no entry — the feature name is the fallback in
/// [`resolved_features`].
///
/// The bindings are loaded through [`crate::cache::MetaCache::feature_bindings`]
/// (cached, invalidated with the attribute definitions): with a classification
/// standard installed **every** feature definition carries a binding, so the
/// query is 17 397 rows / ~0.3 s and must not run per object.
pub(crate) async fn feature_bindings(
    cache: &MetaCache,
    db: &Db,
) -> Result<Arc<HashMap<String, String>>, ApiError> {
    cache.feature_bindings(db).await
}

/// The attribute that carries a feature's value: the **binding** wins, otherwise
/// the feature's own name is the attribute name (the path of a standard whose
/// features *are* attributes). Pure so the rule is unit-testable.
pub(crate) fn resolve_attribute(feature: &str, bindings: &HashMap<String, String>) -> String {
    bindings
        .get(feature)
        .cloned()
        .unwrap_or_else(|| feature.to_owned())
}

/// Ancestor chain of a node using single-node queries (per-object path).
pub(crate) async fn ancestor_chain_live(db: &Db, node_key: &str) -> Result<Vec<String>, ApiError> {
    let mut chain = vec![node_key.to_owned()];
    let mut seen: HashSet<String> = HashSet::from([node_key.to_owned()]);
    let mut current = node_key.to_owned();
    for _ in 0..MAX_DEPTH {
        let Some(parent) = parent_key(db, &current).await? else {
            break;
        };
        if parent == "object_root" || !seen.insert(parent.clone()) {
            break;
        }
        chain.push(parent.clone());
        current = parent;
    }
    Ok(chain)
}

/// One resolved feature of an object: the feature as the class defines it, the
/// attribute that carries its value, whether it is mandatory for at least one
/// contributing class, and the contributing classes.
#[derive(Debug, Clone)]
pub(crate) struct ResolvedFeature {
    /// The feature as the class names it — for a classification standard that is
    /// the standard's feature code (`EF…`), which is what an export transmits.
    pub name: String,
    /// The attribute definition whose value carries the feature.
    pub attribute: String,
    pub required: bool,
    pub nodes: Vec<String>,
}

/// Resolves the features an object inherits from its classes, walking each
/// assigned class's ancestor chain (per-object path).
///
/// Returns the features sorted by name; a feature required by one class and
/// optional in another counts as required.
pub(crate) async fn resolved_features(
    cache: &MetaCache,
    db: &Db,
    object_key: &str,
) -> Result<Vec<ResolvedFeature>, ApiError> {
    let relations = cache.category_relation_keys(db).await?;
    let assigned = assigned_node_keys(db, object_key, &relations).await?;
    let bindings = feature_bindings(cache, db).await?;
    let mut features: HashMap<String, ResolvedFeature> = HashMap::new();
    let mut seen_nodes: HashSet<String> = HashSet::new();
    for node in assigned {
        for chain_node in ancestor_chain_live(db, &node).await? {
            if !seen_nodes.insert(chain_node.clone()) {
                continue;
            }
            for (name, required) in node_features(db, &chain_node).await? {
                let attribute = resolve_attribute(&name, &bindings);
                let entry = features.entry(name.clone()).or_insert(ResolvedFeature {
                    name,
                    attribute,
                    required: false,
                    nodes: Vec::new(),
                });
                entry.required = entry.required || required;
                if !entry.nodes.contains(&chain_node) {
                    entry.nodes.push(chain_node.clone());
                }
            }
        }
    }
    let mut resolved: Vec<ResolvedFeature> = features.into_values().collect();
    resolved.sort_by(|a, b| a.name.cmp(&b.name));
    Ok(resolved)
}

/// Names of the attributes an object **must** have (required by at least one of
/// its classes). Used by the completeness computation: it counts **values**, so a
/// feature bound to an own attribute contributes that attribute's name.
pub(crate) async fn class_required_names(
    cache: &MetaCache,
    db: &Db,
    object_key: &str,
) -> Result<Vec<String>, ApiError> {
    let mut names: Vec<String> = Vec::new();
    for feature in resolved_features(cache, db, object_key).await? {
        if feature.required && !names.contains(&feature.attribute) {
            names.push(feature.attribute);
        }
    }
    Ok(names)
}

/// Class-required names for many objects in one pass.
///
/// `object_keys` are the bare object keys of the batch, `parents` the already
/// loaded `depends_on` map (`child → parent`), as `completeness::compute_batch`
/// has it. Two bulk queries (categorisation edges, feature rows) plus the
/// in-memory ancestor walk keep this cheap for large result sets.
pub(crate) async fn class_required_names_batch(
    cache: &MetaCache,
    db: &Db,
    object_keys: &[String],
    parents: &HashMap<String, String>,
) -> Result<HashMap<String, Vec<String>>, ApiError> {
    let mut out: HashMap<String, Vec<String>> = HashMap::new();
    if object_keys.is_empty() {
        return Ok(out);
    }
    let relations = cache.category_relation_keys(db).await?;
    if relations.is_empty() {
        return Ok(out);
    }
    let bindings = feature_bindings(cache, db).await?;
    // Assignments of the whole batch, read from the version-bound cache instead of
    // scanning every category edge per call.
    let edges = cache.category_edges(db).await?;
    let wanted: HashSet<&str> = object_keys.iter().map(String::as_str).collect();
    let mut assigned: HashMap<String, Vec<String>> = HashMap::new();
    for (object, nodes) in edges.iter() {
        if wanted.contains(object.as_str()) {
            assigned.insert(object.clone(), nodes.clone());
        }
    }
    if assigned.is_empty() {
        return Ok(out);
    }
    // Feature rows of the **contributing** classes only (the assigned nodes and
    // their ancestors). Loading the whole table instead costs ~2 s per call with a
    // classification standard installed (ETIM 10.0: **76 625** rows / 6.8 MB,
    // measured) — even when a single object of the batch is categorised, which is
    // what made `/objects/stats` and a page of products take seconds.
    let mut wanted_nodes: Vec<String> = Vec::new();
    let mut seen_nodes: HashSet<String> = HashSet::new();
    for nodes in assigned.values() {
        for node in nodes {
            for chain_node in ancestor_chain(node, parents) {
                if seen_nodes.insert(chain_node.clone()) {
                    wanted_nodes.push(chain_node);
                }
            }
        }
    }
    let mut response = db
        .inner()
        .query(format!(
            "SELECT category_id, attribute, required FROM category_attribute\n             WHERE category_id IN {};",
            crate::api::keys::record_list_literal("object", wanted_nodes.iter().map(String::as_str))
        ))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    let mut features: HashMap<String, Vec<(String, bool)>> = HashMap::new();
    for row in rows {
        let node = row
            .get("category_id")
            .and_then(Value::as_str)
            .map(key_of)
            .map(str::to_owned);
        let name = row.get("attribute").and_then(Value::as_str);
        if let (Some(node), Some(name)) = (node, name) {
            let required = row
                .get("required")
                .and_then(Value::as_bool)
                .unwrap_or(false);
            features
                .entry(node)
                .or_default()
                .push((name.to_owned(), required));
        }
    }
    for (object, nodes) in assigned {
        let mut names: Vec<String> = Vec::new();
        let mut seen: HashSet<String> = HashSet::new();
        for node in nodes {
            for chain_node in ancestor_chain(&node, parents) {
                if !seen.insert(chain_node.clone()) {
                    continue;
                }
                for (name, required) in features.get(&chain_node).cloned().unwrap_or_default() {
                    // A bound feature is checked through its own attribute.
                    let attribute = resolve_attribute(&name, &bindings);
                    if required && !names.contains(&attribute) {
                        names.push(attribute);
                    }
                }
            }
        }
        names.sort();
        out.insert(object, names);
    }
    Ok(out)
}

/// Merges `extra` names into `required` (keeping the order, skipping duplicates).
pub(crate) fn merge_names(required: &mut Vec<String>, extra: &[String]) {
    for name in extra {
        if !required.iter().any(|existing| existing == name) {
            required.push(name.clone());
        }
    }
}

/// The object's own attribute map (`{}` when the object has none).
pub(crate) fn own_attributes(object: &Value) -> Map<String, Value> {
    object
        .get("attributes")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn parents(pairs: &[(&str, &str)]) -> HashMap<String, String> {
        pairs
            .iter()
            .map(|(child, parent)| (child.to_string(), parent.to_string()))
            .collect()
    }

    #[test]
    fn ancestor_chain_starts_at_the_node_and_walks_up() {
        let map = parents(&[("leaf", "middle"), ("middle", "top")]);

        assert_eq!(ancestor_chain("leaf", &map), vec!["leaf", "middle", "top"]);
        assert_eq!(ancestor_chain("top", &map), vec!["top"]);
        assert_eq!(ancestor_chain("orphan", &map), vec!["orphan"]);
    }

    #[test]
    fn ancestor_chain_survives_a_cycle() {
        let map = parents(&[("a", "b"), ("b", "a")]);

        // The visited set stops the walk instead of looping forever.
        assert_eq!(ancestor_chain("a", &map), vec!["a", "b"]);
    }

    #[test]
    fn merge_names_keeps_the_order_and_skips_duplicates() {
        let mut required = vec!["sku".to_owned(), "power".to_owned()];

        merge_names(&mut required, &["power".to_owned(), "voltage".to_owned()]);

        assert_eq!(required, vec!["sku", "power", "voltage"]);
    }

    #[test]
    fn resolve_attribute_prefers_the_binding_over_the_name() {
        let bindings = HashMap::from([("EF000123".to_owned(), "nominal_current".to_owned())]);

        // A bound standard code is read from the own attribute …
        assert_eq!(resolve_attribute("EF000123", &bindings), "nominal_current");
        // … an unbound feature keeps its own name (the standard-as-attribute path).
        assert_eq!(resolve_attribute("EF009999", &bindings), "EF009999");
        assert_eq!(resolve_attribute("width", &HashMap::new()), "width");
    }

    #[test]
    fn own_attributes_reads_the_attribute_map() {
        let object = json!({ "attributes": { "sku": "A-1", "power": null } });

        let own = own_attributes(&object);

        assert_eq!(own.len(), 2);
        assert!(own.contains_key("sku"));
        // An explicit `null` counts as present (it breaks inheritance) — the
        // completeness split treats it as "not set" later on.
        assert!(own["power"].is_null());
        assert!(own_attributes(&json!({})).is_empty());
    }
}
