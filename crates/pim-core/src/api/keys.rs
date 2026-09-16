//! Business-key handling: definition validation, uniqueness on write, and the
//! key-lookup endpoint.
//!
//! A business key is an `attribute_definition` with `is_key: true` (see
//! `plan/detail/datenmodell.md`). The key value lives inside the object's
//! `attributes` object; uniqueness is enforced per object type at write time
//! (empty values never collide, values are trimmed before comparison).

use std::collections::HashMap;

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
///
/// The lookup must be **index-backed**: scanning the candidates of a type costs
/// ~150 ms per write at demo size, which dominated `POST /objects` (measured
/// 2026-09-16: 143 ms of a 220 ms request). Two details make the difference
/// (measured against the live instance):
///
/// - the **static** field path (`attributes.sku`) uses the index that
///   [`ensure_key_indexes`] defines for every declared key attribute, while the
///   dynamic form (`attributes[$key]`) stays on `idx_object_type` (71 ms vs
///   ~1 000 ms for the same lookup);
/// - the **type is filtered in Rust**, not in SQL: with `type = $type` in the
///   predicate the planner prefers `idx_object_type` and scans every object of
///   that type (~615 ms), even though the key index would answer directly. The
///   result set is tiny (objects that share a key *value*, usually none or one),
///   so filtering it here is exact and free.
///
/// Attribute names that are not plain identifiers (and therefore not indexable)
/// keep the dynamic form with the type filter: correct, just slower.
async fn key_value_exists(
    db: &Db,
    object_type: &str,
    key_name: &str,
    value: &str,
    exclude_key: Option<&str>,
) -> Result<bool, ApiError> {
    let field = key_field_ref(key_name);
    let sql = match &field {
        Some(field) => format!(
            "SELECT type::string(id) AS id, type FROM object \
             WHERE {field} = $value AND deleted_at IS NONE;"
        ),
        None => "SELECT type::string(id) AS id, type FROM object \
                 WHERE type = $type AND attributes[$key] = $value AND deleted_at IS NONE;"
            .to_owned(),
    };
    let mut query = db
        .inner()
        .query(sql)
        .bind(("value", value.to_owned()))
        .bind(("type", object_type.to_owned()));
    if field.is_none() {
        query = query.bind(("key", key_name.to_owned()));
    }
    let mut response = query.await?.check()?;
    let rows: Vec<Value> = response.take(0)?;
    for row in rows {
        if row.get("type").and_then(Value::as_str) != Some(object_type) {
            continue;
        }
        let id = row.get("id").and_then(Value::as_str).unwrap_or_default();
        if exclude_key.is_some_and(|excluded| key_of(id) == excluded) {
            continue;
        }
        return Ok(true);
    }
    Ok(false)
}

/// Static field reference of an attribute, when the name is a plain identifier.
///
/// Only such names can be indexed as `object.attributes.<name>`; anything else
/// (dots, spaces, leading digits) is rejected by SurrealQL's parser and falls back
/// to the dynamic access form at the call site.
pub fn key_field_ref(attribute: &str) -> Option<String> {
    let mut chars = attribute.chars();
    let first = chars.next()?;
    if !(first.is_ascii_alphabetic() || first == '_') {
        return None;
    }
    if !chars.all(|c| c.is_ascii_alphanumeric() || c == '_') {
        return None;
    }
    Some(format!("attributes.{attribute}"))
}

/// Index name for a key attribute (`idx_object_key_<name>`).
fn key_index_name(attribute: &str) -> String {
    format!("idx_object_key_{}", attribute.to_ascii_lowercase())
}

/// Defines a lookup index for every declared business-key attribute.
///
/// Uniqueness is checked on **every** create/update with a key in the payload, so
/// that lookup has to be index-backed (see [`key_value_exists`]). The index is
/// defined per attribute name, which is why it cannot live in `schema.rs`: the
/// registry decides which attributes are keys. Idempotent (`IF NOT EXISTS`), so
/// callers can run it at startup and after an object-type write.
pub async fn ensure_key_indexes(db: &Db, key_attributes: &[String]) -> Result<usize, ApiError> {
    let mut defined = 0;
    for attribute in key_attributes {
        let Some(field) = key_field_ref(attribute) else {
            tracing::debug!(%attribute, "business key is not a plain identifier; no index defined");
            continue;
        };
        let sql = format!(
            "DEFINE INDEX IF NOT EXISTS {} ON TABLE object FIELDS {field};",
            key_index_name(attribute)
        );
        let mut response = db.inner().query(sql).await?.check()?;
        let _: Vec<Value> = response.take(0)?;
        defined += 1;
    }
    Ok(defined)
}

/// Key attributes of every registered object type (for the startup index sync).
pub async fn registered_key_attributes(db: &Db) -> Result<Vec<String>, ApiError> {
    let mut response = db
        .inner()
        .query("SELECT key_attributes FROM object_type;")
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    let mut names: Vec<String> = Vec::new();
    for row in rows {
        for name in row
            .get("key_attributes")
            .and_then(Value::as_array)
            .map(|array| array.iter().filter_map(Value::as_str))
            .into_iter()
            .flatten()
        {
            if !names.iter().any(|existing| existing == name) {
                names.push(name.to_owned());
            }
        }
    }
    Ok(names)
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
    for (name, trimmed) in trim_key_values(key_names, attributes) {
        if key_value_exists(db, object_type, &name, &trimmed, exclude_key).await? {
            return Err(ApiError::conflict(format!(
                "duplicate business key `{name}`: an object of type `{object_type}` \
                 with value `{trimmed}` already exists"
            )));
        }
    }
    Ok(())
}

/// Trims the business-key values in `attributes` and returns `(name, value)` for
/// every key that has a non-empty text value.
///
/// Pure part of [`normalize_key_attributes`]: the batch upsert normalises its
/// whole payload first and then checks all values in **one** query per key
/// attribute ([`key_values_in_use`]), so both paths must trim identically —
/// together with [`key_lock_names`] they define the key semantics.
pub fn trim_key_values(
    key_names: &[String],
    attributes: &mut Map<String, Value>,
) -> Vec<(String, String)> {
    let mut values = Vec::new();
    for name in key_names {
        let Some(value) = attributes.get(name) else {
            continue;
        };
        // Keys are text-only; ignore non-string values.
        let Some(text) = value.as_str() else {
            continue;
        };
        let trimmed = text.trim().to_owned();
        attributes.insert(name.clone(), Value::String(trimmed.clone()));
        if trimmed.is_empty() {
            continue;
        }
        values.push((name.clone(), trimmed));
    }
    values
}

/// Key values of one type that are already in use, resolved with **one** query.
///
/// `key_value_exists` answers for a single value; a batch upsert would pay one
/// round trip per object, which is exactly the cost that makes an import slow
/// (`plan/detail/demodaten-generator.md` §4.3). Returns the live object's record
/// key per value; `exclude_key` skips the object that is being updated, and
/// values used by *other* types are ignored (the type is filtered in Rust for the
/// same reason as in `key_value_exists`: the predicate would defeat the index).
pub async fn key_values_in_use(
    db: &Db,
    object_type: &str,
    key_name: &str,
    values: &[String],
    exclude_key: Option<&str>,
) -> Result<HashMap<String, String>, ApiError> {
    if values.is_empty() {
        return Ok(HashMap::new());
    }
    let field = key_field_ref(key_name);
    let (value_expr, type_filter) = match &field {
        Some(field) => (field.clone(), "deleted_at IS NONE"),
        None => ("attributes[$key]".to_owned(), "deleted_at IS NONE"),
    };
    let mut query = db
        .inner()
        .query(format!(
            "SELECT type::string(id) AS id, type, {value_expr} AS value FROM object \
             WHERE {value_expr} IN $values AND {type_filter};"
        ))
        .bind(("values", values.to_vec()));
    if field.is_none() {
        query = query.bind(("key", key_name.to_owned()));
    }
    let mut response = query.await?.check()?;
    let rows: Vec<Value> = response.take(0)?;

    let mut in_use: HashMap<String, String> = HashMap::new();
    for row in rows {
        if row.get("type").and_then(Value::as_str) != Some(object_type) {
            continue;
        }
        let Some(value) = row.get("value").and_then(Value::as_str) else {
            continue;
        };
        let id = row.get("id").and_then(Value::as_str).unwrap_or_default();
        let key = key_of(id).to_owned();
        if exclude_key.is_some_and(|excluded| excluded == key) {
            continue;
        }
        in_use.entry(value.to_owned()).or_insert(key);
    }
    Ok(in_use)
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
    use serde_json::json;

    #[test]
    fn trim_key_values_trims_and_reports_only_non_empty_text() {
        let key_names = vec![
            "sku".to_owned(),
            "oe_number".to_owned(),
            "weight".to_owned(),
        ];
        let mut attributes: Map<String, Value> = serde_json::from_value(json!({
            "sku": "  A-1  ",
            "oe_number": "   ",
            "weight": 12,
            "name": "irrelevant",
        }))
        .expect("attributes");

        let values = trim_key_values(&key_names, &mut attributes);

        // Only the text key with content takes part in the uniqueness check…
        assert_eq!(values, vec![("sku".to_owned(), "A-1".to_owned())]);
        // …and the value in the payload is the trimmed one the check compared.
        assert_eq!(attributes.get("sku"), Some(&json!("A-1")));
        assert_eq!(attributes.get("oe_number"), Some(&json!("")));
    }

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
