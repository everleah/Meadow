//! Reference (graph edge) handlers.
//!
//! References are native SurrealDB RELATION records: `in` = source, `out` =
//! target (see `plan/detail/relationen.md`).

use std::collections::{HashMap, HashSet};
use std::sync::Arc;

use axum::Json;
use axum::extract::{Path, Query, State};
use axum::http::{HeaderMap, StatusCode};
use serde::Deserialize;
use serde_json::{Value, json};
use utoipa::{IntoParams, ToSchema};

use crate::app::AppState;
use crate::audit::{self, AuditEntry};
use crate::auth::AuthUser;
use crate::db::Db;
use crate::error::ApiError;

const COMPOSITION_DEFINITION: &str = "rel_depends_on";

/// Payload for creating a reference between two objects.
#[derive(Debug, Deserialize, ToSchema)]
pub struct CreateReference {
    /// Source object id (`object:<ulid>`).
    pub source: String,
    /// Target object id (`object:<ulid>`).
    pub target: String,
    /// `relation_definition` record key (e.g. `rel_category_member`).
    pub definition_id: String,
    /// Edge metadata.
    #[serde(default = "default_object")]
    pub attributes: Value,
}

/// Query parameters for `POST /references`.
#[derive(Debug, Deserialize, ToSchema, IntoParams)]
#[into_params(parameter_in = Query)]
pub struct ReferenceWriteQuery {
    /// When `false`, no `reference.created` event is written to the outbox.
    #[serde(default)]
    pub emit_events: Option<bool>,
}

/// Payload for re-parenting an object.
#[derive(Debug, Deserialize, ToSchema)]
pub struct ReparentObject {
    /// New composition parent id (`object:<ulid>`).
    pub parent: String,
}

fn default_object() -> Value {
    Value::Object(serde_json::Map::new())
}

/// Splits an `object:<key>` id and returns the key.
fn parse_object_key(id: &str) -> Result<&str, ApiError> {
    let (table, key) = id
        .split_once(':')
        .ok_or_else(|| ApiError::bad_request(format!("invalid object id `{id}`")))?;
    if table != "object" {
        return Err(ApiError::bad_request(format!(
            "id `{id}` does not reference table `object`"
        )));
    }
    Ok(key)
}

/// Extracts the key part of a record id (`table:key` → `key`).
fn key_of(record_id: &str) -> String {
    record_id
        .split_once(':')
        .map(|(_, key)| key.to_owned())
        .unwrap_or_else(|| record_id.to_owned())
}

/// Loads a single record without table-specific assumptions.
async fn fetch_record(db: &Db, table: &str, key: &str) -> Result<Option<Value>, ApiError> {
    let sql = format!("LET $r = type::record('{table}', $key); SELECT * FROM $r;");
    let mut response = db.inner().query(sql).bind(("key", key)).await?.check()?;
    let rows: Vec<Value> = response.take(1)?;
    Ok(rows.into_iter().next())
}

async fn fetch_object(db: &Db, key: &str) -> Result<Option<Value>, ApiError> {
    fetch_record(db, "object", key).await
}

async fn fetch_relation_definition(db: &Db, key: &str) -> Result<Option<Value>, ApiError> {
    fetch_record(db, "relation_definition", key).await
}

/// Returns the composition parent's key of an object (or `None` for the root).
async fn parent_key(db: &Db, key: &str) -> Result<Option<String>, ApiError> {
    let sql = "
        LET $src = type::record('object', $key);
        SELECT out FROM reference WHERE in = $src AND definition_id = 'rel_depends_on' LIMIT 1;
    ";
    let mut response = db.inner().query(sql).bind(("key", key)).await?.check()?;
    let rows: Vec<Value> = response.take(1)?;
    let out = rows
        .first()
        .and_then(|row| row.get("out"))
        .and_then(Value::as_str);
    Ok(out.map(key_of))
}

/// Whether `maybe_ancestor_key` is an ancestor of `start_key` (walking up the
/// `depends_on` chain). Used for cycle protection.
async fn is_ancestor(db: &Db, start_key: &str, maybe_ancestor_key: &str) -> Result<bool, ApiError> {
    let mut current = start_key.to_owned();
    for _ in 0..10_000 {
        match parent_key(db, &current).await? {
            None => return Ok(false),
            Some(parent) => {
                if parent == maybe_ancestor_key {
                    return Ok(true);
                }
                current = parent;
            }
        }
    }
    Ok(false)
}

/// Validates the source/target types against the relation definition.
fn check_type_restriction(
    def: &Value,
    source_type: &str,
    target_type: &str,
) -> Result<(), ApiError> {
    let source_types = def
        .get("source_types")
        .and_then(Value::as_array)
        .map(|array| array.iter().filter_map(Value::as_str).collect::<Vec<_>>())
        .unwrap_or_default();
    let target_types = def
        .get("target_types")
        .and_then(Value::as_array)
        .map(|array| array.iter().filter_map(Value::as_str).collect::<Vec<_>>())
        .unwrap_or_default();

    if !source_types.is_empty() && !source_types.contains(&source_type) {
        return Err(ApiError::bad_request(format!(
            "source type `{source_type}` is not allowed for this relation"
        )));
    }
    if !target_types.is_empty() && !target_types.contains(&target_type) {
        return Err(ApiError::bad_request(format!(
            "target type `{target_type}` is not allowed for this relation"
        )));
    }
    Ok(())
}

/// Creates a reference (graph edge) between two objects.
#[utoipa::path(
    post,
    path = "/references",
    request_body = CreateReference,
    responses(
        (status = 201, description = "Created reference", body = Value),
        (status = 400, description = "Invalid payload or violation", body = Value),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Forbidden", body = Value)
    )
)]
pub async fn create_reference(
    State(state): State<AppState>,
    headers: HeaderMap,
    Query(params): Query<ReferenceWriteQuery>,
    Json(body): Json<CreateReference>,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    let user_id = crate::auth::api_key_or_role(&headers, &state.config, crate::auth::ROLE_EDITOR)?;
    let emit_events = params.emit_events.unwrap_or(true);
    let source_key = parse_object_key(&body.source)?;
    let target_key = parse_object_key(&body.target)?;
    if source_key == target_key {
        return Err(ApiError::bad_request("source and target must differ"));
    }

    let source = fetch_object(&state.db, source_key)
        .await?
        .ok_or_else(|| ApiError::bad_request("source object not found"))?;
    let target = fetch_object(&state.db, target_key)
        .await?
        .ok_or_else(|| ApiError::bad_request("target object not found"))?;
    let source_type = source
        .get("type")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let target_type = target
        .get("type")
        .and_then(Value::as_str)
        .unwrap_or_default();

    let definition = fetch_relation_definition(&state.db, &body.definition_id)
        .await?
        .ok_or_else(|| ApiError::bad_request("unknown relation definition"))?;
    let kind = definition
        .get("kind")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let name = definition
        .get("name")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();

    check_type_restriction(&definition, source_type, target_type)?;

    if kind == "composition" {
        if parent_key(&state.db, source_key).await?.is_some() {
            return Err(ApiError::bad_request(
                "source object already has a composition parent; use re-parenting",
            ));
        }
        if is_ancestor(&state.db, target_key, source_key).await? {
            return Err(ApiError::bad_request("would create a cycle"));
        }
    }

    let audit_definition_id = body.definition_id.clone();
    let outbox = crate::event::content(&crate::event::OutboxEvent {
        event_type: crate::event::EVENT_REFERENCE_CREATED,
        object_type: source_type.to_owned(),
        object_id: body.source.clone(),
        changes: json!({
            "target": body.target.clone(),
            "definition_id": body.definition_id.clone(),
        }),
        metadata: json!({ "user_id": user_id.clone() }),
    });

    // Statements: BEGIN(0), LET(1), LET(2), RELATE(3), [CREATE outbox(4)],
    // RETURN(4|5), COMMIT(5|6).
    let sql = if emit_events {
        "
        BEGIN TRANSACTION;
        LET $src = type::record('object', $source_key);
        LET $tgt = type::record('object', $target_key);
        LET $edge = (RELATE $src->reference->$tgt SET definition_id = $definition_id, type = $definition_name, attributes = $attributes);
        CREATE outbox_event CONTENT $outbox;
        RETURN $edge;
        COMMIT TRANSACTION;
        "
        .to_owned()
    } else {
        "
        BEGIN TRANSACTION;
        LET $src = type::record('object', $source_key);
        LET $tgt = type::record('object', $target_key);
        LET $edge = (RELATE $src->reference->$tgt SET definition_id = $definition_id, type = $definition_name, attributes = $attributes);
        RETURN $edge;
        COMMIT TRANSACTION;
        "
        .to_owned()
    };
    let mut response = state
        .db
        .inner()
        .query(sql)
        .bind(("source_key", source_key))
        .bind(("target_key", target_key))
        .bind(("definition_id", body.definition_id))
        .bind(("definition_name", name))
        .bind(("attributes", body.attributes))
        .bind(("outbox", outbox))
        .await?
        .check()?;
    let return_index = if emit_events { 5 } else { 4 };
    let edges: Vec<Value> = response.take(return_index)?;
    let edge = edges
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::internal("created reference was not returned"))?;

    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &user_id,
            action: "reference_created",
            object_id: Some(&body.source),
            from: None,
            to: None,
            metadata: Some(json!({
                "target": body.target,
                "definition_id": audit_definition_id,
            })),
        },
    )
    .await;

    Ok((StatusCode::CREATED, Json(edge)))
}

/// Lists relation definitions (used by the reference picker).
#[utoipa::path(
    get,
    path = "/relation-definitions",
    responses(
        (status = 200, description = "Relation definitions", body = Vec<Value>),
        (status = 403, description = "Forbidden", body = Value)
    )
)]
pub async fn list_relation_definitions(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
) -> Result<Json<Vec<Value>>, ApiError> {
    if !claims.is_editor() {
        return Err(ApiError::forbidden("requires editor role"));
    }
    let sql = "SELECT * FROM relation_definition ORDER BY name;";
    let mut response = state.db.inner().query(sql).await?.check()?;
    let definitions: Vec<Value> = response.take(0)?;
    Ok(Json(definitions))
}

/// Query parameters for `GET /objects/{id}/references`.
#[derive(Debug, Deserialize, ToSchema, IntoParams)]
#[into_params(parameter_in = Query)]
pub struct ReferenceListParams {
    /// Language code used to resolve the referenced objects' labels.
    pub locale: Option<String>,
}

/// Lists all references of an object (incoming and outgoing).
///
/// Each reference is enhanced with `in_object`/`out_object` (`{id, type, label,
/// status}`) so the client can render them as chips with a resolved label.
#[utoipa::path(
    get,
    path = "/objects/{id}/references",
    params(ReferenceListParams),
    responses(
        (status = 200, description = "References", body = Vec<Value>),
        (status = 400, description = "Invalid id", body = Value),
        (status = 404, description = "Object not found", body = Value)
    )
)]
pub async fn list_object_references(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
    Query(params): Query<ReferenceListParams>,
) -> Result<Json<Vec<Value>>, ApiError> {
    if !claims.is_editor() {
        return Err(ApiError::forbidden("requires editor role"));
    }
    let key = parse_object_key(&id)?;
    if fetch_object(&state.db, key).await?.is_none() {
        return Err(ApiError::not_found(format!("object `{id}` not found")));
    }
    let sql = "
        LET $src = type::record('object', $key);
        SELECT * FROM reference WHERE in = $src OR out = $src;
    ";
    let mut response = state
        .db
        .inner()
        .query(sql)
        .bind(("key", key))
        .await?
        .check()?;
    let references: Vec<Value> = response.take(1)?;

    let locale = params
        .locale
        .as_deref()
        .map(str::trim)
        .filter(|l| !l.is_empty());

    let chain: Option<Arc<Vec<String>>> = match locale {
        Some(locale) => Some(state.cache.fallback_chain(&state.db, locale).await?),
        None => None,
    };

    let mut result = Vec::new();
    for mut reference in references {
        for field in ["in", "out"] {
            let Some(rec) = reference
                .get(field)
                .and_then(Value::as_str)
                .map(str::to_owned)
            else {
                continue;
            };
            let Some(obj) = fetch_object(&state.db, &key_of(&rec)).await? else {
                continue;
            };
            let attributes = obj
                .get("attributes")
                .and_then(Value::as_object)
                .cloned()
                .unwrap_or_default();
            let obj_type = obj
                .get("type")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_owned();
            let label = crate::language::resolve_label(
                locale,
                chain.as_deref().map(|v| v.as_slice()),
                attributes.get("name"),
            )
            .unwrap_or_else(|| obj_type.clone());
            reference[format!("{field}_object")] = json!({
                "id": rec,
                "type": obj_type,
                "label": label,
                "status": obj.get("status").and_then(Value::as_str).unwrap_or(""),
            });
        }
        result.push(reference);
    }
    Ok(Json(result))
}

/// Deletes a reference (aggregation) by its record id.
#[utoipa::path(
    delete,
    path = "/references/{id}",
    responses(
        (status = 204, description = "Deleted"),
        (status = 400, description = "Invalid id", body = Value),
        (status = 404, description = "Reference not found", body = Value)
    )
)]
pub async fn delete_reference(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<StatusCode, ApiError> {
    if !claims.is_editor() {
        return Err(ApiError::forbidden("requires editor role"));
    }
    let (table, key) = id
        .split_once(':')
        .ok_or_else(|| ApiError::bad_request(format!("invalid reference id `{id}`")))?;
    if table != "reference" {
        return Err(ApiError::bad_request(format!(
            "id `{id}` does not reference table `reference`"
        )));
    }
    if fetch_record(&state.db, "reference", key).await?.is_none() {
        return Err(ApiError::not_found(format!("reference `{id}` not found")));
    }
    let sql = "DELETE type::record('reference', $key);";
    state
        .db
        .inner()
        .query(sql)
        .bind(("key", key))
        .await?
        .check()?;
    Ok(StatusCode::NO_CONTENT)
}

/// Query parameters for `GET /objects/{id}/children`.
#[derive(Debug, Deserialize, ToSchema, IntoParams)]
#[into_params(parameter_in = Query)]
pub struct ChildrenListParams {
    /// Maximum number of children to return (default: all, up to 1000).
    #[serde(default)]
    pub limit: Option<u32>,
    /// Number of children to skip.
    #[serde(default)]
    pub offset: u32,
    /// When true, each returned category child carries a `memberCount` (number
    /// of objects assigned via `category_member`, including subcategories).
    #[serde(default)]
    pub count_members: bool,
}

/// Lists the direct composition children of an object.
///
/// Each child is returned as its object (`id`/`type`/`status`/`attributes`) plus
/// a `hasChildren` flag so the structure tree can show the expand arrow without
/// loading the whole subtree (lazy loading); `?limit`/`?offset` paginate nodes
/// with many direct children. With `?count_members=true`, category children also
/// carry a `memberCount` for the category tree's counter.
#[utoipa::path(
    get,
    path = "/objects/{id}/children",
    params(ChildrenListParams),
    responses(
        (status = 200, description = "Children", body = Vec<Value>),
        (status = 400, description = "Invalid id", body = Value),
        (status = 404, description = "Object not found", body = Value)
    )
)]
pub async fn list_object_children(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
    Query(params): Query<ChildrenListParams>,
) -> Result<Json<Vec<Value>>, ApiError> {
    if !claims.is_editor() {
        return Err(ApiError::forbidden("requires editor role"));
    }
    let key = parse_object_key(&id)?;
    if fetch_object(&state.db, key).await?.is_none() {
        return Err(ApiError::not_found(format!("object `{id}` not found")));
    }

    // Direct children of the parent (paginated).
    let children_sql = "
        LET $parent = type::record('object', $key);
        SELECT in AS child FROM reference
        WHERE out = $parent AND definition_id = 'rel_depends_on'
        ORDER BY in LIMIT $limit START $offset;
    ";
    let mut response = state
        .db
        .inner()
        .query(children_sql)
        .bind(("key", key.to_owned()))
        .bind(("limit", params.limit.unwrap_or(1000).clamp(1, 1000) as i64))
        .bind(("offset", params.offset as i64))
        .await?
        .check()?;
    let child_rows: Vec<Value> = response.take(1)?;
    let child_ids: Vec<String> = child_rows
        .iter()
        .filter_map(|row| row.get("child").and_then(Value::as_str).map(str::to_owned))
        .collect();
    if child_ids.is_empty() {
        return Ok(Json(vec![]));
    }

    // Fetch the child objects (so the client can resolve labels without the full tree).
    let keys: Vec<String> = child_ids.iter().map(|id| key_of(id)).collect();
    let object_sql = "
        LET $recs = array::map($keys, |$k| type::record('object', $k));
        SELECT * FROM object WHERE id IN $recs;
    ";
    let mut response = state
        .db
        .inner()
        .query(object_sql)
        .bind(("keys", keys.clone()))
        .await?
        .check()?;
    let objects: Vec<Value> = response.take(1)?;

    // Which of the children are themselves parents (have children)?
    let has_sql = "
        LET $recs = array::map($keys, |$k| type::record('object', $k));
        SELECT out AS parent FROM reference
        WHERE definition_id = 'rel_depends_on' AND out IN $recs;
    ";
    let mut response = state
        .db
        .inner()
        .query(has_sql)
        .bind(("keys", keys))
        .await?
        .check()?;
    let parent_rows: Vec<Value> = response.take(1)?;
    let parent_set: HashSet<String> = parent_rows
        .iter()
        .filter_map(|row| row.get("parent").and_then(Value::as_str).map(str::to_owned))
        .collect();

    // Build the response, preserving the pagination order. Children that point
    // to a non-existent object (broken/dangling `depends_on` reference) are
    // skipped so the structure tree stays clean.
    let by_id: HashMap<String, Value> = objects
        .into_iter()
        .filter_map(|obj| {
            let id = obj.get("id").and_then(Value::as_str)?.to_owned();
            Some((id, obj))
        })
        .collect();
    let result: Vec<Value> = child_ids
        .iter()
        .filter_map(|cid| {
            let mut value = by_id.get(cid)?.clone();
            value["hasChildren"] = json!(parent_set.contains(cid));
            Some(value)
        })
        .collect();
    if params.count_members {
        // Category children: add the number of objects assigned via
        // `category_member` to the category subtree (category tree counter).
        let mut counted: Vec<Value> = Vec::new();
        for mut value in result {
            if value.get("type").and_then(Value::as_str) == Some("category") {
                let cid = value.get("id").and_then(Value::as_str).unwrap_or("");
                let keys = category_member_keys(&state.db, cid).await?;
                value["memberCount"] = json!(keys.len());
            }
            counted.push(value);
        }
        return Ok(Json(counted));
    }
    Ok(Json(result))
}

/// Returns the composition tree rooted at the Universal-Root as nested
/// `{ id, children: [...] }` nodes. Used by the grid's structure navigation.
#[utoipa::path(
    get,
    path = "/objects/tree",
    responses(
        (status = 200, description = "Composition tree", body = Value),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Forbidden", body = Value)
    )
)]
pub async fn get_object_tree(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
) -> Result<Json<Value>, ApiError> {
    if !claims.is_editor() {
        return Err(ApiError::forbidden("requires editor role"));
    }
    let sql =
        "SELECT in AS child, out AS parent FROM reference WHERE definition_id = 'rel_depends_on';";
    let mut response = state.db.inner().query(sql).await?.check()?;
    let rows: Vec<Value> = response.take(0)?;

    let mut children: HashMap<String, Vec<String>> = HashMap::new();
    for row in &rows {
        let child = row.get("child").and_then(Value::as_str).map(str::to_owned);
        let parent = row.get("parent").and_then(Value::as_str).map(str::to_owned);
        if let (Some(child), Some(parent)) = (child, parent) {
            children.entry(parent).or_default().push(child);
        }
    }

    fn build(
        node: &str,
        children: &HashMap<String, Vec<String>>,
        visited: &mut HashSet<String>,
    ) -> Value {
        if visited.contains(node) {
            return json!({ "id": node, "children": [] });
        }
        visited.insert(node.to_owned());
        let child_ids = children.get(node).cloned().unwrap_or_default();
        let child_nodes: Vec<Value> = child_ids
            .iter()
            .map(|child| build(child, children, visited))
            .collect();
        json!({ "id": node, "children": child_nodes })
    }

    let mut visited = HashSet::new();
    let tree = build("object:object_root", &children, &mut visited);
    Ok(Json(Value::Array(vec![tree])))
}

/// Returns every descendant (and itself) of a composition node as bare object
/// keys, breadth-first. Used for the server-side subtree filter
/// (`GET /objects?ancestor=...`) that replaces client-side tree traversal once
/// the structure tree is lazy-loaded.
pub async fn subtree_keys(db: &Db, root_id: &str) -> Result<Vec<String>, ApiError> {
    let sql =
        "SELECT in AS child, out AS parent FROM reference WHERE definition_id = 'rel_depends_on';";
    let mut response = db.inner().query(sql).await?.check()?;
    let rows: Vec<Value> = response.take(0)?;
    let mut children: HashMap<String, Vec<String>> = HashMap::new();
    for row in &rows {
        let child = row.get("child").and_then(Value::as_str).map(str::to_owned);
        let parent = row.get("parent").and_then(Value::as_str).map(str::to_owned);
        if let (Some(child), Some(parent)) = (child, parent) {
            children.entry(parent).or_default().push(child);
        }
    }
    let mut result: Vec<String> = vec![root_id.to_owned()];
    let mut seen: HashSet<String> = HashSet::new();
    let mut queue: Vec<String> = vec![root_id.to_owned()];
    while let Some(current) = queue.pop() {
        if !seen.insert(current.clone()) {
            continue;
        }
        if let Some(next) = children.get(&current) {
            for child in next {
                if !seen.contains(child) {
                    result.push(child.clone());
                    queue.push(child.clone());
                }
            }
        }
    }
    Ok(result.iter().map(|id| key_of(id)).collect())
}

/// Returns the bare keys of every object assigned (via `category_member`) to
/// the given category or any of its composition descendants. Used for the
/// server-side category filter (`GET /objects?category=...`).
pub async fn category_member_keys(db: &Db, category_id: &str) -> Result<Vec<String>, ApiError> {
    let category_keys = subtree_keys(db, category_id).await?;
    let sql = "
        LET $cats = array::map($keys, |$k| type::record('object', $k));
        SELECT in AS member FROM reference
        WHERE definition_id = 'rel_category_member' AND out IN $cats;
    ";
    let mut response = db
        .inner()
        .query(sql)
        .bind(("keys", category_keys))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(1)?;
    let mut seen: HashSet<String> = HashSet::new();
    let mut result: Vec<String> = Vec::new();
    for row in &rows {
        if let Some(member) = row.get("member").and_then(Value::as_str) {
            let key = key_of(member).to_owned();
            if seen.insert(key.clone()) {
                result.push(key);
            }
        }
    }
    Ok(result)
}

/// Changes an object's composition parent (re-parenting with cycle protection).
#[utoipa::path(
    patch,
    path = "/objects/{id}/parent",
    request_body = ReparentObject,
    responses(
        (status = 200, description = "Updated object", body = Value),
        (status = 400, description = "Invalid payload or violation", body = Value),
        (status = 404, description = "Not found", body = Value)
    )
)]
pub async fn reparent_object(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
    Json(body): Json<ReparentObject>,
) -> Result<Json<Value>, ApiError> {
    if !claims.is_editor() {
        return Err(ApiError::forbidden("requires editor role"));
    }
    let child_key = parse_object_key(&id)?;
    let new_parent_key = parse_object_key(&body.parent)?;
    if child_key == new_parent_key {
        return Err(ApiError::bad_request("an object cannot be its own parent"));
    }

    let child = fetch_object(&state.db, child_key)
        .await?
        .ok_or_else(|| ApiError::not_found(format!("object `{id}` not found")))?;
    let parent = fetch_object(&state.db, new_parent_key)
        .await?
        .ok_or_else(|| ApiError::not_found(format!("object `{}` not found", body.parent)))?;
    let child_type = child
        .get("type")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let parent_type = parent
        .get("type")
        .and_then(Value::as_str)
        .unwrap_or_default();

    let definition = fetch_relation_definition(&state.db, COMPOSITION_DEFINITION)
        .await?
        .ok_or_else(|| ApiError::internal("composition definition is missing"))?;
    check_type_restriction(&definition, child_type, parent_type)?;

    let old_parent_key = parent_key(&state.db, child_key)
        .await?
        .ok_or_else(|| ApiError::bad_request("object has no composition parent"))?;
    if is_ancestor(&state.db, new_parent_key, child_key).await? {
        return Err(ApiError::bad_request("would create a cycle"));
    }

    // SurrealDB does not allow changing the direction (`out`) of an existing
    // relation record, so re-parenting deletes the old edge and creates a new
    // one inside a transaction.
    let old_parent_id = format!("object:{old_parent_key}");
    let outbox_deleted = crate::event::content(&crate::event::OutboxEvent {
        event_type: crate::event::EVENT_REFERENCE_DELETED,
        object_type: child_type.to_owned(),
        object_id: id.clone(),
        changes: json!({ "parent": old_parent_id.clone() }),
        metadata: json!({ "user_id": claims.sub.clone() }),
    });
    let outbox_created = crate::event::content(&crate::event::OutboxEvent {
        event_type: crate::event::EVENT_REFERENCE_CREATED,
        object_type: child_type.to_owned(),
        object_id: id.clone(),
        changes: json!({ "parent": body.parent.clone() }),
        metadata: json!({ "user_id": claims.sub.clone() }),
    });

    // Statements: BEGIN(0), LET child(1), LET parent(2), DELETE(3),
    // RELATE(4), CREATE outbox deleted(5), CREATE outbox created(6),
    // SELECT(7), COMMIT(8).
    let sql = "
        BEGIN TRANSACTION;
        LET $child = type::record('object', $child_key);
        LET $parent = type::record('object', $parent_key);
        DELETE reference WHERE in = $child AND definition_id = 'rel_depends_on';
        RELATE $child->reference->$parent SET definition_id = 'rel_depends_on', type = 'depends_on', attributes = {};
        CREATE outbox_event CONTENT $outbox_deleted;
        CREATE outbox_event CONTENT $outbox_created;
        SELECT * FROM $child;
        COMMIT TRANSACTION;
    ";
    let mut response = state
        .db
        .inner()
        .query(sql)
        .bind(("child_key", child_key))
        .bind(("parent_key", new_parent_key))
        .bind(("outbox_deleted", outbox_deleted))
        .bind(("outbox_created", outbox_created))
        .await?
        .check()?;
    let objects: Vec<Value> = response.take(7)?;
    let object = objects
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::internal("updated object was not returned"))?;

    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &claims.sub,
            action: "reference_reparented",
            object_id: Some(&id),
            from: Some(json!({ "parent": old_parent_id })),
            to: Some(json!({ "parent": body.parent })),
            metadata: None,
        },
    )
    .await;

    Ok(Json(object))
}
