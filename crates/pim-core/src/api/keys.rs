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

/// Returns the names of all attribute definitions marked as business keys.
pub async fn key_attribute_names(db: &Db) -> Result<Vec<String>, ApiError> {
    let sql = "SELECT VALUE name FROM attribute_definition WHERE is_key = true";
    let mut response = db.inner().query(sql).await?.check()?;
    let names: Vec<String> = response.take(0)?;
    Ok(names)
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
        SELECT id FROM object WHERE type = $type AND attributes[$key] = $value;
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

/// Trims all business-key values in `attributes` and enforces uniqueness per
/// object type. Empty values are allowed and never collide. `exclude_key`
/// excludes the object being updated from the uniqueness check.
pub async fn normalize_key_attributes(
    db: &Db,
    object_type: &str,
    attributes: &mut Map<String, Value>,
    exclude_key: Option<&str>,
) -> Result<(), ApiError> {
    let names = key_attribute_names(db).await?;
    for name in names {
        let Some(value) = attributes.get(&name) else {
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
        if key_value_exists(db, object_type, &name, &trimmed, exclude_key).await? {
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
}
