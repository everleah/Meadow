//! Attribute value inheritance along the composition (`depends_on`) chain.
//!
//! Only `inheritable: true` attribute definitions participate. An attribute
//! is considered "set" when its key exists in the object's `attributes`
//! (even `null` or an empty string) — a set value stops inheritance. Missing
//! keys are resolved by walking up to the nearest ancestor that has the key.
//!
//! Each ancestor is read with its attributes in a single query, so resolving
//! the chain costs one query per level (the attribute-name list is cached).

use std::sync::Arc;

use serde_json::{Map, Value};

use crate::cache::MetaCache;
use crate::db::Db;
use crate::error::ApiError;

const MAX_DEPTH: usize = 1000;

/// Extracts the key part of a record id (`table:key` → `key`).
fn key_of(record_id: &str) -> &str {
    record_id
        .split_once(':')
        .map(|(_, key)| key)
        .unwrap_or(record_id)
}

/// Returns the composition parent's key together with its `attributes`
/// in a single query (`None` for the root or when there is no parent).
async fn parent_with_attributes(
    db: &Db,
    key: &str,
) -> Result<Option<(String, Map<String, Value>)>, ApiError> {
    let sql = "
        LET $src = type::record('object', $key);
        SELECT out AS parent, out.attributes AS attributes
        FROM reference WHERE in = $src AND definition_id = 'rel_depends_on' LIMIT 1;
    ";
    let mut response = db
        .inner()
        .query(sql)
        .bind(("key", key.to_owned()))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(1)?;
    let Some(row) = rows.into_iter().next() else {
        return Ok(None);
    };
    let Some(parent) = row
        .get("parent")
        .and_then(Value::as_str)
        .map(key_of)
        .map(str::to_owned)
    else {
        return Ok(None);
    };
    let attributes = row
        .get("attributes")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    Ok(Some((parent, attributes)))
}

/// Resolves inheritable attributes that are missing from `attributes` by
/// walking up the `depends_on` chain. `locale` optionally resolves
/// multilingual ancestor values before inheriting them.
pub async fn resolve_inheritance(
    cache: &MetaCache,
    db: &Db,
    object_key: &str,
    locale: Option<&str>,
    attributes: &mut Map<String, Value>,
) -> Result<(), ApiError> {
    let names = cache.inheritable_names(db).await?;
    let mut missing: Vec<String> = names
        .iter()
        .filter(|name| !attributes.contains_key(name.as_str()))
        .cloned()
        .collect();
    if missing.is_empty() {
        return Ok(());
    }

    let fallback_chain: Option<Arc<Vec<String>>> = match locale {
        Some(locale) => Some(cache.fallback_chain(db, locale).await?),
        None => None,
    };

    let mut current = object_key.to_owned();
    for _ in 0..MAX_DEPTH {
        let Some((parent, parent_attributes)) = parent_with_attributes(db, &current).await? else {
            break;
        };
        let mut still_missing: Vec<String> = Vec::new();
        for name in missing {
            match parent_attributes.get(&name) {
                Some(value) => {
                    let inherited = match (locale, &fallback_chain, value.is_object()) {
                        (Some(locale), Some(chain), true) => {
                            crate::language::resolve_value_with_chain(locale, value, chain)
                                .unwrap_or(Value::Null)
                        }
                        _ => value.clone(),
                    };
                    attributes.insert(name, inherited);
                }
                None => still_missing.push(name),
            }
        }
        missing = still_missing;
        if missing.is_empty() {
            break;
        }
        current = parent;
    }
    Ok(())
}
