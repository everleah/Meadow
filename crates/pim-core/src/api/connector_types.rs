//! Registered connector types (`plan/detail/konnektor-konfiguration.md` §14.1).
//!
//! The three built-in types live in `connector_schema.rs` (Rust literals). This module
//! adds the **data-driven** part: a row in `connector_type` carries name, label key,
//! direction and the same schema JSON that `GET /connector-types` returns, so the
//! validator, the form and the export work for it without a Rust change.
//!
//! A registered type is **`wired: false`** — no consumer reads its parameters yet
//! (`MEADOW_CONNECTOR_PLUGINS` is a later step). The UI says so instead of pretending
//! a connector of that type delivers anything.

use axum::Json;
use axum::extract::{Path, State};
use axum::http::StatusCode;
use serde::Deserialize;
use serde_json::{Value, json};
use utoipa::ToSchema;

use crate::api::connector_schema::{Catalog, TypeSpec};
use crate::app::AppState;
use crate::auth::AuthUser;
use crate::db::Db;
use crate::error::ApiError;

/// Table of the registered connector types.
pub const TABLE: &str = "connector_type";

/// Payload for creating or updating a registered type.
#[derive(Debug, Deserialize, ToSchema)]
pub struct RegisterConnectorType {
    /// Type key used in a connector's `type` (`sap_idoc`): lowercase letters, digits
    /// and underscores, never the name of a built-in type.
    pub name: String,
    /// i18n key of the label (`connectors.type_<name>` by convention).
    #[serde(default)]
    pub label_key: String,
    /// `inbound` or `outbound` (display hint).
    #[serde(default)]
    pub direction: Option<String>,
    /// The schema: `{ "groups": [ { "key", "label_key", "fields": [ … ] } ] }` — the
    /// same shape the built-in types are served in.
    pub schema: Value,
}

/// Reads one `connector_type` row as a spec.
///
/// The row holds `schema` as an object with `groups`, exactly as the API serves it, so
/// a stored type round-trips through `GET /connector-types` unchanged.
pub fn spec_from_row(row: &Value) -> Result<TypeSpec, String> {
    let name = row
        .get("name")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .trim()
        .to_owned();
    if name.is_empty() {
        return Err("a registered type needs a `name`".to_owned());
    }
    let groups = row
        .get("schema")
        .and_then(|schema| schema.get("groups"))
        .cloned()
        .ok_or_else(|| "the schema needs a `groups` array".to_owned())?;
    let label_key = row
        .get("label_key")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
        .unwrap_or_else(|| format!("connectors.type_{name}"));
    let spec = json!({
        "type": name,
        "label_key": label_key,
        "direction": row
            .get("direction")
            .and_then(Value::as_str)
            .unwrap_or("inbound"),
        // No consumer reads a registered type's parameters yet — that is part of the
        // contract, not a warning (`plan/detail/konnektor-konfiguration.md` §14.1).
        "wired": false,
        "note_key": "connectors.unwired_hint",
        "groups": groups,
    });
    serde_json::from_value(spec).map_err(|error| format!("invalid connector type schema: {error}"))
}

/// Validates a name/schema pair before it is stored.
///
/// None of this is a formality: a schema that cannot be parsed would make every
/// connector of that type unusable, and a name that shadows a built-in would silently
/// change what `webhook` means.
pub fn check_registration(name: &str, schema: &Value) -> Result<(), ApiError> {
    let name = name.trim();
    if name.is_empty() {
        return Err(ApiError::bad_request("`name` must not be empty"));
    }
    if !name.chars().all(|character| {
        character.is_ascii_lowercase() || character.is_ascii_digit() || character == '_'
    }) {
        return Err(ApiError::bad_request(
            "`name` may only use lowercase letters, digits and `_`",
        ));
    }
    if crate::api::connector_schema::spec(name).is_some() {
        return Err(ApiError::conflict(format!(
            "`{name}` is a built-in connector type"
        )));
    }
    let groups = schema
        .get("groups")
        .and_then(Value::as_array)
        .ok_or_else(|| ApiError::bad_request("`schema` needs a `groups` array"))?;
    if groups.is_empty() {
        return Err(ApiError::bad_request("`schema.groups` must not be empty"));
    }
    let mut seen: Vec<String> = Vec::new();
    for group in groups {
        let fields = group
            .get("fields")
            .and_then(Value::as_array)
            .ok_or_else(|| ApiError::bad_request("every group needs a `fields` array"))?;
        for field in fields {
            let key = field
                .get("key")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .trim()
                .to_owned();
            if key.is_empty() {
                return Err(ApiError::bad_request("every field needs a `key`"));
            }
            if seen.contains(&key) {
                return Err(ApiError::bad_request(format!(
                    "duplicate field `{key}` in the schema"
                )));
            }
            seen.push(key);
        }
    }
    // The parser has the last word: a schema that does not deserialize is refused.
    spec_from_row(&json!({ "name": name, "schema": schema }))
        .map(|_| ())
        .map_err(ApiError::bad_request)
}

/// Loads the registered types and returns a catalog of built-in + registered types.
pub async fn catalog(db: &Db) -> Result<Catalog, ApiError> {
    Ok(Catalog::builtin().with_registered(registered_specs(db).await?))
}

/// The stored rows (what the admin page lists).
async fn rows(db: &Db) -> Result<Vec<Value>, ApiError> {
    let mut response = db
        .inner()
        .query(format!("SELECT * FROM {TABLE} ORDER BY name;"))
        .await?
        .check()?;
    Ok(response.take(0)?)
}

/// The stored types as specs; a row that no longer parses is skipped and logged.
async fn registered_specs(db: &Db) -> Result<Vec<TypeSpec>, ApiError> {
    let mut specs = Vec::new();
    for row in rows(db).await? {
        match spec_from_row(&row) {
            Ok(spec) => specs.push(spec),
            Err(error) => {
                tracing::warn!(%error, "ignored a connector type that no longer parses");
            }
        }
    }
    Ok(specs)
}

/// The registered types as the `GET /connector-types` document lists them.
pub async fn registered_document(db: &Db) -> Result<Vec<Value>, ApiError> {
    Ok(registered_specs(db)
        .await?
        .into_iter()
        .map(|spec| serde_json::to_value(spec).unwrap_or(Value::Null))
        .collect())
}

/// Registers a connector type (admin only).
#[utoipa::path(
    post,
    path = "/connector-types",
    request_body = RegisterConnectorType,
    responses(
        (status = 201, description = "Registered type", body = Value),
        (status = 400, description = "Invalid schema", body = Value),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Forbidden", body = Value),
        (status = 409, description = "Name is a built-in type", body = Value)
    )
)]
pub async fn create_connector_type(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Json(body): Json<RegisterConnectorType>,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    claims.require("integration.manage")?;
    let name = body.name.trim().to_owned();
    check_registration(&name, &body.schema)?;
    if fetch_type(&state.db, &name).await.is_ok() {
        return Err(ApiError::conflict(format!(
            "connector type `{name}` is already registered"
        )));
    }
    let sql = format!(
        "LET $r = type::record('{TABLE}', $name);\
         CREATE $r SET name = $name, label_key = $label, direction = $direction, schema = $schema;\
         SELECT * FROM $r;"
    );
    let mut response = state
        .db
        .inner()
        .query(sql)
        .bind(("name", name.clone()))
        .bind(("label", body.label_key.trim().to_owned()))
        .bind((
            "direction",
            body.direction.unwrap_or_else(|| "inbound".to_owned()),
        ))
        .bind(("schema", body.schema))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(2)?;
    let row = rows
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::internal("registered connector type was not returned"))?;
    tracing::info!(type_key = %name, "registered a connector type");
    Ok((StatusCode::CREATED, Json(row)))
}

/// Replaces a registered type (admin only). The name is the identity, it cannot change.
#[utoipa::path(
    patch,
    path = "/connector-types/{name}",
    request_body = RegisterConnectorType,
    responses(
        (status = 200, description = "Updated type", body = Value),
        (status = 400, description = "Invalid schema", body = Value),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Forbidden", body = Value),
        (status = 404, description = "Not found", body = Value)
    )
)]
pub async fn update_connector_type(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(name): Path<String>,
    Json(body): Json<RegisterConnectorType>,
) -> Result<Json<Value>, ApiError> {
    claims.require("integration.manage")?;
    let name = name.trim().to_owned();
    fetch_type(&state.db, &name).await?;
    check_registration(&name, &body.schema)?;
    let sql = format!(
        "LET $r = type::record('{TABLE}', $name);\
         UPDATE $r SET label_key = $label, direction = $direction, schema = $schema;\
         SELECT * FROM $r;"
    );
    let mut response = state
        .db
        .inner()
        .query(sql)
        .bind(("name", name))
        .bind(("label", body.label_key.trim().to_owned()))
        .bind((
            "direction",
            body.direction.unwrap_or_else(|| "inbound".to_owned()),
        ))
        .bind(("schema", body.schema))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(2)?;
    let row = rows
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::internal("updated connector type was not returned"))?;
    Ok(Json(row))
}

/// Deletes a registered type (admin only).
///
/// Refused while a connector still uses it: without its schema that connector could
/// neither be validated nor edited.
#[utoipa::path(
    delete,
    path = "/connector-types/{name}",
    responses(
        (status = 204, description = "Deleted"),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Forbidden", body = Value),
        (status = 404, description = "Not found", body = Value),
        (status = 409, description = "Still in use", body = Value)
    )
)]
pub async fn delete_connector_type(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(name): Path<String>,
) -> Result<StatusCode, ApiError> {
    claims.require("integration.manage")?;
    let name = name.trim().to_owned();
    fetch_type(&state.db, &name).await?;
    let used: Vec<Value> = state
        .db
        .inner()
        .query("SELECT id FROM connector WHERE type = $name LIMIT 1;")
        .bind(("name", name.clone()))
        .await?
        .check()?
        .take(0)?;
    if !used.is_empty() {
        return Err(ApiError::conflict(format!(
            "connector type `{name}` is still used by a connector"
        )));
    }
    state
        .db
        .inner()
        .query(format!("DELETE type::record('{TABLE}', $name);"))
        .bind(("name", name.clone()))
        .await?
        .check()?;
    tracing::info!(type_key = %name, "deleted a connector type");
    Ok(StatusCode::NO_CONTENT)
}

/// Loads one registered type or answers `404`.
async fn fetch_type(db: &Db, name: &str) -> Result<Value, ApiError> {
    let mut response = db
        .inner()
        .query(format!("SELECT * FROM type::record('{TABLE}', $name);"))
        .bind(("name", name.to_owned()))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    rows.into_iter()
        .next()
        .ok_or_else(|| ApiError::not_found(format!("connector type `{name}` not found")))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn schema() -> Value {
        json!({
            "groups": [
                {
                    "key": "target",
                    "label_key": "connectors.group_target",
                    "fields": [
                        {"key": "endpoint", "type": "string", "widget": "text", "required": true},
                        {"key": "timeout_s", "type": "number", "widget": "number", "minimum": 1}
                    ]
                }
            ]
        })
    }

    #[test]
    fn a_stored_type_becomes_a_spec_without_a_consumer() {
        let spec = spec_from_row(&json!({
            "name": "sap_idoc",
            "label_key": "",
            "direction": "outbound",
            "schema": schema()
        }))
        .expect("parses");
        assert_eq!(spec.type_key, "sap_idoc");
        assert_eq!(spec.direction, "outbound");
        // The convention fills a missing label key, so the form has something to show.
        assert_eq!(spec.label_key, "connectors.type_sap_idoc");
        assert!(!spec.wired, "no consumer reads a registered type yet");
        assert_eq!(spec.note_key.as_deref(), Some("connectors.unwired_hint"));
        assert_eq!(spec.groups[0].fields[0].key, "endpoint");
    }

    #[test]
    fn the_schema_of_a_registered_type_validates_like_a_built_in_one() {
        let spec =
            spec_from_row(&json!({"name": "shop_export", "schema": schema()})).expect("spec");
        let catalog = Catalog::builtin().with_registered(vec![spec]);
        assert!(catalog.is_known_type("shop_export"));
        let problems = catalog.validate("shop_export", &serde_json::Map::new());
        assert_eq!(problems.len(), 1);
        assert_eq!(problems[0].field, "endpoint");
        assert_eq!(problems[0].message, "is required");

        let mut filled = serde_json::Map::new();
        filled.insert("endpoint".to_owned(), json!("https://erp.example/out"));
        assert!(catalog.validate("shop_export", &filled).is_empty());
        assert!(
            catalog
                .validate_strict("shop_export", &filled, true)
                .is_empty()
        );
    }

    #[test]
    fn a_registered_name_cannot_shadow_a_built_in() {
        let error = check_registration("webhook", &schema()).expect_err("built-in");
        assert!(error.message().contains("built-in"));
        // The catalog keeps the built-in even if a stale row exists.
        let spec = spec_from_row(&json!({"name": "webhook", "schema": schema()})).expect("spec");
        let catalog = Catalog::builtin().with_registered(vec![spec]);
        let keys: Vec<String> = catalog.specs().into_iter().map(|s| s.type_key).collect();
        assert_eq!(keys.iter().filter(|key| *key == "webhook").count(), 1);
    }

    #[test]
    fn a_schema_with_duplicate_fields_or_a_bad_name_is_refused() {
        assert!(check_registration("SAP", &schema()).is_err());
        assert!(check_registration("sap-idoc", &schema()).is_err());
        assert!(check_registration("", &schema()).is_err());
        assert!(check_registration("sap_idoc", &json!({})).is_err());

        let duplicate = json!({
            "groups": [{"key": "a", "label_key": "l", "fields": [
                {"key": "x", "type": "string", "widget": "text"},
                {"key": "x", "type": "string", "widget": "text"}
            ]}]
        });
        let error = check_registration("sap_idoc", &duplicate).expect_err("duplicate");
        assert!(error.message().contains("duplicate field `x`"));
    }

    #[test]
    fn an_unknown_widget_is_refused_by_the_parser() {
        let broken = json!({
            "groups": [{"key": "a", "label_key": "l", "fields": [
                {"key": "x", "type": "string", "widget": "carousel"}
            ]}]
        });
        let error = check_registration("sap_idoc", &broken).expect_err("widget");
        assert!(error.message().contains("invalid connector type schema"));
    }
}
