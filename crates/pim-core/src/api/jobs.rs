//! Job-definition CRUD and job-run status handlers (Phase 5).
//!
//! `job_definition` describes recurring or manual tasks; `job_run` records
//! every execution attempt. The scheduler creates `job_run` rows and (Step 2)
//! publishes them to NATS JetStream; the worker executes them and reports
//! status back via `PATCH /jobs/runs/{run_id}`.

use axum::Json;
use axum::extract::{Path, Query, State};
use axum::http::{HeaderMap, StatusCode};
use serde::Deserialize;
use serde_json::{Map, Value, json};
use utoipa::{IntoParams, ToSchema};

use crate::app::AppState;
use crate::audit::{self, AuditEntry};
use crate::auth::AuthUser;
use crate::config::Config;
use crate::db::Db;
use crate::error::ApiError;

const TABLE: &str = "job_definition";
const RUN_TABLE: &str = "job_run";
/// Every job type a definition may carry. The parameter contract of each type
/// lives in [`super::job_schema`], which keeps this list the single source.
pub(crate) const VALID_JOB_TYPES: [&str; 7] = [
    "outbound_export",
    "inbound_import",
    "business_rule",
    "bulk_operation",
    "collection_refresh",
    "config_import",
    "connector_test",
];
const VALID_RUN_STATUSES: [&str; 5] = ["pending", "running", "success", "failed", "retry"];

/// Whether the queue would accept this job type.
pub fn is_known_job_type(job_type: &str) -> bool {
    VALID_JOB_TYPES.contains(&job_type)
}

/// Payload for creating a job definition.
#[derive(Debug, Deserialize, ToSchema)]
pub struct CreateJobDefinition {
    pub name: String,
    #[serde(rename = "type")]
    pub job_type: String,
    /// Cron expression (6 fields, seconds first); omitted = manual only.
    #[serde(default)]
    pub schedule: Option<String>,
    #[serde(default = "default_parameters")]
    pub parameters: Value,
    #[serde(default = "default_enabled")]
    pub enabled: bool,
}

fn default_parameters() -> Value {
    Value::Object(Map::new())
}

fn default_enabled() -> bool {
    true
}

/// Payload for a partial job-definition update.
#[derive(Debug, Deserialize, ToSchema)]
pub struct UpdateJobDefinition {
    #[serde(default)]
    pub name: Option<String>,
    /// `null` clears the schedule (manual only).
    #[serde(default)]
    pub schedule: Option<Option<String>>,
    #[serde(default)]
    pub parameters: Option<Value>,
    #[serde(default)]
    pub enabled: Option<bool>,
}

/// Query parameters for listing job definitions.
#[derive(Debug, Deserialize, ToSchema, IntoParams)]
#[into_params(parameter_in = Query)]
pub struct ListJobs {
    #[serde(rename = "type")]
    #[param(rename = "type")]
    pub job_type: Option<String>,
    pub enabled: Option<bool>,
    #[serde(default = "default_limit")]
    pub limit: u32,
    #[serde(default)]
    pub start: u32,
}

/// Query parameters for listing job runs.
#[derive(Debug, Deserialize, ToSchema, IntoParams)]
#[into_params(parameter_in = Query)]
pub struct ListJobRuns {
    /// Comma-separated `job_run:<key>` ids to fetch (UI polling).
    pub ids: Option<String>,
    pub status: Option<String>,
    #[serde(default = "default_limit")]
    pub limit: u32,
    #[serde(default)]
    pub start: u32,
    /// `false` omits `result` from the answer.
    ///
    /// A bulk import stores its full report there (tens of thousands of entries per
    /// run), so fetching a handful of runs with results pulls tens of megabytes out
    /// of SurrealDB and **resets the connection** (found live 2026-09-16 while
    /// polling an import: `GET /jobs/runs?limit=50` → `500 Connection reset`, which
    /// killed the import script while its worker kept working). Polling should pass
    /// `include_result=false` and fetch a single report afterwards.
    ///
    /// **`result_summary` is always in the answer** (a few bytes): the scalar
    /// top-level fields of `result`, so a listing can still say what a run did
    /// (`format`, `exported`, `connector`) without the payload. See
    /// [`result_summary`].
    #[serde(default = "default_true")]
    pub include_result: bool,
}

/// Payload for a worker status update (`PATCH /jobs/runs/{run_id}`).
#[derive(Debug, Deserialize, ToSchema)]
pub struct UpdateJobRun {
    /// `running` | `success` | `failed` | `retry`.
    pub status: String,
    #[serde(default)]
    pub result: Option<Value>,
    #[serde(default)]
    pub error: Option<String>,
    #[serde(default)]
    pub worker_id: Option<String>,
}

fn default_limit() -> u32 {
    100
}

fn default_true() -> bool {
    true
}

/// `SELECT` projection of a run listing: the `result` payload only when asked for
/// (see [`ListJobRuns::include_result`]) — `OMIT` keeps it out of the transfer, a
/// post-fetch strip would still pull the megabytes through the connection.
fn run_projection(include_result: bool) -> &'static str {
    if include_result { "*" } else { "* OMIT result" }
}

/// The larger a single field of a `result` may be to still enter `result_summary`.
///
/// 2 KB is a **hard** bound, not a target: the summary sits in every run row and is
/// read by every listing, so it has to stay small by construction. It is comfortably
/// above what a report's *description* needs (`columns` of an export, `format`,
/// counters, a small `report`) and far below any payload.
const SUMMARY_FIELD_LIMIT: usize = 2048;

/// The top-level fields of a run's `result` that fit in [`SUMMARY_FIELD_LIMIT`],
/// stored as `result_summary`.
///
/// A `result` is a **report**, and the part a listing cares about is one line
/// (`format: "bmecat"`, `exported: 120`, `connector: "json_export"`) while the
/// payload next to it is huge: an export stores its whole document there, an import
/// its per-object report. A list read (`GET /jobs/runs?limit=100`) must not transfer
/// that — asking for the newest 100 runs *with* results answered with **20 MB** and
/// the next step **reset the SurrealDB connection** (`500 Connection reset`,
/// measured 2026-09-18: `include_result=true&limit=50` failed, `limit=20` delivered
/// 20 MB); the export page did exactly that.
///
/// The rule is deliberately generic: **size, not type and not job type**. Scalars
/// pass without measuring, containers are measured once while the result is written;
/// nothing nested is dropped selectively, so no job type needs its own reader and a
/// new field is covered the moment it is small. `result` itself stays the complete
/// report — the single-run read (`ids=<id>&limit=1`) is what sees it, which is what
/// a report dialog and the download path do.
fn result_summary(result: &Value) -> Value {
    let Some(entries) = result.as_object() else {
        return Value::Null;
    };
    let mut summary = Map::new();
    for (key, value) in entries {
        let small = match value {
            Value::Null | Value::Bool(_) | Value::Number(_) | Value::String(_) => true,
            _ => serde_json::to_string(value).is_ok_and(|text| text.len() <= SUMMARY_FIELD_LIMIT),
        };
        if small {
            summary.insert(key.clone(), value.clone());
        }
    }
    if summary.is_empty() {
        Value::Null
    } else {
        Value::Object(summary)
    }
}

/// Splits a `table:key` path segment and verifies the expected table.
fn parse_key<'a>(id: &'a str, table: &str) -> Result<&'a str, ApiError> {
    let (actual_table, key) = id
        .split_once(':')
        .ok_or_else(|| ApiError::bad_request(format!("invalid id `{id}`")))?;
    if actual_table != table {
        return Err(ApiError::bad_request(format!(
            "id `{id}` does not reference table `{table}`"
        )));
    }
    Ok(key)
}

/// Loads a single job definition record.
async fn fetch_definition(db: &Db, key: &str) -> Result<Option<Value>, ApiError> {
    let sql = "
        LET $d = type::record('job_definition', $key);
        SELECT * FROM $d;
    ";
    let mut response = db.inner().query(sql).bind(("key", key)).await?.check()?;
    let rows: Vec<Value> = response.take(1)?;
    Ok(rows.into_iter().next())
}

/// Loads a single job run record.
async fn fetch_run(db: &Db, key: &str) -> Result<Option<Value>, ApiError> {
    let sql = "
        LET $r = type::record('job_run', $key);
        SELECT * FROM $r;
    ";
    let mut response = db.inner().query(sql).bind(("key", key)).await?.check()?;
    let rows: Vec<Value> = response.take(1)?;
    Ok(rows.into_iter().next())
}

/// Checks that a job-definition name is not already taken.
async fn ensure_unique_name(db: &Db, name: &str, except: Option<&str>) -> Result<(), ApiError> {
    let rows: Vec<Value> = db
        .inner()
        .query("SELECT id FROM job_definition WHERE name = $name")
        .bind(("name", name.to_owned()))
        .await?
        .check()?
        .take(0)?;
    let conflict = rows
        .iter()
        .any(|row| row.get("id").and_then(Value::as_str) != except);
    if conflict {
        return Err(ApiError::conflict(format!(
            "job definition `{name}` already exists"
        )));
    }
    Ok(())
}

/// Creates a job definition.
#[utoipa::path(
    post,
    path = "/jobs",
    request_body = CreateJobDefinition,
    responses(
        (status = 201, description = "Created job definition", body = Value),
        (status = 400, description = "Invalid payload", body = Value),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Forbidden", body = Value),
        (status = 409, description = "Name already exists", body = Value)
    )
)]
pub async fn create_job_definition(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Json(body): Json<CreateJobDefinition>,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    claims.require("job.manage")?;
    let name = body.name.trim().to_owned();
    if name.is_empty() {
        return Err(ApiError::bad_request("`name` must not be empty"));
    }
    if !VALID_JOB_TYPES.contains(&body.job_type.as_str()) {
        return Err(ApiError::bad_request(format!(
            "unknown job type `{}`",
            body.job_type
        )));
    }
    ensure_unique_name(&state.db, &name, None).await?;

    let mut content = Map::new();
    content.insert("name".to_owned(), Value::String(name));
    content.insert("type".to_owned(), Value::String(body.job_type));
    if let Some(schedule) = body.schedule {
        let schedule = schedule.trim().to_owned();
        if !schedule.is_empty() {
            crate::scheduler::validate_schedule(&schedule)?;
            content.insert("schedule".to_owned(), Value::String(schedule));
        }
    }
    content.insert("parameters".to_owned(), body.parameters);
    content.insert("enabled".to_owned(), Value::Bool(body.enabled));
    content.insert("updated_by".to_owned(), Value::String(claims.sub.clone()));

    let sql = "
        LET $d = type::record('job_definition', rand::ulid());
        CREATE $d CONTENT $content;
        SELECT * FROM $d;
    ";
    let mut response = state
        .db
        .inner()
        .query(sql)
        .bind(("content", Value::Object(content)))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(2)?;
    let definition = rows
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::internal("created job definition was not returned"))?;
    let definition_id = definition
        .get("id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();

    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &claims.sub,
            action: "job_definition_created",
            object_id: Some(&definition_id),
            from: None,
            to: None,
            metadata: Some(json!({ "name": definition.get("name") })),
        },
    )
    .await;

    Ok((StatusCode::CREATED, Json(definition)))
}

/// Lists job definitions.
#[utoipa::path(
    get,
    path = "/jobs",
    params(ListJobs),
    responses((status = 200, description = "Job definitions", body = Vec<Value>))
)]
pub async fn list_job_definitions(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Query(params): Query<ListJobs>,
) -> Result<Json<Vec<Value>>, ApiError> {
    claims.require("job.read")?;
    let limit = params.limit.clamp(1, 1000) as i64;
    let start = params.start as i64;

    let mut sql = String::from("SELECT * FROM job_definition");
    let mut conditions: Vec<&str> = Vec::new();
    if params.job_type.is_some() {
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
    if let Some(job_type) = &params.job_type {
        query = query.bind(("type", job_type.clone()));
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

/// Loads a single job definition.
#[utoipa::path(
    get,
    path = "/jobs/{id}",
    responses(
        (status = 200, description = "Job definition", body = Value),
        (status = 404, description = "Not found", body = Value)
    )
)]
pub async fn get_job_definition(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<Json<Value>, ApiError> {
    claims.require("job.read")?;
    let key = parse_key(&id, TABLE)?;
    let definition = fetch_definition(&state.db, key)
        .await?
        .ok_or_else(|| ApiError::not_found(format!("job definition `{id}` not found")))?;
    Ok(Json(definition))
}

/// Partially updates a job definition.
#[utoipa::path(
    patch,
    path = "/jobs/{id}",
    request_body = UpdateJobDefinition,
    responses(
        (status = 200, description = "Updated job definition", body = Value),
        (status = 400, description = "Invalid id or payload", body = Value),
        (status = 404, description = "Not found", body = Value),
        (status = 409, description = "Name already exists", body = Value)
    )
)]
pub async fn update_job_definition(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
    Json(body): Json<UpdateJobDefinition>,
) -> Result<Json<Value>, ApiError> {
    claims.require("job.manage")?;
    let key = parse_key(&id, TABLE)?;
    let current = fetch_definition(&state.db, key)
        .await?
        .ok_or_else(|| ApiError::not_found(format!("job definition `{id}` not found")))?;

    let mut changes = Map::new();
    let mut clear_schedule = false;
    if let Some(name) = body.name {
        let name = name.trim().to_owned();
        if name.is_empty() {
            return Err(ApiError::bad_request("`name` must not be empty"));
        }
        ensure_unique_name(&state.db, &name, Some(id.as_str())).await?;
        changes.insert("name".to_owned(), Value::String(name));
    }
    if let Some(schedule) = body.schedule {
        match schedule {
            Some(value) => {
                let value = value.trim().to_owned();
                if value.is_empty() {
                    clear_schedule = true;
                } else {
                    crate::scheduler::validate_schedule(&value)?;
                    changes.insert("schedule".to_owned(), Value::String(value));
                }
            }
            None => {
                clear_schedule = true;
            }
        }
    }
    if let Some(parameters) = body.parameters {
        changes.insert("parameters".to_owned(), parameters);
    }
    if let Some(enabled) = body.enabled {
        changes.insert("enabled".to_owned(), Value::Bool(enabled));
    }

    if changes.is_empty() && !clear_schedule {
        return Ok(Json(current));
    }

    let set_clause = if clear_schedule {
        "UPDATE $d SET schedule = NONE, updated_at = time::now(), updated_by = $user;"
    } else {
        "UPDATE $d SET updated_at = time::now(), updated_by = $user;"
    };
    let sql = format!(
        "
        LET $d = type::record('job_definition', $key);
        UPDATE $d MERGE $changes;
        {set_clause}
        SELECT * FROM $d;
        "
    );
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
    let definition = rows
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::not_found(format!("job definition `{id}` not found")))?;

    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &claims.sub,
            action: "job_definition_updated",
            object_id: Some(&id),
            from: None,
            to: None,
            metadata: None,
        },
    )
    .await;

    Ok(Json(definition))
}

/// Deletes a job definition and its associated job runs (admin only).
#[utoipa::path(
    delete,
    path = "/jobs/{id}",
    responses(
        (status = 204, description = "Deleted job definition", body = Value),
        (status = 400, description = "Invalid id", body = Value),
        (status = 404, description = "Not found", body = Value)
    )
)]
pub async fn delete_job_definition(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<StatusCode, ApiError> {
    claims.require("job.manage")?;
    let key = parse_key(&id, TABLE)?;
    if fetch_definition(&state.db, key).await?.is_none() {
        return Err(ApiError::not_found(format!(
            "job definition `{id}` not found"
        )));
    }
    // Remove the definition and every run that references it (no orphans).
    let sql = "
        DELETE FROM job_run WHERE job_definition_id = $id;
        LET $d = type::record('job_definition', $key);
        DELETE $d;
    ";
    state
        .db
        .inner()
        .query(sql)
        .bind(("id", id.clone()))
        .bind(("key", key))
        .await?
        .check()?;

    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &claims.sub,
            action: "job_definition_deleted",
            object_id: Some(&id),
            from: None,
            to: None,
            metadata: None,
        },
    )
    .await;

    Ok(StatusCode::NO_CONTENT)
}

/// Lists job runs for a definition.
#[utoipa::path(
    get,
    path = "/jobs/{id}/runs",
    responses((status = 200, description = "Job runs", body = Vec<Value>))
)]
pub async fn list_definition_runs(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
    Query(params): Query<ListJobRuns>,
) -> Result<Json<Vec<Value>>, ApiError> {
    claims.require("job.read")?;
    parse_key(&id, TABLE)?;
    let limit = params.limit.clamp(1, 1000) as i64;
    let start = params.start as i64;

    let mut sql = format!(
        "SELECT {} FROM job_run WHERE job_definition_id = $definition_id",
        run_projection(params.include_result)
    );
    if params.status.is_some() {
        sql.push_str(" AND status = $status");
    }
    sql.push_str(" ORDER BY created_at DESC LIMIT $limit START $start");

    let mut query = state
        .db
        .inner()
        .query(sql)
        .bind(("definition_id", id.clone()));
    if let Some(status) = &params.status {
        query = query.bind(("status", status.clone()));
    }
    let mut response = query
        .bind(("limit", limit))
        .bind(("start", start))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    Ok(Json(rows))
}

/// Lists job runs (optionally filtered by ids or status).
#[utoipa::path(
    get,
    path = "/jobs/runs",
    params(ListJobRuns),
    responses((status = 200, description = "Job runs", body = Vec<Value>))
)]
pub async fn list_job_runs(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Query(params): Query<ListJobRuns>,
) -> Result<Json<Vec<Value>>, ApiError> {
    claims.require("job.read")?;
    let limit = params.limit.clamp(1, 1000) as i64;
    let start = params.start as i64;

    let mut sql = format!(
        "SELECT {} FROM job_run",
        run_projection(params.include_result)
    );
    let mut conditions: Vec<String> = Vec::new();
    if let Some(ids) = &params.ids {
        let list = ids
            .split(',')
            .map(str::trim)
            .filter(|token| !token.is_empty())
            .map(|token| {
                let key = parse_key(token, RUN_TABLE)?;
                if !key
                    .chars()
                    .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
                {
                    return Err(ApiError::bad_request(format!(
                        "invalid job run id `{token}`"
                    )));
                }
                Ok::<String, ApiError>(format!("type::record('job_run', '{key}')"))
            })
            .collect::<Result<Vec<_>, _>>()?;
        conditions.push(format!("id IN [{}]", list.join(", ")));
    }
    if params.status.is_some() {
        conditions.push("status = $status".to_owned());
    }
    if !conditions.is_empty() {
        sql.push_str(" WHERE ");
        sql.push_str(&conditions.join(" AND "));
    }
    sql.push_str(" ORDER BY created_at DESC LIMIT $limit START $start");

    let mut query = state.db.inner().query(sql);
    if let Some(status) = &params.status {
        query = query.bind(("status", status.clone()));
    }
    let mut response = query
        .bind(("limit", limit))
        .bind(("start", start))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    Ok(Json(rows))
}

/// Updates a job-run status (worker, machine-to-machine or admin).
#[utoipa::path(
    patch,
    path = "/jobs/runs/{run_id}",
    request_body = UpdateJobRun,
    responses(
        (status = 200, description = "Updated job run", body = Value),
        (status = 400, description = "Invalid id or status", body = Value),
        (status = 404, description = "Not found", body = Value)
    )
)]
pub async fn update_job_run(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(run_id): Path<String>,
    Json(body): Json<UpdateJobRun>,
) -> Result<Json<Value>, ApiError> {
    // The machine path (the worker reports run results) and the admin fallback are one
    // catalog check.
    crate::auth::require_permission(&state, &headers, "job.manage")?;
    if !VALID_RUN_STATUSES.contains(&body.status.as_str()) {
        return Err(ApiError::bad_request(format!(
            "invalid job-run status `{}`",
            body.status
        )));
    }
    let key = parse_key(&run_id, RUN_TABLE)?;
    if fetch_run(&state.db, key).await?.is_none() {
        return Err(ApiError::not_found(format!("job run `{run_id}` not found")));
    }

    let mut changes = Map::new();
    changes.insert("status".to_owned(), Value::String(body.status.clone()));
    if let Some(worker_id) = body.worker_id {
        changes.insert("worker_id".to_owned(), Value::String(worker_id));
    }
    if let Some(result) = body.result {
        // The summary is written **with** the result, from the result: whoever stores
        // a report stores its one-line essence, and no reader has to derive it from a
        // payload it must not transfer (see [`result_summary`]).
        changes.insert("result_summary".to_owned(), result_summary(&result));
        changes.insert("result".to_owned(), result);
    }
    if let Some(error) = body.error {
        changes.insert("error".to_owned(), Value::String(error));
    }

    let mut extra = String::new();
    match body.status.as_str() {
        "running" => {
            // Fresh attempt: clear any stale error/result from a previous attempt.
            extra.push_str(
                "UPDATE $r SET started_at = (IF started_at IS NONE THEN time::now() ELSE started_at END), error = NONE, result = NONE, result_summary = NONE;",
            );
        }
        "success" => {
            extra.push_str("UPDATE $r SET finished_at = time::now(), error = NONE;");
        }
        "failed" => {
            extra.push_str("UPDATE $r SET finished_at = time::now();");
        }
        "retry" => {
            extra.push_str(
                "UPDATE $r SET attempt = attempt + 1, error = NONE, result = NONE, result_summary = NONE;",
            );
        }
        _ => {}
    }

    // Statements: LET(0), MERGE(1), [extra UPDATE(2)], final SET(3), SELECT(4).
    let has_extra = !extra.is_empty();
    let sql = format!(
        "
        LET $r = type::record('job_run', $key);
        UPDATE $r MERGE $changes;
        {extra}
        UPDATE $r SET updated_at = time::now();
        SELECT * FROM $r;
        "
    );
    let mut response = state
        .db
        .inner()
        .query(sql)
        .bind(("key", key))
        .bind(("changes", Value::Object(changes)))
        .await?
        .check()?;
    let select_index = if has_extra { 4 } else { 3 };
    let rows: Vec<Value> = response.take(select_index)?;
    let run = rows
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::not_found(format!("job run `{run_id}` not found")))?;

    Ok(Json(run))
}

/// Deletes a single job run (admin only).
#[utoipa::path(
    delete,
    path = "/jobs/runs/{run_id}",
    responses(
        (status = 204, description = "Deleted job run", body = Value),
        (status = 400, description = "Invalid id", body = Value),
        (status = 404, description = "Not found", body = Value)
    )
)]
pub async fn delete_job_run(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(run_id): Path<String>,
) -> Result<StatusCode, ApiError> {
    claims.require("job.manage")?;
    let key = parse_key(&run_id, RUN_TABLE)?;
    if fetch_run(&state.db, key).await?.is_none() {
        return Err(ApiError::not_found(format!("job run `{run_id}` not found")));
    }
    let sql = "
        LET $r = type::record('job_run', $key);
        DELETE $r;
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
            action: "job_run_deleted",
            object_id: Some(&run_id),
            from: None,
            to: None,
            metadata: None,
        },
    )
    .await;

    Ok(StatusCode::NO_CONTENT)
}

/// Marks a job run as failed (best effort), used when queueing fails.
pub(crate) async fn fail_run(db: &Db, run_id: &str, error: &str) {
    let key = run_id.split_once(':').map(|(_, k)| k).unwrap_or(run_id);
    let sql = "
        LET $r = type::record('job_run', $key);
        UPDATE $r SET status = 'failed', error = $error, finished_at = time::now(), updated_at = time::now();
    ";
    if let Err(err) = db
        .inner()
        .query(sql)
        .bind(("key", key))
        .bind(("error", error.to_owned()))
        .await
        .and_then(|response| response.check())
    {
        tracing::warn!(?err, run_id, "failed to mark job run as failed");
    }
}

/// Creates a `job_run` and publishes its job message to NATS JetStream.
///
/// On a queue failure the run is marked `failed` and the error is returned.
/// `job_definition_id` is `None` for ad-hoc jobs (e.g. a rule recompute).
pub(crate) async fn queue_run(
    db: &Db,
    config: &Config,
    job_definition_id: Option<&str>,
    job_type: &str,
    parameters: Value,
    trigger: &str,
    user_id: &str,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    let mut content = json!({
        "trigger": trigger,
        "status": "pending",
        "attempt": 1,
    });
    if let Some(definition_id) = job_definition_id {
        content["job_definition_id"] = Value::String(definition_id.to_owned());
    }
    let sql = "
        LET $r = type::record('job_run', rand::ulid());
        CREATE $r CONTENT $content;
        SELECT * FROM $r;
    ";
    let mut response = db
        .inner()
        .query(sql)
        .bind(("content", content))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(2)?;
    let run = rows
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::internal("created job run was not returned"))?;
    let run_id = run
        .get("id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();

    let payload = json!({
        "run_id": run_id.clone(),
        "job_definition_id": job_definition_id,
        "type": job_type,
        "parameters": parameters,
    });
    if let Err(error) = crate::jobqueue::publish_job(
        &config.jobs_nats_url,
        job_type,
        config.jobs_stream_max_age_minutes,
        payload,
    )
    .await
    {
        fail_run(db, &run_id, error.message()).await;
        return Err(error);
    }

    let mut metadata = json!({ "trigger": trigger, "job_type": job_type });
    if let Some(definition_id) = job_definition_id {
        metadata["job_definition_id"] = Value::String(definition_id.to_owned());
    }
    audit::record_best_effort(
        db,
        AuditEntry {
            user_id,
            action: "job_run_created",
            object_id: Some(&run_id),
            from: None,
            to: None,
            metadata: Some(metadata),
        },
    )
    .await;

    Ok((StatusCode::ACCEPTED, Json(run)))
}

/// Manually triggers a job definition: creates a `job_run` and publishes it
/// to the NATS JetStream job queue (`jobs.<type>`).
#[utoipa::path(
    post,
    path = "/jobs/{id}/run",
    responses(
        (status = 202, description = "Job run queued", body = Value),
        (status = 400, description = "Job disabled or invalid id", body = Value),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Forbidden", body = Value),
        (status = 404, description = "Not found", body = Value),
        (status = 503, description = "Job queue unavailable", body = Value)
    )
)]
pub async fn run_job(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    claims.require("job.run")?;
    let key = parse_key(&id, TABLE)?;
    let definition = fetch_definition(&state.db, key)
        .await?
        .ok_or_else(|| ApiError::not_found(format!("job definition `{id}` not found")))?;
    if !definition
        .get("enabled")
        .and_then(Value::as_bool)
        .unwrap_or(false)
    {
        return Err(ApiError::bad_request("job definition is disabled"));
    }
    let job_type = definition
        .get("type")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    let parameters = definition
        .get("parameters")
        .cloned()
        .unwrap_or_else(|| json!({}));

    queue_run(
        &state.db,
        &state.config,
        Some(&id),
        &job_type,
        parameters,
        "manual",
        &claims.sub,
    )
    .await
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A payload of the size an export really stores (hundreds of objects).
    fn big_document() -> Value {
        json!({
            "objects": (0..400)
                .map(|index| json!({"id": format!("product:{index}"), "name": "Brake disc"}))
                .collect::<Vec<_>>(),
        })
    }

    #[test]
    fn result_summary_keeps_the_description_and_drops_a_large_payload() {
        let result = json!({
            "format": "bmecat",
            "exported": 120,
            "resolved_inheritance": true,
            "columns": ["sku", "name"],
            "document": big_document(),
        });

        assert_eq!(
            result_summary(&result),
            json!({
                "format": "bmecat",
                "exported": 120,
                "resolved_inheritance": true,
                "columns": ["sku", "name"],
            })
        );
    }

    #[test]
    fn result_summary_decides_by_size_not_by_name() {
        // A *small* document is tiny enough to keep, a *large* report is dropped: the
        // rule is the size of the field, not the field's name and not the job type.
        let big = json!({
            "unpriced": (0..400).map(|index| format!("product:{index}")).collect::<Vec<_>>(),
        });
        let small = json!({"unpriced": ["product:1"]});

        assert_eq!(
            result_summary(&json!({"format": "bmecat", "report": big})),
            json!({"format": "bmecat"})
        );
        assert_eq!(
            result_summary(&json!({"format": "bmecat", "report": small})),
            json!({"format": "bmecat", "report": small})
        );
        assert_eq!(
            result_summary(&json!({"document": {"objects": []}})),
            json!({"document": {"objects": []}})
        );
    }

    #[test]
    fn result_summary_is_null_without_usable_fields() {
        assert_eq!(
            result_summary(&json!({"document": big_document()})),
            Value::Null
        );
        assert_eq!(result_summary(&json!({})), Value::Null);
        assert_eq!(result_summary(&json!("not an object")), Value::Null);
    }

    #[test]
    fn result_summary_keeps_zero_and_false() {
        // `0` and `false` are results, not "nothing" — a summary must not lose them.
        let result = json!({"exported": 0, "failed": false, "items": []});

        assert_eq!(
            result_summary(&result),
            json!({"exported": 0, "failed": false, "items": []})
        );
    }
}
