//! UI-guided data export endpoints (Phase 9).
//!
//! `POST /exports` accepts a **structured** export request (source, fields,
//! format, locales, options) and queues an `outbound_export` job; the worker
//! resolves the source, builds tabular columns/rows (or a MEF document for
//! JSON) and stores the serializer-ready output in `job_run.result`
//! (`csv` text / `xlsx_base64` / `document`). `GET /exports/{run_id}/file`
//! reads that result and returns it as a downloadable attachment.

use axum::Json;
use axum::body::Body;
use axum::extract::{Path, Query, State};
use axum::http::{HeaderMap, HeaderValue, StatusCode, header};
use axum::response::Response;
use base64::Engine;
use serde::Deserialize;
use serde_json::{Value, json};
use utoipa::{IntoParams, ToSchema};

use crate::api::jobs::queue_run;
use crate::app::AppState;
use crate::auth::{ROLE_EDITOR, api_key_or_role};
use crate::error::ApiError;

/// The object source for an export.
#[derive(Debug, Deserialize, serde::Serialize, ToSchema)]
pub struct ExportSource {
    /// `collection` | `type` | `category` | `saved_search` | `selection`.
    pub kind: String,
    /// Collection or saved-search id (for `collection`/`saved_search`).
    #[serde(default)]
    pub id: Option<String>,
    /// Object type (for `type`).
    #[serde(default, rename = "type")]
    pub object_type: Option<String>,
    /// Category id (for `category`).
    #[serde(default)]
    pub category: Option<String>,
    /// Object ids (for `selection`).
    #[serde(default)]
    pub object_ids: Option<Vec<String>>,
}

/// Serialization options.
#[derive(Debug, Deserialize, serde::Serialize, ToSchema)]
pub struct ExportOptions {
    #[serde(default = "default_true")]
    pub header: bool,
    #[serde(default = "default_separator")]
    pub separator: String,
    #[serde(default)]
    pub include_system: bool,
    /// E-mail recipients; when set (and SMTP is configured) the worker sends
    /// the finished export as an attachment.
    #[serde(default)]
    pub email_to: Option<Vec<String>>,
}

/// Structured export request for `POST /exports`.
#[derive(Debug, Deserialize, serde::Serialize, ToSchema)]
pub struct ExportRequest {
    pub source: ExportSource,
    /// Field/column names; empty => the worker derives them from the objects.
    #[serde(default)]
    pub fields: Option<Vec<String>>,
    /// `csv` | `xlsx` | `json`.
    #[serde(default = "default_format")]
    pub format: String,
    /// Locales for multilingual columns (`<attr>_<code>`); empty => default locale.
    #[serde(default)]
    pub locales: Option<Vec<String>>,
    #[serde(default)]
    pub options: Option<ExportOptions>,
}

/// Query params for `GET /exports/{run_id}/file`.
#[derive(Debug, Deserialize, IntoParams, ToSchema)]
#[into_params(parameter_in = Query)]
pub struct ExportFileQuery {
    /// Override the stored format: `csv` | `xlsx` | `json`.
    #[serde(default)]
    pub format: Option<String>,
}

fn default_format() -> String {
    "json".to_owned()
}

fn default_true() -> bool {
    true
}

fn default_separator() -> String {
    ",".to_owned()
}

fn strip_prefix<'a>(id: &'a str, table: &str) -> &'a str {
    let prefix = format!("{table}:");
    id.strip_prefix(&prefix).unwrap_or(id)
}

/// Creates and queues an `outbound_export` job from a structured request.
#[utoipa::path(
    post,
    path = "/exports",
    request_body = ExportRequest,
    responses(
        (status = 202, description = "Export job queued", body = Value),
        (status = 400, description = "Invalid request", body = Value),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Forbidden", body = Value)
    )
)]
pub async fn create_export(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(req): Json<ExportRequest>,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    let user_id = api_key_or_role(&headers, &state.config, ROLE_EDITOR)?;

    let kind = req.source.kind.as_str();
    if !matches!(
        kind,
        "collection" | "type" | "category" | "saved_search" | "selection"
    ) {
        return Err(ApiError::bad_request(format!(
            "unsupported source kind `{kind}`"
        )));
    }
    if !matches!(req.format.as_str(), "csv" | "xlsx" | "json") {
        return Err(ApiError::bad_request(format!(
            "unsupported format `{}` (expected csv | xlsx | json)",
            req.format
        )));
    }
    match kind {
        "collection" | "saved_search" if req.source.id.is_none() => {
            return Err(ApiError::bad_request(format!(
                "source kind `{kind}` requires an `id`"
            )));
        }
        "type" if req.source.object_type.is_none() => {
            return Err(ApiError::bad_request(
                "source kind `type` requires a `type`",
            ));
        }
        "category" if req.source.category.is_none() => {
            return Err(ApiError::bad_request(
                "source kind `category` requires a `category`",
            ));
        }
        "selection"
            if req
                .source
                .object_ids
                .as_deref()
                .unwrap_or_default()
                .is_empty() =>
        {
            return Err(ApiError::bad_request(
                "source kind `selection` requires `object_ids`",
            ));
        }
        _ => {}
    }

    let parameters = json!({
        "source": req.source,
        "fields": req.fields,
        "format": req.format,
        "locales": req.locales,
        "options": req.options,
    });
    let (_, run) = queue_run(
        &state.db,
        &state.config,
        None,
        "outbound_export",
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

/// Loads a single job run record.
async fn fetch_run(db: &crate::db::Db, key: &str) -> Result<Option<Value>, ApiError> {
    let sql = "
        LET $r = type::record('job_run', $key);
        SELECT * FROM $r;
    ";
    let mut response = db.inner().query(sql).bind(("key", key)).await?.check()?;
    let rows: Vec<Value> = response.take(1)?;
    Ok(rows.into_iter().next())
}

/// Renders the job-run export result as a downloadable file.
#[utoipa::path(
    get,
    path = "/exports/{run_id}/file",
    params(ExportFileQuery),
    responses(
        (status = 200, description = "Export file"),
        (status = 400, description = "No result / wrong format"),
        (status = 404, description = "Not found")
    )
)]
pub async fn get_export_file(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(run_id): Path<String>,
    Query(params): Query<ExportFileQuery>,
) -> Result<Response, ApiError> {
    api_key_or_role(&headers, &state.config, ROLE_EDITOR)?;
    let key = strip_prefix(&run_id, "job_run").to_owned();
    let Some(run) = fetch_run(&state.db, &key).await? else {
        return Err(ApiError::not_found(format!("job run `{run_id}` not found")));
    };
    if run.get("status").and_then(Value::as_str) != Some("success") {
        return Err(ApiError::bad_request("export is not finished yet"));
    }
    let Some(result) = run.get("result") else {
        return Err(ApiError::bad_request("job run has no export result"));
    };
    let stored_format = result
        .get("format")
        .and_then(Value::as_str)
        .unwrap_or("json");
    let format = params.format.as_deref().unwrap_or(stored_format);

    let (content_type, filename, body): (&str, String, Vec<u8>) = match format {
        "csv" => {
            let csv = result
                .get("csv")
                .and_then(Value::as_str)
                .ok_or_else(|| ApiError::bad_request("no csv result"))?;
            (
                "text/csv; charset=utf-8",
                "meadow-export.csv".to_owned(),
                csv.as_bytes().to_vec(),
            )
        }
        "xlsx" => {
            let b64 = result
                .get("xlsx_base64")
                .and_then(Value::as_str)
                .ok_or_else(|| ApiError::bad_request("no xlsx result"))?;
            let decoded = base64::engine::general_purpose::STANDARD
                .decode(b64)
                .map_err(|_| ApiError::bad_request("corrupt xlsx payload"))?;
            (
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "meadow-export.xlsx".to_owned(),
                decoded,
            )
        }
        "json" => {
            let document = result.get("document").cloned().unwrap_or(Value::Null);
            (
                "application/json",
                "meadow-export.json".to_owned(),
                serde_json::to_vec(&document)
                    .map_err(|e| ApiError::internal(format!("serialize json: {e}")))?,
            )
        }
        other => {
            return Err(ApiError::bad_request(format!(
                "unsupported format `{other}`"
            )));
        }
    };

    let mut response = Response::builder()
        .status(StatusCode::OK)
        .header(header::CONTENT_TYPE, content_type)
        .header(
            header::CONTENT_DISPOSITION,
            format!("attachment; filename=\"{filename}\""),
        )
        .body(Body::from(body))
        .map_err(|e| ApiError::internal(format!("build response: {e}")))?;
    response.headers_mut().insert(
        header::ACCESS_CONTROL_EXPOSE_HEADERS,
        HeaderValue::from_static("Content-Disposition"),
    );
    Ok(response)
}
