//! Attribute value inheritance along the composition (`depends_on`) chain.
//!
//! Only `inheritable: true` attribute definitions participate. An attribute
//! is considered "set" when its key exists in the object's `attributes`
//! (even `null` or an empty string) — a set value stops inheritance. Missing
//! keys are resolved by walking up to the nearest ancestor that has the key.

use serde_json::{Map, Value};

use crate::db::Db;
use crate::error::ApiError;

const MAX_DEPTH: usize = 1000;

/// Names of all attribute definitions marked `inheritable`.
pub async fn inheritable_attribute_names(db: &Db) -> Result<Vec<String>, ApiError> {
    let sql = "SELECT VALUE name FROM attribute_definition WHERE inheritable = true";
    let mut response = db.inner().query(sql).await?.check()?;
    let names: Vec<String> = response.take(0)?;
    Ok(names)
}

/// Extracts the key part of a record id (`table:key` → `key`).
fn key_of(record_id: &str) -> &str {
    record_id
        .split_once(':')
        .map(|(_, key)| key)
        .unwrap_or(record_id)
}

/// Returns the composition parent's key of an object (or `None` for the root).
async fn parent_key(db: &Db, key: &str) -> Result<Option<String>, ApiError> {
    let sql = "
        LET $src = type::record('object', $key);
        SELECT out FROM reference WHERE in = $src AND definition_id = 'rel_depends_on' LIMIT 1;
    ";
    let mut response = db
        .inner()
        .query(sql)
        .bind(("key", key.to_owned()))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(1)?;
    let out = rows
        .first()
        .and_then(|row| row.get("out"))
        .and_then(Value::as_str)
        .map(key_of)
        .map(str::to_owned);
    Ok(out)
}

/// Loads an object's `attributes` object (or `None` if not found).
async fn fetch_attributes(db: &Db, key: &str) -> Result<Option<Map<String, Value>>, ApiError> {
    let sql = "
        LET $obj = type::record('object', $key);
        SELECT VALUE attributes FROM $obj;
    ";
    let mut response = db
        .inner()
        .query(sql)
        .bind(("key", key.to_owned()))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(1)?;
    Ok(rows
        .into_iter()
        .next()
        .and_then(|value| value.as_object().cloned()))
}

/// Resolves inheritable attributes that are missing from `attributes` by
/// walking up the `depends_on` chain. `locale` optionally resolves
/// multilingual ancestor values before inheriting them.
pub async fn resolve_inheritance(
    db: &Db,
    object_key: &str,
    locale: Option<&str>,
    attributes: &mut Map<String, Value>,
) -> Result<(), ApiError> {
    let names = inheritable_attribute_names(db).await?;
    let missing: Vec<String> = names
        .into_iter()
        .filter(|name| !attributes.contains_key(name))
        .collect();
    if missing.is_empty() {
        return Ok(());
    }

    // Walk up the composition chain once (including the universal root).
    let mut chain: Vec<String> = Vec::new();
    let mut current = object_key.to_owned();
    for _ in 0..MAX_DEPTH {
        match parent_key(db, &current).await? {
            Some(parent) => {
                chain.push(parent.clone());
                current = parent;
            }
            None => break,
        }
    }

    for name in missing {
        for ancestor_key in &chain {
            let Some(ancestor_attributes) = fetch_attributes(db, ancestor_key).await? else {
                continue;
            };
            if let Some(value) = ancestor_attributes.get(&name) {
                let inherited = match (locale, value.is_object()) {
                    (Some(locale), true) => crate::language::resolve_value(db, locale, value)
                        .await?
                        .unwrap_or(Value::Null),
                    _ => value.clone(),
                };
                attributes.insert(name, inherited);
                break;
            }
        }
    }
    Ok(())
}
