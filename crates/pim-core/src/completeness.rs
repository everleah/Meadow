//! Completeness (Vollständigkeit) computation for objects.
//!
//! An object's completeness is the percentage of its type's required
//! attributes (`required_types` on `attribute_definition`) that are set. A
//! required attribute counts as *set* when its key is present in the object's
//! own `attributes` (including an explicitly empty/null value, which breaks
//! inheritance) or when a non-null value is inherited along `depends_on`.
//!
//! Computation lives in the core so it is consistent across grid, form and
//! dashboard (per object type × working language).

use std::collections::HashMap;
use std::sync::Arc;

use serde_json::{Map, Value};

use crate::cache::MetaCache;
use crate::db::Db;
use crate::error::ApiError;

/// Maximum composition depth walked during inheritance resolution.
const MAX_DEPTH: usize = 1000;

/// Result of a completeness computation for a single object.
pub struct Completeness {
    /// Percentage of required attributes that are set (0-100).
    pub percent: i64,
    /// Names of the required attributes that are not set.
    pub missing: Vec<String>,
}

/// Bare key part of a record id (`table:key` → `key`).
fn key_of(record_id: &str) -> &str {
    record_id
        .split_once(':')
        .map(|(_, key)| key)
        .unwrap_or(record_id)
}

/// Attribute names required for the given object type. This is `required_types`
/// on `attribute_definition` **plus** the type's `key_attributes` (a key that a
/// type declares counts as mandatory for that type, per the object-type model).
pub(crate) async fn required_names(db: &Db, object_type: &str) -> Result<Vec<String>, ApiError> {
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

/// Splits `required` into (set, missing) for an object.
///
/// `own` are the object's own attributes (before inheritance); `resolved` is
/// `own` after `resolve_inheritance` fills inherited values. A name is *set*
/// when its key is present in `own` (even an explicit empty/null value, which
/// breaks inheritance) or when it was inherited as a non-null value.
fn split_set(
    required: &[String],
    own: &Map<String, Value>,
    resolved: &Map<String, Value>,
) -> (Vec<String>, Vec<String>) {
    let mut set = Vec::new();
    let mut missing = Vec::new();
    for name in required {
        if own.contains_key(name.as_str()) {
            set.push(name.clone());
        } else if let Some(value) = resolved.get(name.as_str()) {
            if !value.is_null() {
                set.push(name.clone());
            } else {
                missing.push(name.clone());
            }
        } else {
            missing.push(name.clone());
        }
    }
    (set, missing)
}

/// Computes the completeness for a single object.
///
/// `object_id` is the full record id (`object:<key>`), `object_type` the type,
/// `own_attributes` the object's own attribute map. Returns 100 (and no
/// missing) when the type has no required attributes.
pub async fn compute(
    cache: &MetaCache,
    db: &Db,
    object_id: &str,
    object_type: &str,
    locale: Option<&str>,
    own_attributes: &Map<String, Value>,
) -> Result<Completeness, ApiError> {
    let required = required_names(db, object_type).await?;
    compute_with_required(cache, db, object_id, locale, own_attributes, &required).await
}

/// Completeness for a single object with the required attribute names already
/// resolved (so callers aggregating over many objects can reuse them).
pub(crate) async fn compute_with_required(
    cache: &MetaCache,
    db: &Db,
    object_id: &str,
    locale: Option<&str>,
    own_attributes: &Map<String, Value>,
    required: &[String],
) -> Result<Completeness, ApiError> {
    if required.is_empty() {
        return Ok(Completeness {
            percent: 100,
            missing: Vec::new(),
        });
    }
    // Fast path: if every required name is already present in `own` (even an
    // explicit empty/null value, which stops inheritance), the object is
    // complete without resolving inheritance.
    if required
        .iter()
        .all(|name| own_attributes.contains_key(name.as_str()))
    {
        return Ok(Completeness {
            percent: 100,
            missing: Vec::new(),
        });
    }
    let mut resolved = own_attributes.clone();
    crate::inheritance::resolve_inheritance(cache, db, key_of(object_id), locale, &mut resolved)
        .await?;
    let (set, missing) = split_set(required, own_attributes, &resolved);
    let percent = (set.len() as i64 * 100 / required.len() as i64).min(100);
    Ok(Completeness { percent, missing })
}

/// Loads the single-parent `depends_on` map (`child key → parent key`) in one
/// query, for the batched in-memory inheritance resolution.
async fn load_parent_map(db: &Db) -> Result<HashMap<String, String>, ApiError> {
    let sql =
        "SELECT in AS child, out AS parent FROM reference WHERE definition_id = 'rel_depends_on';";
    let mut response = db.inner().query(sql).await?.check()?;
    let rows: Vec<Value> = response.take(0)?;
    let mut map: HashMap<String, String> = HashMap::new();
    for row in rows {
        let child = row.get("child").and_then(Value::as_str).map(key_of);
        let parent = row.get("parent").and_then(Value::as_str).map(key_of);
        if let (Some(child), Some(parent)) = (child, parent) {
            map.entry(child.to_owned())
                .or_insert_with(|| parent.to_owned());
        }
    }
    Ok(map)
}

/// Computes completeness for many objects in one pass (Phase 8 optimization).
///
/// Items are `(object_id, object_type, own_attributes)`. The optional `locale`
/// resolves multilingual ancestor values before inheriting them (same semantics
/// as [`compute_with_required`]). Inheritance is resolved **in memory** from a
/// single `depends_on` edge query instead of issuing one query per ancestor per
/// object, so aggregating over a whole dataset (e.g. `GET /objects/stats`) does
/// not hammer SurrealDB per level.
pub(crate) async fn compute_batch(
    cache: &MetaCache,
    db: &Db,
    items: &[(String, String, Map<String, Value>)],
    locale: Option<&str>,
) -> Result<HashMap<String, Completeness>, ApiError> {
    let mut out = HashMap::new();
    if items.is_empty() {
        return Ok(out);
    }

    // Required names per type, resolved once per type.
    let mut required_by_type: HashMap<String, Vec<String>> = HashMap::new();
    for (_, otype, _) in items {
        if !required_by_type.contains_key(otype) {
            required_by_type.insert(otype.clone(), required_names(db, otype).await?);
        }
    }
    // Only `inheritable` attribute names can be filled from ancestors (matching
    // `resolve_inheritance`); a required non-inheritable name stays missing
    // when it is absent from the object's own attributes.
    let inheritable = cache.inheritable_names(db).await?;
    let inheritable_set: std::collections::HashSet<&str> =
        inheritable.iter().map(String::as_str).collect();
    // Own attributes and type per object key (for in-memory ancestor lookup).
    let mut attrs_by_key: HashMap<String, Map<String, Value>> = HashMap::new();
    for (object_id, _, own) in items {
        attrs_by_key.insert(key_of(object_id).to_owned(), own.clone());
    }
    let parent_by_key = load_parent_map(db).await?;
    let fallback_chain: Option<Arc<Vec<String>>> = match locale {
        Some(locale) => Some(cache.fallback_chain(db, locale).await?),
        None => None,
    };

    for (object_id, otype, own) in items {
        let required = required_by_type.get(otype).cloned().unwrap_or_default();
        if required.is_empty() {
            out.insert(
                object_id.clone(),
                Completeness {
                    percent: 100,
                    missing: Vec::new(),
                },
            );
            continue;
        }
        // Only inheritable names missing from `own` need inheritance resolution.
        let missing: Vec<String> = required
            .iter()
            .filter(|name| {
                !own.contains_key(name.as_str()) && inheritable_set.contains(name.as_str())
            })
            .cloned()
            .collect();
        if missing.is_empty() {
            out.insert(
                object_id.clone(),
                Completeness {
                    percent: 100,
                    missing: Vec::new(),
                },
            );
            continue;
        }
        let resolved = resolve_missing_in_memory(
            own,
            &missing,
            &parent_by_key,
            &attrs_by_key,
            locale,
            fallback_chain.as_ref().map(|chain| chain.as_slice()),
            key_of(object_id),
        );
        let (set, missing_names) = split_set(&required, own, &resolved);
        let percent = (set.len() as i64 * 100 / required.len() as i64).min(100);
        out.insert(
            object_id.clone(),
            Completeness {
                percent,
                missing: missing_names,
            },
        );
    }
    Ok(out)
}

/// Resolves the given `missing` (inheritable) attribute names from an in-memory
/// parent map, starting at `start_key`. Returns `own` with the inherited values
/// filled (`NULL` where an ancestor declares the key but the value is empty).
///
/// Mirrors [`crate::inheritance::resolve_inheritance`]'s semantics: walking one
/// query per level is replaced by a `parent_by_key`/`attrs_by_key` lookup, so
/// aggregating over a whole dataset needs no per-ancestor database round-trips.
#[allow(clippy::too_many_arguments)]
fn resolve_missing_in_memory(
    own: &Map<String, Value>,
    missing: &[String],
    parent_by_key: &HashMap<String, String>,
    attrs_by_key: &HashMap<String, Map<String, Value>>,
    locale: Option<&str>,
    fallback_chain: Option<&[String]>,
    start_key: &str,
) -> Map<String, Value> {
    let mut resolved = own.clone();
    let mut current = start_key.to_owned();
    let mut todo = missing.to_vec();
    for _ in 0..MAX_DEPTH {
        let Some(parent) = parent_by_key.get(&current).cloned() else {
            break;
        };
        let Some(parent_attrs) = attrs_by_key.get(&parent) else {
            break;
        };
        let mut still_missing = Vec::new();
        for name in todo {
            match parent_attrs.get(&name) {
                Some(value) => {
                    let inherited = match (locale, fallback_chain, value.is_object()) {
                        (Some(locale), Some(chain), true) => {
                            crate::language::resolve_value_with_chain(locale, value, chain)
                                .unwrap_or(Value::Null)
                        }
                        _ => value.clone(),
                    };
                    resolved.insert(name, inherited);
                }
                None => still_missing.push(name),
            }
        }
        todo = still_missing;
        if todo.is_empty() {
            break;
        }
        current = parent;
    }
    resolved
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn split_set_own_and_inherited() {
        let required = vec!["name".to_owned(), "sku".to_owned(), "price".to_owned()];
        let mut own = Map::new();
        own.insert("name".to_owned(), Value::from("Produkt"));
        own.insert("price".to_owned(), Value::Null); // explicit empty → set (interruption)
        let mut resolved = own.clone();
        resolved.insert("sku".to_owned(), Value::from("SKU-1")); // inherited
        let (set, missing) = split_set(&required, &own, &resolved);
        assert_eq!(
            set,
            vec!["name".to_owned(), "sku".to_owned(), "price".to_owned()]
        );
        assert!(missing.is_empty());
    }

    #[test]
    fn split_set_missing_not_counted() {
        let required = vec!["name".to_owned(), "sku".to_owned()];
        let own = Map::new();
        let resolved = Map::new();
        let (set, missing) = split_set(&required, &own, &resolved);
        assert!(set.is_empty());
        assert_eq!(missing.len(), 2);
    }

    #[test]
    fn split_set_inherited_null_not_set() {
        let required = vec!["name".to_owned()];
        let own = Map::new();
        let mut resolved = Map::new();
        resolved.insert("name".to_owned(), Value::Null); // resolved to nothing → not set
        let (set, missing) = split_set(&required, &own, &resolved);
        assert!(set.is_empty());
        assert_eq!(missing, vec!["name".to_owned()]);
    }

    #[test]
    fn resolve_missing_inherits_from_parent() {
        let own = Map::new();
        let missing = vec!["sku".to_owned()];
        let mut parents = HashMap::new();
        parents.insert("child".to_owned(), "parent".to_owned());
        let mut attrs = HashMap::new();
        let mut parent_attrs = Map::new();
        parent_attrs.insert("sku".to_owned(), Value::from("SKU-1"));
        attrs.insert("parent".to_owned(), parent_attrs);
        let resolved =
            resolve_missing_in_memory(&own, &missing, &parents, &attrs, None, None, "child");
        assert_eq!(resolved.get("sku").and_then(Value::as_str), Some("SKU-1"));
    }

    #[test]
    fn resolve_missing_stops_at_nearest_ancestor() {
        // Child misses `sku`; grandparent also has it, but the nearest parent wins.
        let own = Map::new();
        let missing = vec!["sku".to_owned()];
        let mut parents = HashMap::new();
        parents.insert("child".to_owned(), "parent".to_owned());
        parents.insert("parent".to_owned(), "grandparent".to_owned());
        let mut attrs = HashMap::new();
        let mut parent_attrs = Map::new();
        parent_attrs.insert("sku".to_owned(), Value::from("nearest"));
        let mut grand_attrs = Map::new();
        grand_attrs.insert("sku".to_owned(), Value::from("farther"));
        attrs.insert("parent".to_owned(), parent_attrs);
        attrs.insert("grandparent".to_owned(), grand_attrs);
        let resolved =
            resolve_missing_in_memory(&own, &missing, &parents, &attrs, None, None, "child");
        assert_eq!(resolved.get("sku").and_then(Value::as_str), Some("nearest"));
    }

    #[test]
    fn resolve_missing_null_value_is_not_set() {
        // A parent declares `sku` as null → it stops inheritance but does not set
        // the value (the further ancestor is never reached).
        let own = Map::new();
        let missing = vec!["sku".to_owned()];
        let mut parents = HashMap::new();
        parents.insert("child".to_owned(), "parent".to_owned());
        parents.insert("parent".to_owned(), "grandparent".to_owned());
        let mut attrs = HashMap::new();
        let mut parent_attrs = Map::new();
        parent_attrs.insert("sku".to_owned(), Value::Null);
        let mut grand_attrs = Map::new();
        grand_attrs.insert("sku".to_owned(), Value::from("farther"));
        attrs.insert("parent".to_owned(), parent_attrs);
        attrs.insert("grandparent".to_owned(), grand_attrs);
        let resolved =
            resolve_missing_in_memory(&own, &missing, &parents, &attrs, None, None, "child");
        assert_eq!(resolved.get("sku"), Some(&Value::Null));
        let (set, missing) = split_set(&["sku".to_owned()], &own, &resolved);
        assert!(set.is_empty());
        assert_eq!(missing, vec!["sku".to_owned()]);
    }

    #[test]
    fn resolve_missing_multilingual_locale() {
        let own = Map::new();
        let missing = vec!["title".to_owned()];
        let mut parents = HashMap::new();
        parents.insert("child".to_owned(), "parent".to_owned());
        let mut attrs = HashMap::new();
        let mut parent_attrs = Map::new();
        parent_attrs.insert(
            "title".to_owned(),
            serde_json::json!({ "root": "Produkt", "de": "Produkt DE", "en": "Product" }),
        );
        attrs.insert("parent".to_owned(), parent_attrs);
        let chain = vec!["root".to_owned()];
        let resolved = resolve_missing_in_memory(
            &own,
            &missing,
            &parents,
            &attrs,
            Some("de"),
            Some(&chain),
            "child",
        );
        assert_eq!(
            resolved.get("title").and_then(Value::as_str),
            Some("Produkt DE")
        );
    }
}
