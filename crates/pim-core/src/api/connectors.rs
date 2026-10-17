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
use ulid::Ulid;
use utoipa::{IntoParams, ToSchema};

use crate::app::AppState;
use crate::audit::{self, AuditEntry};
use crate::auth::AuthUser;
use crate::db::Db;
use crate::error::ApiError;

const TABLE: &str = "connector";

/// Connector type of the webhook outbound connector.
pub const CONNECTOR_TYPE_WEBHOOK: &str = "webhook";
/// Connector type whose parameters act as defaults for `POST /imports`.
pub const CONNECTOR_TYPE_JSON_IMPORT: &str = "json_import";
/// Connector type whose parameters act as defaults for `POST /exports`.
pub const CONNECTOR_TYPE_JSON_EXPORT: &str = "json_export";

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
    /// Connector parameters. The structure per type is described by
    /// `GET /connector-types`; declared fields are validated (types, required
    /// fields, value ranges) and missing defaults are filled in.
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
    /// How `parameters` is applied: `replace` (default, whole object) or
    /// `merge` (recursive; `null` deletes a key). Letzteres nutzt das
    /// Konfigurationsformular, damit nicht angezeigte Felder (z. B. maskierte
    /// Header-Token) erhalten bleiben.
    #[serde(default)]
    pub parameters_mode: Option<String>,
}

/// Query parameters for listing connectors.
#[derive(Debug, Deserialize, ToSchema, IntoParams)]
#[into_params(parameter_in = Query)]
pub struct ListConnectors {
    #[serde(rename = "type")]
    #[param(rename = "type")]
    pub connector_type: Option<String>,
    pub enabled: Option<bool>,
    /// Replace secret references with their stored values. **Machine caller only**
    /// (the worker): a session never receives a token, it only ever sets one.
    #[serde(default)]
    pub resolve_secrets: Option<bool>,
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

/// Reads the `parameters` object of a payload.
fn parameters_object(value: Value) -> Result<Map<String, Value>, ApiError> {
    match value {
        Value::Object(map) => Ok(map),
        _ => Err(ApiError::bad_request("`parameters` must be an object")),
    }
}

/// Rejects parameters that violate the type's schema (message names the field path).
///
/// `strict` adds the undeclared-key rule (`connector_strict_parameters`).
fn ensure_valid_parameters(
    catalog: &crate::api::connector_schema::Catalog,
    connector_type: &str,
    parameters: &Map<String, Value>,
    strict: bool,
) -> Result<(), ApiError> {
    let problems = catalog.validate_strict(connector_type, parameters, strict);
    if problems.is_empty() {
        Ok(())
    } else {
        // Structured too: the form marks the widgets, the message stays the flat one.
        Err(ApiError::invalid_fields(
            problems
                .iter()
                .map(|problem| {
                    (
                        format!("parameters.{}", problem.field),
                        problem.message.clone(),
                    )
                })
                .collect(),
        ))
    }
}

/// Whether undeclared parameter keys are rejected (`connector_strict_parameters`).
///
/// Off by default: connectors written before the schema existed keep working, and
/// the form still shows their undeclared keys as a hint.
fn strict_parameters(state: &AppState) -> bool {
    state
        .settings
        .read()
        .map(|guard| {
            guard
                .get("connector_strict_parameters")
                .and_then(Value::as_bool)
                .unwrap_or(false)
        })
        .unwrap_or(false)
}

/// Table of the apart-stored connector secrets (schema: `src/schema.rs`).
const SECRET_TABLE: &str = "connector_secret";

/// The stored secret values of one connector (`{path: value}`).
async fn secret_values(db: &Db, key: &str) -> Result<Map<String, Value>, ApiError> {
    let sql = format!("LET $r = type::record('{SECRET_TABLE}', $key); SELECT values FROM $r;");
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
        .and_then(|row| row.get("values").and_then(Value::as_object).cloned())
        .unwrap_or_default())
}

/// Writes the secret values of one connector: `store` sets, `cleared` removes.
///
/// One record per connector under the connector's key, so deleting a connector
/// (`delete_connector`) can drop it in the same breath. An empty result deletes the
/// record instead of keeping an empty shell.
async fn store_secrets(
    db: &Db,
    key: &str,
    store: &Map<String, Value>,
    cleared: &[String],
) -> Result<(), ApiError> {
    let mut values = secret_values(db, key).await?;
    for path in cleared {
        values.remove(path);
    }
    for (path, value) in store {
        values.insert(path.clone(), value.clone());
    }
    let sql = if values.is_empty() {
        format!("LET $r = type::record('{SECRET_TABLE}', $key); DELETE $r;")
    } else {
        format!("LET $r = type::record('{SECRET_TABLE}', $key); UPSERT $r SET values = $values;")
    };
    db.inner()
        .query(sql)
        .bind(("key", key.to_owned()))
        .bind(("values", Value::Object(values)))
        .await?
        .check()?;
    Ok(())
}

/// Replaces the secret references of one row with the stored values (machine caller).
async fn resolve_row_secrets(db: &Db, row: &Value) -> Result<Value, ApiError> {
    let type_key = row
        .get("type")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    let key = row
        .get("id")
        .and_then(Value::as_str)
        .and_then(|id| id.split_once(':'))
        .map(|(_, key)| key.to_owned())
        .unwrap_or_default();
    if key.is_empty() {
        return Ok(row.clone());
    }
    let parameters = row
        .get("parameters")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    let values = secret_values(db, &key).await?;
    if values.is_empty() && parameters.is_empty() {
        return Ok(row.clone());
    }
    let resolved =
        crate::api::connector_schema::resolve_secret_references(&type_key, &parameters, &values);
    let mut row = row.clone();
    if let Some(object) = row.as_object_mut() {
        object.insert("parameters".to_owned(), Value::Object(resolved));
    }
    Ok(row)
}

/// Top-level keys of a submitted `parameters` patch (for the audit trail).
fn changed_keys(patch: &Map<String, Value>) -> Vec<String> {
    let mut keys: Vec<String> = patch.keys().cloned().collect();
    keys.sort();
    keys
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

/// Payload for `POST /connectors/validate`: parameters to check without saving.
#[derive(Debug, Deserialize, ToSchema)]
pub struct ValidateConnector {
    /// Connector type whose schema the parameters are checked against.
    pub connector_type: String,
    /// The parameter object (same shape as on create/update).
    pub parameters: Value,
}

/// Structural check of a connector's parameters — **without saving** (admin).
///
/// The same `connector_schema::validate` the write path uses, so a caller (the
/// connector form, or the AI assistant checking a suggestion) gets exactly the
/// verdict a save would produce. Nothing is written, nothing is required to exist.
#[utoipa::path(
    post,
    path = "/connectors/validate",
    request_body = ValidateConnector,
    responses(
        (status = 200, description = "Validation result", body = Value),
        (status = 400, description = "Invalid payload", body = Value),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Forbidden", body = Value)
    )
)]
pub async fn validate_connector(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<ValidateConnector>,
) -> Result<Json<Value>, ApiError> {
    // The catalog check keeps the worker's machine path (it validates AI suggestions) and
    // the admin form on one rule.
    crate::auth::require_permission(&state, &headers, "integration.manage")?;
    let catalog = crate::api::connector_types::catalog(&state.db).await?;
    if !catalog.is_known_type(&body.connector_type) {
        return Err(ApiError::bad_request(format!(
            "unknown connector type `{}`",
            body.connector_type
        )));
    }
    let Ok(mut parameters) = parameters_object(body.parameters) else {
        return Ok(Json(
            json!({ "ok": false, "errors": ["`parameters` must be an object"] }),
        ));
    };
    // Normalisation and defaults are part of the write path, so the check has to
    // see the same values the save would store.
    catalog.normalize(&body.connector_type, &mut parameters);
    catalog.apply_defaults(&body.connector_type, &mut parameters);
    let problems =
        catalog.validate_strict(&body.connector_type, &parameters, strict_parameters(&state));
    // `errors` stays the flat list every caller already reads (the worker checks AI
    // suggestions against it); `fields` is the same verdict keyed by path, which is
    // what the form marks.
    let fields: serde_json::Map<String, Value> = problems
        .iter()
        .map(|problem| {
            (
                format!("parameters.{}", problem.field),
                Value::String(problem.message.clone()),
            )
        })
        .collect();
    let errors: Vec<String> = problems.iter().map(|problem| problem.text()).collect();
    Ok(Json(
        json!({ "ok": problems.is_empty(), "errors": errors, "fields": fields }),
    ))
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
    claims.require("integration.manage")?;
    let name = body.name.trim().to_owned();
    if name.is_empty() {
        return Err(ApiError::bad_request("`name` must not be empty"));
    }
    let catalog = crate::api::connector_types::catalog(&state.db).await?;
    if !catalog.is_known_type(&body.connector_type) {
        return Err(ApiError::bad_request(format!(
            "unknown connector type `{}`",
            body.connector_type
        )));
    }
    ensure_unique_name(&state.db, &name, None).await?;

    let mut parameters = parameters_object(body.parameters)?;
    catalog.normalize(&body.connector_type, &mut parameters);
    catalog.apply_defaults(&body.connector_type, &mut parameters);
    ensure_valid_parameters(
        &catalog,
        &body.connector_type,
        &parameters,
        strict_parameters(&state),
    )?;

    // The key is made here, not by the database: the secret reference has to name the
    // connector before its row exists (`plan/detail/konnektor-konfiguration.md` §14.2).
    let connector_key = Ulid::new().to_string();
    let connector_id = format!("{TABLE}:{connector_key}");
    let split = catalog.split_secrets(&connector_id, &body.connector_type, &parameters);
    store_secrets(&state.db, &connector_key, &split.store, &split.cleared).await?;
    let secrets_changed: Vec<String> = split
        .store
        .keys()
        .chain(split.cleared.iter())
        .cloned()
        .collect();

    let mut content = Map::new();
    content.insert("name".to_owned(), Value::String(name));
    content.insert(
        "type".to_owned(),
        Value::String(body.connector_type.clone()),
    );
    content.insert("enabled".to_owned(), Value::Bool(body.enabled));
    content.insert("parameters".to_owned(), Value::Object(split.parameters));
    content.insert("updated_by".to_owned(), Value::String(claims.sub.clone()));

    let sql = "
        LET $c = type::record('connector', $key);
        CREATE $c CONTENT $content;
        SELECT * FROM $c;
    ";
    let mut response = state
        .db
        .inner()
        .query(sql)
        .bind(("key", connector_key))
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
            metadata: Some(json!({
                "name": connector.get("name"),
                "type": connector.get("type"),
                "secrets_changed": secrets_changed
            })),
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
    let caller = crate::auth::require_caller(&state, &headers, "integration.manage")?;
    // A token leaves the core towards the worker only: a browser session may write a
    // secret, never read one back (`plan/detail/konnektor-konfiguration.md` §14.2).
    if params.resolve_secrets.unwrap_or(false) && !matches!(caller, crate::auth::Caller::System) {
        return Err(ApiError::forbidden(
            "`resolve_secrets` is only available to the machine caller (the API key)",
        ));
    }
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
    let mut rows: Vec<Value> = response.take(0)?;
    if params.resolve_secrets.unwrap_or(false) {
        let mut resolved = Vec::with_capacity(rows.len());
        for row in &rows {
            resolved.push(resolve_row_secrets(&state.db, row).await?);
        }
        rows = resolved;
    }
    Ok(Json(rows))
}

/// Queues a connection test for a connector (admin only).
///
/// The core has no HTTP client: the test runs in the worker, which sends a
/// synthetic event for `webhook` connectors and resolves the profile values for
/// `json_import`/`json_export` (dry run). The result lands in `job_run.result`
/// (`{ok, …}`) — no data is changed by the test.
#[utoipa::path(
    post,
    path = "/connectors/{id}/test",
    responses(
        (status = 202, description = "Test queued", body = Value),
        (status = 400, description = "Invalid id", body = Value),
        (status = 404, description = "Not found", body = Value),
        (status = 503, description = "Job queue unavailable", body = Value)
    )
)]
pub async fn test_connector(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    claims.require("integration.manage")?;
    let key = parse_key(&id)?;
    let connector = fetch_connector(&state.db, key)
        .await?
        .ok_or_else(|| ApiError::not_found(format!("connector `{id}` not found")))?;
    let connector_type = connector
        .get("type")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();

    let parameters = json!({
        "connector_id": connector.get("id"),
        "name": connector.get("name"),
        "connector_type": connector_type,
        "parameters": connector.get("parameters").cloned().unwrap_or(json!({})),
    });
    let (_, run) = crate::api::jobs::queue_run(
        &state.db,
        &state.config,
        None,
        "connector_test",
        parameters,
        "manual",
        &claims.sub,
    )
    .await?;
    let run_id = run
        .get("id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();

    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &claims.sub,
            action: "connector_tested",
            object_id: Some(&id),
            from: None,
            to: None,
            metadata: Some(json!({ "name": connector.get("name"), "type": connector_type })),
        },
    )
    .await;

    Ok((StatusCode::ACCEPTED, Json(json!({ "job_run_id": run_id }))))
}

/// Payload of `POST /connectors/{id}/status`: one delivery outcome.
#[derive(Debug, Deserialize, ToSchema)]
pub struct ConnectorStatus {
    /// Whether the delivery succeeded.
    pub ok: bool,
    /// The failure message (stored truncated; never a stack trace).
    #[serde(default)]
    pub error: Option<String>,
}

/// Longest stored failure message — enough for the cause, short enough for a list.
const STATUS_ERROR_MAX_CHARS: usize = 500;

/// Records the outcome of one delivery (**machine key** or admin).
///
/// The webhook worker reports **only on change** (the first failure and the first
/// success after one), so the list can answer "is it running?" without a log search
/// (`plan/detail/konnektor-konfiguration.md` §14.4). A success clears the last error:
/// the connector is healthy again, not "healthy, but once broken".
#[utoipa::path(
    post,
    path = "/connectors/{id}/status",
    request_body = ConnectorStatus,
    responses(
        (status = 204, description = "Recorded"),
        (status = 400, description = "Invalid id", body = Value),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Forbidden", body = Value),
        (status = 404, description = "Not found", body = Value)
    )
)]
pub async fn report_connector_status(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(id): Path<String>,
    Json(body): Json<ConnectorStatus>,
) -> Result<StatusCode, ApiError> {
    crate::auth::require_permission(&state, &headers, "integration.manage")?;
    let key = parse_key(&id)?.to_owned();
    let sql = if body.ok {
        "
        LET $c = type::record('connector', $key);
        UPDATE $c SET last_success_at = time::now(), last_error = NONE, last_error_at = NONE RETURN AFTER;
        "
    } else {
        "
        LET $c = type::record('connector', $key);
        UPDATE $c SET last_error = $error, last_error_at = time::now() RETURN AFTER;
        "
    };
    let message: String = body
        .error
        .unwrap_or_else(|| "delivery failed".to_owned())
        .chars()
        .take(STATUS_ERROR_MAX_CHARS)
        .collect();
    let mut response = state
        .db
        .inner()
        .query(sql)
        .bind(("key", key))
        .bind(("error", message))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(1)?;
    if rows.is_empty() {
        return Err(ApiError::not_found(format!("connector `{id}` not found")));
    }
    Ok(StatusCode::NO_CONTENT)
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
    crate::auth::require_permission(&state, &headers, "integration.manage")?;
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
    claims.require("integration.manage")?;
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
    let catalog = crate::api::connector_types::catalog(&state.db).await?;
    if let Some(connector_type) = body.connector_type {
        if !catalog.is_known_type(&connector_type) {
            return Err(ApiError::bad_request(format!(
                "unknown connector type `{connector_type}`"
            )));
        }
        changes.insert("type".to_owned(), Value::String(connector_type));
    }
    if let Some(enabled) = body.enabled {
        changes.insert("enabled".to_owned(), Value::Bool(enabled));
    }

    let effective_type = changes
        .get("type")
        .and_then(Value::as_str)
        .or_else(|| current.get("type").and_then(Value::as_str))
        .unwrap_or_default()
        .to_owned();
    let current_parameters: Map<String, Value> = current
        .get("parameters")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();

    let mut audit_keys: Vec<String> = Vec::new();
    let mut secrets_changed: Vec<String> = Vec::new();
    if let Some(parameters) = body.parameters {
        let patch = parameters_object(parameters)?;
        audit_keys = changed_keys(&patch);
        let mut merged = match body.parameters_mode.as_deref() {
            None | Some("replace") => patch,
            Some("merge") => {
                crate::api::connector_schema::merge_parameters(&current_parameters, &patch)
            }
            Some(other) => {
                return Err(ApiError::bad_request(format!(
                    "unknown parameters_mode `{other}` (expected `replace` or `merge`)"
                )));
            }
        };
        catalog.normalize(&effective_type, &mut merged);
        ensure_valid_parameters(
            &catalog,
            &effective_type,
            &merged,
            strict_parameters(&state),
        )?;
        // Values of declared secret fields move out of `parameters`; a reference that
        // is already there means "unchanged" (the form never saw the value).
        let split = catalog.split_secrets(&id, &effective_type, &merged);
        if !split.store.is_empty() || !split.cleared.is_empty() {
            secrets_changed = split
                .store
                .keys()
                .chain(split.cleared.iter())
                .cloned()
                .collect();
            store_secrets(&state.db, key, &split.store, &split.cleared).await?;
        }
        changes.insert("parameters".to_owned(), Value::Object(split.parameters));
    } else if changes.contains_key("type") {
        // A type switch alone must not leave a configuration behind the new
        // type's schema rejects.
        ensure_valid_parameters(
            &catalog,
            &effective_type,
            &current_parameters,
            strict_parameters(&state),
        )?;
    }

    if changes.is_empty() {
        return Ok(Json(current));
    }

    // `parameters` is written with an explicit `SET`: a `MERGE` would deep-merge
    // into the stored object, so keys deleted by the caller would survive.
    let stored_parameters = match changes.remove("parameters") {
        Some(Value::Object(merged)) => merged,
        _ => current_parameters,
    };

    let sql = "
        LET $c = type::record('connector', $key);
        UPDATE $c MERGE $changes;
        UPDATE $c SET parameters = $parameters;
        UPDATE $c SET updated_at = time::now(), updated_by = $user;
        SELECT * FROM $c;
    ";
    let mut response = state
        .db
        .inner()
        .query(sql)
        .bind(("key", key))
        .bind(("changes", Value::Object(changes)))
        .bind(("parameters", Value::Object(stored_parameters)))
        .bind(("user", claims.sub.clone()))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(4)?;
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
            // Names only — never the values (headers may carry tokens).
            metadata: Some(json!({
                "name": connector.get("name"),
                "type": connector.get("type"),
                "changed_keys": audit_keys,
                "secrets_changed": secrets_changed
            })),
        },
    )
    .await;

    Ok(Json(connector))
}

/// Parameters of the first enabled connector of a type, used as job defaults.
///
/// The `json_import`/`json_export` types carry no runtime configuration of
/// their own; they act as a **profile** for the import/export jobs. When more
/// than one enabled connector of a type exists, the name decides (stable
/// choice). Returns an empty map when no such connector exists — the caller
/// then falls back to its own default.
pub async fn type_defaults(db: &Db, type_key: &str) -> Result<Map<String, Value>, ApiError> {
    let sql = "
        SELECT name, parameters FROM connector
        WHERE type = $type AND enabled = true
        ORDER BY name
        LIMIT 1;
    ";
    let mut response = db
        .inner()
        .query(sql)
        .bind(("type", type_key.to_owned()))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    let parameters = rows
        .into_iter()
        .next()
        .and_then(|row| row.get("parameters").cloned())
        .unwrap_or(Value::Null);
    Ok(parameters.as_object().cloned().unwrap_or_default())
}

/// Reads a (possibly nested, dotted) default from a defaults map.
pub fn default_value<'a>(defaults: &'a Map<String, Value>, path: &str) -> Option<&'a Value> {
    let mut current: Option<&Value> = None;
    for (index, part) in path.split('.').enumerate() {
        let node = if index == 0 {
            defaults.get(part)
        } else {
            current
                .and_then(Value::as_object)
                .and_then(|map| map.get(part))
        };
        current = node;
        node?;
    }
    current
}

/// Reads a non-empty string default (blank strings count as unset).
pub fn default_string(defaults: &Map<String, Value>, path: &str) -> Option<String> {
    default_value(defaults, path)
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
}

/// Reads a boolean default.
pub fn default_bool(defaults: &Map<String, Value>, path: &str) -> Option<bool> {
    default_value(defaults, path).and_then(Value::as_bool)
}

/// Reads a non-empty string-list default.
pub fn default_string_list(defaults: &Map<String, Value>, path: &str) -> Option<Vec<String>> {
    let items: Vec<String> = default_value(defaults, path)
        .and_then(Value::as_array)
        .map(|values| {
            values
                .iter()
                .filter_map(Value::as_str)
                .map(str::trim)
                .filter(|value| !value.is_empty())
                .map(str::to_owned)
                .collect()
        })
        .unwrap_or_default();
    if items.is_empty() { None } else { Some(items) }
}

/// Returns the parameter schema of every connector type.
///
/// The admin UI renders its configuration form from this description; other
/// clients (MCP, CLI) use it for validation hints. Open to the machine-to-machine
/// API key and admins.
#[utoipa::path(
    get,
    path = "/connector-types",
    responses(
        (status = 200, description = "Parameter schema per connector type", body = Vec<Value>),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Forbidden", body = Value)
    )
)]
pub async fn list_connector_types(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    crate::auth::require_permission(&state, &headers, "integration.manage")?;
    // Built-ins **and** registered types: the form renders whatever is here, so a
    // registered type needs no UI change (`plan/detail/konnektor-konfiguration.md` §14.1).
    let catalog = crate::api::connector_types::catalog(&state.db).await?;
    Ok(Json(catalog.schema_document()))
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
    claims.require("integration.manage")?;
    let key = parse_key(&id)?;
    fetch_connector(&state.db, key)
        .await?
        .ok_or_else(|| ApiError::not_found(format!("connector `{id}` not found")))?;

    // The secret record goes with the connector — a leftover token would be a
    // secret nobody can see, rotate or delete (`…/konnektor-konfiguration.md` §14.2).
    let sql = "
        LET $c = type::record('connector', $key);
        LET $s = type::record('connector_secret', $key);
        DELETE $c;
        DELETE $s;
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
        let catalog = crate::api::connector_schema::Catalog::builtin();
        assert!(catalog.is_known_type("webhook"));
        assert!(catalog.is_known_type("json_import"));
        assert!(catalog.is_known_type("json_export"));
        assert!(!catalog.is_known_type("ftp"));
    }

    #[test]
    fn parameters_must_be_an_object() {
        assert!(parameters_object(json!({})).is_ok());
        assert!(parameters_object(Value::Array(Vec::new())).is_err());
    }

    #[test]
    fn invalid_parameters_are_reported_with_their_path() {
        let catalog = crate::api::connector_schema::Catalog::builtin();
        let error = ensure_valid_parameters(&catalog, "webhook", &Map::new(), false)
            .expect_err("missing url");
        assert_eq!(error.message(), "parameters.url: is required");

        let mut valid = Map::new();
        valid.insert(
            "url".to_owned(),
            Value::String("https://x.example".to_owned()),
        );
        assert!(ensure_valid_parameters(&catalog, "webhook", &valid, false).is_ok());
    }

    #[test]
    fn parameter_modes_are_merged_or_replaced() {
        let current: Map<String, Value> = json!({
            "url": "https://x.example",
            "headers": {"Authorization": "Bearer token"}
        })
        .as_object()
        .cloned()
        .unwrap_or_default();
        let patch: Map<String, Value> = json!({"headers": {"X-Extra": "1"}})
            .as_object()
            .cloned()
            .unwrap_or_default();

        let merged = crate::api::connector_schema::merge_parameters(&current, &patch);
        assert_eq!(
            merged.get("headers"),
            Some(&json!({"Authorization": "Bearer token", "X-Extra": "1"}))
        );
    }
}
