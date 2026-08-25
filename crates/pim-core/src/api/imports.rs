//! MEF import endpoint (Phase 6).
//!
//! `POST /imports` accepts a Meadow Exchange Format (MEF) document as a JSON
//! body, validates it superficially, stores it in the `import_document` table,
//! and queues an `inbound_import` job run. The caller is never blocked: the
//! worker fetches the document via `GET /imports/{document_id}` and performs
//! the actual import (upsert by business key).

use axum::Json;
use axum::extract::{Path, Query, State};
use axum::http::{HeaderMap, StatusCode};
use serde::Deserialize;
use serde_json::{Value, json};
use utoipa::{IntoParams, ToSchema};

use crate::api::jobs::queue_run;
use crate::app::AppState;
use crate::error::ApiError;

const TABLE: &str = "import_document";

/// Query parameters for `POST /imports`.
#[derive(Debug, Deserialize, ToSchema, IntoParams)]
#[into_params(parameter_in = Query)]
pub struct ImportParams {
    /// Business-key attribute name used for upsert matching (e.g. `sku`).
    #[serde(default)]
    pub match_by_key: Option<String>,
    /// Source-system label (overrides the document's `source` field).
    #[serde(default)]
    pub source: Option<String>,
    /// Whether per-object CRUD events are emitted (default `true`; a summary
    /// event is a later Phase 6 step).
    #[serde(default)]
    pub emit_events: Option<bool>,
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

/// Whether a MEF document is structurally acceptable.
fn validate_mef(document: &Value) -> Result<(), ApiError> {
    let Value::Object(map) = document else {
        return Err(ApiError::bad_request("MEF document must be a JSON object"));
    };
    let has_objects = map
        .get("objects")
        .is_some_and(|value| value.as_array().is_some());
    let has_references = map
        .get("references")
        .is_some_and(|value| value.as_array().is_some());
    if !has_objects && !has_references {
        return Err(ApiError::bad_request(
            "MEF document must contain an `objects` or `references` array",
        ));
    }
    Ok(())
}

/// Accepts a MEF document, stores it, and queues an `inbound_import` job.
#[utoipa::path(
    post,
    path = "/imports",
    params(ImportParams),
    request_body = Value,
    responses(
        (status = 202, description = "Import job queued", body = Value),
        (status = 400, description = "Invalid MEF document", body = Value),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Forbidden", body = Value),
        (status = 503, description = "Job queue unavailable", body = Value)
    )
)]
pub async fn create_import(
    State(state): State<AppState>,
    headers: HeaderMap,
    Query(params): Query<ImportParams>,
    Json(document): Json<Value>,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    let user_id = crate::auth::api_key_or_role(&headers, &state.config, crate::auth::ROLE_EDITOR)?;
    validate_mef(&document)?;

    let format_version = document
        .get("format_version")
        .and_then(Value::as_str)
        .map(str::to_owned);
    let source = params
        .source
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
        .or_else(|| {
            document
                .get("source")
                .and_then(Value::as_str)
                .map(str::to_owned)
        });

    let mut content = serde_json::Map::new();
    content.insert("document".to_owned(), document);
    if let Some(version) = format_version {
        content.insert("format_version".to_owned(), Value::String(version));
    }
    if let Some(source) = source.clone() {
        content.insert("source".to_owned(), Value::String(source));
    }
    content.insert("created_by".to_owned(), Value::String(user_id.clone()));

    let sql = "
        LET $d = type::record('import_document', rand::ulid());
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
    let record = rows
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::internal("created import document was not returned"))?;
    let document_id = record
        .get("id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();

    let parameters = json!({
        "document_id": document_id,
        "match_by_key": params.match_by_key.as_deref().map(str::trim).filter(|v| !v.is_empty()),
        "source": source,
        "emit_events": params.emit_events.unwrap_or(true),
    });
    let (_, run) = queue_run(
        &state.db,
        &state.config,
        None,
        "inbound_import",
        parameters,
        "manual",
        &user_id,
    )
    .await?;
    let run_id = run
        .get("id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();

    Ok((StatusCode::ACCEPTED, Json(json!({ "job_run_id": run_id }))))
}

/// Loads a stored MEF document (worker, machine-to-machine or admin).
#[utoipa::path(
    get,
    path = "/imports/{document_id}",
    responses(
        (status = 200, description = "Stored MEF document", body = Value),
        (status = 400, description = "Invalid id", body = Value),
        (status = 403, description = "Forbidden", body = Value),
        (status = 404, description = "Not found", body = Value)
    )
)]
pub async fn get_import_document(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(document_id): Path<String>,
) -> Result<Json<Value>, ApiError> {
    crate::auth::api_key_or_role(&headers, &state.config, crate::auth::ROLE_ADMIN)?;
    let key = parse_key(&document_id, TABLE)?;
    let sql = "
        LET $d = type::record('import_document', $key);
        SELECT * FROM $d;
    ";
    let mut response = state
        .db
        .inner()
        .query(sql)
        .bind(("key", key))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(1)?;
    let record = rows
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::not_found(format!("import document `{document_id}` not found")))?;
    Ok(Json(record))
}
