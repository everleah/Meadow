//! Business-key handling: definition validation, uniqueness on write, and the
//! key-lookup endpoint.
//!
//! A business key is an `attribute_definition` with `is_key: true` (see
//! `plan/detail/datenmodell.md`). The key value lives inside the object's
//! `attributes` object; uniqueness is enforced per object type at write time
//! (empty values never collide, values are trimmed before comparison).

use axum::Json;
use axum::extract::{Query, State};
use serde::Deserialize;
use serde_json::{Map, Value};
use utoipa::{IntoParams, ToSchema};

use crate::app::AppState;
use crate::db::Db;
use crate::error::ApiError;

/// Extracts the key part of a record id (`table:key` → `key`).
fn key_of(record_id: &str) -> &str {
    record_id
        .split_once(':')
        .map(|(_, key)| key)
        .unwrap_or(record_id)
}

/// Renders a SurrealDB **record list literal** (`[object:a, object:b]`) from record
/// keys, for `WHERE id IN <list>`.
///
/// The shape decides the runtime: `id IN array::map($keys, |$k| type::record('object', $k))`
/// is evaluated **per candidate row**, which is quadratic. Measured on this
/// dataset (5694 objects): 100 keys → 1,9 s, 1000 keys → 16,6 s, 5658 keys →
/// 110 s — while the literal record list drives the id index (1000 keys →
/// 0,18 s, 5658 → 0,08 s). Use this helper instead of `array::map` for any
/// id-list filter.
///
/// Only plain identifiers (`[A-Za-z0-9_-]`) are rendered; anything else is
/// **dropped**. That keeps the query text free of attacker-controlled input
/// (a `?ids=` parameter passes user strings through here) and loses nothing:
/// object record keys are generated ULIDs or explicit seed keys
/// (`plan/detail/datenmodell.md`), so a key outside that charset cannot exist.
pub(crate) fn record_list_literal<'a>(
    table: &str,
    keys: impl IntoIterator<Item = &'a str>,
) -> String {
    let mut literal = String::from("[");
    let mut first = true;
    for key in keys {
        let key = key_of(key);
        if key.is_empty()
            || !key
                .chars()
                .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-')
        {
            continue;
        }
        if !first {
            literal.push_str(", ");
        }
        literal.push_str(table);
        literal.push(':');
        literal.push_str(key);
        first = false;
    }
    literal.push(']');
    literal
}

/// Validates the definition-level constraints for a business key.
///
/// `is_key: true` requires `type == "text"` and forbids `multilingual`
/// (`inheritable` is forced to `false` by the caller).
pub fn validate_key_definition(
    definition_type: &str,
    multilingual: bool,
    is_key: bool,
) -> Result<(), ApiError> {
    if !is_key {
        return Ok(());
    }
    if definition_type != "text" {
        return Err(ApiError::bad_request(
            "business keys (`is_key: true`) require `type: \"text\"`",
        ));
    }
    if multilingual {
        return Err(ApiError::bad_request(
            "business keys (`is_key: true`) cannot be multilingual",
        ));
    }
    Ok(())
}

/// Whether another object of the same type already carries the given key value.
async fn key_value_exists(
    db: &Db,
    object_type: &str,
    key_name: &str,
    value: &str,
    exclude_key: Option<&str>,
) -> Result<bool, ApiError> {
    let sql = "
        SELECT id FROM object WHERE type = $type AND attributes[$key] = $value
        AND deleted_at IS NONE;
    ";
    let mut response = db
        .inner()
        .query(sql)
        .bind(("type", object_type.to_owned()))
        .bind(("key", key_name.to_owned()))
        .bind(("value", value.to_owned()))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    for row in rows {
        let id = row
            .get("id")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_owned();
        if let Some(excluded) = exclude_key
            && key_of(&id) == excluded
        {
            continue;
        }
        return Ok(true);
    }
    Ok(false)
}

/// Lock names for the business keys present in `attributes`.
///
/// The names are what `KeyLocks` serialises on: they must therefore contain the
/// **trimmed** value that the uniqueness check compares, so that two writers of
/// the same key always compute the same name. Keys without a text value are not
/// part of the check and get no lock.
pub fn key_lock_names(
    object_type: &str,
    key_names: &[String],
    attributes: &Map<String, Value>,
) -> Vec<String> {
    let mut names = Vec::new();
    for name in key_names {
        let Some(text) = attributes.get(name).and_then(Value::as_str) else {
            continue;
        };
        let trimmed = text.trim();
        if trimmed.is_empty() {
            continue;
        }
        // `\u{1}` cannot appear in an object type or attribute name.
        names.push(format!("{object_type}\u{1}{name}\u{1}{trimmed}"));
    }
    names
}

/// Trims all business-key values in `attributes` and enforces uniqueness per
/// object type. Empty values are allowed and never collide. `exclude_key`
/// excludes the object being updated from the uniqueness check.
///
/// The key attributes come from the **object type registry** (`object_type
/// .key_attributes`) for the given type — not from a global `is_key` flag. A
/// type without keys (or an unregistered type) enforces nothing.
///
/// The caller passes the names in (`object_types::key_attributes_for`), so it can
/// lock them for the write that follows the check: check and write are one
/// critical section (`crate::key_lock`), otherwise two parallel writers can leave
/// two live rows with the same key.
pub async fn normalize_key_attributes(
    db: &Db,
    object_type: &str,
    key_names: &[String],
    attributes: &mut Map<String, Value>,
    exclude_key: Option<&str>,
) -> Result<(), ApiError> {
    for name in key_names {
        let Some(value) = attributes.get(name) else {
            continue;
        };
        let Some(text) = value.as_str() else {
            // Keys are text-only; ignore non-string values.
            continue;
        };
        let trimmed = text.trim().to_owned();
        attributes.insert(name.clone(), Value::String(trimmed.clone()));
        if trimmed.is_empty() {
            continue;
        }
        if key_value_exists(db, object_type, name, &trimmed, exclude_key).await? {
            return Err(ApiError::conflict(format!(
                "duplicate business key `{name}`: an object of type `{object_type}` \
                 with value `{trimmed}` already exists"
            )));
        }
    }
    Ok(())
}

/// Query parameters for `GET /objects/by-key`.
#[derive(Debug, Deserialize, ToSchema, IntoParams)]
#[into_params(parameter_in = Query)]
pub struct ByKeyParams {
    /// Object type (business role), e.g. `product`.
    #[serde(rename = "type")]
    #[param(rename = "type")]
    pub object_type: String,
    /// Attribute name of the business key, e.g. `sku`.
    pub key: String,
    /// Exact key value (case-sensitive, trimmed).
    pub value: String,
}

/// Loads exactly one object by its business key.
#[utoipa::path(
    get,
    path = "/objects/by-key",
    params(ByKeyParams),
    responses(
        (status = 200, description = "Matching object", body = Value),
        (status = 400, description = "Missing query parameter", body = Value),
        (status = 404, description = "No object matches the key", body = Value)
    )
)]
pub async fn get_by_key(
    State(state): State<AppState>,
    Query(params): Query<ByKeyParams>,
) -> Result<Json<Value>, ApiError> {
    let object_type = params.object_type.trim();
    let key = params.key.trim();
    if object_type.is_empty() {
        return Err(ApiError::bad_request("`type` must not be empty"));
    }
    if key.is_empty() {
        return Err(ApiError::bad_request("`key` must not be empty"));
    }
    let value = params.value.trim();

    let sql = "
        SELECT * FROM object WHERE type = $type AND attributes[$key] = $value
        AND deleted_at IS NONE LIMIT 1;
    ";
    let mut response = state
        .db
        .inner()
        .query(sql)
        .bind(("type", object_type.to_owned()))
        .bind(("key", key.to_owned()))
        .bind(("value", value.to_owned()))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    let object = rows
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::not_found("no object matches the given business key"))?;
    Ok(Json(object))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn key_definition_requires_text() {
        assert!(validate_key_definition("number", false, true).is_err());
        assert!(validate_key_definition("text", false, true).is_ok());
    }

    #[test]
    fn key_definition_forbids_multilingual() {
        assert!(validate_key_definition("text", true, true).is_err());
    }

    #[test]
    fn non_key_definitions_skip_validation() {
        assert!(validate_key_definition("number", true, false).is_ok());
        assert!(validate_key_definition("text", true, false).is_ok());
    }

    #[test]
    fn record_list_literal_renders_records_and_strips_the_table_prefix() {
        let literal = record_list_literal("object", ["object:abc", "def", "object_root"]);
        assert_eq!(literal, "[object:abc, object:def, object:object_root]");
    }

    #[test]
    fn record_list_literal_drops_keys_that_are_not_plain_identifiers() {
        // A `?ids=` parameter passes user input through here: anything that is not
        // a ULID/seed-key charset must never reach the query text.
        let literal = record_list_literal(
            "object",
            ["abc", "x'); DELETE object; --", "weird key", "", "ok-1"],
        );
        assert_eq!(literal, "[object:abc, object:ok-1]");
    }

    #[test]
    fn record_list_literal_of_nothing_is_an_empty_list() {
        assert_eq!(record_list_literal("object", [] as [&str; 0]), "[]");
    }

    #[test]
    fn key_lock_names_use_the_trimmed_value() {
        let names = vec!["sku".to_owned(), "ean".to_owned()];
        let attributes = Map::from_iter([
            ("sku".to_owned(), Value::String("  A-1 ".to_owned())),
            ("ean".to_owned(), Value::String(String::new())),
            ("other".to_owned(), Value::String("ignored".to_owned())),
        ]);
        assert_eq!(
            key_lock_names("product", &names, &attributes),
            vec!["product\u{1}sku\u{1}A-1".to_owned()]
        );
    }

    #[test]
    fn key_lock_names_skip_non_text_and_unknown_keys() {
        let names = vec!["sku".to_owned(), "missing".to_owned()];
        let attributes = Map::from_iter([
            ("sku".to_owned(), json_number(42)),
            ("other".to_owned(), Value::String("x".to_owned())),
        ]);
        assert!(key_lock_names("product", &names, &attributes).is_empty());
    }

    /// `serde_json::json!` is not imported in this test module on purpose.
    fn json_number(value: i64) -> Value {
        Value::Number(value.into())
    }
}
