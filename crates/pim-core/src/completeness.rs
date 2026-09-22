//! Completeness (Vollständigkeit) computation for objects.
//!
//! An object's completeness is the percentage of its required attributes that
//! are set. Required are the attributes with `required_types` for the object's
//! type (plus the type's `key_attributes`) **and** the class features its
//! taxonomy nodes mark as mandatory (`category_attribute.required`, see
//! `class_features`). A required attribute counts as *set* when its key is
//! present in the object's own `attributes` (including an explicitly empty/null
//! value, which breaks inheritance) or when a non-null value is inherited along
//! `depends_on`.
//!
//! Computation lives in the core so it is consistent across grid, form and
//! dashboard (per object type × working language).

use std::collections::HashMap;
use std::sync::Arc;

use serde_json::{Map, Value};

use crate::cache::MetaCache;
use crate::db::Db;
use crate::error::ApiError;

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
///
/// Cached per type ([`MetaCache::required_names`]) — the read paths ask for it
/// per object, and the lookup is not free.
pub(crate) async fn required_names(
    cache: &MetaCache,
    db: &Db,
    object_type: &str,
) -> Result<std::sync::Arc<Vec<String>>, ApiError> {
    cache.required_names(db, object_type).await
}

/// Object types whose objects **can** be incomplete: the types with required
/// attributes (cached) plus the types that are actually categorised — their
/// classes may mark features as mandatory ([`crate::class_features`]).
///
/// Callers that aggregate over a whole dataset (`GET /objects/stats`) use this to
/// narrow the scan: objects of every other type are complete by definition, so
/// their attributes never have to be loaded.
pub(crate) async fn incomplete_candidates(
    cache: &MetaCache,
    db: &Db,
) -> Result<Vec<String>, ApiError> {
    let mut types = cache.types_with_required(db).await?.as_ref().clone();
    let relations = cache.category_relation_keys(db).await?;
    if !relations.is_empty() {
        let mut response = db
            .inner()
            .query(
                "SELECT array::distinct(in.type) AS types FROM reference \
                 WHERE definition_id IN $relations GROUP ALL;",
            )
            .bind(("relations", relations))
            .await?
            .check()?;
        let rows: Vec<Value> = response.take(0)?;
        for row in rows {
            for name in row
                .get("types")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .filter_map(Value::as_str)
            {
                if !types.iter().any(|existing| existing == name) {
                    types.push(name.to_owned());
                }
            }
        }
    }
    types.sort();
    Ok(types)
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
    let mut required = required_names(cache, db, object_type)
        .await?
        .as_ref()
        .clone();
    // Class features (Stage B): the mandatory attributes of the object's
    // taxonomy classes count as required too.
    let class_required =
        crate::class_features::class_required_names(cache, db, key_of(object_id)).await?;
    crate::class_features::merge_names(&mut required, &class_required);
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
    if all_required_present(required, own_attributes) {
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

/// Whether `own` carries every required name itself — the completeness fast path.
///
/// A name that is absent from `own` can still be complete through inheritance,
/// but only if it is `inheritable`; a non-inheritable name can never be filled.
/// The fast path must therefore ask `own`, **not** the inheritable subset:
/// asking the subset reported objects as complete whose only deficits were
/// non-inheritable class features (found 2026-09-17 — `GET /objects` said 100 %
/// for a variant that the single-object endpoint scored 40 %).
fn all_required_present(required: &[String], own: &Map<String, Value>) -> bool {
    required.iter().all(|name| own.contains_key(name.as_str()))
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

    // Required names per type, resolved once per type (cached across calls).
    let mut required_by_type: HashMap<String, Vec<String>> = HashMap::new();
    for (_, otype, _) in items {
        if !required_by_type.contains_key(otype) {
            required_by_type.insert(
                otype.clone(),
                required_names(cache, db, otype).await?.as_ref().clone(),
            );
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
    // The composition map and the ancestors' attributes come from
    // `crate::inheritance` — the same two helpers the grid's inheritance
    // resolution uses, so both describe a page identically. The two calls of one
    // page (completeness + inheritance) each read them, which is ~0.2 s together
    // and not worth a request-scoped cache.
    let parent_by_key = crate::inheritance::load_parent_map(db).await?;
    // Attributes of the batch's **ancestors** (they are not part of `items` when a
    // caller passes one page of objects). Without them the in-memory walk stops
    // at the first unknown parent and the page would under-report inherited
    // values — that is why `list_objects` used to compute completeness per row.
    // One extra query, deduplicated across the batch.
    let ancestor_attrs = crate::inheritance::load_ancestor_attributes(
        db,
        &parent_by_key,
        attrs_by_key.keys().map(String::as_str),
    )
    .await?;
    for (key, attrs, _) in ancestor_attrs {
        attrs_by_key.entry(key).or_insert(attrs);
    }
    // Class features (Stage B): mandatory class attributes per object, resolved
    // from the categorisation edges plus the parent map we already have.
    let object_keys: Vec<String> = items
        .iter()
        .map(|(object_id, _, _)| key_of(object_id).to_owned())
        .collect();
    let class_required_by_key =
        crate::class_features::class_required_names_batch(cache, db, &object_keys, &parent_by_key)
            .await?;
    let fallback_chain: Option<Arc<Vec<String>>> = match locale {
        Some(locale) => Some(cache.fallback_chain(db, locale).await?),
        None => None,
    };

    for (object_id, otype, own) in items {
        let mut required = required_by_type.get(otype).cloned().unwrap_or_default();
        if let Some(class_required) = class_required_by_key.get(key_of(object_id)) {
            crate::class_features::merge_names(&mut required, class_required);
        }
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
        // Same fast path as `compute_with_required` (see `all_required_present`).
        if all_required_present(&required, own) {
            out.insert(
                object_id.clone(),
                Completeness {
                    percent: 100,
                    missing: Vec::new(),
                },
            );
            continue;
        }
        // Only inheritable names missing from `own` can be filled from an ancestor;
        // the others stay missing and are counted by `split_set` below.
        let missing: Vec<String> = required
            .iter()
            .filter(|name| {
                !own.contains_key(name.as_str()) && inheritable_set.contains(name.as_str())
            })
            .cloned()
            .collect();
        let (resolved, _) = crate::inheritance::resolve_missing_in_memory(
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
    fn fast_path_requires_the_names_in_own_attributes() {
        let required = vec!["sku".to_owned(), "weight_kg".to_owned()];
        let mut own = Map::new();
        own.insert("sku".to_owned(), Value::from("SKU-1"));
        // One name is absent → the object is **not** complete, no matter whether
        // the name could be inherited (`compute_batch` used to ask the inheritable
        // subset here and therefore reported such objects as 100 %).
        assert!(!all_required_present(&required, &own));
        own.insert("weight_kg".to_owned(), Value::Null);
        // Present even with a `null` value counts as "own" (it stops inheritance).
        assert!(all_required_present(&required, &own));
        assert!(all_required_present(&[], &Map::new()));
    }
}
