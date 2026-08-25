//! Persisted connector configuration CRUD (Phase 6).
//!
//! `connector` is the durable source of truth for inbound/outbound connector
//! configuration. Environment variables seed the initial value; the database
//! records are managed through this API (and later the admin UI). The worker
//! loads enabled connectors through `GET /connectors` with the machine-to-
//! machine API key.

use axum::Json;
use axum::extract::{Path, Query, State};
use axum::http::{HeaderMap, StatusCode};
use serde::Deserialize;
use serde_json::{Map, Value, json};
use utoipa::{IntoParams, ToSchema};

use crate::app::AppState;
use crate::audit::{self, AuditEntry};
use crate::auth::{AuthUser, ROLE_ADMIN};
use crate::db::Db;
use crate::error::ApiError;

const TABLE: &str = "connector";
const VALID_CONNECTOR_TYPES: [&str; 3] = ["webhook", "json_import", "json_export"];

/// Payload for creating a connector.
#[derive(Debug, Deserialize, ToSchema)]
pub struct CreateConnector {
    /// Unique human-readable name (also the worker-side connector key).
    pub name: String,
    /// `webhook` | `json_import` | `json_export`.
    #[serde(rename = "type")]
    pub connector_type: String,
    #[serde(default = "default_enabled")]
    pub enabled: bool,
    /// Free-form parameters (URL, method, headers, filter, coalesce, …).
    #[serde(default = "default_parameters")]
    pub parameters: Value,
}

/// Payload for a partial connector update.
#[derive(Debug, Deserialize, ToSchema)]
pub struct UpdateConnector {
    #[serde(default)]
    pub name: Option<String>,
    #[serde(rename = "type", default)]
    pub connector_type: Option<String>,
    #[serde(default)]
    pub enabled: Option<bool>,
    #[serde(default)]
    pub parameters: Option<Value>,
}

/// Query parameters for listing connectors.
#[derive(Debug, Deserialize, ToSchema, IntoParams)]
#[into_params(parameter_in = Query)]
pub struct ListConnectors {
    #[serde(rename = "type")]
    #[param(rename = "type")]
    pub connector_type: Option<String>,
    pub enabled: Option<bool>,
    #[serde(default = "default_limit")]
    pub limit: u32,
    #[serde(default)]
    pub start: u32,
}

fn default_enabled() -> bool {
    true
}

fn default_parameters() -> Value {
    Value::Object(Map::new())
}

fn default_limit() -> u32 {
    100
}

/// Splits a `connector:<key>` path segment and verifies the expected table.
fn parse_key(id: &str) -> Result<&str, ApiError> {
    let (table, key) = id
        .split_once(':')
        .ok_or_else(|| ApiError::bad_request(format!("invalid id `{id}`")))?;
    if table != TABLE {
        return Err(ApiError::bad_request(format!(
            "id `{id}` does not reference table `{TABLE}`"
        )));
    }
    Ok(key)
}

/// Loads a single connector record.
async fn fetch_connector(db: &Db, key: &str) -> Result<Option<Value>, ApiError> {
    let sql = "
        LET $c = type::record('connector', $key);
        SELECT * FROM $c;
    ";
    let mut response = db.inner().query(sql).bind(("key", key)).await?.check()?;
    let rows: Vec<Value> = response.take(1)?;
    Ok(rows.into_iter().next())
}

/// Checks that a connector name is not already taken.
async fn ensure_unique_name(db: &Db, name: &str, except: Option<&str>) -> Result<(), ApiError> {
    let rows: Vec<Value> = db
        .inner()
        .query("SELECT id FROM connector WHERE name = $name")
        .bind(("name", name.to_owned()))
        .await?
        .check()?
        .take(0)?;
    let conflict = rows
        .iter()
        .any(|row| row.get("id").and_then(Value::as_str) != except);
    if conflict {
        return Err(ApiError::conflict(format!(
            "connector `{name}` already exists"
        )));
    }
    Ok(())
}

/// Creates a connector (admin only).
#[utoipa::path(
    post,
    path = "/connectors",
    request_body = CreateConnector,
    responses(
        (status = 201, description = "Created connector", body = Value),
        (status = 400, description = "Invalid payload", body = Value),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Forbidden", body = Value),
        (status = 409, description = "Name already exists", body = Value)
    )
)]
pub async fn create_connector(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Json(body): Json<CreateConnector>,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    if !claims.is_admin() {
        return Err(ApiError::forbidden("requires admin role"));
    }
    let name = body.name.trim().to_owned();
    if name.is_empty() {
        return Err(ApiError::bad_request("`name` must not be empty"));
    }
    if !VALID_CONNECTOR_TYPES.contains(&body.connector_type.as_str()) {
        return Err(ApiError::bad_request(format!(
            "unknown connector type `{}`",
            body.connector_type
        )));
    }
    ensure_unique_name(&state.db, &name, None).await?;

    let mut content = Map::new();
    content.insert("name".to_owned(), Value::String(name));
    content.insert("type".to_owned(), Value::String(body.connector_type));
    content.insert("enabled".to_owned(), Value::Bool(body.enabled));
    content.insert("parameters".to_owned(), body.parameters);
    content.insert("updated_by".to_owned(), Value::String(claims.sub.clone()));

    let sql = "
        LET $c = type::record('connector', rand::ulid());
        CREATE $c CONTENT $content;
        SELECT * FROM $c;
    ";
    let mut response = state
        .db
        .inner()
        .query(sql)
        .bind(("content", Value::Object(content)))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(2)?;
    let connector = rows
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::internal("created connector was not returned"))?;
    let connector_id = connector
        .get("id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();

    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &claims.sub,
            action: "connector_created",
            object_id: Some(&connector_id),
            from: None,
            to: None,
            metadata: Some(json!({ "name": connector.get("name") })),
        },
    )
    .await;

    Ok((StatusCode::CREATED, Json(connector)))
}

/// Lists connectors, optionally filtered by type and/or enabled state.
///
/// Open to the machine-to-machine API key (the worker loads its connectors
/// here) or an admin JWT.
#[utoipa::path(
    get,
    path = "/connectors",
    params(ListConnectors),
    responses(
        (status = 200, description = "Connectors", body = Vec<Value>),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Forbidden", body = Value)
    )
)]
pub async fn list_connectors(
    State(state): State<AppState>,
    headers: HeaderMap,
    Query(params): Query<ListConnectors>,
) -> Result<Json<Vec<Value>>, ApiError> {
    crate::auth::api_key_or_role(&headers, &state.config, ROLE_ADMIN)?;
    let limit = params.limit.clamp(1, 1000) as i64;
    let start = params.start as i64;

    let mut sql = String::from("SELECT * FROM connector");
    let mut conditions: Vec<&str> = Vec::new();
    if params.connector_type.is_some() {
        conditions.push("type = $type");
    }
    if params.enabled.is_some() {
        conditions.push("enabled = $enabled");
    }
    if !conditions.is_empty() {
        sql.push_str(" WHERE ");
        sql.push_str(&conditions.join(" AND "));
    }
    sql.push_str(" ORDER BY name LIMIT $limit START $start");

    let mut query = state.db.inner().query(sql);
    if let Some(connector_type) = &params.connector_type {
        query = query.bind(("type", connector_type.clone()));
    }
    if let Some(enabled) = params.enabled {
        query = query.bind(("enabled", enabled));
    }
    let mut response = query
        .bind(("limit", limit))
        .bind(("start", start))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    Ok(Json(rows))
}

/// Loads a single connector (API key or admin).
#[utoipa::path(
    get,
    path = "/connectors/{id}",
    responses(
        (status = 200, description = "Connector", body = Value),
        (status = 404, description = "Not found", body = Value)
    )
)]
pub async fn get_connector(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(id): Path<String>,
) -> Result<Json<Value>, ApiError> {
    crate::auth::api_key_or_role(&headers, &state.config, ROLE_ADMIN)?;
    let key = parse_key(&id)?;
    let connector = fetch_connector(&state.db, key)
        .await?
        .ok_or_else(|| ApiError::not_found(format!("connector `{id}` not found")))?;
    Ok(Json(connector))
}

/// Partially updates a connector (admin only).
#[utoipa::path(
    patch,
    path = "/connectors/{id}",
    request_body = UpdateConnector,
    responses(
        (status = 200, description = "Updated connector", body = Value),
        (status = 400, description = "Invalid id or payload", body = Value),
        (status = 404, description = "Not found", body = Value),
        (status = 409, description = "Name already exists", body = Value)
    )
)]
pub async fn update_connector(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
    Json(body): Json<UpdateConnector>,
) -> Result<Json<Value>, ApiError> {
    if !claims.is_admin() {
        return Err(ApiError::forbidden("requires admin role"));
    }
    let key = parse_key(&id)?;
    let current = fetch_connector(&state.db, key)
        .await?
        .ok_or_else(|| ApiError::not_found(format!("connector `{id}` not found")))?;

    let mut changes = Map::new();
    if let Some(name) = body.name {
        let name = name.trim().to_owned();
        if name.is_empty() {
            return Err(ApiError::bad_request("`name` must not be empty"));
        }
        ensure_unique_name(&state.db, &name, Some(id.as_str())).await?;
        changes.insert("name".to_owned(), Value::String(name));
    }
    if let Some(connector_type) = body.connector_type {
        if !VALID_CONNECTOR_TYPES.contains(&connector_type.as_str()) {
            return Err(ApiError::bad_request(format!(
                "unknown connector type `{connector_type}`"
            )));
        }
        changes.insert("type".to_owned(), Value::String(connector_type));
    }
    if let Some(enabled) = body.enabled {
        changes.insert("enabled".to_owned(), Value::Bool(enabled));
    }
    if let Some(parameters) = body.parameters {
        changes.insert("parameters".to_owned(), parameters);
    }

    if changes.is_empty() {
        return Ok(Json(current));
    }

    let sql = "
        LET $c = type::record('connector', $key);
        UPDATE $c MERGE $changes;
        UPDATE $c SET updated_at = time::now(), updated_by = $user;
        SELECT * FROM $c;
    ";
    let mut response = state
        .db
        .inner()
        .query(sql)
        .bind(("key", key))
        .bind(("changes", Value::Object(changes)))
        .bind(("user", claims.sub.clone()))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(3)?;
    let connector = rows
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::not_found(format!("connector `{id}` not found")))?;

    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &claims.sub,
            action: "connector_updated",
            object_id: Some(&id),
            from: None,
            to: None,
            metadata: None,
        },
    )
    .await;

    Ok(Json(connector))
}

/// Deletes a connector (admin only).
#[utoipa::path(
    delete,
    path = "/connectors/{id}",
    responses(
        (status = 204, description = "Deleted connector"),
        (status = 404, description = "Not found", body = Value)
    )
)]
pub async fn delete_connector(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<StatusCode, ApiError> {
    if !claims.is_admin() {
        return Err(ApiError::forbidden("requires admin role"));
    }
    let key = parse_key(&id)?;
    fetch_connector(&state.db, key)
        .await?
        .ok_or_else(|| ApiError::not_found(format!("connector `{id}` not found")))?;

    let sql = "
        LET $c = type::record('connector', $key);
        DELETE $c;
    ";
    state
        .db
        .inner()
        .query(sql)
        .bind(("key", key))
        .await?
        .check()?;

    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &claims.sub,
            action: "connector_deleted",
            object_id: Some(&id),
            from: None,
            to: None,
            metadata: None,
        },
    )
    .await;

    Ok(StatusCode::NO_CONTENT)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_connector_ids() {
        assert_eq!(parse_key("connector:abc123").unwrap(), "abc123");
        assert!(parse_key("job_definition:abc123").is_err());
        assert!(parse_key("abc123").is_err());
    }

    #[test]
    fn rejects_unknown_connector_types() {
        assert!(VALID_CONNECTOR_TYPES.contains(&"webhook"));
        assert!(VALID_CONNECTOR_TYPES.contains(&"json_import"));
        assert!(VALID_CONNECTOR_TYPES.contains(&"json_export"));
        assert!(!VALID_CONNECTOR_TYPES.contains(&"ftp"));
    }
}
