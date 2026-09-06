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

/// Attribute names required for the given object type (from `required_types`).
pub(crate) async fn required_names(db: &Db, object_type: &str) -> Result<Vec<String>, ApiError> {
    let sql = "SELECT VALUE name FROM attribute_definition WHERE $type IN required_types";
    let mut response = db
        .inner()
        .query(sql)
        .bind(("type", object_type.to_owned()))
        .await?
        .check()?;
    let names: Vec<String> = response.take(0)?;
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
    let mut resolved = own_attributes.clone();
    crate::inheritance::resolve_inheritance(cache, db, key_of(object_id), locale, &mut resolved)
        .await?;
    let (set, missing) = split_set(required, own_attributes, &resolved);
    let percent = (set.len() as i64 * 100 / required.len() as i64).min(100);
    Ok(Completeness { percent, missing })
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
}
