//! Object CRUD handlers.
//!
//! Every object is part of the composition tree: creating an object also
//! creates its `depends_on` edge to the universal root.

use std::collections::HashMap;
use std::sync::Arc;

use axum::Json;
use axum::extract::{Path, Query, State};
use axum::http::{HeaderMap, StatusCode};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use utoipa::{IntoParams, ToSchema};

use crate::api::keys;
use crate::app::AppState;
use crate::audit::{self, AuditEntry};
use crate::auth::AuthUser;
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
    } = body;
    let object_type = object_type.trim().to_owned();
    let user_id = crate::auth::api_key_or_role(&headers, &state.config, crate::auth::ROLE_EDITOR)?;
    let locale = params
        .locale
        .as_deref()
        .map(str::trim)
        .filter(|l| !l.is_empty());
    let emit_events = params.emit_events.unwrap_or(true);

    let Value::Object(mut attributes) = attributes else {
        return Err(ApiError::bad_request("`attributes` must be an object"));
    };
    // Keep the object-type registry complete: an object of a not-yet-registered
    // type auto-registers it (label defaults to the type code).
    crate::api::object_types::ensure_registered(&state.db, &object_type).await?;
    keys::normalize_key_attributes(&state.db, &object_type, &mut attributes, None).await?;

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
        &state.db,
        &state.rules,
        &candidate,
        &["validation", "computed"],
        "save",
        locale,
        Some(&user_id),
    )
    .await?;
    crate::rules::enforce(&outcome)?;
    let computed = crate::rules::resolve_computed(&state.db, &outcome.computed_object()).await?;
    for (name, value) in computed {
        attributes.insert(name, value);
    }

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

    // Statements: BEGIN(0), LET(1), CREATE(2), RELATE(3), [CREATE outbox(4)],
    // SELECT(4|5), COMMIT(5|6).
    let sql = if emit_events {
        "
        BEGIN TRANSACTION;
        LET $obj = type::record('object', rand::ulid());
        CREATE $obj CONTENT $content;
        RELATE $obj->reference->object:object_root SET definition_id = 'rel_depends_on', type = 'depends_on', attributes = {};
        CREATE outbox_event SET event_type = 'object.created', object_type = $object_type, object_id = type::string($obj.id), changes = $changes, metadata = $metadata, status = 'pending';
        SELECT * FROM $obj;
        COMMIT TRANSACTION;
        "
        .to_owned()
    } else {
        "
        BEGIN TRANSACTION;
        LET $obj = type::record('object', rand::ulid());
        CREATE $obj CONTENT $content;
        RELATE $obj->reference->object:object_root SET definition_id = 'rel_depends_on', type = 'depends_on', attributes = {};
        SELECT * FROM $obj;
        COMMIT TRANSACTION;
        "
        .to_owned()
    };
    let mut response = state
        .db
        .inner()
        .query(sql)
        .bind(("content", Value::Object(content)))
        .bind(("object_type", object_type))
        .bind(("changes", changes))
        .bind(("metadata", json!({ "user_id": user_id.clone() })))
        .await?
        .check()?;
    let select_index = if emit_events { 5 } else { 4 };
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
    Ok((StatusCode::CREATED, Json(object)))
}

/// Deletes an object (soft delete) with cascade over its composition subtree.
///
/// `DELETE /objects/{id}` marks the object and every `depends_on` descendant
/// as deleted (`deleted_at`), removes all reference edges (composition and
/// aggregation) that touch a deleted object so the graph stays clean, and
/// writes an `object.deleted` outbox event. The universal root cannot be
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

    // Statements: BEGIN(0), LET(1), UPDATE(2), DELETE refs(3), [CREATE outbox(4)],
    // COMMIT(4|5).
    let sql = if emit_events {
        "
        BEGIN TRANSACTION;
        LET $recs = array::map($keys, |$k| type::record('object', $k));
        UPDATE object SET deleted_at = time::now(), deleted_by = $user_id, updated_at = time::now() WHERE id IN $recs;
        DELETE reference WHERE in IN $recs OR out IN $recs;
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
/// Returns the object counts by type and status plus the number of incomplete
/// objects per type (for a working locale) — so the frontend no longer loads
/// up to `limit` objects just to count them.
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

    let sql = "SELECT id, type, status, attributes FROM object WHERE deleted_at IS NONE;";
    let mut response = state.db.inner().query(sql).await?.check()?;
    let objects: Vec<Value> = response.take(0)?;

    let mut total: i64 = 0;
    let mut by_type: HashMap<String, i64> = HashMap::new();
    let mut by_status: HashMap<String, i64> = HashMap::new();
    let mut incomplete_by_type: HashMap<String, i64> = HashMap::new();

    // Build the per-object inputs for the batched completeness computation and
    // the type/status aggregation in a single pass.
    let mut items: Vec<(String, String, serde_json::Map<String, Value>)> = Vec::new();
    for object in &objects {
        let otype = object.get("type").and_then(Value::as_str).unwrap_or("");
        let status = object.get("status").and_then(Value::as_str).unwrap_or("");
        let object_id = object.get("id").and_then(Value::as_str).unwrap_or("");
        let own_attributes = object
            .get("attributes")
            .and_then(Value::as_object)
            .cloned()
            .unwrap_or_default();
        total += 1;
        *by_type.entry(otype.to_owned()).or_default() += 1;
        *by_status.entry(status.to_owned()).or_default() += 1;
        items.push((object_id.to_owned(), otype.to_owned(), own_attributes));
    }

    // Resolve completeness for all objects with a single edge query and
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

    Ok(Json(json!({
        "total": total,
        "by_type": by_type,
        "by_status": by_status,
        "incomplete_by_type": incomplete_by_type,
    })))
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
    if params.ancestor.is_some() {
        conditions
            .push("id IN array::map($ancestor_ids, |$k| type::record('object', $k))".to_owned());
    }
    if params.category.is_some() {
        conditions.push(
            "id IN array::map($category_member_ids, |$k| type::record('object', $k))".to_owned(),
        );
    }
    if params.collection.is_some() {
        conditions.push(
            "id IN array::map($collection_member_ids, |$k| type::record('object', $k))".to_owned(),
        );
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
    if params.uncategorized {
        conditions.push(
            "id NOT IN (SELECT VALUE in FROM reference WHERE definition_id = $category_definition) AND type NOT IN ['category', 'root']"
                .to_owned(),
        );
    }
    let mut object_ids: Vec<String> = Vec::new();
    if let Some(ids) = &params.ids {
        object_ids = ids
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
            conditions
                .push("id IN array::map($object_ids, |$k| type::record('object', $k))".to_owned());
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
    let sql = format!(
        "SELECT * FROM object{where_clause} ORDER BY created_at DESC LIMIT $limit START $start"
    );

    let mut query = state.db.inner().query(sql);
    if let Some(object_type) = &params.object_type {
        query = query.bind(("type", object_type.clone()));
    }
    if let Some(ancestor) = &params.ancestor {
        let keys = crate::api::references::subtree_keys(&state.db, ancestor).await?;
        query = query.bind(("ancestor_ids", keys));
    }
    if let Some(category) = &params.category {
        let definition = category_definition
            .as_deref()
            .unwrap_or(crate::api::references::PRIMARY_CATEGORY_DEFINITION);
        let keys =
            crate::api::references::category_member_keys(&state.db, category, definition).await?;
        query = query.bind(("category_member_ids", keys));
    }
    if let Some(definition) = &category_definition {
        query = query.bind(("category_definition", definition.clone()));
    }
    if let Some(collection) = &params.collection {
        let keys = crate::api::references::collection_member_keys(&state.db, collection).await?;
        query = query.bind(("collection_member_ids", keys));
    }
    if !object_ids.is_empty() {
        query = query.bind(("object_ids", object_ids));
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
    if locale.is_some() || completeness_filter {
        let locale_ref = locale.as_deref();
        let mut kept: Vec<Value> = Vec::new();
        for mut object in objects {
            let object_id = object
                .get("id")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_owned();
            let object_type = object
                .get("type")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_owned();
            let own_attributes = object
                .get("attributes")
                .and_then(Value::as_object)
                .cloned()
                .unwrap_or_default();
            let result = crate::completeness::compute(
                &state.cache,
                &state.db,
                &object_id,
                &object_type,
                locale_ref,
                &own_attributes,
            )
            .await?;
            if completeness_filter
                && !passes_completeness_filter(
                    &result,
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
}

/// Searches for objects by label to pick a reference target.
///
/// Returns compact hits (`id`, `type`, `label`, `status`); the label is the
/// resolved multilingual `name` for `?locale=`. With `?locale=` each hit also
/// carries a `completeness` value for that language, so a picker can show
/// whether the target still misses required attributes. Used by the reference
/// and relation pickers.
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
    let objects: Vec<Value> = response.take(0)?;

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
        let label = crate::language::resolve_label(
            locale.as_deref(),
            chain.as_deref().map(|v| v.as_slice()),
            attributes.get("name"),
        )
        .unwrap_or_else(|| object_type.clone());
        let mut hit = json!({
            "id": &id,
            "type": &object_type,
            "label": label,
            "status": &status,
        });
        if let Some(locale) = &locale {
            // Few hits per query (default 20), so the per-object computation is
            // cheap; `compute_batch` would need the ancestors to be part of the
            // hit list (see `list_objects`).
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

    let mut changed_fields: Vec<&str> = Vec::new();
    let mut changes = serde_json::Map::new();
    if let Some(attributes) = body.attributes {
        let Value::Object(mut attributes) = attributes else {
            return Err(ApiError::bad_request("`attributes` must be an object"));
        };
        keys::normalize_key_attributes(&state.db, &object_type, &mut attributes, Some(key)).await?;
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

    let status_changed = changed_fields.contains(&"status");
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
        &state.db,
        &state.rules,
        &candidate,
        &["validation", "computed"],
        "save",
        locale,
        Some(&user_id),
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
            &state.db,
            &state.rules,
            &candidate,
            &["workflow_guard"],
            "transition",
            locale,
            Some(&user_id),
        )
        .await?;
        crate::rules::enforce(&guard)?;
    }

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
    /// `consolidate_inheritance` | `delete`.
    pub op: String,
    /// Explicit object ids (full record ids) the operation applies to. The
    /// frontend resolves the selection (all filter hits / page / checked rows)
    /// into this list; the core stays lean and does not re-resolve a filter.
    #[serde(default)]
    pub object_ids: Vec<String>,
    /// Op-specific parameters (attribute name/value, status, category, parent).
    #[serde(default)]
    pub parameters: Value,
}

const BULK_OPS: [&str; 12] = [
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
}
