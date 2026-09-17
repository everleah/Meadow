//! Object CRUD handlers.
//!
//! Every object is part of the composition tree: creating an object also
//! creates its `depends_on` edge to the universal root.

use std::collections::{HashMap, HashSet};
use std::sync::Arc;

use axum::Json;
use axum::extract::{Path, Query, State};
use axum::http::{HeaderMap, StatusCode};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, json};
use ulid::Ulid;
use utoipa::{IntoParams, ToSchema};

use crate::api::keys;
use crate::app::AppState;
use crate::audit::{self, AuditEntry};
use crate::auth::AuthUser;
use crate::db::Db;
use crate::error::ApiError;

const TABLE: &str = "object";

/// Payload for creating an object.
#[derive(Debug, Deserialize, ToSchema)]
pub struct CreateObject {
    /// Object type (business role), e.g. `product`, `category`, `asset`.
    #[serde(rename = "type")]
    pub object_type: String,
    /// Flexible attribute values as JSON.
    #[serde(default = "default_attributes")]
    pub attributes: Value,
    /// Workflow status.
    #[serde(default = "default_status")]
    pub status: String,
    /// External id within a source system (MEF object `id`), for source-reference dedup.
    #[serde(default)]
    pub source_id: Option<String>,
    /// Source-system label (MEF document `source`).
    #[serde(default)]
    pub source: Option<String>,
    /// **Composition parent** of the new object (`object:<key>`); default is the
    /// universal root. The parent must exist, and the composition definition's
    /// type restrictions apply. A move of an existing object is
    /// `PATCH /objects/{id}/parent`, not a create.
    #[serde(default)]
    pub parent: Option<String>,
}

fn default_status() -> String {
    "draft".to_owned()
}

fn default_attributes() -> Value {
    Value::Object(serde_json::Map::new())
}

/// Payload for a partial object update.
#[derive(Debug, Deserialize, ToSchema)]
pub struct UpdateObject {
    /// Replace the whole `attributes` object.
    #[serde(default)]
    pub attributes: Option<Value>,
    /// Change the workflow status.
    #[serde(default)]
    pub status: Option<String>,
    /// Attribute names whose keys are **removed** from the object, so the value
    /// is inherited again ("back to inherited"). Applied after `attributes`.
    #[serde(default)]
    pub remove_attributes: Vec<String>,
}

/// Query parameters for reading a single object.
#[derive(Debug, Deserialize, ToSchema, IntoParams)]
#[into_params(parameter_in = Query)]
pub struct GetObjectQuery {
    /// Resolve multilingual attributes to this language code (e.g. `en-US`).
    pub locale: Option<String>,
    /// Whether to resolve inherited attribute values along `depends_on`.
    #[serde(default)]
    pub resolve_inheritance: Option<bool>,
}

/// Query parameters for object writes: locale for rule evaluation.
#[derive(Debug, Clone, Deserialize, ToSchema, IntoParams)]
#[into_params(parameter_in = Query)]
pub struct WriteObjectQuery {
    /// Language code used to resolve multilingual + inherited values for rules.
    pub locale: Option<String>,
    /// When `false`, no `object.created`/`object.updated` event is written to the
    /// outbox (bulk import with `emit_events: false`).
    #[serde(default)]
    pub emit_events: Option<bool>,
}

/// Query parameters for listing objects.
#[derive(Debug, Deserialize, ToSchema, IntoParams)]
#[into_params(parameter_in = Query)]
pub struct ListObjects {
    /// Filter by object type.
    #[serde(rename = "type")]
    #[param(rename = "type")]
    pub object_type: Option<String>,
    /// Filter to the composition subtree of this object (server-side lazy tree filter).
    #[serde(default)]
    pub ancestor: Option<String>,
    /// Filter to objects assigned (via a category relation) to this category or
    /// any of its composition descendants (category tree in the grid). By
    /// default the built-in primary category type is used; `definition_id`
    /// selects another relation type with the usage `category`.
    #[serde(default)]
    pub category: Option<String>,
    /// Relation type used by `category`/`uncategorized` (must be a category
    /// relation); empty = the built-in primary category type.
    #[serde(default)]
    pub definition_id: Option<String>,
    /// Filter to objects that are members of this materialized collection
    /// (via `collection_member`; flat, no hierarchy).
    #[serde(default)]
    pub collection: Option<String>,
    /// Only return objects that are **released for this channel**: they carry a
    /// `release` row for the channel code (or `channel:<code>`) whose state
    /// matches `release_status` (default `published`).
    #[serde(default)]
    pub released_for: Option<String>,
    /// State a channel release must have for `released_for` (default `published`).
    #[serde(default)]
    pub release_status: Option<String>,
    /// Attach the channel releases of every object as
    /// `releases: [{channel, status}]` (used by the grid's release column).
    #[serde(default)]
    pub releases: bool,
    /// Comma-separated object ids (bare keys or full `object:<key>`) to
    /// return; used for grid selections and saved-search results.
    #[serde(default)]
    pub ids: Option<String>,
    /// **Advanced search filter** (the grid's condition dialog): a Query DSL
    /// document as JSON (`{"where": {…}, "references": […], "fulltext": {…}}`),
    /// validated against the search field catalog (`GET /search-fields`) and
    /// rendered by the same filter renderer as saved searches.
    #[serde(default, rename = "where")]
    #[param(rename = "where")]
    pub where_: Option<String>,
    /// Only return objects that are NOT assigned to any category (no reference
    /// of the category relation as member, see `definition_id`); the
    /// "Nicht kategorisiert" entry.
    #[serde(default)]
    pub uncategorized: bool,
    /// Only return objects whose completeness for `locale` is **below** this
    /// percentage, e.g. `?completeness_lt=100` = only incomplete objects.
    #[serde(default)]
    pub completeness_lt: Option<i64>,
    /// Only return objects that are missing at least one required attribute for
    /// `locale` (the grid's "nur unvollständige" filter).
    #[serde(default)]
    pub missing_required: bool,
    /// Working language code (e.g. `en-US`); when set, each object gets a
    /// `completeness` value for this language.
    pub locale: Option<String>,
    /// Fill inheritable attributes that are missing locally from the nearest
    /// ancestor and report their origin in an `inherited` field per object
    /// (`?resolve_inheritance=true`, one edge query per hierarchy level).
    #[serde(default)]
    pub resolve_inheritance: Option<bool>,
    /// Maximum number of results (capped at 1000).
    #[serde(default = "default_limit")]
    pub limit: u32,
    /// Number of results to skip.
    #[serde(default)]
    pub start: u32,
}

fn default_limit() -> u32 {
    100
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

/// Splits an `object:<key>` id and verifies the table (shared with the
/// channel-release endpoints).
pub(crate) fn parse_object_key(id: &str) -> Result<&str, ApiError> {
    parse_key(id, TABLE)
}

/// The object type of a **live** object, or `None` when it does not exist.
async fn live_object_type(db: &Db, key: &str) -> Result<Option<String>, ApiError> {
    let mut response = db
        .inner()
        .query("SELECT type FROM type::record('object', $key) WHERE deleted_at IS NONE;")
        .bind(("key", key.to_owned()))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    Ok(rows
        .first()
        .and_then(|row| row.get("type"))
        .and_then(Value::as_str)
        .map(str::to_owned))
}

/// The object types of many **live** objects in one query (`key -> type`); keys
/// that do not exist are absent. Used by the batch upsert, which must not run one
/// lookup per entry.
async fn live_object_types(db: &Db, keys: &[String]) -> Result<HashMap<String, String>, ApiError> {
    let mut wanted: Vec<&str> = keys.iter().map(String::as_str).collect();
    wanted.sort_unstable();
    wanted.dedup();
    let mut types = HashMap::with_capacity(wanted.len());
    if wanted.is_empty() {
        return Ok(types);
    }
    let literal = crate::api::keys::record_list_literal(TABLE, wanted.iter().copied());
    let mut response = db
        .inner()
        .query(format!(
            "SELECT type::string(id) AS id, type FROM object WHERE id IN {literal} AND deleted_at IS NONE;"
        ))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    for row in rows {
        if let (Some(id), Some(object_type)) = (
            row.get("id").and_then(Value::as_str),
            row.get("type").and_then(Value::as_str),
        ) {
            let key = id.split_once(':').map(|(_, key)| key).unwrap_or(id);
            types.insert(key.to_owned(), object_type.to_owned());
        }
    }
    Ok(types)
}

/// Validates a `parent` value against the composition definition.
///
/// `parent` addresses the composition parent as `object:<key>` (a bare key is
/// accepted). `None`/empty means the universal root. The type restriction of the
/// composition definition applies (`check_type_restriction`).
async fn check_composition_parent_type(
    state: &AppState,
    child_type: &str,
    parent_type: &str,
) -> Result<(), ApiError> {
    let definition = crate::api::references::fetch_relation_definition(
        &state.db,
        crate::api::references::COMPOSITION_DEFINITION,
    )
    .await?
    .ok_or_else(|| ApiError::internal("composition definition is missing"))?;
    crate::api::references::check_type_restriction(&definition, child_type, parent_type)
}

/// The composition parent's bare key of a **new** object, or `None` for the
/// universal root.
///
/// A parent that does not exist — or whose type the composition definition does
/// not allow — rejects the create. Rationale (measured 2026-09-17): creating the
/// object anyway would park it on `object:object_root`, i.e. in the wrong place in
/// the structure tree, and the caller would only see a failed reference
/// afterwards (the import reported `success` while the object had the wrong
/// parent). Object writes must therefore never create an object whose intended
/// parent is missing.
async fn validate_composition_parent(
    state: &AppState,
    parent: Option<&str>,
    child_type: &str,
) -> Result<Option<String>, ApiError> {
    let Some(raw) = parent.map(str::trim).filter(|value| !value.is_empty()) else {
        return Ok(None);
    };
    let key = raw.strip_prefix("object:").unwrap_or(raw);
    if key.is_empty() {
        return Err(ApiError::bad_request("`parent` must not be empty"));
    }
    let Some(parent_type) = live_object_type(&state.db, key).await? else {
        return Err(ApiError::bad_request(format!(
            "parent object `{raw}` not found"
        )));
    };
    check_composition_parent_type(state, child_type, &parent_type).await?;
    Ok(Some(key.to_owned()))
}

/// Creates a new object with a `depends_on` edge to the universal root.
#[utoipa::path(
    post,
    path = "/objects",
    request_body = CreateObject,
    params(WriteObjectQuery),
    responses(
        (status = 201, description = "Created object", body = Value),
        (status = 400, description = "Invalid payload", body = Value),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Forbidden", body = Value),
        (status = 500, description = "Internal error", body = Value)
    )
)]
pub async fn create_object(
    State(state): State<AppState>,
    headers: HeaderMap,
    Query(params): Query<WriteObjectQuery>,
    Json(body): Json<CreateObject>,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    if body.object_type.trim().is_empty() {
        return Err(ApiError::bad_request("`type` must not be empty"));
    }
    let CreateObject {
        object_type,
        attributes,
        status,
        source_id,
        source,
        parent,
    } = body;
    let object_type = object_type.trim().to_owned();
    let started = std::time::Instant::now();
    let mut last;
    let mut phases: Vec<(&'static str, f64)> = Vec::new();
    let user_id = crate::auth::api_key_or_role(&headers, &state.config, crate::auth::ROLE_EDITOR)?;
    let locale = params
        .locale
        .as_deref()
        .map(str::trim)
        .filter(|l| !l.is_empty());
    let emit_events = params.emit_events.unwrap_or(true);
    // The composition parent is resolved **before** anything is written: an object
    // whose intended parent does not exist must not be created at all (it would
    // otherwise live on the universal root, in the wrong place in the tree, and
    // the caller would only see a failed reference afterwards — measured
    // 2026-09-17). `None` = the universal root.
    let parent_key = validate_composition_parent(&state, parent.as_deref(), &object_type).await?;

    let Value::Object(mut attributes) = attributes else {
        return Err(ApiError::bad_request("`attributes` must be an object"));
    };
    // Keep the object-type registry complete: an object of a not-yet-registered
    // type auto-registers it (label defaults to the type code).
    crate::api::object_types::ensure_registered(&state.db, &object_type).await?;
    // The uniqueness check and the write below are one critical section per key:
    // without it two parallel writers both see "no conflict" and leave two live
    // rows with the same business key (`crate::key_lock`).
    let key_names = crate::api::object_types::key_attributes_for(&state.db, &object_type).await?;
    phases.push(("registry", started.elapsed().as_secs_f64() * 1000.0));
    last = std::time::Instant::now();
    let _key_guards = state
        .key_locks
        .lock_keys(&keys::key_lock_names(&object_type, &key_names, &attributes))
        .await;
    keys::normalize_key_attributes(&state.db, &object_type, &key_names, &mut attributes, None)
        .await?;
    // Data-type rules (`url` values must be openable addresses).
    crate::attribute_values::validate_attribute_values(&state.cache, &state.db, &attributes)
        .await?;
    phases.push(("prepare", last.elapsed().as_secs_f64() * 1000.0));
    last = std::time::Instant::now();

    // Resolved view for rule evaluation. A new object has no inherited values
    // yet; multilingual own values are resolved to `locale`.
    let mut eval_attributes = attributes.clone();
    if let Some(locale) = locale {
        crate::language::resolve_attributes(&state.cache, &state.db, locale, &mut eval_attributes)
            .await?;
    }

    // Evaluate active validation/computed rules against the resolved view.
    let candidate = json!({
        "id": "",
        "type": object_type.clone(),
        "status": status.clone(),
        "attributes": Value::Object(eval_attributes),
    });
    let outcome = crate::rules::evaluate_for_write(
        &state.cache,
        &state.db,
        &state.rules,
        &candidate,
        crate::rules::WriteEvaluation::new(
            &["validation", "computed"],
            "save",
            locale,
            Some(&user_id),
        ),
    )
    .await?;
    crate::rules::enforce(&outcome)?;
    let computed = crate::rules::resolve_computed(&state.db, &outcome.computed_object()).await?;
    for (name, value) in computed {
        attributes.insert(name, value);
    }
    phases.push(("rules", last.elapsed().as_secs_f64() * 1000.0));
    last = std::time::Instant::now();

    let mut content = serde_json::Map::new();
    content.insert("type".to_owned(), Value::String(object_type.clone()));
    content.insert("attributes".to_owned(), Value::Object(attributes));
    content.insert("status".to_owned(), Value::String(status));
    content.insert("version".to_owned(), json!(1));
    content.insert("updated_by".to_owned(), Value::String(user_id.clone()));
    if let Some(source_id) = source_id {
        content.insert("source_id".to_owned(), Value::String(source_id));
    }
    if let Some(source) = source {
        content.insert("source".to_owned(), Value::String(source));
    }
    let changes = json!({
        "attributes": content
            .get("attributes")
            .cloned()
            .unwrap_or_else(|| json!({})),
    });

    // Statements: BEGIN(0), LET $obj(1), LET $parent(2), CREATE(3), RELATE(4),
    // [CREATE outbox(5)], SELECT(5|6), COMMIT(6|7). The parent is bound in the
    // same transaction: either the requested parent or the universal root, so the
    // edge is the object's **first** and only composition edge (no root edge that
    // a later re-parent has to replace).
    let parent_let = match parent_key {
        Some(_) => "LET $parent = type::record('object', $parent_key);",
        None => "LET $parent = object:object_root;",
    };
    let sql = if emit_events {
        format!(
            "
        BEGIN TRANSACTION;
        LET $obj = type::record('object', rand::ulid());
        {parent_let}
        CREATE $obj CONTENT $content;
        RELATE $obj->reference->$parent SET definition_id = 'rel_depends_on', type = 'depends_on', attributes = {{}};
        CREATE outbox_event SET event_type = 'object.created', object_type = $object_type, object_id = type::string($obj.id), changes = $changes, metadata = $metadata, status = 'pending';
        SELECT * FROM $obj;
        COMMIT TRANSACTION;
        "
        )
    } else {
        format!(
            "
        BEGIN TRANSACTION;
        LET $obj = type::record('object', rand::ulid());
        {parent_let}
        CREATE $obj CONTENT $content;
        RELATE $obj->reference->$parent SET definition_id = 'rel_depends_on', type = 'depends_on', attributes = {{}};
        SELECT * FROM $obj;
        COMMIT TRANSACTION;
        "
        )
    };
    let mut response = state
        .db
        .inner()
        .query(sql)
        .bind(("content", Value::Object(content)))
        .bind(("object_type", object_type.clone()))
        .bind(("changes", changes))
        .bind(("metadata", json!({ "user_id": user_id.clone() })))
        .bind(("parent_key", parent_key.clone().unwrap_or_default()))
        .await?
        .check()?;
    let select_index = if emit_events { 6 } else { 5 };
    let objects: Vec<Value> = response.take(select_index)?;
    let object = objects
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::internal("created object was not returned"))?;
    let object_id = object
        .get("id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    phases.push(("write", last.elapsed().as_secs_f64() * 1000.0));
    tracing::debug!(
        total_ms = started.elapsed().as_secs_f64() * 1000.0,
        ?phases,
        object_type = %object_type,
        emit_events,
        "object create phases"
    );
    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &user_id,
            action: "object_created",
            object_id: Some(&object_id),
            from: None,
            to: None,
            metadata: None,
        },
    )
    .await;
    state.cache.invalidate_stats();
    Ok((StatusCode::CREATED, Json(object)))
}

/// Deletes an object (soft delete) with cascade over its composition subtree.
///
/// `DELETE /objects/{id}` marks the object and every `depends_on` descendant
/// as deleted (`deleted_at`), removes all reference edges (composition and
/// aggregation) and the channel releases that touch a deleted object so the
/// graph and the release catalog stay clean, and writes an `object.deleted`
/// outbox event. The universal root cannot be
/// deleted. Aggregation edges never cascade — they are removed, but the
/// connected objects stay intact.
#[utoipa::path(
    delete,
    path = "/objects/{id}",
    params(WriteObjectQuery),
    responses(
        (status = 204, description = "Object deleted"),
        (status = 400, description = "Invalid id or undeletable object", body = Value),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Forbidden", body = Value),
        (status = 404, description = "Object not found", body = Value)
    )
)]
pub async fn delete_object(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(id): Path<String>,
    Query(params): Query<WriteObjectQuery>,
) -> Result<StatusCode, ApiError> {
    let user_id = crate::auth::api_key_or_role(&headers, &state.config, crate::auth::ROLE_EDITOR)?;
    let key = parse_key(&id, TABLE)?;
    if key == "object_root" {
        return Err(ApiError::bad_request(
            "the universal root cannot be deleted",
        ));
    }
    let emit_events = params.emit_events.unwrap_or(true);

    // Ensure the object exists and is not already soft-deleted.
    let mut existing = state
        .db
        .inner()
        .query("SELECT * FROM type::record('object', $key) WHERE deleted_at IS NONE;")
        .bind(("key", key))
        .await?
        .check()?;
    let existing_rows: Vec<Value> = existing.take(0)?;
    let object_type = existing_rows
        .first()
        .and_then(|row| row.get("type"))
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_owned();
    if existing_rows.is_empty() {
        return Err(ApiError::not_found(format!("object `{id}` not found")));
    }

    // Cascade set: the object itself and every `depends_on` descendant (bare keys).
    let cascade_keys = crate::api::references::subtree_keys(&state.db, &id).await?;
    let object_ids: Vec<String> = cascade_keys.iter().map(|k| format!("object:{k}")).collect();

    // Statements: BEGIN(0), LET(1), UPDATE(2), DELETE refs(3), DELETE releases(4),
    // DELETE class features(5), [CREATE outbox(6)], COMMIT(6|7).
    //
    // Releases and class features are deleted per record (`FOR … WHERE object_id
    // = $r`) because `object_id IN $recs` does not match the typed
    // `record<object>` field.
    let sql = if emit_events {
        "
        BEGIN TRANSACTION;
        LET $recs = array::map($keys, |$k| type::record('object', $k));
        UPDATE object SET deleted_at = time::now(), deleted_by = $user_id, updated_at = time::now() WHERE id IN $recs;
        DELETE reference WHERE in IN $recs OR out IN $recs;
        FOR $r IN $recs { DELETE release WHERE object_id = $r; };
        FOR $r IN $recs { DELETE category_attribute WHERE category_id = $r; };
        CREATE outbox_event CONTENT $outbox;
        COMMIT TRANSACTION;
        "
        .to_owned()
    } else {
        "
        BEGIN TRANSACTION;
        LET $recs = array::map($keys, |$k| type::record('object', $k));
        UPDATE object SET deleted_at = time::now(), deleted_by = $user_id, updated_at = time::now() WHERE id IN $recs;
        DELETE reference WHERE in IN $recs OR out IN $recs;
        FOR $r IN $recs { DELETE release WHERE object_id = $r; };
        FOR $r IN $recs { DELETE category_attribute WHERE category_id = $r; };
        COMMIT TRANSACTION;
        "
        .to_owned()
    };
    let outbox = crate::event::content(&crate::event::OutboxEvent {
        event_type: crate::event::EVENT_OBJECT_DELETED,
        object_type,
        object_id: id.clone(),
        changes: json!({ "cascade": cascade_keys.len(), "object_ids": object_ids }),
        metadata: json!({ "user_id": user_id.clone() }),
    });
    state
        .db
        .inner()
        .query(sql)
        .bind(("keys", cascade_keys))
        .bind(("user_id", user_id.clone()))
        .bind(("outbox", outbox))
        .await?
        .check()?;

    // A deleted subtree (often a whole family) changes the counts and completeness.
    state.cache.invalidate_stats();
    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &user_id,
            action: "object_deleted",
            object_id: Some(&id),
            from: None,
            to: Some(json!({ "cascade": object_ids.len() })),
            metadata: None,
        },
    )
    .await;

    Ok(StatusCode::NO_CONTENT)
}

/// Maximum number of ids per batch delete call.
const MAX_BATCH_DELETE_ROWS: usize = 500;

/// Payload for `POST /objects/batch-delete`.
#[derive(Debug, Deserialize, ToSchema)]
pub struct BatchDeleteRequest {
    /// Object record ids (`object:<key>`) or bare keys; duplicates are ignored.
    pub ids: Vec<String>,
}

/// Result of `POST /objects/batch-delete`.
#[derive(Debug, Serialize, ToSchema)]
pub struct BatchDeleteResult {
    /// Root objects that were deleted (one per existing input id).
    pub deleted: usize,
    /// Additional objects the composition cascade removed below them.
    pub cascaded: usize,
    /// Ids that do not exist (deleted already, or removed by a cascade) — not an
    /// error, so a cleanup can pass a stale list.
    pub missing: Vec<String>,
}

/// Normalises and validates the ids of a batch delete (duplicates dropped).
fn normalize_delete_ids(ids: &[String]) -> Result<Vec<String>, ApiError> {
    if ids.is_empty() {
        return Err(ApiError::bad_request("`ids` must not be empty"));
    }
    if ids.len() > MAX_BATCH_DELETE_ROWS {
        return Err(ApiError::payload_too_large(format!(
            "batch exceeds the limit of {MAX_BATCH_DELETE_ROWS} ids"
        )));
    }
    let mut seen: HashSet<String> = HashSet::new();
    let mut keys: Vec<String> = Vec::with_capacity(ids.len());
    for id in ids {
        let key = id
            .split_once(':')
            .map(|(_, key)| key)
            .unwrap_or(id.as_str())
            .trim();
        if key.is_empty()
            || !key
                .chars()
                .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-')
        {
            return Err(ApiError::bad_request(format!("invalid object id `{id}`")));
        }
        if key == "object_root" {
            return Err(ApiError::bad_request(
                "the universal root cannot be deleted",
            ));
        }
        if seen.insert(key.to_owned()) {
            keys.push(key.to_owned());
        }
    }
    Ok(keys)
}

/// Deletes many objects in **one** transaction (cascade included).
///
/// `DELETE /objects/{id}` costs ~0,4 s for a leaf and ~1–3 s for a subtree: the
/// statements themselves run in 80–400 µs, the time sits in the per-request
/// write path (transaction, audit, outbox, HTTP). A cleanup of 3 000 objects is
/// therefore dominated by call overhead, not by the deletion
/// (`plan/detail/demodaten-generator.md` §4.3/§9). This endpoint amortises that:
/// one request, one transaction, **one** composition-edge query for all
/// subtrees, one audit row — and one outbox event per deleted root, so event
/// consumers see exactly the same messages as before.
///
/// Unknown ids are reported in `missing` instead of failing the call: a cleanup
/// passes a list it fetched itself, and a parent's cascade may have removed a
/// child in the meantime.
#[utoipa::path(
    post,
    path = "/objects/batch-delete",
    request_body = BatchDeleteRequest,
    params(WriteObjectQuery),
    responses(
        (status = 200, description = "Deleted", body = BatchDeleteResult),
        (status = 400, description = "Invalid id or undeletable object", body = Value),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Forbidden", body = Value),
        (status = 413, description = "Too many ids", body = Value)
    )
)]
pub async fn batch_delete_objects(
    State(state): State<AppState>,
    headers: HeaderMap,
    Query(params): Query<WriteObjectQuery>,
    Json(body): Json<BatchDeleteRequest>,
) -> Result<Json<BatchDeleteResult>, ApiError> {
    let user_id = crate::auth::api_key_or_role(&headers, &state.config, crate::auth::ROLE_EDITOR)?;
    let keys = normalize_delete_ids(&body.ids)?;
    let emit_events = params.emit_events.unwrap_or(true);

    // Which of the requested objects exist? One index-backed id-list query (the
    // literal list, never `array::map` — that form is per-row and quadratic).
    let literal = crate::api::keys::record_list_literal(TABLE, keys.iter().map(String::as_str));
    let mut response = state
        .db
        .inner()
        .query(format!(
            "SELECT type::string(id) AS id FROM object WHERE id IN {literal} AND deleted_at IS NONE;"
        ))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    let existing: Vec<String> = rows
        .iter()
        .filter_map(|row| row.get("id").and_then(Value::as_str))
        .map(|id| {
            id.rsplit_once(':')
                .map(|(_, key)| key)
                .unwrap_or(id)
                .to_owned()
        })
        .collect();
    let missing: Vec<String> = keys
        .iter()
        .filter(|key| !existing.iter().any(|found| found == *key))
        .map(|key| format!("object:{key}"))
        .collect();
    if existing.is_empty() {
        return Ok(Json(BatchDeleteResult {
            deleted: 0,
            cascaded: 0,
            missing,
        }));
    }

    // Cascade set: the requested objects plus every `depends_on` descendant,
    // computed from **one** edge query instead of one traversal per root.
    let children = crate::api::references::composition_children(&state.db).await?;
    let mut seen: HashSet<String> = HashSet::new();
    let mut cascade_keys: Vec<String> = Vec::new();
    for key in &existing {
        for member in crate::api::references::subtree_keys_in(&children, &format!("object:{key}")) {
            if seen.insert(member.clone()) {
                cascade_keys.push(member);
            }
        }
    }
    let cascaded = cascade_keys.len() - existing.len();

    // Statements: BEGIN(0), LET(1), UPDATE(2), DELETE refs(3), DELETE releases(4),
    // DELETE class features(5), [CREATE outbox(6)], COMMIT(6|7).
    let sql = if emit_events {
        "
        BEGIN TRANSACTION;
        LET $recs = array::map($keys, |$k| type::record('object', $k));
        UPDATE object SET deleted_at = time::now(), deleted_by = $user_id, updated_at = time::now() WHERE id IN $recs;
        DELETE reference WHERE in IN $recs OR out IN $recs;
        FOR $r IN $recs { DELETE release WHERE object_id = $r; };
        FOR $r IN $recs { DELETE category_attribute WHERE category_id = $r; };
        FOR $event IN $events { CREATE outbox_event CONTENT $event; };
        COMMIT TRANSACTION;
        "
        .to_owned()
    } else {
        "
        BEGIN TRANSACTION;
        LET $recs = array::map($keys, |$k| type::record('object', $k));
        UPDATE object SET deleted_at = time::now(), deleted_by = $user_id, updated_at = time::now() WHERE id IN $recs;
        DELETE reference WHERE in IN $recs OR out IN $recs;
        FOR $r IN $recs { DELETE release WHERE object_id = $r; };
        FOR $r IN $recs { DELETE category_attribute WHERE category_id = $r; };
        COMMIT TRANSACTION;
        "
        .to_owned()
    };
    let events: Vec<Value> = if emit_events {
        let cascade = cascade_keys.len();
        existing
            .iter()
            .map(|key| {
                crate::event::content(&crate::event::OutboxEvent {
                    event_type: crate::event::EVENT_OBJECT_DELETED,
                    object_type: "object".to_owned(),
                    object_id: format!("object:{key}"),
                    changes: json!({ "cascade": cascade, "batch": existing.len() }),
                    metadata: json!({ "user_id": user_id.clone() }),
                })
            })
            .collect()
    } else {
        Vec::new()
    };
    state
        .db
        .inner()
        .query(sql)
        .bind(("keys", cascade_keys))
        .bind(("user_id", user_id.clone()))
        .bind(("events", events))
        .await?
        .check()?;

    state.cache.invalidate_stats();
    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &user_id,
            action: "objects_deleted",
            object_id: None,
            from: None,
            to: Some(json!({ "deleted": existing.len(), "cascaded": cascaded })),
            metadata: None,
        },
    )
    .await;

    Ok(Json(BatchDeleteResult {
        deleted: existing.len(),
        cascaded,
        missing,
    }))
}

/// Maximum number of objects in one `POST /objects/batch-upsert` call.
pub const MAX_BATCH_UPSERT_ROWS: usize = 500;

/// Additional match of a batch upsert (the business key is **always** tried first,
/// exactly like the single write path).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum UpsertMatch {
    /// `source` + `source_id` (the import case: a MEF document carries both).
    Source,
    /// Before the source reference, an entry whose `source_id` is an existing
    /// object record id updates that object (MEF roundtrip, `POST /imports?
    /// match_by_id=true`).
    Id,
}

impl UpsertMatch {
    fn parse(raw: Option<&str>) -> Result<Self, ApiError> {
        match raw.map(str::trim).unwrap_or("") {
            "" | "source" => Ok(Self::Source),
            "id" => Ok(Self::Id),
            other => Err(ApiError::bad_request(format!(
                "unknown `match_by` `{other}` (expected `source` or `id`)"
            ))),
        }
    }
}

/// One create-or-update entry of [`BatchUpsertRequest`].
#[derive(Debug, Deserialize, ToSchema)]
pub struct BatchUpsertItem {
    /// Object type (business role), e.g. `product`.
    #[serde(rename = "type")]
    pub object_type: String,
    /// Attribute values (the complete map, as a single write would carry it).
    #[serde(default)]
    pub attributes: Value,
    /// Workflow status (defaults to `draft`).
    #[serde(default)]
    pub status: Option<String>,
    /// Source system (with `source_id` it is the default match).
    #[serde(default)]
    pub source: Option<String>,
    /// Identifier in the source system.
    #[serde(default)]
    pub source_id: Option<String>,
    /// **Composition parent for a new object** (`object:<key>`, bare key accepted);
    /// default is the universal root. Ignored for entries that update an existing
    /// object (moving is `PATCH /objects/{id}/parent` or a `depends_on`
    /// reference). A parent that does not exist fails the entry — the object is
    /// then **not** created (2026-09-17: an object must never be parked on the
    /// root because its intended parent is missing).
    #[serde(default)]
    pub parent: Option<String>,
}

/// Payload of `POST /objects/batch-upsert`.
#[derive(Debug, Deserialize, ToSchema)]
pub struct BatchUpsertRequest {
    /// `source` (default) or `id`.
    #[serde(default)]
    pub match_by: Option<String>,
    /// Attribute used to match existing objects (e.g. `sku`). Default: the key
    /// attributes the object type declares. This mirrors `match_by_key` of
    /// `POST /imports`; the **uniqueness** check always uses the registered key
    /// attributes.
    #[serde(default)]
    pub key: Option<String>,
    pub objects: Vec<BatchUpsertItem>,
}

/// Per-entry outcome of a batch upsert.
#[derive(Debug, Serialize, ToSchema)]
pub struct BatchUpsertItemResult {
    /// Position in the request's `objects` array.
    pub index: usize,
    /// Record id of the created/updated object.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub id: Option<String>,
    /// `created`, `updated` or `failed`.
    pub action: String,
    /// Failure message (only for `failed`).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

/// Result of `POST /objects/batch-upsert`.
#[derive(Debug, Serialize, ToSchema)]
pub struct BatchUpsertResponse {
    pub created: usize,
    pub updated: usize,
    pub failed: usize,
    pub results: Vec<BatchUpsertItemResult>,
}

/// Creates or updates many objects with **one** lookup, **one** transaction and
/// **one** audit statement per batch.
///
/// A single object write costs ~4–5 database round trips (type registry, key
/// uniqueness, the write transaction, the audit row, the outbox row); at 16 k
/// objects an import therefore spends its time in round trips, not in the
/// database — measured 2026-09-16: each round trip that SurrealDB answers in
/// 300–500 ms under load multiplies into ~450 ms per object
/// (`plan/detail/demodaten-generator.md` §4.3/§5). This endpoint keeps the
/// semantics of the single endpoints and amortises the round trips:
///
/// - the object types of the batch are registered once,
/// - the business-key uniqueness is checked with **one** query per key attribute
///   (plus a check for duplicates *inside* the batch),
/// - all keys of the batch are locked as one critical section
///   (`crate::key_lock`), so the check-then-write window stays closed,
/// - creates and updates go into **one** transaction, root edges included,
/// - audit entries are written per object but with one statement.
///
/// Rules keep running per entry (`validation`/`computed` in `save` mode, plus
/// `workflow_guard` in `transition` mode when the status changes) and stay
/// fail-closed: an entry a rule rejects is reported as `failed`, the rest of the
/// batch is written. Channel releases and class features are deliberately *not*
/// part of this endpoint — they are separate calls with their own guard rules.
#[utoipa::path(
    post,
    path = "/objects/batch-upsert",
    request_body = BatchUpsertRequest,
    params(WriteObjectQuery),
    responses(
        (status = 200, description = "Per-entry results", body = BatchUpsertResponse),
        (status = 400, description = "Invalid payload", body = Value),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Forbidden", body = Value),
        (status = 413, description = "Too many rows", body = Value)
    )
)]
pub async fn batch_upsert_objects(
    State(state): State<AppState>,
    headers: HeaderMap,
    Query(params): Query<WriteObjectQuery>,
    Json(body): Json<BatchUpsertRequest>,
) -> Result<Json<BatchUpsertResponse>, ApiError> {
    let user_id = crate::auth::api_key_or_role(&headers, &state.config, crate::auth::ROLE_EDITOR)?;
    if body.objects.is_empty() {
        return Err(ApiError::bad_request("`objects` must not be empty"));
    }
    if body.objects.len() > MAX_BATCH_UPSERT_ROWS {
        return Err(ApiError::payload_too_large(format!(
            "batch exceeds the limit of {MAX_BATCH_UPSERT_ROWS} objects"
        )));
    }
    let match_mode = UpsertMatch::parse(body.match_by.as_deref())?;
    // The attribute the *matching* uses: the request's `key` when given (the
    // import job's `match_by_key`), else each type's declared key attributes.
    let explicit_key: Option<String> = body
        .key
        .as_deref()
        .map(str::trim)
        .filter(|key| !key.is_empty())
        .map(str::to_owned);
    let emit_events = params.emit_events.unwrap_or(true);
    let locale = params
        .locale
        .as_deref()
        .map(str::trim)
        .filter(|l| !l.is_empty());
    let started = std::time::Instant::now();

    // ---- per-entry preparation (no database access) -------------------------
    let mut results: Vec<BatchUpsertItemResult> = Vec::with_capacity(body.objects.len());
    let mut prepared: Vec<Option<PreparedBatchItem>> = Vec::with_capacity(body.objects.len());
    for item in &body.objects {
        let object_type = item.object_type.trim().to_owned();
        if object_type.is_empty() {
            results.push(failed_result(prepared.len(), "entry without `type`"));
            prepared.push(None);
            continue;
        }
        let Value::Object(attributes) = &item.attributes else {
            results.push(failed_result(
                prepared.len(),
                "`attributes` must be an object",
            ));
            prepared.push(None);
            continue;
        };
        let status = item
            .status
            .as_deref()
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .unwrap_or(default_status().as_str())
            .to_owned();
        prepared.push(Some(PreparedBatchItem {
            object_type,
            status,
            attributes: attributes.clone(),
            source: item.source.clone(),
            source_id: item.source_id.clone(),
            // `object:<key>` (a bare key is accepted); empty = universal root.
            parent: item
                .parent
                .as_deref()
                .map(str::trim)
                .filter(|value| !value.is_empty())
                .map(|value| value.strip_prefix("object:").unwrap_or(value).to_owned()),
        }));
        results.push(pending_result(prepared.len() - 1));
    }

    // Keep the type registry complete, and read the key attributes of every type
    // that appears — once per type, not once per object.
    let mut key_names: std::collections::HashMap<String, Vec<String>> =
        std::collections::HashMap::new();
    for item in prepared.iter().flatten() {
        if key_names.contains_key(&item.object_type) {
            continue;
        }
        crate::api::object_types::ensure_registered(&state.db, &item.object_type).await?;
        let names =
            crate::api::object_types::key_attributes_for(&state.db, &item.object_type).await?;
        key_names.insert(item.object_type.clone(), names);
    }

    // One critical section for the whole batch: the uniqueness check below and
    // the write must not be separated by another writer of the same key.
    let mut lock_names: Vec<String> = Vec::new();
    for item in prepared.iter().flatten() {
        let names = key_names
            .get(&item.object_type)
            .map(Vec::as_slice)
            .unwrap_or(&[]);
        lock_names.extend(crate::api::keys::key_lock_names(
            &item.object_type,
            names,
            &item.attributes,
        ));
    }
    let _key_guards = state.key_locks.lock_keys(&lock_names).await;

    // Trim the key values (identical semantics as the single write path).
    let mut key_values: Vec<Vec<(String, String)>> = Vec::with_capacity(prepared.len());
    for item in prepared.iter_mut() {
        match item {
            Some(entry) => {
                let names = key_names
                    .get(&entry.object_type)
                    .map(Vec::as_slice)
                    .unwrap_or(&[]);
                key_values.push(crate::api::keys::trim_key_values(
                    names,
                    &mut entry.attributes,
                ));
            }
            None => key_values.push(Vec::new()),
        }
    }

    // Values used to find an existing object: the trimmed key attributes plus the
    // explicit match key (the `key` values are also needed for the uniqueness
    // check below).
    let mut lookup_values: Vec<Vec<(String, String)>> = Vec::with_capacity(prepared.len());
    for (index, item) in prepared.iter().enumerate() {
        let mut values = key_values[index].clone();
        if let (Some(name), Some(entry)) = (explicit_key.as_ref(), item.as_ref())
            && let Some(text) = entry.attributes.get(name).and_then(Value::as_str)
        {
            let trimmed = text.trim();
            if !trimmed.is_empty() && !values.iter().any(|(n, _)| n == name) {
                values.push((name.clone(), trimmed.to_owned()));
            }
        }
        lookup_values.push(values);
    }

    // ---- lookups: existing objects and key values in use --------------------
    // Duplicates *inside* the batch would be a conflict the database cannot see.
    let mut in_batch: std::collections::HashSet<String> = std::collections::HashSet::new();
    let mut conflicts: Vec<Option<String>> = vec![None; prepared.len()];
    for (index, values) in key_values.iter().enumerate() {
        let Some(entry) = prepared.get(index).and_then(Option::as_ref) else {
            continue;
        };
        for (name, value) in values {
            if !in_batch.insert(format!("{}\u{1}{name}\u{1}{value}", entry.object_type)) {
                conflicts[index] = Some(format!(
                    "duplicate business key `{name}` inside the batch: `{value}` appears twice"
                ));
            }
        }
    }

    // Values that another live object of the same type already carries.
    let mut used: std::collections::HashMap<String, String> = std::collections::HashMap::new();
    let mut grouped: std::collections::HashMap<(String, String), Vec<String>> =
        std::collections::HashMap::new();
    for (index, values) in lookup_values.iter().enumerate() {
        let Some(entry) = prepared.get(index).and_then(Option::as_ref) else {
            continue;
        };
        for (name, value) in values {
            grouped
                .entry((entry.object_type.clone(), name.clone()))
                .or_default()
                .push(value.clone());
        }
    }
    for ((object_type, name), mut values) in grouped {
        values.sort_unstable();
        values.dedup();
        let in_use =
            crate::api::keys::key_values_in_use(&state.db, &object_type, &name, &values, None)
                .await?;
        for (value, key) in in_use {
            used.insert(format!("{object_type}\u{1}{name}\u{1}{value}"), key);
        }
    }

    // Which entries update an existing object?
    //
    // **Both** lookups always run and one is the fallback of the other: the single
    // write path (`_upsert_object` in the import worker) tries the business key
    // first and the source reference second, so an import must not create a second
    // object for a keyless MEF entry (or for one whose key was added later).
    let mut by_source_match: Vec<Option<String>> = vec![None; prepared.len()];
    let mut by_key_match: Vec<Option<String>> = vec![None; prepared.len()];
    let mut by_id_match: Vec<Option<String>> = vec![None; prepared.len()];
    {
        let mut by_source: std::collections::HashMap<String, Vec<(usize, String)>> =
            std::collections::HashMap::new();
        for (index, item) in prepared.iter().enumerate() {
            let Some(entry) = item.as_ref() else {
                continue;
            };
            let Some(source_id) = entry.source_id.clone() else {
                continue;
            };
            let label = entry
                .source
                .clone()
                .unwrap_or_else(|| SOURCE_FALLBACK.to_owned());
            by_source.entry(label).or_default().push((index, source_id));
        }
        for (source, entries) in by_source {
            let ids: Vec<String> = entries.iter().map(|(_, id)| id.clone()).collect();
            let mut response = state
                .db
                .inner()
                .query(
                    "SELECT type::string(id) AS id, status, source_id FROM object \
                     WHERE source = $source AND source_id IN $ids AND deleted_at IS NONE;",
                )
                .bind(("source", source))
                .bind(("ids", ids))
                .await?
                .check()?;
            let rows: Vec<Value> = response.take(0)?;
            let mut found: std::collections::HashMap<String, (String, String)> =
                std::collections::HashMap::new();
            for row in rows {
                let (Some(id), Some(source_id)) = (
                    row.get("id").and_then(Value::as_str),
                    row.get("source_id").and_then(Value::as_str),
                ) else {
                    continue;
                };
                let status = row
                    .get("status")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_owned();
                found.insert(source_id.to_owned(), (id.to_owned(), status));
            }
            for (index, source_id) in entries {
                if let Some((id, _)) = found.get(&source_id) {
                    by_source_match[index] = Some(id.clone());
                }
            }
        }
        for (index, item) in prepared.iter().enumerate() {
            let Some(entry) = item.as_ref() else {
                continue;
            };
            for (name, value) in &lookup_values[index] {
                let lookup = format!("{}\u{1}{name}\u{1}{value}", entry.object_type);
                if let Some(key) = used.get(&lookup) {
                    by_key_match[index] = Some(format!("object:{key}"));
                }
            }
        }
        // `match_by=id`: a `source_id` that is an existing record id wins over the
        // source reference (the single write path does the same check).
        if match_mode == UpsertMatch::Id {
            let candidates: Vec<String> = prepared
                .iter()
                .flatten()
                .filter_map(|entry| entry.source_id.clone())
                .filter(|id| id.starts_with("object:"))
                .collect();
            if !candidates.is_empty() {
                let literal = crate::api::keys::record_list_literal(
                    TABLE,
                    candidates.iter().map(String::as_str),
                );
                let mut response = state
                    .db
                    .inner()
                    .query(format!(
                        "SELECT type::string(id) AS id FROM object WHERE id IN {literal} \
                         AND deleted_at IS NONE;"
                    ))
                    .await?
                    .check()?;
                let rows: Vec<Value> = response.take(0)?;
                let existing_ids: std::collections::HashSet<String> = rows
                    .iter()
                    .filter_map(|row| row.get("id").and_then(Value::as_str))
                    .map(str::to_owned)
                    .collect();
                for (index, item) in prepared.iter().enumerate() {
                    if let Some(entry) = item.as_ref()
                        && let Some(source_id) = entry.source_id.as_deref()
                        && existing_ids.contains(source_id)
                    {
                        by_id_match[index] = Some(source_id.to_owned());
                    }
                }
            }
        }
    }
    let mut existing: Vec<Option<String>> = Vec::with_capacity(prepared.len());
    for index in 0..prepared.len() {
        // The order matches the single write path: business key, then (with
        // `match_by=id`) the record id, then the source reference.
        existing.push(
            by_key_match[index]
                .clone()
                .or_else(|| by_id_match[index].clone())
                .or_else(|| by_source_match[index].clone()),
        );
    }
    // Statuses of the matched objects (needed for the workflow guard).
    let mut existing_status: Vec<Option<String>> = vec![None; prepared.len()];
    let matched_keys: Vec<String> = existing
        .iter()
        .filter_map(|id| {
            id.as_deref()
                .and_then(|id| id.split_once(':').map(|(_, k)| k.to_owned()))
        })
        .collect();
    if !matched_keys.is_empty() {
        let literal =
            crate::api::keys::record_list_literal(TABLE, matched_keys.iter().map(String::as_str));
        let mut response = state
            .db
            .inner()
            .query(format!(
                "SELECT type::string(id) AS id, status FROM object WHERE id IN {literal};"
            ))
            .await?
            .check()?;
        let rows: Vec<Value> = response.take(0)?;
        let mut statuses: std::collections::HashMap<String, String> =
            std::collections::HashMap::new();
        for row in rows {
            if let (Some(id), Some(status)) = (
                row.get("id").and_then(Value::as_str),
                row.get("status").and_then(Value::as_str),
            ) {
                statuses.insert(id.to_owned(), status.to_owned());
            }
        }
        for (index, id) in existing.iter().enumerate() {
            if let Some(status) = id.as_ref().and_then(|id| statuses.get(id)) {
                existing_status[index] = Some(status.clone());
            }
        }
    }

    // Composition parents of the entries that will be **created** (one query for
    // the whole batch): a missing parent must fail the entry instead of parking the
    // object on the universal root, and the composition definition's type
    // restriction has to hold. Updates keep their parent (a move is a re-parent).
    let parent_keys: Vec<String> = prepared
        .iter()
        .enumerate()
        .filter(|(index, _)| existing[*index].is_none())
        .filter_map(|(_, item)| item.as_ref().and_then(|entry| entry.parent.clone()))
        .collect();
    let parent_types = live_object_types(&state.db, &parent_keys).await?;
    let composition_definition = crate::api::references::fetch_relation_definition(
        &state.db,
        crate::api::references::COMPOSITION_DEFINITION,
    )
    .await?
    .ok_or_else(|| ApiError::internal("composition definition is missing"))?;

    // ---- per-entry validation, rules and content ----------------------------
    let mut creates: Vec<Value> = Vec::new();
    let mut updates: Vec<Value> = Vec::new();
    let mut audits: Vec<Value> = Vec::new();
    let mut events: Vec<Value> = Vec::new();
    let mut cascades: Vec<String> = Vec::new();
    let mut created_count = 0usize;
    let mut updated_count = 0usize;
    let mut failed_count = 0usize;

    for (index, item) in prepared.into_iter().enumerate() {
        let Some(PreparedBatchItem {
            object_type,
            status,
            mut attributes,
            source,
            source_id,
            parent,
        }) = item
        else {
            failed_count += 1;
            continue;
        };
        if let Some(conflict) = conflicts[index].take() {
            results[index] = failed_at(index, &conflict);
            failed_count += 1;
            continue;
        }
        // The matched object may itself carry the key value — then it is not a
        // conflict, it is the object we are updating.
        let own_key = existing[index]
            .as_deref()
            .and_then(|id| id.split_once(':').map(|(_, key)| key.to_owned()));
        if let Some(conflict) =
            key_conflict(&object_type, &key_values[index], &used, own_key.as_deref())
        {
            results[index] = failed_at(index, &conflict);
            failed_count += 1;
            continue;
        }
        // A new object is created **under its intended parent** or not at all: the
        // parent must be a live object and the composition type restriction must
        // hold. (Updates keep their parent; moving is a re-parent.)
        if existing[index].is_none()
            && let Some(parent_key) = parent.as_deref()
        {
            match parent_types.get(parent_key) {
                None => {
                    results[index] = failed_at(
                        index,
                        &format!("parent object `object:{parent_key}` not found"),
                    );
                    failed_count += 1;
                    continue;
                }
                Some(parent_type) => {
                    if let Err(error) = crate::api::references::check_type_restriction(
                        &composition_definition,
                        &object_type,
                        parent_type,
                    ) {
                        results[index] = failed_at(index, error.message());
                        failed_count += 1;
                        continue;
                    }
                }
            }
        }
        if let Err(error) =
            crate::attribute_values::validate_attribute_values(&state.cache, &state.db, &attributes)
                .await
        {
            results[index] = failed_at(index, error.message());
            failed_count += 1;
            continue;
        }

        // Rules: `save` mode for every entry, `transition` mode when the status
        // changes (the single write paths do the same).
        let new_status = if status.is_empty() {
            default_status()
        } else {
            status.clone()
        };
        let status_changed = existing[index].is_some()
            && existing_status[index].as_deref() != Some(new_status.as_str());
        let mut eval_attributes = attributes.clone();
        if let Some(locale) = locale
            && let Err(error) = crate::language::resolve_attributes(
                &state.cache,
                &state.db,
                locale,
                &mut eval_attributes,
            )
            .await
        {
            results[index] = failed_at(index, error.message());
            failed_count += 1;
            continue;
        }
        let candidate = json!({
            "id": existing[index].clone().unwrap_or_default(),
            "type": object_type.clone(),
            "status": new_status.clone(),
            "attributes": Value::Object(eval_attributes),
        });
        match crate::rules::evaluate_for_write(
            &state.cache,
            &state.db,
            &state.rules,
            &candidate,
            crate::rules::WriteEvaluation::new(
                &["validation", "computed"],
                "save",
                locale,
                Some(&user_id),
            ),
        )
        .await
        {
            Ok(outcome) => {
                if let Err(error) = crate::rules::enforce(&outcome) {
                    results[index] = failed_at(index, error.message());
                    failed_count += 1;
                    continue;
                }
                match crate::rules::resolve_computed(&state.db, &outcome.computed_object()).await {
                    Ok(computed) => {
                        for (name, value) in computed {
                            attributes.insert(name, value);
                        }
                    }
                    Err(error) => {
                        results[index] = failed_at(index, error.message());
                        failed_count += 1;
                        continue;
                    }
                }
                if status_changed {
                    let guard = match crate::rules::evaluate_for_write(
                        &state.cache,
                        &state.db,
                        &state.rules,
                        &candidate,
                        crate::rules::WriteEvaluation::new(
                            &["workflow_guard"],
                            "transition",
                            locale,
                            Some(&user_id),
                        ),
                    )
                    .await
                    {
                        Ok(guard) => guard,
                        Err(error) => {
                            results[index] = failed_at(index, error.message());
                            failed_count += 1;
                            continue;
                        }
                    };
                    if let Err(error) = crate::rules::enforce(&guard) {
                        results[index] = failed_at(index, error.message());
                        failed_count += 1;
                        continue;
                    }
                }
            }
            Err(error) => {
                results[index] = failed_at(index, error.message());
                failed_count += 1;
                continue;
            }
        }

        let mut content = Map::new();
        content.insert("type".to_owned(), Value::String(object_type.clone()));
        content.insert("attributes".to_owned(), Value::Object(attributes));
        content.insert("status".to_owned(), Value::String(new_status.clone()));
        content.insert("updated_by".to_owned(), Value::String(user_id.clone()));
        if let Some(source_id) = source_id {
            content.insert("source_id".to_owned(), Value::String(source_id));
        }
        if let Some(source) = source {
            content.insert("source".to_owned(), Value::String(source));
        }

        match &existing[index] {
            Some(id) => {
                let key = id
                    .split_once(':')
                    .map(|(_, key)| key.to_owned())
                    .unwrap_or_else(|| id.clone());
                let change_view = content
                    .get("attributes")
                    .cloned()
                    .unwrap_or_else(|| json!({}));
                updates.push(json!({ "key": key, "content": Value::Object(content) }));
                audits.push(audit::content(AuditEntry {
                    user_id: &user_id,
                    action: if status_changed {
                        "status_changed"
                    } else {
                        "object_updated"
                    },
                    object_id: Some(id),
                    from: None,
                    to: status_changed.then(|| Value::String(new_status.clone())),
                    metadata: None,
                }));
                if emit_events {
                    events.push(crate::event::content(&crate::event::OutboxEvent {
                        event_type: crate::event::EVENT_OBJECT_UPDATED,
                        object_type: object_type.clone(),
                        object_id: id.clone(),
                        changes: json!({ "attributes": change_view }),
                        metadata: json!({ "user_id": user_id.clone() }),
                    }));
                }
                cascades.push(id.clone());
                updated_count += 1;
                results[index] = BatchUpsertItemResult {
                    index,
                    id: Some(id.clone()),
                    action: "updated".to_owned(),
                    error: None,
                };
            }
            None => {
                content.insert("version".to_owned(), json!(1));
                let ulid = Ulid::new().to_string();
                let record_id = format!("object:{ulid}");
                let change_view = content
                    .get("attributes")
                    .cloned()
                    .unwrap_or_else(|| json!({}));
                // The parent key is attached only when one was requested: the
                // transaction's `RELATE` falls back to the universal root when the
                // key is `NONE` (a bound JSON `null` would be `NULL`, not `NONE`,
                // and `type::record('object', NULL)` fails).
                let mut row = json!({ "id": ulid, "content": Value::Object(content) });
                if let Some(parent_key) = parent.as_deref() {
                    row["parent"] = Value::String(parent_key.to_owned());
                }
                creates.push(row);
                audits.push(audit::content(AuditEntry {
                    user_id: &user_id,
                    action: "object_created",
                    object_id: Some(&record_id),
                    from: None,
                    to: None,
                    metadata: Some(json!({ "batch": true })),
                }));
                if emit_events {
                    events.push(crate::event::content(&crate::event::OutboxEvent {
                        event_type: crate::event::EVENT_OBJECT_CREATED,
                        object_type: object_type.clone(),
                        object_id: record_id.clone(),
                        changes: json!({ "attributes": change_view }),
                        metadata: json!({ "user_id": user_id.clone() }),
                    }));
                }
                created_count += 1;
                results[index] = BatchUpsertItemResult {
                    index,
                    id: Some(record_id),
                    action: "created".to_owned(),
                    error: None,
                };
            }
        }
    }

    // ---- one transaction for the whole batch --------------------------------
    if !creates.is_empty() || !updates.is_empty() || !events.is_empty() {
        let mut sql = String::from("BEGIN TRANSACTION;\n");
        if !creates.is_empty() {
            sql.push_str("FOR $c IN $creates { CREATE type::record('object', $c.id) CONTENT $c.content; };\n");
            // The composition edge needs `RELATE`: a directly created edge record
            // (`CREATE reference SET in = …, out = …`) is stored but is **not** a
            // relation ("Found record … which is not a relation, but expected a
            // RELATION"), so no traversal finds it. `RELATE <function call>->…`
            // does not parse ("Unexpected token `::`"), therefore the loop binds a
            // record first — verified against the running server, including that
            // the edge is traversable afterwards. A row with a `parent` key hangs on
            // that parent instead of the universal root (the object is then created
            // **with** its intended parent, in one transaction).
            sql.push_str(
                "FOR $c IN $creates { LET $new = type::record('object', $c.id); \
                 LET $parent = IF $c.parent IS NONE THEN object:object_root \
                 ELSE type::record('object', $c.parent) END; \
                 RELATE $new->reference->$parent SET definition_id = 'rel_depends_on', \
                 type = 'depends_on', attributes = {}; };\n",
            );
        }
        if !updates.is_empty() {
            sql.push_str(
                "FOR $u IN $updates { UPDATE type::record('object', $u.key) MERGE $u.content; };\n",
            );
            sql.push_str(
                "FOR $u IN $updates { UPDATE type::record('object', $u.key) \
                 SET version = version + 1, updated_at = time::now(), updated_by = $user_id; };\n",
            );
        }
        if !events.is_empty() {
            sql.push_str("FOR $e IN $events { CREATE outbox_event CONTENT $e; };\n");
        }
        sql.push_str("COMMIT TRANSACTION;");
        state
            .db
            .inner()
            .query(sql)
            .bind(("creates", creates))
            .bind(("updates", updates))
            .bind(("events", events))
            .bind(("user_id", user_id.clone()))
            .await?
            .check()?;
        state.cache.invalidate_stats();
    }

    if !audits.is_empty() {
        audit::record_many_best_effort(&state.db, audits).await;
    }
    for id in cascades {
        if let Err(error) =
            crate::rules::queue_inherited_cascade(&state, &id, locale, &user_id).await
        {
            tracing::warn!(?error, "failed to enqueue inherited-computed cascade");
        }
    }
    tracing::debug!(
        total_ms = started.elapsed().as_secs_f64() * 1000.0,
        created = created_count,
        updated = updated_count,
        failed = failed_count,
        "batch upsert"
    );

    Ok(Json(BatchUpsertResponse {
        created: created_count,
        updated: updated_count,
        failed: failed_count,
        results,
    }))
}

/// A validated batch-upsert entry on its way to the write (see
/// [`batch_upsert_objects`]).
struct PreparedBatchItem {
    object_type: String,
    status: String,
    attributes: Map<String, Value>,
    source: Option<String>,
    source_id: Option<String>,
    /// Composition parent key for a **new** object (`None` = universal root).
    parent: Option<String>,
}

/// Fallback source label when an entry carries a `source_id` but no `source`
/// (the object table stores `source` as a plain string, so the lookup needs one).
const SOURCE_FALLBACK: &str = "api";

/// Result placeholder for an entry that has not been decided yet.
fn pending_result(index: usize) -> BatchUpsertItemResult {
    BatchUpsertItemResult {
        index,
        id: None,
        action: "failed".to_owned(),
        error: Some("entry was not processed".to_owned()),
    }
}

/// Result of an entry that failed before the write.
fn failed_result(index: usize, error: &str) -> BatchUpsertItemResult {
    failed_at(index, error)
}

fn failed_at(index: usize, error: &str) -> BatchUpsertItemResult {
    BatchUpsertItemResult {
        index,
        id: None,
        action: "failed".to_owned(),
        error: Some(error.to_owned()),
    }
}

/// The first key value of an entry that another live object already carries
/// (the object being updated itself is not a conflict).
fn key_conflict(
    object_type: &str,
    values: &[(String, String)],
    used: &std::collections::HashMap<String, String>,
    own_key: Option<&str>,
) -> Option<String> {
    for (name, value) in values {
        let lookup = format!("{object_type}\u{1}{name}\u{1}{value}");
        if let Some(owner) = used.get(&lookup)
            && own_key != Some(owner.as_str())
        {
            return Some(format!(
                "duplicate business key `{name}`: an object of type `{object_type}` \
                 with value `{value}` already exists"
            ));
        }
    }
    None
}

/// Loads a single object by id, optionally resolving multilingual attributes.
#[utoipa::path(
    get,
    path = "/objects/{id}",
    params(GetObjectQuery),
    responses(
        (status = 200, description = "Object", body = Value),
        (status = 400, description = "Invalid id", body = Value),
        (status = 404, description = "Object not found", body = Value)
    )
)]
pub async fn get_object(
    State(state): State<AppState>,
    Path(id): Path<String>,
    Query(params): Query<GetObjectQuery>,
) -> Result<Json<Value>, ApiError> {
    let key = parse_key(&id, TABLE)?;
    // Statements: LET(0), SELECT(1). A soft-deleted object reads as not found.
    let sql = "
        LET $obj = type::record('object', $key);
        SELECT * FROM $obj WHERE deleted_at IS NONE;
    ";
    let mut response = state
        .db
        .inner()
        .query(sql)
        .bind(("key", key))
        .await?
        .check()?;
    let objects: Vec<Value> = response.take(1)?;
    let mut object = objects
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::not_found(format!("object `{id}` not found")))?;

    let locale = params
        .locale
        .as_deref()
        .map(str::trim)
        .filter(|l| !l.is_empty());

    // Capture the original own attributes (for completeness) before resolving.
    let own_attributes = object
        .get("attributes")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    let object_type = object
        .get("type")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_owned();

    if let Some(attributes) = object.get_mut("attributes").and_then(Value::as_object_mut) {
        if let Some(locale) = locale {
            crate::language::resolve_attributes(&state.cache, &state.db, locale, attributes)
                .await?;
        }
        if params.resolve_inheritance == Some(true) {
            let inherited = crate::inheritance::resolve_inheritance(
                &state.cache,
                &state.db,
                key,
                locale,
                attributes,
            )
            .await?;
            // `from`/`label` per inherited attribute, so the UI can mark the
            // origin of a value ("inherited from …").
            object["inherited"] = Value::Object(inherited);
        }
    }

    let completeness = crate::completeness::compute(
        &state.cache,
        &state.db,
        &id,
        &object_type,
        locale,
        &own_attributes,
    )
    .await?;
    object["completeness"] = json!({ (locale.unwrap_or("default")): completeness.percent });
    object["missing_required"] = json!(completeness.missing);

    Ok(Json(object))
}

/// Query parameters for `GET /objects/stats`.
#[derive(Debug, Deserialize, ToSchema, IntoParams)]
#[into_params(parameter_in = Query)]
pub struct ObjectStatsQuery {
    /// Working language for the incomplete counts (optional).
    pub locale: Option<String>,
}

/// Server-side dashboard aggregation.
///
/// Returns the object counts by type and status, the total number of relations
/// and the number of incomplete objects per type (for a working locale) — so the
/// frontend no longer loads up to `limit` objects just to count them.
#[utoipa::path(
    get,
    path = "/objects/stats",
    params(ObjectStatsQuery),
    responses(
        (status = 200, description = "Dashboard stats", body = Value),
        (status = 401, description = "Unauthorized", body = Value)
    )
)]
pub async fn object_stats(
    AuthUser(_claims): AuthUser,
    State(state): State<AppState>,
    Query(params): Query<ObjectStatsQuery>,
) -> Result<Json<Value>, ApiError> {
    let locale = params
        .locale
        .as_deref()
        .map(str::trim)
        .filter(|l| !l.is_empty());

    // The aggregation reads every object's attributes, the composition edges and the
    // class features (~1 s at demo scale in a release build). It is cached per
    // working language and bound to the data version that every write path bumps, so
    // a stale payload is never returned.
    let cache_key = locale.unwrap_or_default().to_owned();
    let version = state.cache.stats_version();
    if let Some(cached) = state.cache.cached_stats(&cache_key, version).await {
        return Ok(Json((*cached).clone()));
    }

    // A miss is computed **once per key**: without this lock every concurrent
    // request recomputed the whole aggregation in parallel (measured 2026-09-16:
    // 1.8 s for a single cold call, 8.2 s mean for ten waiters at 16 k objects).
    let lock = state.cache.stats_lock(&cache_key);
    let _single_flight = lock.lock().await;
    // Another request may have computed and stored the payload while this one
    // waited — then the wait was the whole cost.
    let version = state.cache.stats_version();
    if let Some(cached) = state.cache.cached_stats(&cache_key, version).await {
        return Ok(Json((*cached).clone()));
    }

    // Counts come from a SQL aggregation (one small row per type/status pair): the
    // handler used to load **every** object with all its attributes just to count
    // them, which transferred ~5 MB and parsed it in the core.
    let mut response = state
        .db
        .inner()
        .query(
            "SELECT type, status, count() AS n FROM object WHERE deleted_at IS NONE \
             GROUP BY type, status;",
        )
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;

    let mut total: i64 = 0;
    let mut by_type: HashMap<String, i64> = HashMap::new();
    let mut by_status: HashMap<String, i64> = HashMap::new();
    for row in &rows {
        let count = row.get("n").and_then(Value::as_i64).unwrap_or(0);
        let otype = row.get("type").and_then(Value::as_str).unwrap_or("");
        let status = row.get("status").and_then(Value::as_str).unwrap_or("");
        total += count;
        *by_type.entry(otype.to_owned()).or_default() += count;
        *by_status.entry(status.to_owned()).or_default() += count;
    }

    // Relation total for the dashboard's informational line. One grouped count on
    // the edge table — measured 146 ms on the demo instance (28 k edges) and 461 ms
    // on the 276 k-edge perf dataset — and it rides along with the cached payload,
    // so it is paid only on a cold `GET /objects/stats` (which already aggregates
    // every object). Reference writes bump the stats version, so a cached count
    // cannot go stale.
    let mut response = state
        .db
        .inner()
        .query("SELECT count() AS n FROM reference GROUP ALL;")
        .await?
        .check()?;
    let reference_rows: Vec<Value> = response.take(0)?;
    let references = reference_rows
        .first()
        .and_then(|row| row.get("n"))
        .and_then(Value::as_i64)
        .unwrap_or(0);

    // Incomplete counts need the objects' attributes — but only for the types that
    // can be incomplete at all (required attributes or categorised objects). Every
    // other type is 100 % complete by definition, so its attributes stay unloaded.
    //
    // The candidates are read in **pages**: with the full `perf` dataset (70 315
    // objects) one response carried ~30 MB of attribute maps and SurrealDB reset
    // the connection (`500 Connection reset`, found live 2026-09-16 — the endpoint
    // is the dashboard's, and `scripts/demo_data.py`'s verification step died on
    // it). Page size 10 000 keeps a response in the few-megabyte range; the
    // completeness of a page is computed with the same `compute_batch` call.
    const STATS_PAGE: i64 = 10_000;
    let mut incomplete_by_type: HashMap<String, i64> = HashMap::new();
    let candidates = crate::completeness::incomplete_candidates(&state.cache, &state.db).await?;
    if !candidates.is_empty() {
        let mut offset: i64 = 0;
        loop {
            let mut response = state
                .db
                .inner()
                .query(
                    "SELECT id, type, attributes FROM object \
                     WHERE deleted_at IS NONE AND type IN $types \
                     ORDER BY id LIMIT $limit START $start;",
                )
                .bind(("types", candidates.clone()))
                .bind(("limit", STATS_PAGE))
                .bind(("start", offset))
                .await?
                .check()?;
            let objects: Vec<Value> = response.take(0)?;
            if objects.is_empty() {
                break;
            }
            let scanned = objects.len();

            // Build the per-object inputs for the batched completeness computation.
            let items: Vec<(String, String, serde_json::Map<String, Value>)> = objects
                .iter()
                .map(|object| {
                    (
                        object
                            .get("id")
                            .and_then(Value::as_str)
                            .unwrap_or_default()
                            .to_owned(),
                        object
                            .get("type")
                            .and_then(Value::as_str)
                            .unwrap_or_default()
                            .to_owned(),
                        object
                            .get("attributes")
                            .and_then(Value::as_object)
                            .cloned()
                            .unwrap_or_default(),
                    )
                })
                .collect();

            // Resolve completeness for the page with a single edge query and
            // in-memory inheritance resolution (no per-ancestor DB round-trips).
            let completeness =
                crate::completeness::compute_batch(&state.cache, &state.db, &items, locale).await?;
            for (object_id, otype, _) in &items {
                if let Some(result) = completeness.get(object_id)
                    && !result.missing.is_empty()
                {
                    *incomplete_by_type.entry(otype.to_owned()).or_default() += 1;
                }
            }

            if scanned < STATS_PAGE as usize {
                break;
            }
            offset += STATS_PAGE;
        }
    }

    let payload = json!({
        "total": total,
        "references": references,
        "by_type": by_type,
        "by_status": by_status,
        "incomplete_by_type": incomplete_by_type,
    });
    state
        .cache
        .store_stats(&cache_key, version, std::sync::Arc::new(payload.clone()))
        .await;
    Ok(Json(payload))
}

/// Lists objects, optionally filtered by type.
#[utoipa::path(
    get,
    path = "/objects",
    params(ListObjects),
    responses((status = 200, description = "Objects", body = Vec<Value>))
)]
pub async fn list_objects(
    State(state): State<AppState>,
    Query(params): Query<ListObjects>,
) -> Result<Json<Vec<Value>>, ApiError> {
    let limit = params.limit.clamp(1, 1000) as i64;
    let start = params.start as i64;
    // The completeness filter is not expressible in SQL (it needs the inherited
    // required attributes), so it is applied to the scanned window before the
    // result is sliced; the documented `limit` cap doubles as the scan window.
    let completeness_filter = params.completeness_lt.is_some() || params.missing_required;
    let (scan_limit, scan_start) = if completeness_filter {
        (1000, 0)
    } else {
        (limit, start)
    };

    // Soft-deleted objects (and their cascade descendants) are hidden.
    let mut conditions: Vec<String> = vec!["deleted_at IS NONE".to_owned()];
    if params.object_type.is_some() {
        conditions.push("type = $type".to_owned());
    }
    // Id-list filters (`?ancestor=`, `?category=`, `?collection=`, `?ids=`) are
    // rendered as **record list literals**: `id IN array::map($keys, …)` is
    // evaluated per candidate row and made a 1000-key filter take 16.6 s (and a
    // root subtree 110 s); the literal uses the id index (0.18 s). The lists are
    // therefore resolved before the SQL is built.
    if let Some(ancestor) = &params.ancestor {
        let keys = crate::api::references::subtree_keys(&state.db, ancestor).await?;
        let literal =
            crate::api::keys::record_list_literal("object", keys.iter().map(String::as_str));
        conditions.push(format!("id IN {literal}"));
    }
    // The category filters work on a single category relation type: the built-in
    // primary type by default, or the one requested via `definition_id`. An
    // explicit `definition_id` is always validated, even if no filter uses it.
    let mut category_definition: Option<String> = None;
    if params.category.is_some() || params.uncategorized || params.definition_id.is_some() {
        category_definition = Some(
            crate::api::references::resolve_category_definition(
                &state.db,
                params.definition_id.as_deref(),
            )
            .await?,
        );
    }
    if let Some(category) = &params.category {
        let definition = category_definition
            .as_deref()
            .unwrap_or(crate::api::references::PRIMARY_CATEGORY_DEFINITION);
        let keys =
            crate::api::references::category_member_keys(&state.db, category, definition).await?;
        let literal =
            crate::api::keys::record_list_literal("object", keys.iter().map(String::as_str));
        conditions.push(format!("id IN {literal}"));
    }
    if let Some(collection) = &params.collection {
        let keys = crate::api::references::collection_member_keys(&state.db, collection).await?;
        let literal =
            crate::api::keys::record_list_literal("object", keys.iter().map(String::as_str));
        conditions.push(format!("id IN {literal}"));
    }
    if params.released_for.is_some() {
        conditions.push(
            "id IN (SELECT VALUE object_id FROM release WHERE channel = $released_for AND status = $release_status)"
                .to_owned(),
        );
    }
    if params.uncategorized {
        // "Nicht kategorisiert" is per taxonomy: every taxonomy node type is
        // excluded (plus the universal root), so a classification hierarchy does
        // not count as a categorisation.
        conditions.push(
            "id NOT IN (SELECT VALUE in FROM reference WHERE definition_id = $category_definition) AND type NOT IN $taxonomy_types"
                .to_owned(),
        );
    }
    if let Some(ids) = &params.ids {
        let object_ids: Vec<String> = ids
            .split(',')
            .filter_map(|key| {
                let key = key.trim();
                if key.is_empty() {
                    return None;
                }
                Some(key.strip_prefix("object:").unwrap_or(key).to_owned())
            })
            .collect();
        if !object_ids.is_empty() {
            let literal = crate::api::keys::record_list_literal(
                "object",
                object_ids.iter().map(String::as_str),
            );
            conditions.push(format!("id IN {literal}"));
        }
    }
    // Advanced search (`?where=`): the same condition tree the search form
    // produces, validated against the catalog and rendered by the shared
    // filter renderer (see `plan/detail/suche-formular.md`).
    let mut filter_params: Vec<(String, Value)> = Vec::new();
    if let Some(raw) = &params.where_ {
        let mut advanced: crate::api::saved_searches::SavedSearchQuery = serde_json::from_str(raw)
            .map_err(|error| ApiError::bad_request(format!("invalid `where`: {error}")))?;
        let catalog = crate::api::search_schema::load(&state.db).await?;
        crate::api::search_schema::normalize_query(&mut advanced);
        let errors = crate::api::search_schema::validate_query(&advanced, &catalog);
        if !errors.is_empty() {
            return Err(ApiError::bad_request(errors.join("; ")));
        }
        let rendered = crate::api::search_schema::render_query(&advanced, &catalog)?;
        conditions.extend(rendered.clauses);
        filter_params = rendered.params;
    }
    let where_clause = if conditions.is_empty() {
        String::new()
    } else {
        format!(" WHERE {}", conditions.join(" AND "))
    };
    // The channel releases are fetched with a correlated subquery (one query,
    // no per-object round trip) and only when the caller asks for them.
    let projection = if params.releases {
        "*, (SELECT VALUE { channel: channel, status: status } FROM release WHERE object_id = $parent.id ORDER BY channel) AS releases"
    } else {
        "*"
    };
    // `id` is the tiebreaker of `created_at`: a batch writes many objects in the
    // same transaction, so their timestamps collide and an unordered page boundary
    // is not stable — a paging client (the complete MEF export) would then skip or
    // repeat rows.
    let sql = format!(
        "SELECT {projection} FROM object{where_clause} ORDER BY created_at DESC, id LIMIT $limit START $start"
    );

    let mut query = state.db.inner().query(sql);
    if let Some(object_type) = &params.object_type {
        query = query.bind(("type", object_type.clone()));
    }
    if let Some(definition) = &category_definition {
        query = query.bind(("category_definition", definition.clone()));
    }
    if params.uncategorized {
        let mut taxonomy_types = crate::api::object_types::taxonomy_types(&state.db).await?;
        taxonomy_types.push("root".to_owned());
        query = query.bind(("taxonomy_types", taxonomy_types));
    }
    if let Some(released_for) = &params.released_for {
        query = query.bind((
            "released_for",
            crate::api::channels::channel_code(released_for),
        ));
        query = query.bind((
            "release_status",
            params
                .release_status
                .as_deref()
                .map(str::trim)
                .filter(|status| !status.is_empty())
                .unwrap_or("published")
                .to_owned(),
        ));
    }
    for (key, value) in &filter_params {
        query = query.bind((key.as_str(), value.clone()));
    }
    let mut response = query
        .bind(("limit", scan_limit))
        .bind(("start", scan_start))
        .await?
        .check()?;
    let mut objects: Vec<Value> = response.take(0)?;

    let locale = params
        .locale
        .as_deref()
        .map(str::trim)
        .filter(|l| !l.is_empty())
        .map(str::to_owned);
    // Completeness (with inheritance) is only computed when the caller needs it:
    // for the `completeness` field of the response and/or for the completeness
    // filters. `locale` may be absent — the filter then uses the default values.
    //
    // **One batch for the whole page**, not one computation per row: the per-row
    // variant issued ~6 queries per object (~25 ms with a classification standard
    // installed), so a 1000-row page spent 18 s in it and the `missing_required`
    // filter (which scans up to 1000 rows) made the grid unusable.
    // `compute_batch` resolves the required names per type (cached), reads the
    // `depends_on` map and the ancestors' attributes once and walks the chains in
    // memory — the same result, without the round-trips.
    if locale.is_some() || completeness_filter {
        let items: Vec<(String, String, serde_json::Map<String, Value>)> = objects
            .iter()
            .map(|object| {
                (
                    object
                        .get("id")
                        .and_then(Value::as_str)
                        .unwrap_or("")
                        .to_owned(),
                    object
                        .get("type")
                        .and_then(Value::as_str)
                        .unwrap_or("")
                        .to_owned(),
                    object
                        .get("attributes")
                        .and_then(Value::as_object)
                        .cloned()
                        .unwrap_or_default(),
                )
            })
            .collect();
        let results =
            crate::completeness::compute_batch(&state.cache, &state.db, &items, locale.as_deref())
                .await?;
        let mut kept: Vec<Value> = Vec::new();
        for mut object in objects {
            let object_id = object
                .get("id")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_owned();
            let Some(result) = results.get(&object_id) else {
                kept.push(object);
                continue;
            };
            if completeness_filter
                && !passes_completeness_filter(
                    result,
                    params.completeness_lt,
                    params.missing_required,
                )
            {
                continue;
            }
            if let Some(locale) = &locale {
                object["completeness"] = json!({ (locale): result.percent });
            }
            kept.push(object);
        }
        objects = kept;
    }
    if completeness_filter {
        objects = objects
            .into_iter()
            .skip(start.max(0) as usize)
            .take(limit.max(0) as usize)
            .collect();
    }

    // Optionally fill inherited values for the whole page (one query per level)
    // and mark each object's inherited attributes with their source.
    if params.resolve_inheritance == Some(true) && !objects.is_empty() {
        let mut items: Vec<(String, serde_json::Map<String, Value>)> = objects
            .iter()
            .map(|object| {
                let id = object
                    .get("id")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_owned();
                let attributes = object
                    .get("attributes")
                    .and_then(Value::as_object)
                    .cloned()
                    .unwrap_or_default();
                (id, attributes)
            })
            .collect();
        let inherited = crate::inheritance::resolve_inheritance_batch(
            &state.cache,
            &state.db,
            locale.as_deref(),
            &mut items,
        )
        .await?;
        for (index, object) in objects.iter_mut().enumerate() {
            let (id, attributes) = &items[index];
            object["attributes"] = Value::Object(attributes.clone());
            let sources = inherited
                .get(id)
                .cloned()
                .unwrap_or_else(|| Value::Object(serde_json::Map::new()));
            object["inherited"] = sources;
        }
    }

    Ok(Json(objects))
}

/// Whether a completeness result passes the optional completeness filters.
///
/// Without any filter every result passes. `completeness_lt` keeps results
/// strictly below the threshold (objects whose type has no required attributes
/// are 100 % → complete), `missing_required` keeps results that still miss at
/// least one required attribute.
fn passes_completeness_filter(
    result: &crate::completeness::Completeness,
    completeness_lt: Option<i64>,
    missing_required: bool,
) -> bool {
    if let Some(threshold) = completeness_lt
        && result.percent >= threshold
    {
        return false;
    }
    if missing_required && result.missing.is_empty() {
        return false;
    }
    true
}

/// Query parameters for `GET /objects/by-source`.
#[derive(Debug, Deserialize, ToSchema, IntoParams)]
#[into_params(parameter_in = Query)]
pub struct BySourceParams {
    /// Source-system label (e.g. the MEF document `source`).
    pub source: String,
    /// External id within the source system (the MEF object `id`).
    pub source_id: String,
}

/// Loads exactly one object by its source reference (`source` + `source_id`).
#[utoipa::path(
    get,
    path = "/objects/by-source",
    params(BySourceParams),
    responses(
        (status = 200, description = "Matching object", body = Value),
        (status = 400, description = "Missing query parameter", body = Value),
        (status = 404, description = "No object matches the source reference", body = Value)
    )
)]
pub async fn get_by_source(
    State(state): State<AppState>,
    Query(params): Query<BySourceParams>,
) -> Result<Json<Value>, ApiError> {
    let source = params.source.trim();
    let source_id = params.source_id.trim();
    if source_id.is_empty() {
        return Err(ApiError::bad_request("`source_id` must not be empty"));
    }
    let sql = "
        SELECT * FROM object WHERE source_id = $source_id AND source = $source
        AND deleted_at IS NONE LIMIT 1;
    ";
    let mut response = state
        .db
        .inner()
        .query(sql)
        .bind(("source_id", source_id.to_owned()))
        .bind(("source", source.to_owned()))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    let object = rows
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::not_found("no object matches the given source reference"))?;
    Ok(Json(object))
}

/// Query parameters for `GET /objects/picker` (reference picker search).
#[derive(Debug, Deserialize, ToSchema, IntoParams)]
#[into_params(parameter_in = Query)]
pub struct ObjectPickerParams {
    /// Search term (case-insensitive, matched against attribute values).
    pub q: String,
    /// Comma-separated object types to restrict to (empty = all).
    pub types: Option<String>,
    /// Language code used to resolve the displayed label.
    pub locale: Option<String>,
    /// Maximum number of hits (capped at 1000).
    #[serde(default = "default_limit")]
    pub limit: u32,
    /// Also match objects whose `code` starts with the term (`EC0123` → all
    /// classes `EC0123…`).
    ///
    /// The full-text index matches **whole** tokens, so a partial code finds
    /// nothing (measured: `attributes @@ 'EC0123'` → 0 of the 61 matching
    /// classes). This is an **extra** query (a range scan over `attributes.code`,
    /// ~30 ms across the 5640 ETIM classes), because a full-text match cannot be
    /// combined with another condition via `OR` (see `code_prefix_hits`).
    #[serde(default)]
    pub code_prefix: bool,
    /// Attach each hit's `completeness` for `locale` (default `true`).
    ///
    /// The per-hit computation costs ~0.3 s (measured on the ETIM bundle), so a
    /// caller that lists many hits *without* showing the badge turns it off.
    #[serde(default = "default_true")]
    pub completeness: bool,
}

/// `true` — the default for optional booleans (`serde` needs a function).
fn default_true() -> bool {
    true
}

/// The exclusive upper bound of the range that matches a code prefix.
///
/// `"EC0123"` → `"EC0124"`: every string starting with the prefix is `>=` the
/// prefix and `<` its incremented form (the standard range trick, valid because
/// the comparison is lexicographic on code points). `None` for an empty prefix
/// (no usable restriction) and for a prefix of only maximum code points.
fn prefix_upper_bound(prefix: &str) -> Option<String> {
    let mut chars: Vec<char> = prefix.chars().collect();
    while let Some(last) = chars.pop() {
        if let Some(next) = char::from_u32(u32::from(last) + 1) {
            chars.push(next);
            return Some(chars.into_iter().collect());
        }
    }
    None
}

/// Objects whose `code` attribute starts with a code-style term.
///
/// Why a separate query: `attributes @@ $q` matches whole tokens only, and it must
/// not share an `OR` with another condition — in SurrealDB 3.2 the full-text side
/// then matches **every** row (measured on the ETIM bundle:
/// `(attributes @@ 'EC0123' OR string::starts_with(attributes.code, 'EC0123'))`
/// → 5640 rows instead of 61; `… OR false` is fine because it is constant-folded).
///
/// The match is the plain comparison `code >= <prefix>` / `< <prefix + 1>`:
/// unlike `string::starts_with`, it does not fail on objects without a `code`
/// (SurrealDB reports `Argument 1 was the wrong type. Expected \`string\` but
/// found \`NONE\``). The term is matched as typed **and** upper-cased, because the
/// codes of the shipped bundle are upper-case while the full-text search is
/// case-insensitive — a lower-case term must not behave differently.
async fn code_prefix_hits(
    state: &AppState,
    query: &str,
    types: &[String],
    limit: i64,
) -> Result<Vec<Value>, ApiError> {
    let mut terms = vec![query.to_owned()];
    let upper = query.to_uppercase();
    if upper != query {
        terms.push(upper);
    }

    let mut hits: Vec<Value> = Vec::new();
    let mut seen: HashSet<String> = HashSet::new();
    for term in terms {
        let Some(high) = prefix_upper_bound(&term) else {
            continue;
        };
        let mut sql = String::from(
            "SELECT * FROM object WHERE attributes.code >= $low \
             AND attributes.code < $high AND deleted_at IS NONE",
        );
        if !types.is_empty() {
            sql.push_str(" AND type IN $types");
        }
        sql.push_str(" ORDER BY attributes.code LIMIT $limit");
        let mut request = state
            .db
            .inner()
            .query(sql)
            .bind(("low", term))
            .bind(("high", high));
        if !types.is_empty() {
            request = request.bind(("types", types.to_vec()));
        }
        let mut response = request.bind(("limit", limit)).await?.check()?;
        let rows: Vec<Value> = response.take(0)?;
        for row in rows {
            let key = row
                .get("id")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_owned();
            if seen.insert(key) {
                hits.push(row);
            }
        }
        if hits.len() >= usize::try_from(limit).unwrap_or(usize::MAX) {
            break;
        }
    }
    Ok(hits)
}

/// Searches for objects by label to pick a reference target.
///
/// Returns compact hits (`id`, `type`, `label`, `status`); the label is the
/// resolved multilingual `name` for `?locale=`, falling back to the `code` (a
/// taxonomy class has no `name`). With `?locale=` each hit also carries a
/// `completeness` value for that language, so a picker can show whether the
/// target still misses required attributes (`?completeness=false` skips it — the
/// tree's search lists many hits and shows no badge). `?code_prefix=true`
/// additionally matches a partial `code` (`EC0123`). Used by the reference and
/// relation pickers and by the taxonomy tree's search.
#[utoipa::path(
    get,
    path = "/objects/picker",
    params(ObjectPickerParams),
    responses(
        (status = 200, description = "Picker hits", body = Vec<Value>),
        (status = 400, description = "Missing query", body = Value)
    )
)]
pub async fn picker(
    State(state): State<AppState>,
    Query(params): Query<ObjectPickerParams>,
) -> Result<Json<Vec<Value>>, ApiError> {
    let query = params.q.trim();
    if query.is_empty() {
        return Err(ApiError::bad_request("`q` must not be empty"));
    }
    let limit = params.limit.clamp(1, 1000) as i64;
    let types: Vec<String> = params
        .types
        .as_deref()
        .map(|s| {
            s.split(',')
                .map(|t| t.trim().to_owned())
                .filter(|t| !t.is_empty())
                .collect()
        })
        .unwrap_or_default();

    // Code hits first: a caller that searches a code wants the exact prefix match
    // on top of the description matches.
    let mut objects: Vec<Value> = Vec::new();
    if params.code_prefix {
        objects = code_prefix_hits(&state, query, &types, limit).await?;
    }

    let mut sql = String::from(
        "SELECT *, search::score(1) AS score FROM object \
         WHERE attributes @@ $q AND deleted_at IS NONE",
    );
    if !types.is_empty() {
        sql.push_str(" AND type IN $types");
    }
    sql.push_str(" ORDER BY score DESC LIMIT $limit");
    let mut request = state.db.inner().query(sql).bind(("q", query.to_owned()));
    if !types.is_empty() {
        request = request.bind(("types", types));
    }
    let mut response = request.bind(("limit", limit)).await?.check()?;
    let text_objects: Vec<Value> = response.take(0)?;
    let mut seen: HashSet<String> = objects
        .iter()
        .filter_map(|object| object.get("id").and_then(Value::as_str))
        .map(str::to_owned)
        .collect();
    for object in text_objects {
        let key = object
            .get("id")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_owned();
        if seen.insert(key) {
            objects.push(object);
        }
    }
    objects.truncate(usize::try_from(limit).unwrap_or(usize::MAX));

    let locale = params
        .locale
        .as_deref()
        .map(str::trim)
        .filter(|l| !l.is_empty())
        .map(str::to_owned);
    let chain: Option<Arc<Vec<String>>> = match &locale {
        Some(locale) => Some(state.cache.fallback_chain(&state.db, locale).await?),
        None => None,
    };

    let mut hits = Vec::new();
    for object in objects {
        let id = object
            .get("id")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_owned();
        let object_type = object
            .get("type")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_owned();
        let status = object
            .get("status")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_owned();
        let attributes = object
            .get("attributes")
            .and_then(Value::as_object)
            .cloned()
            .unwrap_or_default();
        let label = crate::language::resolve_object_label(
            locale.as_deref(),
            chain.as_deref().map(|v| v.as_slice()),
            &attributes,
        )
        .unwrap_or_else(|| object_type.clone());
        let mut hit = json!({
            "id": &id,
            "type": &object_type,
            "label": label,
            "status": &status,
        });
        // The per-hit computation costs ~0.3 s (measured on the ETIM bundle), which
        // is fine for the reference picker's few hits but not for a caller that
        // lists many of them — `?completeness=false` skips it.
        if let Some(locale) = locale.as_deref().filter(|_| params.completeness) {
            let result = crate::completeness::compute(
                &state.cache,
                &state.db,
                &id,
                &object_type,
                Some(locale),
                &attributes,
            )
            .await?;
            hit["completeness"] = json!({ (locale): result.percent });
        }
        hits.push(hit);
    }
    Ok(Json(hits))
}

/// Applies a partial object update (attributes/status) with rule evaluation.
///
/// Shared by `update_object` and `batch_update_objects` so the single and
/// batch write paths remain consistent.
async fn apply_object_update(
    state: &AppState,
    headers: &HeaderMap,
    id: &str,
    body: UpdateObject,
    params: WriteObjectQuery,
) -> Result<Value, ApiError> {
    // Phase timings for the write path (debug level): the per-request write cost
    // is what an import or a grid batch pays per object, and it is spread over
    // lookups, rule evaluation and the transaction. Enable with
    // `RUST_LOG=info,pim_core::api::objects=debug`.
    let started = std::time::Instant::now();
    let mut last;
    let mut phases: Vec<(&'static str, f64)> = Vec::new();
    let key = parse_key(id, TABLE)?;
    let user_id = crate::auth::api_key_or_role(headers, &state.config, crate::auth::ROLE_EDITOR)?;

    // Load the current object: type for uniqueness checks, status for audit.
    let current_sql = "
        LET $obj = type::record('object', $key);
        SELECT * FROM $obj;
    ";
    let mut current_response = state
        .db
        .inner()
        .query(current_sql)
        .bind(("key", key))
        .await?
        .check()?;
    let current_rows: Vec<Value> = current_response.take(1)?;
    let current = current_rows
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::not_found(format!("object `{id}` not found")))?;
    let object_type = current
        .get("type")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    let old_status = current
        .get("status")
        .and_then(Value::as_str)
        .map(str::to_owned);

    let new_status = body.status.clone();
    phases.push(("load", started.elapsed().as_secs_f64() * 1000.0));
    last = std::time::Instant::now();

    // Holds the per-key locks (if any) until the write below is done: a PATCH can
    // change a business key, so it races with a parallel create/update otherwise.
    let key_names = crate::api::object_types::key_attributes_for(&state.db, &object_type).await?;
    let lock_names = match &body.attributes {
        Some(Value::Object(attributes)) => {
            keys::key_lock_names(&object_type, &key_names, attributes)
        }
        _ => Vec::new(),
    };
    let _key_guards = state.key_locks.lock_keys(&lock_names).await;

    let mut changed_fields: Vec<&str> = Vec::new();
    let mut changes = serde_json::Map::new();
    if let Some(attributes) = body.attributes {
        let Value::Object(mut attributes) = attributes else {
            return Err(ApiError::bad_request("`attributes` must be an object"));
        };
        keys::normalize_key_attributes(
            &state.db,
            &object_type,
            &key_names,
            &mut attributes,
            Some(key),
        )
        .await?;
        crate::attribute_values::validate_attribute_values(&state.cache, &state.db, &attributes)
            .await?;
        changes.insert("attributes".to_owned(), Value::Object(attributes));
        changed_fields.push("attributes");
    }
    if let Some(status) = body.status {
        changes.insert("status".to_owned(), Value::String(status));
        changed_fields.push("status");
    }
    // Keys removed from the object ("back to inherited"): applied after the
    // merge by the SQL below and mirrored into the evaluation view here.
    let remove_attributes: Vec<String> = body
        .remove_attributes
        .into_iter()
        .map(|name| name.trim().to_owned())
        .filter(|name| !name.is_empty())
        .collect();
    if !remove_attributes.is_empty() && !changed_fields.contains(&"attributes") {
        changed_fields.push("attributes");
    }

    if changes.is_empty() && remove_attributes.is_empty() {
        return Ok(current);
    }
    phases.push(("prepare", last.elapsed().as_secs_f64() * 1000.0));
    last = std::time::Instant::now();

    let status_changed = changed_fields.contains(&"status");
    let _ = &mut phases;
    let locale = params
        .locale
        .as_deref()
        .map(str::trim)
        .filter(|l| !l.is_empty());
    let emit_events = params.emit_events.unwrap_or(true);

    // Own attributes to persist (pending changes applied).
    let mut own_attributes = changes
        .get("attributes")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_else(|| {
            current
                .get("attributes")
                .and_then(Value::as_object)
                .cloned()
                .unwrap_or_default()
        });
    // Removed keys must not appear in the evaluated view either: dropping them
    // lets the value fall back to the inherited one.
    for name in &remove_attributes {
        own_attributes.remove(name.as_str());
    }

    // Resolved view for rule evaluation: own + language-resolved + inherited.
    let mut eval_attributes = own_attributes.clone();
    if let Some(locale) = locale {
        crate::language::resolve_attributes(&state.cache, &state.db, locale, &mut eval_attributes)
            .await?;
    }
    crate::inheritance::resolve_inheritance(
        &state.cache,
        &state.db,
        key,
        locale,
        &mut eval_attributes,
    )
    .await?;
    phases.push(("inheritance", last.elapsed().as_secs_f64() * 1000.0));
    last = std::time::Instant::now();

    let candidate_status = changes
        .get("status")
        .and_then(Value::as_str)
        .or_else(|| current.get("status").and_then(Value::as_str))
        .unwrap_or_default()
        .to_owned();
    let candidate = json!({
        "id": id.to_owned(),
        "type": object_type.clone(),
        "status": candidate_status,
        "attributes": Value::Object(eval_attributes),
    });

    // validation + computed (save mode)
    let outcome = crate::rules::evaluate_for_write(
        &state.cache,
        &state.db,
        &state.rules,
        &candidate,
        crate::rules::WriteEvaluation::new(
            &["validation", "computed"],
            "save",
            locale,
            Some(&user_id),
        ),
    )
    .await?;
    crate::rules::enforce(&outcome)?;
    let computed = crate::rules::resolve_computed(&state.db, &outcome.computed_object()).await?;
    if !computed.is_empty() {
        let mut merged = own_attributes;
        for (name, value) in computed {
            merged.insert(name, value);
        }
        changes.insert("attributes".to_owned(), Value::Object(merged));
        if !changed_fields.contains(&"attributes") {
            changed_fields.push("attributes");
        }
    }

    // workflow_guard (transition mode) when the status changes
    if status_changed {
        let guard = crate::rules::evaluate_for_write(
            &state.cache,
            &state.db,
            &state.rules,
            &candidate,
            crate::rules::WriteEvaluation::new(
                &["workflow_guard"],
                "transition",
                locale,
                Some(&user_id),
            ),
        )
        .await?;
        crate::rules::enforce(&guard)?;
    }
    phases.push(("rules", last.elapsed().as_secs_f64() * 1000.0));
    last = std::time::Instant::now();

    let outbox = crate::event::content(&crate::event::OutboxEvent {
        event_type: crate::event::EVENT_OBJECT_UPDATED,
        object_type: object_type.clone(),
        object_id: id.to_owned(),
        changes: Value::Object(changes.clone()),
        metadata: json!({ "user_id": user_id.clone() }),
    });

    // Statements: BEGIN(0), LET(1), UPDATE MERGE(2), UPDATE SET(3),
    // [UPDATE remove attributes(4)], [CREATE outbox], SELECT, COMMIT.
    let remove_stmt = if remove_attributes.is_empty() {
        String::new()
    } else {
        "UPDATE $obj SET attributes = object::from_entries(array::filter(object::entries(attributes), |$e| !($e[0] IN $remove)));\n"
            .to_owned()
    };
    let sql = if emit_events {
        format!(
            "
        BEGIN TRANSACTION;
        LET $obj = type::record('object', $key);
        UPDATE $obj MERGE $changes;
        UPDATE $obj SET version = version + 1, updated_at = time::now(), updated_by = $user;
        {remove_stmt}CREATE outbox_event CONTENT $outbox;
        SELECT * FROM $obj;
        COMMIT TRANSACTION;
        "
        )
    } else {
        format!(
            "
        BEGIN TRANSACTION;
        LET $obj = type::record('object', $key);
        UPDATE $obj MERGE $changes;
        UPDATE $obj SET version = version + 1, updated_at = time::now(), updated_by = $user;
        {remove_stmt}SELECT * FROM $obj;
        COMMIT TRANSACTION;
        "
        )
    };
    let mut response = state
        .db
        .inner()
        .query(sql)
        .bind(("key", key))
        .bind(("changes", Value::Object(changes)))
        .bind(("user", user_id.clone()))
        .bind(("remove", remove_attributes.clone()))
        .bind(("outbox", outbox))
        .await?
        .check()?;
    let select_index = if remove_attributes.is_empty() {
        if emit_events { 5 } else { 4 }
    } else if emit_events {
        6
    } else {
        5
    };
    let objects: Vec<Value> = response.take(select_index)?;
    let object = objects
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::not_found(format!("object `{id}` not found")))?;
    phases.push(("write", last.elapsed().as_secs_f64() * 1000.0));
    tracing::debug!(
        total_ms = started.elapsed().as_secs_f64() * 1000.0,
        ?phases,
        %object_type,
        emit_events,
        "object update phases"
    );

    let (from, to) = if status_changed {
        (old_status.map(Value::String), new_status.map(Value::String))
    } else {
        (None, None)
    };
    let action = if changed_fields.len() == 1 && status_changed {
        "status_changed"
    } else {
        "object_updated"
    };
    let metadata = if action == "object_updated" {
        Some(json!({ "fields": changed_fields }))
    } else {
        None
    };
    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &user_id,
            action,
            object_id: Some(id),
            from,
            to,
            metadata,
        },
    )
    .await;

    // Inherited-computed cascade: if this object has descendants with a
    // `computed_reads_inherited` computed attribute, queue a recompute job per
    // affected rule over those descendants (eventual consistency, never
    // inline). Best-effort so a queue hiccup never fails the save.
    if changed_fields.contains(&"attributes") {
        let locale = params
            .locale
            .as_deref()
            .map(str::trim)
            .filter(|l| !l.is_empty());
        if let Err(error) = crate::rules::queue_inherited_cascade(state, id, locale, &user_id).await
        {
            tracing::warn!(?error, "failed to enqueue inherited-computed cascade");
        }
    }

    // The dashboard aggregation counts objects and resolves completeness, so any
    // write to an object invalidates it.
    state.cache.invalidate_stats();
    Ok(object)
}

/// Updates a single object (partial update).
#[utoipa::path(
    patch,
    path = "/objects/{id}",
    request_body = UpdateObject,
    params(WriteObjectQuery),
    responses(
        (status = 200, description = "Updated object", body = Value),
        (status = 400, description = "Invalid id or payload", body = Value),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Forbidden", body = Value),
        (status = 404, description = "Object not found", body = Value)
    )
)]
pub async fn update_object(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(id): Path<String>,
    Query(params): Query<WriteObjectQuery>,
    Json(body): Json<UpdateObject>,
) -> Result<Json<Value>, ApiError> {
    let object = apply_object_update(&state, &headers, &id, body, params).await?;
    Ok(Json(object))
}

/// Maximum number of rows per batch update call.
const MAX_BATCH_UPDATE_ROWS: usize = 500;

/// A single staged grid edit to apply in a batch.
#[derive(Debug, Deserialize, ToSchema)]
pub struct BatchUpdateItem {
    /// Object record id.
    #[serde(rename = "id")]
    pub object_id: String,
    /// Partial attribute values to merge into the object.
    #[serde(default)]
    pub attributes: Option<Value>,
    /// New workflow status (optional).
    #[serde(default)]
    pub status: Option<String>,
}

/// Per-object result of a batch update.
#[derive(Debug, Serialize, ToSchema)]
pub struct BatchUpdateResult {
    pub id: String,
    pub success: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub status: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

/// Applies staged grid inline-edits in a single call (batch save).
///
/// `POST /objects/batch-update` accepts a list of partial updates and applies
/// them independently, reporting a per-object result. Capped at
/// `MAX_BATCH_UPDATE_ROWS` (HTTP 413 when exceeded).
#[utoipa::path(
    post,
    path = "/objects/batch-update",
    request_body = Vec<BatchUpdateItem>,
    responses(
        (status = 200, description = "Batch result", body = Vec<BatchUpdateResult>),
        (status = 400, description = "Invalid payload", body = Value),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Forbidden", body = Value),
        (status = 413, description = "Too many rows", body = Value)
    )
)]
pub async fn batch_update_objects(
    State(state): State<AppState>,
    headers: HeaderMap,
    Query(params): Query<WriteObjectQuery>,
    Json(items): Json<Vec<BatchUpdateItem>>,
) -> Result<Json<Vec<BatchUpdateResult>>, ApiError> {
    if items.is_empty() {
        return Err(ApiError::bad_request("batch must not be empty"));
    }
    if items.len() > MAX_BATCH_UPDATE_ROWS {
        return Err(ApiError::payload_too_large(format!(
            "batch exceeds the limit of {MAX_BATCH_UPDATE_ROWS} rows"
        )));
    }
    let mut results: Vec<BatchUpdateResult> = Vec::with_capacity(items.len());
    for item in items {
        let body = UpdateObject {
            attributes: item.attributes,
            status: item.status,
            // Inline grid edits never remove keys (the form uses the single
            // object endpoint for "back to inherited").
            remove_attributes: Vec::new(),
        };
        match apply_object_update(&state, &headers, &item.object_id, body, params.clone()).await {
            Ok(object) => {
                let status = object
                    .get("status")
                    .and_then(Value::as_str)
                    .map(str::to_owned);
                results.push(BatchUpdateResult {
                    id: item.object_id,
                    success: true,
                    status,
                    error: None,
                });
            }
            Err(error) => {
                results.push(BatchUpdateResult {
                    id: item.object_id,
                    success: false,
                    status: None,
                    error: Some(error.message().to_owned()),
                });
            }
        }
    }
    Ok(Json(results))
}

/// Payload for `POST /objects/bulk` (a mass operation on a selection).
#[derive(Debug, Deserialize, ToSchema)]
pub struct BulkObjectRequest {
    /// Operation key: `set_attribute` | `clear_attribute` | `change_status` |
    /// `send_for_approval` | `add_category` | `remove_category` |
    /// `add_relation` | `remove_relation` | `change_parent` | `replace_text` |
    /// `consolidate_inheritance` | `release_for_channel` |
    /// `withdraw_from_channel` | `delete`.
    pub op: String,
    /// Explicit object ids (full record ids) the operation applies to. The
    /// frontend resolves the selection (all filter hits / page / checked rows)
    /// into this list; the core stays lean and does not re-resolve a filter.
    #[serde(default)]
    pub object_ids: Vec<String>,
    /// Op-specific parameters (attribute name/value, status, category, parent,
    /// channel).
    #[serde(default)]
    pub parameters: Value,
}

const BULK_OPS: [&str; 14] = [
    "set_attribute",
    "clear_attribute",
    "change_status",
    "send_for_approval",
    "add_category",
    "remove_category",
    "add_relation",
    "remove_relation",
    "change_parent",
    "replace_text",
    "consolidate_inheritance",
    "release_for_channel",
    "withdraw_from_channel",
    "delete",
];

/// `POST /objects/bulk` queues a `bulk_operation` job over a selection.
#[utoipa::path(
    post,
    path = "/objects/bulk",
    request_body = BulkObjectRequest,
    responses(
        (status = 202, description = "Bulk job queued", body = Value),
        (status = 400, description = "Invalid payload", body = Value),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Forbidden", body = Value),
        (status = 503, description = "Job queue unavailable", body = Value)
    )
)]
pub async fn bulk_objects(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Json(body): Json<BulkObjectRequest>,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    if !claims.is_editor() {
        return Err(ApiError::forbidden("requires editor role"));
    }
    if !BULK_OPS.contains(&body.op.as_str()) {
        return Err(ApiError::bad_request(format!(
            "unknown bulk operation `{}`",
            body.op
        )));
    }
    if body.object_ids.is_empty() {
        return Err(ApiError::bad_request(
            "`object_ids` must contain at least one object",
        ));
    }
    let parameters = json!({
        "op": body.op,
        "object_ids": body.object_ids,
        "parameters": body.parameters,
    });
    crate::api::jobs::queue_run(
        &state.db,
        &state.config,
        None,
        "bulk_operation",
        parameters,
        "manual",
        &claims.sub,
    )
    .await
}

/// Payload for persisting worker-computed attribute values.
#[derive(Debug, Deserialize, ToSchema)]
pub struct UpdateComputed {
    /// Declared-computed attribute values to merge into the object.
    pub values: Value,
}

/// Persists declared-computed values computed by the worker (recompute job).
///
/// This is the machine-to-machine write-back path for `business_rule`
/// recompute jobs: it merges only attributes that are declared `computed:
/// true` and does **not** re-run the synchronous rule evaluation. Accepts an
/// `X-Api-Key` (`LOG_INGEST_API_KEY`) or an admin JWT.
#[utoipa::path(
    patch,
    path = "/objects/{id}/computed",
    request_body = UpdateComputed,
    responses(
        (status = 200, description = "Updated object", body = Value),
        (status = 400, description = "Invalid id or undeclared computed value", body = Value),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Forbidden", body = Value),
        (status = 404, description = "Object not found", body = Value)
    )
)]
pub async fn update_computed(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(id): Path<String>,
    Json(body): Json<UpdateComputed>,
) -> Result<Json<Value>, ApiError> {
    // Machine-to-machine API key or admin JWT (same policy as job-run updates).
    let api_key_ok = match &state.config.log_ingest_api_key {
        Some(expected) => headers
            .get("x-api-key")
            .and_then(|value| value.to_str().ok())
            .is_some_and(|provided| provided == expected),
        None => false,
    };
    let jwt_ok = headers
        .get(axum::http::header::AUTHORIZATION)
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.strip_prefix("Bearer "))
        .and_then(|token| crate::auth::verify(&state.config.jwt_secret, token).ok())
        .is_some_and(|claims| claims.is_admin());
    if !api_key_ok && !jwt_ok {
        return Err(ApiError::forbidden("requires API key or admin role"));
    }

    let key = parse_key(&id, TABLE)?;
    let Value::Object(values) = body.values else {
        return Err(ApiError::bad_request("`values` must be an object"));
    };
    // Enforce: every value must be declared as `computed: true`.
    let computed = crate::rules::resolve_computed(&state.db, &values).await?;
    if computed.is_empty() {
        return Err(ApiError::bad_request("`values` must not be empty"));
    }

    // Load current attributes and merge the computed values.
    let current_sql = "
        LET $obj = type::record('object', $key);
        SELECT * FROM $obj;
    ";
    let mut current_response = state
        .db
        .inner()
        .query(current_sql)
        .bind(("key", key))
        .await?
        .check()?;
    let current_rows: Vec<Value> = current_response.take(1)?;
    let current = current_rows
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::not_found(format!("object `{id}` not found")))?;
    let mut attributes = current
        .get("attributes")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    for (name, value) in computed {
        attributes.insert(name, value);
    }

    let sql = "
        LET $obj = type::record('object', $key);
        UPDATE $obj SET attributes = $attributes, updated_at = time::now(), updated_by = 'system';
        SELECT * FROM $obj;
    ";
    let mut response = state
        .db
        .inner()
        .query(sql)
        .bind(("key", key))
        .bind(("attributes", Value::Object(attributes)))
        .await?
        .check()?;
    let objects: Vec<Value> = response.take(2)?;
    let object = objects
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::not_found(format!("object `{id}` not found")))?;

    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: "system",
            action: "object_computed",
            object_id: Some(&id),
            from: None,
            to: None,
            metadata: None,
        },
    )
    .await;

    // Computed values change an object's completeness.
    state.cache.invalidate_stats();
    Ok(Json(object))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::completeness::Completeness;

    fn completeness(percent: i64, missing: &[&str]) -> Completeness {
        Completeness {
            percent,
            missing: missing.iter().map(|name| (*name).to_owned()).collect(),
        }
    }

    #[test]
    fn upsert_match_defaults_to_source_and_accepts_id() {
        assert_eq!(UpsertMatch::parse(None).unwrap(), UpsertMatch::Source);
        assert_eq!(UpsertMatch::parse(Some("")).unwrap(), UpsertMatch::Source);
        assert_eq!(
            UpsertMatch::parse(Some(" source ")).unwrap(),
            UpsertMatch::Source
        );
        assert_eq!(UpsertMatch::parse(Some("id")).unwrap(), UpsertMatch::Id);
        assert!(UpsertMatch::parse(Some("key")).is_err());
    }

    #[test]
    fn key_conflict_ignores_the_object_that_is_being_updated() {
        let values = vec![("sku".to_owned(), "A-1".to_owned())];
        let mut used = std::collections::HashMap::new();
        used.insert("product\u{1}sku\u{1}A-1".to_owned(), "own".to_owned());

        // The matched object itself carries the value: an update, not a conflict.
        assert!(key_conflict("product", &values, &used, Some("own")).is_none());
        // Another object carries it: a conflict.
        assert!(key_conflict("product", &values, &used, Some("other")).is_some());
        assert!(key_conflict("product", &values, &used, None).is_some());
        // A different type may reuse the value (the check is per type).
        assert!(key_conflict("variant", &values, &used, None).is_none());
    }

    #[test]
    fn prefix_upper_bound_increments_the_last_character() {
        assert_eq!(prefix_upper_bound("EC0123").as_deref(), Some("EC0124"));
        // No carry: the upper bound is the prefix with its *last* code point
        // incremented, so `EC0129` ends at `EC012:` (not at `EC0130`).
        assert_eq!(prefix_upper_bound("EC0129").as_deref(), Some("EC012:"));
        assert_eq!(prefix_upper_bound("A").as_deref(), Some("B"));
        // An empty prefix restricts nothing.
        assert_eq!(prefix_upper_bound(""), None);
    }

    #[test]
    fn prefix_upper_bound_covers_every_string_with_the_prefix() {
        for low in ["EC0123", "EC0129", "A"] {
            let high = prefix_upper_bound(low).unwrap_or_default();
            for code in [
                low.to_owned(),
                format!("{low}0"),
                format!("{low}A"),
                format!("{low}ZZZ"),
            ] {
                assert!(
                    code.as_str() >= low && code.as_str() < high.as_str(),
                    "{code} must match the prefix {low}"
                );
            }
            for code in ["EC0122", "EC0124", "EC01244", "EC012", "ED0123", "EC012:0"] {
                assert!(
                    !(code >= low && code < high.as_str()),
                    "{code} must not match the prefix {low}"
                );
            }
        }
    }

    #[test]
    fn completeness_filter_without_filters_keeps_every_object() {
        let result = completeness(50, &["price"]);
        assert!(passes_completeness_filter(&result, None, false));
    }

    #[test]
    fn completeness_lt_keeps_only_objects_below_the_threshold() {
        assert!(passes_completeness_filter(
            &completeness(99, &["price"]),
            Some(100),
            false
        ));
        assert!(!passes_completeness_filter(
            &completeness(100, &[]),
            Some(100),
            false
        ));
    }

    #[test]
    fn missing_required_keeps_only_incomplete_objects() {
        assert!(passes_completeness_filter(
            &completeness(50, &["price"]),
            None,
            true
        ));
        // A type without required attributes is complete by definition.
        assert!(!passes_completeness_filter(
            &completeness(100, &[]),
            None,
            true
        ));
    }

    #[test]
    fn completeness_filters_combine_with_and() {
        // 90 % is below 95 but has nothing missing → only the failing bound counts.
        let result = completeness(90, &[]);
        assert!(passes_completeness_filter(&result, Some(95), false));
        assert!(!passes_completeness_filter(&result, Some(95), true));
    }

    #[test]
    fn batch_delete_ids_are_normalised_and_validated() {
        // Bare keys and full record ids are both accepted, duplicates dropped.
        let keys = normalize_delete_ids(&[
            "object:01ABC".to_owned(),
            "01ABC".to_owned(),
            "demo-group-PG-BRK".to_owned(),
        ])
        .expect("valid ids");
        assert_eq!(keys, vec!["01ABC", "demo-group-PG-BRK"]);

        // The universal root never goes away, and junk is refused instead of
        // being dropped silently (the caller would wonder where it went).
        assert!(normalize_delete_ids(&["object:object_root".to_owned()]).is_err());
        assert!(normalize_delete_ids(&["object:".to_owned()]).is_err());
        assert!(normalize_delete_ids(&["object:a b".to_owned()]).is_err());
        assert!(normalize_delete_ids(&[]).is_err());
    }

    #[test]
    fn batch_delete_is_capped() {
        let ids: Vec<String> = (0..MAX_BATCH_DELETE_ROWS + 1)
            .map(|index| format!("object:{index}"))
            .collect();
        assert!(normalize_delete_ids(&ids).is_err());
        assert!(normalize_delete_ids(&ids[..MAX_BATCH_DELETE_ROWS]).is_ok());
    }
}
