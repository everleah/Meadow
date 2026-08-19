//! Language-tree based resolution of multilingual attribute values.
//!
//! Multilingual values are stored as nested JSON objects in an object's
//! `attributes` (keys = language codes). At read time, `?locale=` resolves
//! each multilingual attribute to its effective value using the stored
//! `fallback_order` of the locale's `language_tree` node.

use serde_json::{Map, Value};

use crate::db::Db;
use crate::error::ApiError;

/// Names of all attribute definitions marked `multilingual`.
pub async fn multilingual_attribute_names(db: &Db) -> Result<Vec<String>, ApiError> {
    let sql = "SELECT VALUE name FROM attribute_definition WHERE multilingual = true";
    let mut response = db.inner().query(sql).await?.check()?;
    let names: Vec<String> = response.take(0)?;
    Ok(names)
}

/// Resolves a single multilingual value object for `locale`.
///
/// Exact match wins; otherwise the locale's `fallback_order` (ancestor codes)
/// is walked, ending at `root`. Non-object values are returned unchanged.
pub async fn resolve_value(
    db: &Db,
    locale: &str,
    value: &Value,
) -> Result<Option<Value>, ApiError> {
    let Some(map) = value.as_object() else {
        return Ok(Some(value.clone()));
    };
    if let Some(exact) = map.get(locale) {
        return Ok(Some(exact.clone()));
    }
    let chain = fallback_chain(db, locale).await?;
    for code in chain {
        if let Some(resolved) = map.get(&code) {
            return Ok(Some(resolved.clone()));
        }
    }
    Ok(None)
}

/// Resolves all multilingual attributes in `attributes` for `locale`.
/// Attributes without a resolvable value become JSON `null`.
pub async fn resolve_attributes(
    db: &Db,
    locale: &str,
    attributes: &mut Map<String, Value>,
) -> Result<(), ApiError> {
    let names = multilingual_attribute_names(db).await?;
    for name in names {
        let Some(value) = attributes.get(&name) else {
            continue;
        };
        let resolved = resolve_value(db, locale, value).await?;
        match resolved {
            Some(value) => {
                attributes.insert(name, value);
            }
            None => {
                attributes.insert(name, Value::Null);
            }
        }
    }
    Ok(())
}

/// Returns the ancestor codes of `locale` from its language-tree node,
/// always ending with `root`.
async fn fallback_chain(db: &Db, locale: &str) -> Result<Vec<String>, ApiError> {
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
