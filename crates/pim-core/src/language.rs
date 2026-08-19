//! Language-tree based resolution of multilingual attribute values.
//!
//! Multilingual values are stored as nested JSON objects in an object's
//! `attributes` (keys = language codes). At read time, `?locale=` resolves
//! each multilingual attribute to its effective value using the stored
//! `fallback_order` of the locale's `language_tree` node (cached).

use serde_json::{Map, Value};

use crate::cache::MetaCache;
use crate::db::Db;
use crate::error::ApiError;

/// Resolves a single multilingual value against a precomputed fallback chain.
///
/// Exact match wins; otherwise the chain (ancestor codes, ending at `root`)
/// is walked. Non-object values are returned unchanged.
pub(crate) fn resolve_value_with_chain(
    locale: &str,
    value: &Value,
    chain: &[String],
) -> Option<Value> {
    let Some(map) = value.as_object() else {
        return Some(value.clone());
    };
    if let Some(exact) = map.get(locale) {
        return Some(exact.clone());
    }
    for code in chain {
        if let Some(resolved) = map.get(code) {
            return Some(resolved.clone());
        }
    }
    None
}

/// Resolves all multilingual attributes in `attributes` for `locale`.
/// Attributes without a resolvable value become JSON `null`.
pub async fn resolve_attributes(
    cache: &MetaCache,
    db: &Db,
    locale: &str,
    attributes: &mut Map<String, Value>,
) -> Result<(), ApiError> {
    let names = cache.multilingual_names(db).await?;
    let chain = cache.fallback_chain(db, locale).await?;
    for name in names.iter() {
        let Some(value) = attributes.get(name.as_str()) else {
            continue;
        };
        let resolved = resolve_value_with_chain(locale, value, &chain);
        match resolved {
            Some(value) => {
                attributes.insert(name.clone(), value);
            }
            None => {
                attributes.insert(name.clone(), Value::Null);
            }
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn resolve_value_exact_then_fallback_chain() {
        let value = serde_json::json!({"root": "Produkt", "en": "Product", "en-GB": "Product UK"});
        let en_us = vec!["en".to_owned(), "root".to_owned()];

        assert_eq!(
            resolve_value_with_chain("en-US", &value, &en_us).unwrap(),
            Value::from("Product")
        );
        assert_eq!(
            resolve_value_with_chain("en-GB", &value, &en_us).unwrap(),
            Value::from("Product UK")
        );

        let fr = vec!["fr".to_owned(), "root".to_owned()];
        assert_eq!(
            resolve_value_with_chain("fr-FR", &value, &fr).unwrap(),
            Value::from("Produkt")
        );

        // Non-object values pass through unchanged.
        let scalar = Value::from("x");
        assert_eq!(
            resolve_value_with_chain("en", &scalar, &en_us).unwrap(),
            scalar
        );

        // No resolvable value -> None.
        let empty = serde_json::json!({});
        assert!(resolve_value_with_chain("fr-FR", &empty, &fr).is_none());
    }
}
