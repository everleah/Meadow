//! MEF import endpoint (Phase 6).
//!
//! `POST /imports` accepts a Meadow Exchange Format (MEF) document as a JSON
//! body, validates it superficially, stores it in the `import_document` table,
//! and queues an `inbound_import` job run. The caller is never blocked: the
//! worker fetches the document via `GET /imports/{document_id}` and performs
//! the actual import (upsert by business key).

use axum::Json;
use axum::body::Bytes;
use axum::extract::{Path, Query, State};
use axum::http::{HeaderMap, StatusCode, header};
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
    /// Match the MEF object `id` against existing core object ids. Meadow's own
    /// MEF export writes the core record id (`object:<ulid>`) into `id`; enabling
    /// this turns a MEF roundtrip into an update instead of a duplicate insert.
    #[serde(default)]
    pub match_by_id: Option<bool>,
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

/// Extracts the MEF document from a JSON body or a multipart file upload.
async fn extract_mef_document(headers: &HeaderMap, body: Bytes) -> Result<Value, ApiError> {
    let content_type = headers
        .get(header::CONTENT_TYPE)
        .and_then(|value| value.to_str().ok())
        .unwrap_or_default();
    if content_type.starts_with("multipart/form-data") {
        let boundary = multer::parse_boundary(content_type).map_err(|error| {
            ApiError::bad_request(format!("invalid multipart content-type: {error}"))
        })?;
        let stream =
            futures_util::stream::once(async move { Ok::<_, std::convert::Infallible>(body) });
        let mut multipart = multer::Multipart::new(stream, boundary);
        while let Some(field) = multipart
            .next_field()
            .await
            .map_err(|error| ApiError::bad_request(format!("multipart error: {error}")))?
        {
            if field.name() == Some("file") {
                let data = field.bytes().await.map_err(|error| {
                    ApiError::bad_request(format!("failed to read upload: {error}"))
                })?;
                return serde_json::from_slice(&data).map_err(|error| {
                    ApiError::bad_request(format!("uploaded file is not valid MEF JSON: {error}"))
                });
            }
        }
        return Err(ApiError::bad_request(
            "multipart upload must contain a `file` field",
        ));
    }
    serde_json::from_slice(&body)
        .map_err(|error| ApiError::bad_request(format!("invalid MEF JSON body: {error}")))
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
    body: Bytes,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    let user_id = crate::auth::api_key_or_role(&headers, &state.config, crate::auth::ROLE_EDITOR)?;
    let document = extract_mef_document(&headers, body).await?;
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
        "match_by_id": params.match_by_id.unwrap_or(false),
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

/// Payload for emitting the `import.completed` summary event.
#[derive(Debug, Deserialize, ToSchema)]
pub struct CompleteImport {
    /// The `job_run` id that completed the import.
    pub job_run_id: String,
    /// Source-system label (MEF document `source`).
    #[serde(default)]
    pub source: Option<String>,
    /// Number of objects created.
    #[serde(default)]
    pub created: u64,
    /// Number of objects updated.
    #[serde(default)]
    pub updated: u64,
    /// Number of objects that failed.
    #[serde(default)]
    pub failed: u64,
    /// Total number of processed objects.
    #[serde(default)]
    pub processed: u64,
}

/// Emits a single `import.completed` summary event to the outbox.
///
/// Used by the import worker when `emit_events: false`: instead of one CRUD
/// event per object, a bulk import emits exactly one summary event (relayed to
/// `events.crud` like any other outbox event).
#[utoipa::path(
    post,
    path = "/imports/{document_id}/complete",
    request_body = CompleteImport,
    responses(
        (status = 201, description = "Summary event queued", body = Value),
        (status = 400, description = "Invalid id or payload", body = Value),
        (status = 403, description = "Forbidden", body = Value)
    )
)]
pub async fn complete_import(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(document_id): Path<String>,
    Json(body): Json<CompleteImport>,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    crate::auth::api_key_or_role(&headers, &state.config, crate::auth::ROLE_ADMIN)?;
    parse_key(&document_id, TABLE)?;

    let outbox = crate::event::content(&crate::event::OutboxEvent {
        event_type: crate::event::EVENT_IMPORT_COMPLETED,
        object_type: String::new(),
        object_id: body.job_run_id.clone(),
        changes: json!({
            "created": body.created,
            "updated": body.updated,
            "failed": body.failed,
            "processed": body.processed,
        }),
        metadata: json!({
            "job_run_id": body.job_run_id,
            "source": body.source,
            "document_id": document_id,
        }),
    });

    let sql = "
        LET $e = type::record('outbox_event', rand::ulid());
        CREATE $e CONTENT $outbox;
        SELECT * FROM $e;
    ";
    let mut response = state
        .db
        .inner()
        .query(sql)
        .bind(("outbox", outbox))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(2)?;
    let event = rows
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::internal("created outbox event was not returned"))?;
    Ok((StatusCode::CREATED, Json(event)))
}
