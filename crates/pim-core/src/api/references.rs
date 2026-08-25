//! Reference (graph edge) handlers.
//!
//! References are native SurrealDB RELATION records: `in` = source, `out` =
//! target (see `plan/detail/relationen.md`).

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

/// Lists all references of an object (incoming and outgoing).
#[utoipa::path(
    get,
    path = "/objects/{id}/references",
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
    Ok(Json(references))
}

/// Lists the direct composition children of an object.
#[utoipa::path(
    get,
    path = "/objects/{id}/children",
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
) -> Result<Json<Vec<Value>>, ApiError> {
    if !claims.is_editor() {
        return Err(ApiError::forbidden("requires editor role"));
    }
    let key = parse_object_key(&id)?;
    if fetch_object(&state.db, key).await?.is_none() {
        return Err(ApiError::not_found(format!("object `{id}` not found")));
    }
    let sql = "
        LET $parent = type::record('object', $key);
        SELECT in AS child, attributes FROM reference WHERE out = $parent AND definition_id = 'rel_depends_on';
    ";
    let mut response = state
        .db
        .inner()
        .query(sql)
        .bind(("key", key))
        .await?
        .check()?;
    let children: Vec<Value> = response.take(1)?;
    Ok(Json(children))
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
