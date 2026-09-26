//! Object CRUD handlers.
//!
//! Every object is part of the composition tree: creating an object also
//! creates its `depends_on` edge to the universal root.

use std::collections::{BTreeSet, HashMap, HashSet};
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
use crate::auth::{self, AuthUser, Caller};
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
    /// Sort field of the page: a **sortable** field of the search catalog
    /// (`GET /search-fields`) — `type`, `status`, `created_at`, `updated_at` or
    /// `attributes.<name>[.<lang>]`. Absent keeps the default order
    /// (`created_at` descending, `id` as tiebreaker).
    #[serde(default)]
    pub sort: Option<String>,
    /// Direction of `sort`: `asc` (default) or `desc`.
    #[serde(default)]
    pub order: Option<String>,
    /// How many of the matching objects `GET /objects/attribute-usage` inspects
    /// (capped at 5000, default 1000). Ignored by every other endpoint.
    #[serde(default = "default_sample")]
    pub sample: u32,
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

fn default_sample() -> u32 {
    1000
}

/// Upper bound of `?sample=` on `GET /objects/attribute-usage`.
const MAX_SAMPLE: u32 = 5000;

/// Page size of a completeness-filtered scan (`GET /objects/count`,
/// `GET /objects/attribute-usage`): the same size `/objects/stats` uses to keep
/// one response in the few-megabyte range.
const SCAN_PAGE: i64 = 10_000;

/// The default order of an object list: newest first, `id` as tiebreaker.
///
/// `id` is the tiebreaker of `created_at`: a batch writes many objects in the
/// same transaction, so their timestamps collide and an unordered page boundary
/// is not stable — a paging client (the complete MEF export) would then skip or
/// repeat rows.
const DEFAULT_ORDER: &str = "created_at DESC, id";

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
    let user_id = crate::auth::require_permission(&state, &headers, "object.create")?;
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
    let caller = crate::auth::require_caller(&state, &headers, "object.delete")?;
    let user_id = caller.user_id().to_owned();
    let key = parse_key(&id, TABLE)?;
    if key == "object_root" {
        return Err(ApiError::bad_request(
            "the universal root cannot be deleted",
        ));
    }
    let emit_events = params.emit_events.unwrap_or(true);

    // Ensure the object exists and is not already soft-deleted — and that it is **inside the
    // caller's scope**: a foreign object is not found, so the cascade below never runs.
    let mut conditions: Vec<String> = vec!["deleted_at IS NONE".to_owned()];
    let mut scope_binds: Vec<(String, Value)> = Vec::new();
    crate::scope::apply(
        crate::scope::for_caller(&state, &caller).await?,
        &mut conditions,
        &mut scope_binds,
    );
    let existing_sql = format!(
        "SELECT * FROM type::record('object', $key) WHERE {};",
        conditions.join(" AND ")
    );
    let mut request = state.db.inner().query(existing_sql).bind(("key", key));
    for (name, value) in scope_binds {
        request = request.bind((name, value));
    }
    let mut existing = request.await?.check()?;
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
    let caller = crate::auth::require_caller(&state, &headers, "object.delete")?;
    let user_id = caller.user_id().to_owned();
    let keys = normalize_delete_ids(&body.ids)?;
    let emit_events = params.emit_events.unwrap_or(true);
    reject_out_of_scope_objects(&state, &caller, &keys).await?;

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
    // An upsert needs `create`: it may bring objects that do not exist yet.
    let caller = crate::auth::require_caller(&state, &headers, "object.create")?;
    let user_id = caller.user_id().to_owned();
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
            results.push(failed_at(prepared.len(), "entry without `type`"));
            prepared.push(None);
            continue;
        }
        let Value::Object(attributes) = &item.attributes else {
            results.push(failed_at(prepared.len(), "`attributes` must be an object"));
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

    // A scope covers the matched objects as well: the upsert is the import path and normally
    // runs as the machine (unrestricted), but a *user* with a scope must not have a foreign
    // object overwritten through a business key they happen to know. One query for the batch.
    let matched: Vec<String> = existing
        .iter()
        .flatten()
        .map(|id| {
            id.rsplit_once(':')
                .map_or_else(|| id.clone(), |(_, key)| key.to_owned())
        })
        .collect();
    reject_out_of_scope_objects(&state, &caller, &matched).await?;

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
    headers: HeaderMap,
    Path(id): Path<String>,
    Query(params): Query<GetObjectQuery>,
) -> Result<Json<Value>, ApiError> {
    let caller = crate::auth::require_caller(&state, &headers, "object.read")?;
    let key = parse_key(&id, TABLE)?;
    // Statements: LET(0), SELECT(1). A soft-deleted object reads as not found, and so does one
    // outside the caller's scope: the scope is one more condition on this read, which is cheaper
    // and more honest than loading the object and hiding it afterwards (`404` instead of foreign
    // data — the id does not become an oracle).
    let mut conditions: Vec<String> = vec!["deleted_at IS NONE".to_owned()];
    let mut binds: Vec<(String, Value)> = Vec::new();
    crate::scope::apply(
        crate::scope::for_caller(&state, &caller).await?,
        &mut conditions,
        &mut binds,
    );
    let sql = format!(
        "LET $obj = type::record('object', $key);\n        SELECT * FROM $obj WHERE {};",
        conditions.join(" AND ")
    );
    let mut request = state.db.inner().query(sql).bind(("key", key));
    for (name, value) in binds {
        request = request.bind((name, value));
    }
    let mut response = request.await?.check()?;
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
    State(state): State<AppState>,
    headers: HeaderMap,
    Query(params): Query<ObjectStatsQuery>,
) -> Result<Json<Value>, ApiError> {
    let caller = crate::auth::require_caller(&state, &headers, "object.read")?;
    let locale = params
        .locale
        .as_deref()
        .map(str::trim)
        .filter(|l| !l.is_empty());

    // The aggregation reads every object's attributes, the composition edges and the
    // class features (~1 s at demo scale in a release build). It is cached per
    // working language and bound to the data version that every write path bumps, so
    // a stale payload is never returned.
    // The payload depends on the caller's scope, so the cache key carries it (`scope_key`): a
    // scoped caller must never be answered with the counts of the unscoped view (rule R4).
    let scope = crate::scope::for_caller(&state, &caller).await?;
    let mut scoped_conditions: Vec<String> = vec!["deleted_at IS NONE".to_owned()];
    let mut scoped_binds: Vec<(String, Value)> = Vec::new();
    crate::scope::apply(scope.clone(), &mut scoped_conditions, &mut scoped_binds);
    let cache_key = format!("{}|{}", locale.unwrap_or_default(), scope_key(&scope));
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
    let counts_sql = format!(
        "SELECT type, status, count() AS n FROM object WHERE {} GROUP BY type, status;",
        scoped_conditions.join(" AND ")
    );
    let mut counts = state.db.inner().query(counts_sql);
    for (name, value) in &scoped_binds {
        counts = counts.bind((name.as_str(), value.clone()));
    }
    let mut response = counts.await?.check()?;
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
            let missing_sql = format!(
                "SELECT id, type, attributes FROM object \
                 WHERE {} AND type IN $types \
                 ORDER BY id LIMIT $limit START $start;",
                scoped_conditions.join(" AND ")
            );
            let mut pages = state
                .db
                .inner()
                .query(missing_sql)
                .bind(("types", candidates.clone()))
                .bind(("limit", STATS_PAGE))
                .bind(("start", offset));
            for (name, value) in &scoped_binds {
                pages = pages.bind((name.as_str(), value.clone()));
            }
            let mut response = pages.await?.check()?;
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
            let keys: Vec<&str> = items
                .iter()
                .filter_map(|(object_id, _, _)| object_id.split_once(':').map(|(_, key)| key))
                .collect();
            let composition = crate::inheritance::Composition::load(
                &state.cache,
                &state.db,
                keys.iter().copied(),
            )
            .await?;
            let completeness = crate::completeness::compute_batch(
                &state.cache,
                &state.db,
                &items,
                locale,
                &composition,
            )
            .await?;
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

/// The SurrealQL rendering of an object list's filters, plus the bindings they
/// need.
///
/// Shared by every endpoint that answers "which objects does this view contain"
/// (`GET /objects`, `/objects/count`, `/objects/attribute-usage`), so a filter
/// cannot mean two different things. Building it is the whole condition chain of
/// the grid: type, composition subtree, categorisation, collection, channel
/// release, id list and the `?where=` condition tree — and it is the only place
/// that knows a filter is **not** expressible in SQL.
struct ObjectFilter {
    /// Predicates, joined with `AND` by [`Self::where_clause`].
    conditions: Vec<String>,
    /// Bindings of `conditions` (`type`, `category_definition`, `f0`, …).
    binds: Vec<(String, Value)>,
    /// `?completeness_lt=` (applied in Rust, see [`Self::has_completeness_filter`]).
    completeness_lt: Option<i64>,
    /// `?missing_required=` (applied in Rust).
    missing_required: bool,
}

impl ObjectFilter {
    /// The ` WHERE …` fragment (empty when the view is unfiltered).
    fn where_clause(&self) -> String {
        if self.conditions.is_empty() {
            String::new()
        } else {
            format!(" WHERE {}", self.conditions.join(" AND "))
        }
    }

    /// Whether a completeness filter is active.
    ///
    /// Completeness is the one filter SurrealQL cannot express: it needs the
    /// required attribute names per type **and** the values inherited along
    /// `depends_on`, both of which live in the cache and are resolved in Rust
    /// (`crate::completeness::compute_batch`). Endpoints that need a total or a
    /// column set over such a view therefore scan the candidates
    /// ([`scan_completeness_filtered`]) instead of asking the database.
    fn has_completeness_filter(&self) -> bool {
        self.completeness_lt.is_some() || self.missing_required
    }

    /// The cache key of a completeness-filtered view.
    ///
    /// A view is its rows **in their order**, so the sort belongs to the key. The
    /// conditions already carry every id list as a literal; the remaining bindings
    /// follow, so two filters that differ only in a bound value cannot collide.
    fn view_key(&self, order: &str) -> String {
        let mut key = format!(
            "completeness-view:{order}|{}",
            self.conditions.join(" AND ")
        );
        for (name, value) in &self.binds {
            key.push('|');
            key.push_str(name);
            key.push('=');
            key.push_str(&value.to_string());
        }
        key
    }
}

/// Builds the filter of an object list request.
///
/// `caller` decides the **ABAC scope** (`crate::scope`): the scope of the caller's groups is
/// pushed into this very same condition chain, so the page, the total and the column sample
/// cannot disagree about which objects the view contains. An unrestricted caller — the
/// worker, or a user without groups — adds nothing at all.
async fn build_object_filter(
    state: &AppState,
    params: &ListObjects,
    caller: &Caller,
) -> Result<ObjectFilter, ApiError> {
    // Soft-deleted objects (and their cascade descendants) are hidden.
    let mut conditions: Vec<String> = vec!["deleted_at IS NONE".to_owned()];
    let mut binds: Vec<(String, Value)> = Vec::new();
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
        // The released objects are resolved in Rust and rendered as a **record list
        // literal**, not as `id IN (SELECT VALUE object_id FROM release WHERE …)`:
        // a subquery in an id filter is evaluated per candidate row once the
        // projection is `*` (see the `uncategorized` branch for the measurement).
        let keys = released_object_keys(state, params).await?;
        let literal =
            crate::api::keys::record_list_literal("object", keys.iter().map(String::as_str));
        conditions.push(format!("id IN {literal}"));
    }
    if params.uncategorized {
        // "Nicht kategorisiert" is per taxonomy: every taxonomy node type is
        // excluded (plus the universal root), so a classification hierarchy does
        // not count as a categorisation.
        //
        // The categorised keys are resolved in Rust and rendered as a **record list
        // literal**, like every other id-list filter. The obvious `id NOT IN
        // (SELECT VALUE in FROM reference WHERE …)` is catastrophic here: SurrealDB
        // evaluates that subquery per candidate row once the projection is `*`,
        // measured 2026-09-23 on the dev data (11 633 objects, 2 386 categorised) —
        // `SELECT * … id NOT IN (SELECT …) ORDER BY created_at DESC, id LIMIT 1`
        // took **159 s** against **1.4 s** for the same query with the literal
        // (0.41 s without the sort; `SELECT id` stayed at 0.36 s, so the projection
        // and the subquery interact).
        let definition = category_definition
            .as_deref()
            .unwrap_or(crate::api::references::PRIMARY_CATEGORY_DEFINITION);
        let categorised =
            crate::api::references::categorised_object_keys(&state.db, definition).await?;
        let literal =
            crate::api::keys::record_list_literal("object", categorised.iter().map(String::as_str));
        conditions.push(format!("id NOT IN {literal}"));
        // The taxonomy nodes themselves are not "objects to categorise", so their
        // types are excluded as well.
        let mut taxonomy_types = crate::api::object_types::taxonomy_types(&state.db).await?;
        taxonomy_types.push("root".to_owned());
        binds.push(("taxonomy_types".to_owned(), json!(taxonomy_types)));
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
    if let Some(raw) = &params.where_ {
        let mut advanced: crate::api::saved_searches::SavedSearchQuery = serde_json::from_str(raw)
            .map_err(|error| ApiError::bad_request(format!("invalid `where`: {error}")))?;
        let catalog = state.cache.search_catalog(&state.db).await?;
        crate::api::search_schema::normalize_query(&mut advanced);
        let errors = crate::api::search_schema::validate_query(&advanced, &catalog);
        if !errors.is_empty() {
            return Err(ApiError::bad_request(errors.join("; ")));
        }
        let rendered = crate::api::search_schema::render_query(&advanced, &catalog)?;
        conditions.extend(rendered.clauses);
        binds.extend(rendered.params);
    }

    if let Some(object_type) = &params.object_type {
        binds.push(("type".to_owned(), Value::String(object_type.clone())));
    }
    // The ABAC scope last, so it reads as what it is: one more condition on top of the
    // request's own filters. It becomes part of `conditions`, which means it is part of the
    // view cache key (`ObjectFilter::view_key`) — a scoped view can never reuse the rows of
    // an unscoped one. `apply(None)` (the normal case) changes nothing at all.
    crate::scope::apply(
        crate::scope::for_caller(state, caller).await?,
        &mut conditions,
        &mut binds,
    );
    Ok(ObjectFilter {
        conditions,
        binds,
        completeness_lt: params.completeness_lt,
        missing_required: params.missing_required,
    })
}
/// The bare keys of the objects released for a channel with the requested state
/// (`?released_for=`/`?release_status=`).
///
/// Resolved in Rust so the list query filters on a **record list literal** instead
/// of `id IN (SELECT VALUE object_id FROM release WHERE …)`: a subquery in an id
/// filter is evaluated per candidate row once the projection is `*` (measured
/// 2026-09-23, see the `uncategorized` branch — 159 s against 1.4 s).
async fn released_object_keys(
    state: &AppState,
    params: &ListObjects,
) -> Result<Vec<String>, ApiError> {
    let Some(released_for) = &params.released_for else {
        return Ok(Vec::new());
    };
    let status = params
        .release_status
        .as_deref()
        .map(str::trim)
        .filter(|status| !status.is_empty())
        .unwrap_or("published")
        .to_owned();
    let mut response = state
        .db
        .inner()
        .query("SELECT VALUE object_id FROM release WHERE channel = $channel AND status = $status;")
        .bind(("channel", crate::api::channels::channel_code(released_for)))
        .bind(("status", status))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    Ok(rows
        .iter()
        .filter_map(Value::as_str)
        .map(record_key_owned)
        .collect())
}

/// Applies an [`ObjectFilter`]'s bindings to a query.
fn bind_filter<'r>(
    mut query: surrealdb::method::Query<'r, surrealdb::engine::remote::ws::Client>,
    filter: &ObjectFilter,
) -> surrealdb::method::Query<'r, surrealdb::engine::remote::ws::Client> {
    for (key, value) in &filter.binds {
        query = query.bind((key.as_str(), value.clone()));
    }
    query
}

/// Appends a compiled scope to a query text (one ` AND ` per condition).
///
/// The counterpart of [`crate::scope::apply`] for the handlers that build their SQL as a string
/// rather than as a condition list. An unrestricted caller (`None`) changes nothing, so those
/// queries stay byte-identical to the ones they were before scopes existed.
pub(crate) fn push_scope(sql: &mut String, scope: &Option<crate::scope::Clause>) {
    if let Some(clause) = scope {
        for condition in &clause.conditions {
            sql.push_str(" AND ");
            sql.push_str(condition);
        }
    }
}

/// Binds a compiled scope's bindings on a query (the companion of [`push_scope`]).
pub(crate) fn bind_scope<'r>(
    mut query: surrealdb::method::Query<'r, surrealdb::engine::remote::ws::Client>,
    scope: &Option<crate::scope::Clause>,
) -> surrealdb::method::Query<'r, surrealdb::engine::remote::ws::Client> {
    if let Some(clause) = scope {
        for (name, value) in &clause.binds {
            query = query.bind((name.as_str(), value.clone()));
        }
    }
    query
}

/// Rejects a batch that names objects outside the caller's scope (`403`).
///
/// **One query for the whole batch** (rule R7: a bulk run costs one scope check, not one per
/// object) and **none** for an unrestricted caller — the worker, a user without groups and a
/// superadmin keep writing the whole catalogue. A refusal beats a partial run: deleting or
/// overwriting fewer objects than the request named is the answer that hides a mistake.
pub(crate) async fn reject_out_of_scope_objects(
    state: &AppState,
    caller: &crate::auth::Caller,
    keys: &[String],
) -> Result<(), ApiError> {
    let Some(clause) = crate::scope::for_caller(state, caller).await? else {
        return Ok(());
    };
    let unique: HashSet<&str> = keys.iter().map(String::as_str).collect();
    if unique.is_empty() {
        return Ok(());
    }
    let literal = crate::api::keys::record_list_literal(TABLE, unique.iter().copied());
    let mut conditions = clause.conditions;
    conditions.push(format!("id IN {literal}"));
    let sql = format!(
        "SELECT count() AS n FROM object WHERE {} GROUP ALL;",
        conditions.join(" AND ")
    );
    let mut request = state.db.inner().query(sql);
    for (name, value) in &clause.binds {
        request = request.bind((name.as_str(), value.clone()));
    }
    let mut response = request.await?.check()?;
    let rows: Vec<Value> = response.take(0)?;
    let visible = rows
        .first()
        .and_then(|row| row.get("n"))
        .and_then(Value::as_i64)
        .unwrap_or(0);
    if visible < unique.len() as i64 {
        return Err(ApiError::forbidden(format!(
            "{} of the {} named objects are outside your data scope",
            unique.len() as i64 - visible,
            unique.len()
        )));
    }
    Ok(())
}

/// The identity of a compiled scope, for caches that hold a **filtered** view.
///
/// Rule R4 of the plan: every cache that grows out of a filtered view carries the scope identity
/// in its key, or one caller is served another caller's rows. The conditions alone are not enough
/// — two different groups compile to the same bind *names* (`$scope_group_0_keys`) and differ only
/// in the bound value — so the values are part of the key. `None` (unrestricted) is `all`, which
/// is what an unscoped caller's key was before scopes existed.
pub(crate) fn scope_key(scope: &Option<crate::scope::Clause>) -> String {
    let Some(clause) = scope else {
        return "all".to_owned();
    };
    let mut key = clause.conditions.join(" AND ");
    for (name, value) in &clause.binds {
        key.push('|');
        key.push_str(name);
        key.push('=');
        key.push_str(&value.to_string());
    }
    key
}

/// The working locale of a list request (`?locale=`, trimmed, empty = unset).
fn working_locale(params: &ListObjects) -> Option<String> {
    params
        .locale
        .as_deref()
        .map(str::trim)
        .filter(|locale| !locale.is_empty())
        .map(str::to_owned)
}

/// The `ORDER BY` clause of an object list.
///
/// Without `?sort=` the default order applies (see [`DEFAULT_ORDER`]). A custom
/// sort is validated against the search catalog — the same rule as a saved
/// search's sort clause — and keeps the `id` tiebreaker, so a client can page
/// through a sorted view without skipping or repeating rows.
async fn render_order(state: &AppState, params: &ListObjects) -> Result<String, ApiError> {
    // `?order=` is validated even without `?sort=`: a direction that orders nothing
    // is a client bug, not a silent no-op.
    let direction = render_direction(params.order.as_deref())?;
    let Some(field) = params
        .sort
        .as_deref()
        .map(str::trim)
        .filter(|field| !field.is_empty())
    else {
        return Ok(DEFAULT_ORDER.to_owned());
    };
    // The catalog is cache-backed (`MetaCache::search_catalog`), so validating a
    // sort costs one lookup after the first request of a data version.
    let catalog = state.cache.search_catalog(&state.db).await?;
    let key = crate::api::search_schema::sanitize_field(field)?;
    let Some(declared) = catalog.field(&key) else {
        return Err(ApiError::bad_request(format!(
            "unknown sort field `{field}`"
        )));
    };
    if !declared.sortable {
        return Err(ApiError::bad_request(format!("`{field}` cannot be sorted")));
    }
    Ok(format!("{key} {direction}, id"))
}

/// The SQL direction of `?order=`: `asc` (default) or `desc`.
fn render_direction(order: Option<&str>) -> Result<&'static str, ApiError> {
    match order.map(str::trim).filter(|order| !order.is_empty()) {
        None => Ok("ASC"),
        Some(value) if value.eq_ignore_ascii_case("asc") => Ok("ASC"),
        Some(value) if value.eq_ignore_ascii_case("desc") => Ok("DESC"),
        Some(value) => Err(ApiError::bad_request(format!(
            "invalid `order` `{value}`: expected asc or desc"
        ))),
    }
}

/// The `(id, type, attributes)` triples the batched completeness and inheritance
/// computations take, built from a list of objects.
fn batch_items(objects: &[Value]) -> Vec<(String, String, Map<String, Value>)> {
    objects
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
        .collect()
}

/// The bare keys of a list of objects (`object:<key>` → `<key>`).
fn object_keys(objects: &[Value]) -> Vec<&str> {
    objects
        .iter()
        .filter_map(|object| object.get("id").and_then(Value::as_str))
        .filter_map(|object_id| object_id.split_once(':').map(|(_, key)| key))
        .collect()
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
    headers: HeaderMap,
    Query(params): Query<ListObjects>,
) -> Result<Json<Vec<Value>>, ApiError> {
    let caller = auth::require_caller(&state, &headers, "object.read")?;
    let limit = params.limit.clamp(1, 1000) as i64;
    let start = params.start as i64;
    let filter = build_object_filter(&state, &params, &caller).await?;
    let completeness_filter = filter.has_completeness_filter();
    let locale = working_locale(&params);
    let needs_inheritance = params.resolve_inheritance == Some(true);
    let order = render_order(&state, &params).await?;

    // The channel releases are fetched **for the whole page in one query** after
    // the objects (see below), not as a correlated subquery per row: SurrealDB
    // evaluates `(SELECT … WHERE object_id = $parent.id)` once per candidate row
    // and costs ~11 ms per row (measured 2026-09-22: 18.1 s for a 1000-row page
    // against 0.29 s for one bulk query on the same data). `releases=true` is the
    // grid's release column, so this is on the hot read path.
    let mut objects: Vec<Value> = if completeness_filter {
        // A completeness-filtered view is **not** paged by SurrealQL: only the walk in
        // `completeness_view_keys` can tell its members, and that walk returns the
        // whole view in the requested order (cached). The page is therefore
        // `[start, start + limit)` of those keys and its rows are fetched **by id** —
        // the answer stays complete (`status`, timestamps, `version`) without
        // transferring the payload of every candidate.
        let keys = completeness_view_keys(&state, &filter, locale.as_deref(), &order).await?;
        let page_keys: Vec<&str> = keys
            .iter()
            .skip(start.max(0) as usize)
            .take(limit.max(0) as usize)
            .map(String::as_str)
            .collect();
        if page_keys.is_empty() {
            Vec::new()
        } else {
            // The id literal drives the id index (1000 keys → 0,18 s,
            // `keys::record_list_literal`), and the order is the requested one, so
            // the page comes back exactly as the walk ordered it.
            let literal =
                crate::api::keys::record_list_literal("object", page_keys.iter().copied());
            let sql = format!("SELECT * FROM object WHERE id IN {literal} ORDER BY {order};");
            let mut response = state.db.inner().query(sql).await?.check()?;
            let rows: Vec<Value> = response.take(0)?;
            rows
        }
    } else {
        let sql = format!(
            "SELECT * FROM object{} ORDER BY {order} LIMIT $limit START $start",
            filter.where_clause()
        );
        let mut response = bind_filter(state.db.inner().query(sql), &filter)
            .bind(("limit", limit))
            .bind(("start", start))
            .await?
            .check()?;
        response.take(0)?
    };

    // Channel releases of the page: `releases: [{channel, status}]` per object, in
    // one query for the whole page (see the comment above `render_order`).
    if params.releases && !objects.is_empty() {
        attach_releases(&state.db, &mut objects).await?;
    }

    // Completeness (with inheritance) is only computed when the caller needs it:
    // for the `completeness` field of the response and/or for the completeness
    // filters. `locale` may be absent — the filter then uses the default values.
    //
    // **One batch for the whole page**, not one computation per row: the per-row
    // variant issued ~6 queries per object (~25 ms with a classification standard
    // installed), so a 1000-row page spent 18 s in it and the `missing_required`
    // filter made the grid unusable. `compute_batch` resolves the required names per
    // type (cached) and walks the chains in memory — the same result, without the
    // round-trips.
    //
    // Completeness and inheritance describe the **same** page, so the composition
    // (the `depends_on` map plus the ancestors' attributes) is prepared once and
    // handed to both — loading it twice cost two extra queries per page.
    let needs_completeness = locale.is_some() || completeness_filter;
    if (needs_completeness || needs_inheritance) && !objects.is_empty() {
        let keys = object_keys(&objects);
        let composition_started = std::time::Instant::now();
        let composition =
            crate::inheritance::Composition::load(&state.cache, &state.db, keys.iter().copied())
                .await?;
        tracing::debug!(
            keys = keys.len(),
            ms = composition_started.elapsed().as_millis() as u64,
            "composition loaded"
        );

        if needs_completeness {
            let items = batch_items(&objects);
            let results = crate::completeness::compute_batch(
                &state.cache,
                &state.db,
                &items,
                locale.as_deref(),
                &composition,
            )
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
                        filter.completeness_lt,
                        filter.missing_required,
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

        // Fill inherited values and mark each object's inherited attributes with
        // their source (in-memory walk over the shared composition).
        if needs_inheritance && !objects.is_empty() {
            let mut items = batch_items(&objects);
            let inherited = crate::inheritance::resolve_inheritance_batch(
                &state.cache,
                &state.db,
                locale.as_deref(),
                &mut items,
                &composition,
            )
            .await?;
            for (index, object) in objects.iter_mut().enumerate() {
                let (id, _, attributes) = &items[index];
                object["attributes"] = Value::Object(attributes.clone());
                let sources = inherited
                    .get(id)
                    .cloned()
                    .unwrap_or_else(|| Value::Object(serde_json::Map::new()));
                object["inherited"] = sources;
            }
        }
    }

    Ok(Json(objects))
}

/// Counts the objects a view contains.
///
/// Takes exactly the filters of `GET /objects` (the paging parameters `limit`,
/// `start`, `sort`, `order` and `sample` are ignored), so a page and its total
/// can never disagree. Without a completeness filter the answer is one grouped
/// count in SurrealQL; with one (`?missing_required`/`?completeness_lt`) the
/// candidates are scanned like `/objects/stats` does, because that filter needs
/// the inherited required attributes (see
/// [`ObjectFilter::has_completeness_filter`]).
#[utoipa::path(
    get,
    path = "/objects/count",
    params(ListObjects),
    responses(
        (status = 200, description = "Number of matching objects", body = Value),
        (status = 400, description = "Invalid filter", body = Value)
    )
)]
pub async fn count_objects(
    State(state): State<AppState>,
    headers: HeaderMap,
    Query(params): Query<ListObjects>,
) -> Result<Json<Value>, ApiError> {
    let caller = auth::require_caller(&state, &headers, "object.read")?;
    let filter = build_object_filter(&state, &params, &caller).await?;
    let count = if filter.has_completeness_filter() {
        // `order` is validated like on the list and enters the view key, so a count
        // and a page of the same view share one walk.
        let order = render_order(&state, &params).await?;
        completeness_view_keys(&state, &filter, working_locale(&params).as_deref(), &order)
            .await?
            .len() as i64
    } else {
        let sql = format!(
            "SELECT count() AS n FROM object{} GROUP ALL;",
            filter.where_clause()
        );
        let mut response = bind_filter(state.db.inner().query(sql), &filter)
            .await?
            .check()?;
        let rows: Vec<Value> = response.take(0)?;
        rows.first()
            .and_then(|row| row.get("n"))
            .and_then(Value::as_i64)
            .unwrap_or(0)
    };
    Ok(Json(json!({ "count": count })))
}

/// The attribute names a view carries a value for.
///
/// The grid derives its columns from the **page it loaded**, which is stable
/// while the page is: with paging (`grid_query.loaded_chunk`) the loaded rows grow
/// and a column of an imported classification standard — offered once data for it
/// exists (`reference_data.py`) — would appear mid-scroll. This endpoint answers
/// "which attributes carry data in this view" from the core instead, over the
/// first `?sample=` objects of the view (same order as the list), so the column
/// set is decided once and stays.
///
/// `names` are the attribute names with a non-empty value in at least one of the
/// inspected objects — the same reading as the grid's `present_attribute_names`
/// (`reference_data.has_value`), `sampled` is how many objects were inspected
/// (less than `sample` means the answer covers the whole view).
#[utoipa::path(
    get,
    path = "/objects/attribute-usage",
    params(ListObjects),
    responses(
        (status = 200, description = "Attribute names in use", body = Value),
        (status = 400, description = "Invalid filter", body = Value)
    )
)]
pub async fn attribute_usage(
    State(state): State<AppState>,
    headers: HeaderMap,
    Query(params): Query<ListObjects>,
) -> Result<Json<Value>, ApiError> {
    let caller = auth::require_caller(&state, &headers, "object.read")?;
    let filter = build_object_filter(&state, &params, &caller).await?;
    let sample = params.sample.clamp(1, MAX_SAMPLE) as i64;
    let mut names: BTreeSet<String> = BTreeSet::new();
    let mut sampled: i64 = 0;

    if filter.has_completeness_filter() {
        // The members of such a view come from the cached walk; the sample is then read
        // by id — the names of the view's first `sample` objects, without walking the
        // candidates again for every request.
        let order = render_order(&state, &params).await?;
        let keys =
            completeness_view_keys(&state, &filter, working_locale(&params).as_deref(), &order)
                .await?;
        let sample_keys: Vec<&str> = keys
            .iter()
            .take(sample as usize)
            .map(String::as_str)
            .collect();
        if !sample_keys.is_empty() {
            let literal =
                crate::api::keys::record_list_literal("object", sample_keys.iter().copied());
            let sql = format!("SELECT attributes FROM object WHERE id IN {literal};");
            let mut response = state.db.inner().query(sql).await?.check()?;
            let rows: Vec<Value> = response.take(0)?;
            for row in &rows {
                collect_attribute_names(row.get("attributes"), &mut names);
                sampled += 1;
            }
        }
    } else {
        let sql = format!(
            "SELECT attributes, created_at, id FROM object{} ORDER BY {DEFAULT_ORDER} LIMIT $sample;",
            filter.where_clause()
        );
        let mut response = bind_filter(state.db.inner().query(sql), &filter)
            .bind(("sample", sample))
            .await?
            .check()?;
        let rows: Vec<Value> = response.take(0)?;
        for row in &rows {
            collect_attribute_names(row.get("attributes"), &mut names);
            sampled += 1;
        }
    }

    Ok(Json(json!({ "names": names, "sampled": sampled })))
}

/// Collects the attribute names an `attributes` object carries a value for.
///
/// "Carries a value" is the reading the grid uses to decide whether a column of an
/// imported classification standard has data (`reference_data.has_value` in the
/// frontend): an absent key, `null`, an empty string, an empty list and a language
/// map without a single non-empty entry do not count — a key that only ever holds
/// `null` would offer a column that can never show anything.
fn collect_attribute_names(attributes: Option<&Value>, names: &mut BTreeSet<String>) {
    let Some(Value::Object(attributes)) = attributes else {
        return;
    };
    for (name, value) in attributes {
        if carries_value(value) {
            names.insert(name.clone());
        }
    }
}

/// Whether a stored attribute value counts as present (see
/// [`collect_attribute_names`]).
fn carries_value(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::String(text) => !text.trim().is_empty(),
        Value::Array(items) => !items.is_empty(),
        Value::Object(entries) => entries.values().any(carries_value),
        _ => true,
    }
}

/// The ordered keys of a **completeness-filtered** view, cached per filter and sort.
///
/// Completeness is the one filter SurrealQL cannot express: it needs the required
/// attribute names per type **and** the values inherited along `depends_on`, both of
/// which are resolved in Rust (`crate::completeness::compute_batch`). The members of
/// such a view are therefore only known after walking every candidate — which is why
/// the **keys** are what gets cached (`MetaCache::store_view_keys`): a page, the total
/// and the column set of the same view share one walk, and the list fetches its page
/// **by id** afterwards.
///
/// The walk is narrowed to the types that can be incomplete at all
/// (`completeness::incomplete_candidates`); every other type is complete by
/// definition, so the filter would drop its objects anyway. `order` must be the order
/// the view is read in — it is part of the view.
async fn completeness_view_keys(
    state: &AppState,
    filter: &ObjectFilter,
    locale: Option<&str>,
    order: &str,
) -> Result<Arc<Vec<String>>, ApiError> {
    let key = filter.view_key(order);
    let version = state.cache.stats_version();
    if let Some(keys) = state.cache.cached_view_keys(&key, version).await {
        return Ok(keys);
    }
    // Single-flight like `/objects/stats`: a page, its total and its column set
    // arrive together and must not walk the candidates three times (the walk of the
    // dev data's "nur unvollständige" view costs 3 s).
    let lock = state.cache.stats_lock(&key);
    let _single_flight = lock.lock().await;
    let version = state.cache.stats_version();
    if let Some(keys) = state.cache.cached_view_keys(&key, version).await {
        return Ok(keys);
    }

    let mut keys: Vec<String> = Vec::new();
    let candidates = crate::completeness::incomplete_candidates(&state.cache, &state.db).await?;
    if !candidates.is_empty() {
        let mut conditions = filter.conditions.clone();
        conditions.push("type IN $scan_types".to_owned());
        // Lean rows: `id`, `type` and `attributes` are all the filter needs, and
        // `created_at` carries the order idiom. A page of 10 000 of them stays in the
        // megabytes where the full objects would be in the tens (`/objects/stats`
        // reads the same columns).
        let sql = format!(
            "SELECT id, type, attributes, created_at FROM object WHERE {} ORDER BY {order} \
             LIMIT $limit START $start;",
            conditions.join(" AND ")
        );
        let mut offset: i64 = 0;
        loop {
            let mut response = bind_filter(state.db.inner().query(&sql), filter)
                .bind(("scan_types", json!(candidates)))
                .bind(("limit", SCAN_PAGE))
                .bind(("start", offset))
                .await?
                .check()?;
            let page: Vec<Value> = response.take(0)?;
            if page.is_empty() {
                break;
            }
            let scanned = page.len();
            let page_keys = object_keys(&page);
            let composition = crate::inheritance::Composition::load(
                &state.cache,
                &state.db,
                page_keys.iter().copied(),
            )
            .await?;
            let results = crate::completeness::compute_batch(
                &state.cache,
                &state.db,
                &batch_items(&page),
                locale,
                &composition,
            )
            .await?;
            for object in &page {
                let object_id = object
                    .get("id")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_owned();
                if object_id.is_empty() {
                    continue;
                }
                let passes = match results.get(&object_id) {
                    None => true,
                    Some(result) => passes_completeness_filter(
                        result,
                        filter.completeness_lt,
                        filter.missing_required,
                    ),
                };
                if passes {
                    keys.push(object_id);
                }
            }
            if scanned < SCAN_PAGE as usize {
                break;
            }
            offset += scanned as i64;
        }
    }
    // Stored with the version read **before** the walk: a write that lands while the
    // walk runs bumps the version, so the entry is stale at once and never served.
    let keys = Arc::new(keys);
    state
        .cache
        .store_view_keys(&key, version, keys.clone())
        .await;
    Ok(keys)
}

/// Attaches the channel releases of the page's objects as
/// `releases: [{channel, status}]` (sorted by channel), in **one** query.
///
/// Keys that cannot be a record (`keys::record_list_literal` drops them) cannot
/// have releases, so they are simply absent from the answer.
async fn attach_releases(db: &crate::db::Db, objects: &mut [Value]) -> Result<(), ApiError> {
    let keys: Vec<String> = objects
        .iter()
        .filter_map(|object| object.get("id").and_then(Value::as_str))
        .map(record_key_owned)
        .collect();
    if keys.is_empty() {
        return Ok(());
    }
    let sql = format!(
        "SELECT object_id, channel, status FROM release WHERE object_id IN {};",
        crate::api::keys::record_list_literal("object", keys.iter().map(String::as_str))
    );
    let mut response = db.inner().query(sql).await?.check()?;
    let rows: Vec<Value> = response.take(0)?;
    attach_release_entries(objects, rows);
    Ok(())
}

/// Groups `release` rows (`{object_id, channel, status}`) by object and writes
/// `releases: [{channel, status}]` into each object — sorted by channel, an empty
/// array when an object has no release (the shape the correlated subquery
/// answered with). Pure, so the shape is unit-tested.
fn attach_release_entries(objects: &mut [Value], rows: Vec<Value>) {
    let mut by_object: std::collections::HashMap<String, Vec<Value>> =
        std::collections::HashMap::new();
    for row in rows {
        let Some(object_id) = row.get("object_id").and_then(Value::as_str) else {
            continue;
        };
        let entry = json!({
            "channel": row.get("channel").cloned().unwrap_or(Value::Null),
            "status": row.get("status").cloned().unwrap_or(Value::Null),
        });
        by_object
            .entry(record_key_owned(object_id))
            .or_default()
            .push(entry);
    }
    for object in objects.iter_mut() {
        let key = object
            .get("id")
            .and_then(Value::as_str)
            .map(record_key_owned)
            .unwrap_or_default();
        let mut releases = by_object.remove(&key).unwrap_or_default();
        releases.sort_by(|left, right| {
            let left = left.get("channel").and_then(Value::as_str).unwrap_or("");
            let right = right.get("channel").and_then(Value::as_str).unwrap_or("");
            left.cmp(right)
        });
        object["releases"] = Value::Array(releases);
    }
}

/// The bare key of a record id (`object:<key>` → `<key>`); the table prefix is
/// dropped, a value without one is returned unchanged.
fn record_key_owned(record_id: &str) -> String {
    record_id
        .split_once(':')
        .map(|(_, key)| key)
        .unwrap_or(record_id)
        .to_owned()
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
    headers: HeaderMap,
    Query(params): Query<BySourceParams>,
) -> Result<Json<Value>, ApiError> {
    let caller = crate::auth::require_caller(&state, &headers, "object.read")?;
    let source = params.source.trim();
    let source_id = params.source_id.trim();
    if source_id.is_empty() {
        return Err(ApiError::bad_request("`source_id` must not be empty"));
    }
    let mut conditions: Vec<String> = vec![
        "source_id = $source_id".to_owned(),
        "source = $source".to_owned(),
        "deleted_at IS NONE".to_owned(),
    ];
    let mut scope_binds: Vec<(String, Value)> = Vec::new();
    crate::scope::apply(
        crate::scope::for_caller(&state, &caller).await?,
        &mut conditions,
        &mut scope_binds,
    );
    let sql = format!(
        "SELECT * FROM object WHERE {} LIMIT 1;",
        conditions.join(" AND ")
    );
    let mut request = state
        .db
        .inner()
        .query(sql)
        .bind(("source_id", source_id.to_owned()))
        .bind(("source", source.to_owned()));
    for (name, value) in scope_binds {
        request = request.bind((name, value));
    }
    let mut response = request.await?.check()?;
    let rows: Vec<Value> = response.take(0)?;
    let object = rows
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::not_found("no object matches the given source reference"))?;
    Ok(Json(object))
}

/// The type-level edges of the composition tree (`GET /objects/composition-types`).
///
/// Returns `{root_type, edges: [{parent, child, count}]}`: for every pair of object
/// types that appears along a `depends_on` edge, how many objects of `child` hang
/// under an object of `parent` (counted per pair). `root_type` is the type of the
/// universal root, i.e. the top of the tree.
///
/// A consumer needs this to know whether a type is **reachable** in the composition
/// tree at all: the picker walks the tree from the universal root, and the
/// composition scope renders the product structure **without** the taxonomy nodes —
/// so a product, which hangs below a part group (a taxonomy node), can never be
/// selected there, while a vehicle generation (below model, below make) can
/// (2026-09-17). One grouped query over the edge table, measured **~190 ms** warm on
/// the demo instance (12 197 composition edges out of 28 624) — the picker asks for
/// it on every open, so the payload is cached bound to the data version.
#[utoipa::path(
    get,
    path = "/objects/composition-types",
    responses(
        (status = 200, description = "Composition type edges", body = Value),
        (status = 401, description = "Unauthorized", body = Value)
    )
)]
pub async fn composition_types(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
) -> Result<Json<Value>, ApiError> {
    claims.require("object.read")?;
    let version = state.cache.stats_version();
    if let Some(cached) = state.cache.cached_composition_types(version).await {
        return Ok(Json((*cached).clone()));
    }

    // `in` is the child, `out` the parent of a composition edge; deleted objects
    // have their edges removed, the filter only guards a half-finished cascade (it
    // dereferences both ends, which is most of the query's cost).
    let mut response = state
        .db
        .inner()
        .query(
            "SELECT out.type AS parent, in.type AS child, count() AS count FROM reference \
             WHERE definition_id = $definition AND in.deleted_at IS NONE \
             AND out.deleted_at IS NONE GROUP BY parent, child;",
        )
        .bind((
            "definition",
            crate::api::references::COMPOSITION_DEFINITION.to_owned(),
        ))
        .await?
        .check()?;
    let edges: Vec<Value> = response.take(0)?;

    let mut response = state
        .db
        .inner()
        .query("SELECT type FROM object:object_root;")
        .await?
        .check()?;
    let root: Vec<Value> = response.take(0)?;
    let root_type = root
        .first()
        .and_then(|row| row.get("type"))
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();

    let payload = Arc::new(json!({ "root_type": root_type, "edges": edges }));
    state
        .cache
        .store_composition_types(version, payload.clone())
        .await;
    Ok(Json((*payload).clone()))
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
    /// Also match objects whose **label** (`name`, the conventional label of every
    /// type) starts with the term — the type-ahead behaviour a picker needs:
    /// `brake clean` finds `Brake cleaner` (2026-09-17).
    ///
    /// The full-text index matches whole tokens only, so typing a **partial word**
    /// found nothing (`attributes @@ 'brake clean'` → 0 hits, `… @@ 'brake cleaner'`
    /// → 20 on the dev DB) — measured, and until now a documented open gap: the
    /// reference picker, the grid's filter bar and the search form all searched the
    /// full text only. Prefix matching cannot use the index (it compares lowercased
    /// values), so this is an extra scan **per language of [`prefix_languages`]**,
    /// narrowed by `types`: measured **~250 ms** for a miss (dev core, `LIMIT 20`,
    /// 16 926 products, idle machine). Callers debounce it.
    #[serde(default)]
    pub label_prefix: bool,
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

/// Whether a language code may be interpolated into a SurrealDB **field path**.
///
/// The language is part of `attributes.name.<language>`, which SurrealDB cannot
/// bind as a parameter. The codes come from the fallback chain (data), so they are
/// validated instead of trusted: only characters that are safe inside a
/// backtick-quoted path segment are used, everything else is skipped.
fn safe_language_code(code: &str) -> bool {
    !code.is_empty()
        && code
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
}

/// The languages a prefix scan has to compare, in resolution order.
///
/// The **locale itself** comes first: the fallback chain carries only its
/// *ancestors* and always ends at `root` (measured: `en` → `["root"]`,
/// `de-AT` → `["de", "root"]`), so taking the chain alone would leave no language to
/// scan and a type-ahead would stay silent — exactly the defect this path fixes.
/// `root` stays out of the list: it is the chain's pseudo language and carries no
/// names on this instance (measured: 0 objects with `attributes.name.root` vs 41 007
/// with `attributes.name.en`), so scanning it would double the cost of every search
/// for nothing. Codes that are unsafe as a path segment are skipped as well.
fn prefix_languages(locale: Option<&str>, chain: &[String]) -> Vec<String> {
    let mut languages: Vec<String> = Vec::new();
    for code in locale.into_iter().chain(chain.iter().map(String::as_str)) {
        if code == "root" || !safe_language_code(code) || languages.iter().any(|seen| seen == code)
        {
            continue;
        }
        languages.push(code.to_owned());
    }
    languages
}

/// Objects whose **label** starts with a term, per language of the fallback chain.
///
/// The counterpart of [`code_prefix_hits`] for names: `attributes @@ $q` matches
/// whole tokens, so a partial word finds nothing (`'brake clean'` → 0 hits while
/// `'brake cleaner'` → 20 on the dev DB). The comparison is `string::starts_with`
/// on the **lowercased** value, which no index can serve — the query is therefore
/// narrowed by `types` and by the languages of [`prefix_languages`]. A **scalar**
/// `name` (an OEM number, `"5Q0 936 304 W"`) is matched as well: those objects carry
/// no per-language value at all, so the per-language clause alone left them
/// unfindable by a partial word. The cast `<string>` keeps the object-valued names
/// from failing the comparison (their JSON text cannot start with a typed term).
/// Like the code path it is a **separate** query merged into the hits, because a
/// full-text condition must not be combined via `OR`.
///
/// The guard `attributes.name.<language> != NONE` is required: `string::starts_with`
/// fails with a type error on objects that do not carry that language (the same
/// reason the code path compares instead of casting).
async fn label_prefix_hits(
    state: &AppState,
    query: &str,
    types: &[String],
    languages: &[String],
    limit: i64,
    scope: &Option<crate::scope::Clause>,
) -> Result<Vec<Value>, ApiError> {
    let term = query.to_lowercase();
    if term.is_empty() {
        return Ok(Vec::new());
    }
    let mut hits: Vec<Value> = Vec::new();
    let mut seen: HashSet<String> = HashSet::new();
    for language in languages {
        // The language code is interpolated into the field path (SurrealDB cannot
        // bind one): only codes that are safe as a path segment are used, and the
        // segment is backtick-quoted so `de-AT` stays one key instead of becoming a
        // subtraction.
        if !safe_language_code(language) {
            continue;
        }
        let field = format!("attributes.name.`{language}`");
        let mut sql = format!(
            "SELECT * FROM object WHERE deleted_at IS NONE \
             AND (({field} != NONE \
             AND string::starts_with(string::lowercase(<string>{field}), $term)) \
             OR string::starts_with(string::lowercase(<string>attributes.name), $term))"
        );
        if !types.is_empty() {
            sql.push_str(" AND type IN $types");
        }
        push_scope(&mut sql, scope);
        // Sorted by the label so the same query yields the same list; the sort costs
        // nothing measurable next to the scan (median 423 ms sorted vs 596 ms
        // unsorted over three runs, `LIMIT 20`, dev core — the measurement that
        // suggested otherwise compared a hit list with a miss list and paid the
        // per-hit completeness of the hits).
        sql.push_str(" ORDER BY attributes.name LIMIT $limit");
        let mut request = state
            .db
            .inner()
            .query(sql)
            .bind(("term", term.clone()))
            .bind(("limit", limit));
        if !types.is_empty() {
            request = request.bind(("types", types.to_vec()));
        }
        let mut response = bind_scope(request, scope).await?.check()?;
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
    scope: &Option<crate::scope::Clause>,
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
        push_scope(&mut sql, scope);
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
        let mut response = bind_scope(request, scope)
            .bind(("limit", limit))
            .await?
            .check()?;
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

/// Appends the objects of `extra` that are not in `objects` yet (dedupe by id).
///
/// The picker merges up to three queries (full text, code prefix, label prefix);
/// the id set is small (one `limit` at most), so a linear scan per appended object
/// is cheaper than building a `HashSet` for every merge. An object without an id
/// (never returned by these queries) is appended unchanged.
fn append_unique(objects: &mut Vec<Value>, extra: Vec<Value>) {
    for object in extra {
        let key = object
            .get("id")
            .and_then(Value::as_str)
            .filter(|key| !key.is_empty());
        if let Some(key) = key
            && objects
                .iter()
                .any(|existing| existing.get("id").and_then(Value::as_str) == Some(key))
        {
            continue;
        }
        objects.push(object);
    }
}

/// Searches for objects by label to pick a reference target.
///
/// Returns compact hits (`id`, `type`, `label`, `status`); the label is the
/// resolved multilingual `name` for `?locale=`, falling back to the `code` (a
/// taxonomy class has no `name`). With `?locale=` each hit also carries a
/// `completeness` value for that language, so a picker can show whether the
/// target still misses required attributes (`?completeness=false` skips it — the
/// tree's search lists many hits and shows no badge). `?code_prefix=true`
/// additionally matches a partial `code` (`EC0123`), `?label_prefix=true` a
/// partial **label word** (`brake clean` → `Brake cleaner`, see
/// [`label_prefix_hits`]). Used by the reference and relation pickers, the grid's
/// filter bar, the search form and the taxonomy tree's search.
///
/// The merge order is the relevance order: the relevance-ranked full-text hits
/// come first, then the prefix matches. A prefix scan only runs while the hits do
/// not fill `limit` — hits appended behind a full `limit` would be truncated away
/// (and the scan is the expensive part, ~0.25 s worst case).
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
    headers: HeaderMap,
    Query(params): Query<ObjectPickerParams>,
) -> Result<Json<Vec<Value>>, ApiError> {
    let caller = crate::auth::require_caller(&state, &headers, "reference.read")?;
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

    // The label language chain is needed before the queries: the prefix path walks
    // the chain's languages (the label of a hit is resolved with the same chain).
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

    // The scope of the caller runs through all three queries of the picker: offering an object
    // the caller may not read would be the first step of an edit that the core refuses anyway.
    let scope = crate::scope::for_caller(&state, &caller).await?;
    let mut sql = String::from(
        "SELECT *, search::score(1) AS score FROM object \
         WHERE attributes @@ $q AND deleted_at IS NONE",
    );
    if !types.is_empty() {
        sql.push_str(" AND type IN $types");
    }
    let mut scope_conditions: Vec<String> = Vec::new();
    let mut scope_binds: Vec<(String, Value)> = Vec::new();
    crate::scope::apply(scope.clone(), &mut scope_conditions, &mut scope_binds);
    for condition in &scope_conditions {
        sql.push_str(" AND ");
        sql.push_str(condition);
    }
    sql.push_str(" ORDER BY score DESC LIMIT $limit");
    let mut request = state.db.inner().query(sql).bind(("q", query.to_owned()));
    if !types.is_empty() {
        request = request.bind(("types", types.clone()));
    }
    for (name, value) in scope_binds {
        request = request.bind((name, value));
    }
    let mut response = request.bind(("limit", limit)).await?.check()?;
    let text_objects: Vec<Value> = response.take(0)?;
    let mut objects: Vec<Value> = Vec::new();
    append_unique(&mut objects, text_objects);

    // Prefix matches last (they rank below a real token match) and only while hits
    // are missing: each is its own query, because a full-text match must not share
    // an `OR` with another condition (see `code_prefix_hits`).
    let full = usize::try_from(limit).unwrap_or(usize::MAX);
    if objects.len() < full && params.code_prefix {
        let hits = code_prefix_hits(&state, query, &types, limit, &scope).await?;
        append_unique(&mut objects, hits);
    }
    if objects.len() < full && params.label_prefix {
        let languages = prefix_languages(
            locale.as_deref(),
            chain.as_deref().map(Vec::as_slice).unwrap_or_default(),
        );
        let hits = label_prefix_hits(&state, query, &types, &languages, limit, &scope).await?;
        append_unique(&mut objects, hits);
    }
    objects.truncate(full);

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
    let caller = crate::auth::require_caller(state, headers, "object.update")?;
    let user_id = caller.user_id().to_owned();

    // Load the current object: type for uniqueness checks, status for audit. The scope is part
    // of the load, so an object outside it is not found and the write below never runs.
    let mut conditions: Vec<String> = Vec::new();
    let mut scope_binds: Vec<(String, Value)> = Vec::new();
    crate::scope::apply(
        crate::scope::for_caller(state, &caller).await?,
        &mut conditions,
        &mut scope_binds,
    );
    let current_sql = if conditions.is_empty() {
        "LET $obj = type::record('object', $key);\n        SELECT * FROM $obj;\n    ".to_owned()
    } else {
        format!(
            "LET $obj = type::record('object', $key);\n        SELECT * FROM $obj WHERE {};\n    ",
            conditions.join(" AND ")
        )
    };
    let mut request = state.db.inner().query(current_sql).bind(("key", key));
    for (name, value) in scope_binds {
        request = request.bind((name, value));
    }
    let mut current_response = request.await?.check()?;
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
    claims.require("object.update")?;
    // The run executes in the worker (a machine call, unrestricted), so the scope has to be
    // checked here, at queue time: a selection that names foreign objects is refused as a whole.
    let caller = crate::auth::Caller::User(Box::new(claims.clone()));
    let selected: Vec<String> = body
        .object_ids
        .iter()
        .map(|id| {
            id.rsplit_once(':')
                .map_or_else(|| id.clone(), |(_, key)| key.to_owned())
        })
        .collect();
    reject_out_of_scope_objects(&state, &caller, &selected).await?;
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
    // The worker's API key or a session that may change objects: recomputing writes object
    // values, so it is `object.update` — and the hand-rolled "API key or admin JWT" block that
    // stood here was exactly the kind of duplicate a catalog check replaces.
    let caller = crate::auth::require_caller(&state, &headers, "object.update")?;

    let key = parse_key(&id, TABLE)?;
    let Value::Object(values) = body.values else {
        return Err(ApiError::bad_request("`values` must be an object"));
    };
    // Enforce: every value must be declared as `computed: true`.
    let computed = crate::rules::resolve_computed(&state.db, &values).await?;
    if computed.is_empty() {
        return Err(ApiError::bad_request("`values` must not be empty"));
    }

    // Load current attributes and merge the computed values — inside the caller's scope, so a
    // foreign object is not found and the write below never runs.
    let mut conditions: Vec<String> = Vec::new();
    let mut scope_binds: Vec<(String, Value)> = Vec::new();
    crate::scope::apply(
        crate::scope::for_caller(&state, &caller).await?,
        &mut conditions,
        &mut scope_binds,
    );
    let current_sql = if conditions.is_empty() {
        "LET $obj = type::record('object', $key);\n        SELECT * FROM $obj;\n    ".to_owned()
    } else {
        format!(
            "LET $obj = type::record('object', $key);\n        SELECT * FROM $obj WHERE {};\n    ",
            conditions.join(" AND ")
        )
    };
    let mut request = state.db.inner().query(current_sql).bind(("key", key));
    for (name, value) in scope_binds {
        request = request.bind((name, value));
    }
    let mut current_response = request.await?.check()?;
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
    fn releases_are_grouped_per_object_and_sorted_by_channel() {
        let mut objects = vec![
            serde_json::json!({ "id": "object:one", "type": "product" }),
            serde_json::json!({ "id": "object:two", "type": "product" }),
        ];
        let rows = vec![
            serde_json::json!({ "object_id": "object:one", "channel": "webshop", "status": "published" }),
            serde_json::json!({ "object_id": "object:one", "channel": "print", "status": "draft" }),
            // An object the page does not contain (a stale row) is ignored, and a
            // row without `object_id` cannot be attributed.
            serde_json::json!({ "object_id": "object:other", "channel": "webshop", "status": "published" }),
            serde_json::json!({ "channel": "webshop", "status": "published" }),
        ];
        attach_release_entries(&mut objects, rows);

        assert_eq!(
            objects[0]["releases"],
            serde_json::json!([
                { "channel": "print", "status": "draft" },
                { "channel": "webshop", "status": "published" },
            ])
        );
        // No release: the field is an empty array, exactly like the subquery's.
        assert_eq!(objects[1]["releases"], serde_json::json!([]));
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
    fn prefix_languages_start_with_the_locale_itself() {
        // The measured chains: the locale is *not* part of them, so it must be
        // added — otherwise a type-ahead under `en` would scan nothing at all.
        assert_eq!(
            prefix_languages(Some("en"), &["root".to_owned()]),
            vec!["en"]
        );
        assert_eq!(
            prefix_languages(Some("de-AT"), &["de".to_owned(), "root".to_owned()]),
            vec!["de-AT", "de"]
        );
        // No locale → nothing to search for (`label_prefix` without `locale`).
        assert!(prefix_languages(None, &["root".to_owned()]).is_empty());
        // Duplicates and unsafe codes are dropped.
        assert_eq!(
            prefix_languages(
                Some("de"),
                &["de".to_owned(), "de`".to_owned(), "en".to_owned()]
            ),
            vec!["de", "en"]
        );
    }

    #[test]
    fn safe_language_code_accepts_only_path_segments() {
        for code in ["de", "en", "de-AT", "pt_BR", "x1"] {
            assert!(safe_language_code(code), "{code} must be usable as a path");
        }
        // Everything that could change the query is skipped.
        for code in ["", "de`", "de.at", "de at", "de;DROP", "de'AT"] {
            assert!(!safe_language_code(code), "{code} must be skipped");
        }
    }

    #[test]
    fn append_unique_keeps_the_first_occurrence_of_an_id() {
        let first = json!({"id": "object:a", "label": "first"});
        let duplicate = json!({"id": "object:a", "label": "second"});
        let other = json!({"id": "object:b"});
        let mut objects = vec![first.clone()];
        append_unique(&mut objects, vec![duplicate, other.clone()]);
        assert_eq!(objects.len(), 2);
        assert_eq!(objects[0]["label"], "first");
        assert_eq!(objects[1]["id"], "object:b");
        // An object without an id is appended once and never deduplicated away.
        let mut without_id = Vec::new();
        append_unique(
            &mut without_id,
            vec![json!({"label": "x"}), json!({"label": "y"})],
        );
        assert_eq!(without_id.len(), 2);
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
    fn a_view_key_separates_filters_sorts_and_bound_values() {
        let filter = |conditions: Vec<&str>, binds: Vec<(&str, Value)>| ObjectFilter {
            conditions: conditions.iter().map(|entry| (*entry).to_owned()).collect(),
            binds: binds
                .into_iter()
                .map(|(name, value)| (name.to_owned(), value))
                .collect(),
            completeness_lt: None,
            missing_required: true,
        };
        let base = filter(vec!["deleted_at IS NONE"], vec![]);
        // The sort is part of the view: the same filter read in another order is
        // another list, so the cached keys must not be shared.
        assert_ne!(base.view_key("created_at DESC, id"), base.view_key("id"));
        // A bound value is part of the filter even though `conditions` only carries
        // its `$name` reference.
        assert_ne!(
            filter(vec!["deleted_at IS NONE"], vec![("f0", json!("a"))]).view_key("id"),
            filter(vec!["deleted_at IS NONE"], vec![("f0", json!("b"))]).view_key("id"),
        );
        // The same view is the same key (a cached view is found again).
        assert_eq!(
            base.view_key("created_at DESC, id"),
            filter(vec!["deleted_at IS NONE"], vec![]).view_key("created_at DESC, id")
        );
    }

    #[test]
    fn the_scope_key_separates_two_scopes_with_the_same_condition_text() {
        let scope = |keys: Value| {
            Some(crate::scope::Clause {
                conditions: vec!["type = $scope_type AND id IN $scope_group_0_keys".to_owned()],
                binds: vec![("scope_group_0_keys".to_owned(), keys)],
            })
        };
        assert_eq!(scope_key(&None), "all");
        // Two groups compile to the same condition text and differ only in the bound values:
        // without the values in the key, one group would be served the other's cached view.
        assert_ne!(
            scope_key(&scope(json!(["object:a"]))),
            scope_key(&scope(json!(["object:b"]))),
        );
        assert_eq!(
            scope_key(&scope(json!(["object:a"]))),
            scope_key(&scope(json!(["object:a"])))
        );
    }

    #[test]
    fn order_accepts_asc_desc_and_rejects_anything_else() {
        assert_eq!(render_direction(None).expect("default"), "ASC");
        assert_eq!(render_direction(Some("")).expect("empty"), "ASC");
        assert_eq!(render_direction(Some(" ASC ")).expect("asc"), "ASC");
        assert_eq!(render_direction(Some("Desc")).expect("desc"), "DESC");
        assert!(render_direction(Some("sideways")).is_err());
    }

    #[test]
    fn attribute_usage_counts_a_key_only_when_it_carries_a_value() {
        let mut names = BTreeSet::new();
        collect_attribute_names(
            Some(&json!({
                "name": {"de": "Bremse", "en": ""},
                "empty_language_map": {"de": "", "en": ""},
                "blank": "   ",
                "nothing": Value::Null,
                "zero": 0,
                "flag": false,
                "empty_list": [],
                "list": ["x"],
                "range": {"from": 1, "to": 2},
            })),
            &mut names,
        );
        assert_eq!(
            names.into_iter().collect::<Vec<_>>(),
            vec!["flag", "list", "name", "range", "zero"]
        );
    }

    #[test]
    fn attribute_usage_ignores_objects_without_attributes() {
        let mut names = BTreeSet::new();
        collect_attribute_names(None, &mut names);
        collect_attribute_names(Some(&json!("not an object")), &mut names);
        assert!(names.is_empty());
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
