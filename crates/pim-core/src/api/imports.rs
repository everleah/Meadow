//! MEF import endpoint (Phase 6).
//!
//! `POST /imports` accepts a Meadow Exchange Format (MEF) document as a JSON
//! body, validates it superficially, stores it in the `import_document` table,
//! and queues an `inbound_import` job run. The caller is never blocked: the
//! worker fetches the document via `GET /imports/{document_id}` and performs
//! the actual import (upsert by business key).
//!
//! `POST /imports/preview` answers what such an import **would** do, object by
//! object, without writing anything (see [`preview_import`]).

use axum::Json;
use axum::body::Bytes;
use axum::extract::{Path, Query, State};
use axum::http::{HeaderMap, StatusCode, header};
use serde::Deserialize;
use serde_json::{Value, json};
use utoipa::{IntoParams, ToSchema};

use std::collections::HashMap;

use crate::api::jobs::queue_run;
use crate::api::objects::{BatchUpsertItem, BatchUpsertRequest, WriteObjectQuery};
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
    let user_id = crate::auth::require_permission(&state, &headers, "job.run")?;
    let document = extract_mef_document(&headers, body).await?;
    validate_mef(&document)?;

    // Defaults of the `json_import` connector profile (empty without one):
    // explicit query parameters win, then the profile, then the document field.
    let defaults = crate::api::connectors::type_defaults(
        &state.db,
        crate::api::connectors::CONNECTOR_TYPE_JSON_IMPORT,
    )
    .await?;

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
        .or_else(|| crate::api::connectors::default_string(&defaults, "source"))
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

    let match_by_key = params
        .match_by_key
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
        .or_else(|| crate::api::connectors::default_string(&defaults, "match_by_key"));
    let match_by_id = params
        .match_by_id
        .or_else(|| crate::api::connectors::default_bool(&defaults, "match_by_id"))
        .unwrap_or(false);
    let emit_events = params
        .emit_events
        .or_else(|| crate::api::connectors::default_bool(&defaults, "emit_events"))
        .unwrap_or(true);

    let parameters = json!({
        "document_id": document_id,
        "match_by_key": match_by_key,
        "match_by_id": match_by_id,
        "source": source,
        "emit_events": emit_events,
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
    crate::auth::require_permission(&state, &headers, "job.manage")?;
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
    crate::auth::require_permission(&state, &headers, "job.manage")?;
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

/// Query parameters for `POST /imports/preview` — the matching semantics of `POST /imports`.
#[derive(Debug, Deserialize, ToSchema, IntoParams)]
#[into_params(parameter_in = Query)]
pub struct PreviewImportParams {
    /// Business-key attribute name used for matching (as `match_by_key` of the import).
    #[serde(default)]
    pub match_by_key: Option<String>,
    /// Match the MEF object `id` against existing core object ids.
    #[serde(default)]
    pub match_by_id: Option<bool>,
    /// Source-system label (overrides the document's `source` field).
    #[serde(default)]
    pub source: Option<String>,
    /// Per-object rows in `results` (default 100, capped at 500).
    #[serde(default)]
    pub limit: Option<u32>,
}

/// Default and maximum number of per-object rows a preview returns.
const PREVIEW_DEFAULT_ROWS: u32 = 100;
const PREVIEW_MAX_ROWS: u32 = 500;

/// Reports what an import **would** do, object by object — without writing anything.
///
/// **The write path's own checks:** the plan comes from
/// [`crate::api::objects::upsert_objects`] in report mode, so the object-type
/// registry, the business-key match, the scope, the attribute values and the rules
/// answer exactly as they would on the write. The import job's composition rule is
/// added on top: a parent that is neither part of the document nor present in the
/// system fails its child (the object would not be created), and a parent inside the
/// document is checked against the composition definition the same way the write
/// checks it.
///
/// **Nothing is written:** no object, no edge, no outbox event, no audit row, and no
/// object type is registered (the answer names the types the import would add). The
/// rule evaluation runs in report mode, so a preview cannot move a rule's failure
/// counter or write rule warnings as log entries.
///
/// **Not covered:** relation rows (`references`) are counted but not resolved — the
/// core writes them through `POST /references/batch`, a check this endpoint does not
/// mirror; and the import worker's value normalization/unit conversion runs on the
/// worker, so the preview judges the document as it stands.
#[utoipa::path(
    post,
    path = "/imports/preview",
    params(PreviewImportParams),
    request_body = Value,
    responses(
        (status = 200, description = "What the import would do", body = Value),
        (status = 400, description = "Invalid MEF document", body = Value),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Forbidden", body = Value)
    )
)]
pub async fn preview_import(
    State(state): State<AppState>,
    headers: HeaderMap,
    Query(params): Query<PreviewImportParams>,
    body: Bytes,
) -> Result<Json<Value>, ApiError> {
    // The preview never writes, but it judges a write: the import right (`job.run`)
    // plus the write check the plan itself runs (`object.create`).
    crate::auth::require_permission(&state, &headers, "job.run")?;
    let document = extract_mef_document(&headers, body).await?;
    validate_mef(&document)?;
    Ok(Json(
        preview_document(&state, &headers, &params, &document).await?,
    ))
}

/// The MEF id of an object entry (`id`; a Meadow export writes the record id there).
fn entry_id(entry: &Value) -> &str {
    entry
        .get("id")
        .and_then(Value::as_str)
        .map(str::trim)
        .unwrap_or_default()
}

/// Composition parents of a MEF document: child id -> parent id.
///
/// The same rule the import worker uses (`meadow/worker/imports.py`): a
/// `depends_on`/`rel_depends_on` reference whose child is part of this document.
fn document_parents(objects: &[Value], references: &[Value]) -> HashMap<String, String> {
    let known: std::collections::HashSet<&str> = objects.iter().map(entry_id).collect();
    let mut parents: HashMap<String, String> = HashMap::new();
    for reference in references {
        let definition = reference
            .get("type")
            .and_then(Value::as_str)
            .unwrap_or_default();
        if !matches!(definition, "depends_on" | "rel_depends_on") {
            continue;
        }
        let (Some(child), Some(parent)) = (
            reference.get("in").and_then(Value::as_str),
            reference.get("out").and_then(Value::as_str),
        ) else {
            continue;
        };
        if child.is_empty() || parent.is_empty() || !known.contains(child) {
            continue;
        }
        parents
            .entry(child.to_owned())
            .or_insert_with(|| parent.to_owned());
    }
    parents
}

/// The plan behind [`preview_import`]: per-object verdicts plus the document's counts.
async fn preview_document(
    state: &AppState,
    headers: &HeaderMap,
    params: &PreviewImportParams,
    document: &Value,
) -> Result<Value, ApiError> {
    let objects = document
        .get("objects")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let references = document
        .get("references")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let source = params
        .source
        .as_deref()
        .map(str::trim)
        .filter(|label| !label.is_empty())
        .map(str::to_owned)
        .or_else(|| {
            document
                .get("source")
                .and_then(Value::as_str)
                .map(str::trim)
                .filter(|label| !label.is_empty())
                .map(str::to_owned)
        });
    let parents = document_parents(&objects, &references);
    let types: HashMap<&str, &str> = objects
        .iter()
        .map(|entry| {
            (
                entry_id(entry),
                entry
                    .get("type")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .trim(),
            )
        })
        .collect();

    // Parents that are **not** part of the document have to exist already: one query
    // for the whole document (the write would resolve them the same way).
    let label = source
        .clone()
        .unwrap_or_else(|| crate::api::objects::SOURCE_FALLBACK.to_owned());
    let mut wanted: Vec<String> = parents
        .values()
        .filter(|parent| !types.contains_key(parent.as_str()))
        .cloned()
        .collect();
    wanted.sort_unstable();
    wanted.dedup();
    let mut resolved: HashMap<String, String> = HashMap::new();
    if !wanted.is_empty() {
        let mut response = state
            .db
            .inner()
            .query(
                "SELECT type::string(id) AS id, source_id FROM object \
                 WHERE source = $source AND source_id IN $ids AND deleted_at IS NONE;",
            )
            .bind(("source", label))
            .bind(("ids", wanted))
            .await?
            .check()?;
        let rows: Vec<Value> = response.take(0)?;
        for row in rows {
            if let (Some(id), Some(source_id)) = (
                row.get("id").and_then(Value::as_str),
                row.get("source_id").and_then(Value::as_str),
            ) {
                resolved.insert(source_id.to_owned(), id.to_owned());
            }
        }
    }

    // `parent` is handed to the plan only where it resolves to a record id **now** —
    // then existence and type restriction are checked by the same code the write
    // uses. A parent inside the document has no record id yet; its verdict is
    // computed below.
    let mut items: Vec<BatchUpsertItem> = Vec::with_capacity(objects.len());
    let mut verdicts: Vec<&'static str> = Vec::with_capacity(objects.len());
    for entry in &objects {
        let id = entry_id(entry);
        let (parent, verdict) = match parents.get(id) {
            None => (None, "root"),
            Some(parent_id) if types.contains_key(parent_id.as_str()) => (None, "document"),
            Some(parent_id) => match resolved.get(parent_id) {
                Some(record_id) => (Some(record_id.clone()), "existing"),
                None => (None, "missing"),
            },
        };
        verdicts.push(verdict);
        items.push(BatchUpsertItem {
            object_type: types.get(id).copied().unwrap_or_default().to_owned(),
            attributes: entry
                .get("attributes")
                .cloned()
                .unwrap_or_else(|| json!({})),
            status: entry
                .get("status")
                .and_then(Value::as_str)
                .map(str::to_owned),
            source: source.clone(),
            source_id: (!id.is_empty()).then(|| id.to_owned()),
            parent,
        });
    }

    let request = BatchUpsertRequest {
        match_by: Some(
            if params.match_by_id.unwrap_or(false) {
                "id"
            } else {
                "source"
            }
            .to_owned(),
        ),
        key: params.match_by_key.clone(),
        objects: items,
    };
    let query = WriteObjectQuery {
        locale: None,
        emit_events: Some(false),
    };
    let plan = crate::api::objects::upsert_objects(state, headers, &query, &request, true).await?;

    // The import job's composition rule on top of the plan: a child whose parent is
    // neither in the document nor in the system is **not** created, and a
    // document-internal parent must satisfy the composition type restriction.
    let composition = crate::api::references::fetch_relation_definition(
        &state.db,
        crate::api::references::COMPOSITION_DEFINITION,
    )
    .await?;
    let mut results = plan.results;
    let (mut created, updated, mut failed) = (plan.created, plan.updated, plan.failed);
    for (index, verdict) in verdicts.iter().enumerate() {
        if results[index].action != "created" {
            continue;
        }
        let id = entry_id(&objects[index]);
        let error = match *verdict {
            "missing" => Some(format!(
                "composition parent `{}` is neither written in this document nor present in the system",
                parents.get(id).map(String::as_str).unwrap_or_default()
            )),
            "document" => {
                let parent_type = parents
                    .get(id)
                    .and_then(|parent| types.get(parent.as_str()))
                    .copied()
                    .unwrap_or_default();
                composition
                    .as_ref()
                    .and_then(|definition| {
                        crate::api::references::check_type_restriction(
                            definition,
                            types.get(id).copied().unwrap_or_default(),
                            parent_type,
                        )
                        .err()
                    })
                    .map(|error| error.message().to_owned())
            }
            _ => None,
        };
        if let Some(message) = error {
            created -= 1;
            failed += 1;
            results[index].action = "failed".to_owned();
            results[index].error = Some(message);
        }
    }

    // Object types the import would register — the preview itself does not.
    let mut new_types: Vec<&str> = Vec::new();
    for object_type in types.values().copied() {
        if object_type.is_empty()
            || new_types.contains(&object_type)
            || crate::api::object_types::fetch_type(&state.db, object_type)
                .await?
                .is_some()
        {
            continue;
        }
        new_types.push(object_type);
    }

    let limit = params
        .limit
        .unwrap_or(PREVIEW_DEFAULT_ROWS)
        .min(PREVIEW_MAX_ROWS) as usize;
    let rows: Vec<Value> = results
        .iter()
        .take(limit)
        .map(|result| {
            let id = entry_id(&objects[result.index]);
            json!({
                "index": result.index,
                "action": result.action,
                "id": result.id,
                "type": types.get(id).copied().unwrap_or_default(),
                "parent": verdicts[result.index],
                "parent_id": parents.get(id),
                "error": result.error,
            })
        })
        .collect();

    Ok(json!({
        "source": source,
        "objects": {
            "total": objects.len(),
            "create": created,
            "update": updated,
            "failed": failed,
        },
        "results": rows,
        "results_truncated": results.len() > limit,
        "new_object_types": new_types,
        "references": { "total": references.len(), "resolved": false },
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn object(id: &str, object_type: &str) -> Value {
        json!({ "id": id, "type": object_type, "attributes": {} })
    }

    fn reference(definition: &str, child: &str, parent: &str) -> Value {
        json!({ "type": definition, "in": child, "out": parent })
    }

    #[test]
    fn document_parents_reads_only_composition_edges_of_the_document() {
        let objects = vec![object("a", "product"), object("b", "product")];
        let references = vec![
            reference("depends_on", "b", "a"),
            // A child that is not part of the document is not a parent of it.
            reference("depends_on", "outside", "a"),
            // Not a composition edge.
            reference("rel_category_member", "b", "category:1"),
        ];
        let parents = document_parents(&objects, &references);
        assert_eq!(parents.get("b").map(String::as_str), Some("a"));
        assert_eq!(parents.len(), 1);
    }

    #[test]
    fn the_first_parent_edge_wins_like_the_import_job() {
        let objects = vec![
            object("a", "product"),
            object("b", "product"),
            object("c", "product"),
        ];
        let references = vec![
            reference("depends_on", "c", "a"),
            reference("depends_on", "c", "b"),
        ];
        let parents = document_parents(&objects, &references);
        assert_eq!(parents.get("c").map(String::as_str), Some("a"));
    }

    #[test]
    fn an_entry_without_id_or_type_is_read_as_empty() {
        assert_eq!(entry_id(&json!({ "type": "product" })), "");
        assert_eq!(entry_id(&json!({ "id": "  ", "type": "product" })), "");
        assert_eq!(entry_id(&json!({ "id": "  a  ", "type": "product" })), "a");
    }
}
