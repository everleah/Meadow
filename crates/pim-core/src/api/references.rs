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
use serde_json::{Map, Value, json};
use utoipa::{IntoParams, ToSchema};

use crate::app::AppState;
use crate::audit::{self, AuditEntry};
use crate::auth::AuthUser;
use crate::db::Db;
use crate::error::ApiError;

const COMPOSITION_DEFINITION: &str = "rel_depends_on";

/// Usage classification of a relation type (`relation_definition.role`):
/// * `composition` – the (single) composition type: inheritance, cascade, tree.
/// * `category` – categorisation: target is a `category` object.
/// * `collection` – membership in a materialised collection: target is a
///   `collection` object, edges carry `rank`/`added_at`.
/// * `link` – plain association (**default** for new types).
///
/// `composition` and `collection` are reserved for the built-in types; custom
/// types may be `link` or `category` (see `plan/detail/relationen.md`).
pub const ROLE_COMPOSITION: &str = "composition";
pub const ROLE_CATEGORY: &str = "category";
pub const ROLE_COLLECTION: &str = "collection";
pub const ROLE_LINK: &str = "link";

/// Role a relation type has when none is stored (records created before the
/// classification was introduced).
pub const DEFAULT_ROLE: &str = ROLE_LINK;

/** Roles custom relation types may carry. */
pub(crate) const CUSTOM_ROLES: [&str; 2] = [ROLE_LINK, ROLE_CATEGORY];

/// Key of the built-in primary category relation type (used when no other
/// category type is requested).
pub const PRIMARY_CATEGORY_DEFINITION: &str = "rel_category_member";

/// Returns the relation type's usage (`link` when nothing is stored).
pub fn role_of(definition: &Value) -> &str {
    definition
        .get("role")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|role| !role.is_empty())
        .unwrap_or(DEFAULT_ROLE)
}

/// The definition's name for messages (falls back to the given key).
fn definition_id_of(definition: &Value, fallback: &str) -> String {
    definition
        .get("name")
        .and_then(Value::as_str)
        .map(str::to_owned)
        .unwrap_or_else(|| fallback.to_owned())
}

/// Resolves the relation type used for the category filters: the requested one
/// (must have role `category`) or the built-in primary category type.
pub async fn resolve_category_definition(
    db: &Db,
    requested: Option<&str>,
) -> Result<String, ApiError> {
    if let Some(key) = requested.map(str::trim).filter(|key| !key.is_empty()) {
        let definition = fetch_relation_definition(db, key)
            .await?
            .ok_or_else(|| ApiError::bad_request(format!("unknown relation type `{key}`")))?;
        if role_of(&definition) != ROLE_CATEGORY {
            return Err(ApiError::bad_request(format!(
                "relation type `{key}` is not a category relation"
            )));
        }
        return Ok(key.to_owned());
    }
    let rows: Vec<Value> = db
        .inner()
        .query("SELECT id FROM relation_definition WHERE role = $role AND system = true LIMIT 1;")
        .bind(("role", ROLE_CATEGORY))
        .await?
        .check()?
        .take(0)?;
    Ok(rows
        .first()
        .and_then(|row| row.get("id"))
        .and_then(Value::as_str)
        .map(key_of)
        .unwrap_or_else(|| PRIMARY_CATEGORY_DEFINITION.to_owned()))
}

/// Validates a requested role for a **custom** relation type.
pub(crate) fn custom_role(raw: Option<&str>) -> Result<&str, ApiError> {
    let role = raw
        .map(str::trim)
        .filter(|role| !role.is_empty())
        .unwrap_or(DEFAULT_ROLE);
    if CUSTOM_ROLES.contains(&role) {
        return Ok(role);
    }
    Err(ApiError::bad_request(format!(
        "role `{role}` is reserved for the built-in relation types (allowed for custom types: {})",
        CUSTOM_ROLES.join(", ")
    )))
}

/// Target types a role fixes **independent of the registry** (empty = the role's
/// target rule is data-driven). Only `collection` is fixed to a built-in type:
/// a categorisation may point at *any* taxonomy object type
/// (`object_type.taxonomy`), which is resolved at write time.
fn role_target_types(role: &str) -> Vec<String> {
    match role {
        ROLE_COLLECTION => vec!["collection".to_owned()],
        _ => Vec::new(),
    }
}

/// The object types a categorisation relation may point at: every registered
/// taxonomy type, always including the built-in primary taxonomy (`category`).
pub(crate) async fn category_target_types(db: &Db) -> Result<Vec<String>, ApiError> {
    let mut types = crate::api::object_types::taxonomy_types(db).await?;
    if !types
        .iter()
        .any(|name| name == crate::api::object_types::PRIMARY_TAXONOMY_TYPE)
    {
        types.insert(
            0,
            crate::api::object_types::PRIMARY_TAXONOMY_TYPE.to_owned(),
        );
    }
    Ok(types)
}

/// Pure rule: an explicit target list of a `category` relation may only name
/// registered taxonomy types.
fn check_taxonomy_targets(requested: &[String], allowed: &[String]) -> Result<(), ApiError> {
    if requested.is_empty() {
        return Ok(());
    }
    for name in requested {
        if !allowed.iter().any(|t| t == name) {
            return Err(ApiError::bad_request(format!(
                "role `{ROLE_CATEGORY}` may only point at taxonomy object types (allowed: {}); \
                 `{name}` is not marked as a taxonomy type",
                allowed.join(", ")
            )));
        }
    }
    Ok(())
}

/// Rejects an explicit target-type list that contradicts the role's rule.
pub(crate) async fn check_role_target_types(
    db: &Db,
    role: &str,
    requested: &[String],
) -> Result<(), ApiError> {
    if role == ROLE_CATEGORY {
        let allowed = category_target_types(db).await?;
        return check_taxonomy_targets(requested, &allowed);
    }
    let enforced = role_target_types(role);
    if enforced.is_empty() || requested.is_empty() || requested == enforced.as_slice() {
        return Ok(());
    }
    Err(ApiError::bad_request(format!(
        "role `{role}` fixes the target type to `{}`",
        enforced.join(", ")
    )))
}

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

/// Payload for creating a custom relation definition (always an aggregation).
#[derive(Debug, Deserialize, ToSchema)]
pub struct CreateRelationDefinition {
    /// Relation-type code (e.g. `supplier_link`), used to derive the record key.
    pub name: String,
    /// Multilingual label.
    #[serde(default)]
    pub label: Option<Value>,
    /// Optional list of object types allowed as source (empty = all).
    #[serde(default)]
    pub source_types: Option<Vec<String>>,
    /// Optional list of object types allowed as target (empty = all).
    #[serde(default)]
    pub target_types: Option<Vec<String>>,
    /// Attribute names (referencing `attribute_definition`) available on the edge.
    #[serde(default)]
    pub attributes: Option<Vec<String>>,
    /// Usage: `link` (default) or `category`. `composition`/`collection` are
    /// reserved for the built-in types.
    #[serde(default)]
    pub role: Option<String>,
}

/// Payload for partially updating a relation definition.
#[derive(Debug, Deserialize, ToSchema)]
pub struct UpdateRelationDefinition {
    #[serde(default)]
    pub label: Option<Value>,
    #[serde(default)]
    pub source_types: Option<Vec<String>>,
    #[serde(default)]
    pub target_types: Option<Vec<String>>,
    #[serde(default)]
    pub attributes: Option<Vec<String>>,
    /// Usage: only changeable for custom types (`system: false`).
    #[serde(default)]
    pub role: Option<String>,
}

/// Payload for updating an edge's metadata (attributes) on a reference.
#[derive(Debug, Deserialize, ToSchema)]
pub struct UpdateReferenceBody {
    /// Edge metadata, e.g. `{"quantity": 3, "position": 1}`.
    pub attributes: Value,
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

/// Whether an edge with exactly these endpoints and this relation definition
/// already exists.
///
/// Uses the `(in, definition_id)` index and filters the target afterwards, so the
/// lookup stays cheap for hub objects with many edges.
async fn edge_exists(
    db: &Db,
    source_key: &str,
    target_key: &str,
    definition_id: &str,
) -> Result<bool, ApiError> {
    let sql = "
        LET $src = type::record('object', $source_key);
        LET $tgt = type::record('object', $target_key);
        SELECT count() AS n FROM reference
          WHERE in = $src AND out = $tgt AND definition_id = $definition_id GROUP ALL;
    ";
    let mut response = db
        .inner()
        .query(sql)
        .bind(("source_key", source_key))
        .bind(("target_key", target_key))
        .bind(("definition_id", definition_id))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(2)?;
    Ok(rows
        .first()
        .and_then(|row| row.get("n"))
        .and_then(Value::as_i64)
        .unwrap_or(0)
        > 0)
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

/// Validates the edge-metadata keys against the relation definition's declared
/// `attributes` template. When the template is empty, arbitrary metadata is
/// allowed (built-in types / auto-managed edges). When it is non-empty, every
/// provided key must be declared (a value may be omitted).
async fn validate_edge_attributes(
    db: &Db,
    definition_key: &str,
    attributes: &Value,
) -> Result<(), ApiError> {
    if !attributes.is_object() && !attributes.is_null() {
        return Err(ApiError::bad_request("`attributes` must be an object"));
    }
    let definition = fetch_relation_definition(db, definition_key).await?;
    let Some(definition) = definition else {
        return Ok(());
    };
    let template = definition
        .get("attributes")
        .and_then(Value::as_array)
        .map(|array| array.iter().filter_map(Value::as_str).collect::<Vec<_>>())
        .unwrap_or_default();
    if template.is_empty() {
        return Ok(());
    }
    if let Some(map) = attributes.as_object() {
        for key in map.keys() {
            if !template.contains(&key.as_str()) {
                return Err(ApiError::bad_request(format!(
                    "attribute `{key}` is not declared for this relation type"
                )));
            }
        }
    }
    Ok(())
}

/// Validates a list of relation-type attribute names. Unknown attribute names
/// are allowed as forward references (the attribute becomes available once the
/// `attribute_definition` is created).
async fn validate_relation_attributes(db: &Db, attributes: &[String]) -> Result<(), ApiError> {
    for name in attributes {
        let trimmed = name.trim();
        if trimmed.is_empty() {
            return Err(ApiError::bad_request(
                "relation-type attribute names must not be empty",
            ));
        }
        let rows: Vec<Value> = db
            .inner()
            .query("SELECT name FROM attribute_definition WHERE name = $name LIMIT 1;")
            .bind(("name", trimmed.to_owned()))
            .await?
            .check()?
            .take(0)?;
        if rows.is_empty() {
            // Forward reference: allowed so the type can be created before the
            // attribute definition exists.
            continue;
        }
    }
    Ok(())
}

/// Renders a set of type names as a SurrealDB JSON array value.
fn type_names_array(values: &[String]) -> Value {
    Value::Array(
        values
            .iter()
            .map(|v| Value::String(v.trim().to_owned()))
            .filter(|v| v.as_str().map(|s| !s.is_empty()).unwrap_or(false))
            .collect(),
    )
}

/// Renders a set of attribute names as a SurrealDB JSON array value.
fn attr_names_array(values: &[String]) -> Value {
    Value::Array(
        values
            .iter()
            .map(|v| Value::String(v.trim().to_owned()))
            .filter(|v| v.as_str().map(|s| !s.is_empty()).unwrap_or(false))
            .collect(),
    )
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
        (status = 403, description = "Forbidden", body = Value),
        (status = 409, description = "That reference already exists", body = Value)
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
    // The usage decides which object the edge may point to (see `role`): a
    // categorisation only points at taxonomy objects (categories/classification
    // nodes, `object_type.taxonomy`), a collection membership only at
    // collections. This makes a misconfigured role impossible to use silently.
    let role = role_of(&definition);
    if role == ROLE_CATEGORY {
        if !crate::api::object_types::is_taxonomy_type(&state.db, target_type).await? {
            let allowed = category_target_types(&state.db).await?;
            return Err(ApiError::bad_request(format!(
                "relation type `{}` is a {} relation: the target must be an object of a taxonomy \
                 type (allowed: {})",
                definition_id_of(&definition, &body.definition_id),
                role,
                allowed.join(", ")
            )));
        }
    } else {
        for required in role_target_types(role) {
            if target_type != required {
                return Err(ApiError::bad_request(format!(
                    "relation type `{}` is a {} relation: the target must be an object of type `{required}`",
                    definition_id_of(&definition, &body.definition_id),
                    role
                )));
            }
        }
    }
    validate_edge_attributes(&state.db, &body.definition_id, &body.attributes).await?;

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
    // One edge per (source, target, definition): an identical edge is reported as a
    // conflict instead of being duplicated silently — a repeated MEF import then
    // stays idempotent (the worker maps the `409` to `existing`,
    // `plan/detail/demodaten-generator.md` §9). The check and the write below are
    // one critical section per edge, otherwise two parallel writers could both
    // pass the check.
    let edge_lock = [
        "edge",
        body.definition_id.as_str(),
        body.source.as_str(),
        body.target.as_str(),
    ]
    .join("\u{1}");
    let _edge_guard = state.key_locks.lock_keys(&[edge_lock]).await;
    if edge_exists(&state.db, source_key, target_key, &body.definition_id).await? {
        return Err(ApiError::conflict(format!(
            "reference `{}` from `{}` to `{}` already exists",
            audit_definition_id, body.source, body.target
        )));
    }
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

/// Lists relation definitions (used by the reference picker and by the worker to
/// resolve relation roles). Readable with an editor JWT or the worker API key.
#[utoipa::path(
    get,
    path = "/relation-definitions",
    responses(
        (status = 200, description = "Relation definitions", body = Vec<Value>),
        (status = 403, description = "Forbidden", body = Value)
    )
)]
pub async fn list_relation_definitions(
    headers: HeaderMap,
    State(state): State<AppState>,
) -> Result<Json<Vec<Value>>, ApiError> {
    crate::auth::api_key_or_role(&headers, &state.config, crate::auth::ROLE_EDITOR)?;
    let sql = "SELECT * FROM relation_definition ORDER BY name;";
    let mut response = state.db.inner().query(sql).await?.check()?;
    let definitions: Vec<Value> = response.take(0)?;
    Ok(Json(definitions))
}

/// Creates a custom relation definition (Phase 9). Custom types are **always**
/// aggregations and `system: false`; the single `depends_on` composition stays
/// the only composition definition.
#[utoipa::path(
    post,
    path = "/relation-definitions",
    request_body = CreateRelationDefinition,
    responses(
        (status = 201, description = "Created", body = Value),
        (status = 400, description = "Invalid payload", body = Value),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Forbidden", body = Value)
    )
)]
pub async fn create_relation_definition(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Json(body): Json<CreateRelationDefinition>,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    if !claims.is_admin() {
        return Err(ApiError::forbidden("requires admin role"));
    }
    let name = body.name.trim().to_owned();
    if name.is_empty() {
        return Err(ApiError::bad_request("`name` must not be empty"));
    }
    if !name
        .chars()
        .all(|c| c.is_alphanumeric() || c == '_' || c == '-')
    {
        return Err(ApiError::bad_request(
            "`name` may only contain letters, digits, `_` and `-`",
        ));
    }
    let existing: Vec<Value> = state
        .db
        .inner()
        .query("SELECT id FROM relation_definition WHERE name = $name LIMIT 1")
        .bind(("name", name.clone()))
        .await?
        .check()?
        .take(0)?;
    if !existing.is_empty() {
        return Err(ApiError::conflict(format!(
            "relation type `{name}` already exists"
        )));
    }

    let source_types = type_names_array(&body.source_types.unwrap_or_default());
    let role = custom_role(body.role.as_deref())?.to_owned();
    let requested_targets = body.target_types.clone().unwrap_or_default();
    check_role_target_types(&state.db, &role, &requested_targets).await?;
    // A role may fix the target type (e.g. `collection` ⇒ `collection` objects).
    // A categorisation keeps its requested taxonomy types, or none at all — then
    // every taxonomy type is accepted (resolved at reference creation).
    let enforced_targets = role_target_types(&role);
    let target_types = if enforced_targets.is_empty() {
        type_names_array(&requested_targets)
    } else {
        type_names_array(&enforced_targets)
    };
    let attributes_list = body.attributes.unwrap_or_default();
    let attributes = attr_names_array(&attributes_list);
    validate_relation_attributes(&state.db, &attributes_list).await?;

    let key = format!("rel_{name}");
    let mut content = Map::new();
    content.insert("name".to_owned(), Value::String(name.clone()));
    content.insert("kind".to_owned(), Value::String("aggregation".to_owned()));
    content.insert("role".to_owned(), Value::String(role.clone()));
    if let Some(label) = body.label.clone() {
        content.insert("label".to_owned(), label);
    } else {
        content.insert("label".to_owned(), json!({ "en": name }));
    }
    content.insert("source_types".to_owned(), source_types);
    content.insert("target_types".to_owned(), target_types);
    content.insert("attributes".to_owned(), attributes);
    content.insert("system".to_owned(), Value::Bool(false));

    state
        .db
        .inner()
        .query("LET $t = type::record('relation_definition', $key); CREATE $t CONTENT $content;")
        .bind(("key", key.clone()))
        .bind(("content", Value::Object(content)))
        .await?
        .check()?;

    let record = fetch_relation_definition(&state.db, &key)
        .await?
        .ok_or_else(|| ApiError::internal("created relation definition was not returned"))?;
    let record_id = record
        .get("id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &claims.sub,
            action: "relation_definition_created",
            object_id: Some(&record_id),
            from: None,
            to: None,
            metadata: Some(json!({ "name": name, "role": role })),
        },
    )
    .await;
    Ok((StatusCode::CREATED, Json(record)))
}

/// Partially updates a relation definition (label, type restrictions, attributes).
#[utoipa::path(
    patch,
    path = "/relation-definitions/{id}",
    request_body = UpdateRelationDefinition,
    responses(
        (status = 200, description = "Updated", body = Value),
        (status = 400, description = "Invalid payload", body = Value),
        (status = 403, description = "Forbidden", body = Value),
        (status = 404, description = "Not found", body = Value)
    )
)]
pub async fn update_relation_definition(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
    Json(body): Json<UpdateRelationDefinition>,
) -> Result<Json<Value>, ApiError> {
    if !claims.is_admin() {
        return Err(ApiError::forbidden("requires admin role"));
    }
    let key = key_of(&id);
    let current = fetch_relation_definition(&state.db, &key)
        .await?
        .ok_or_else(|| ApiError::not_found(format!("relation type `{key}` not found")))?;
    let record_id = current
        .get("id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();

    let mut content = Map::new();
    if let Some(label) = body.label {
        content.insert("label".to_owned(), label);
    }
    let is_system = current
        .get("system")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    // The usage of the built-in types is fixed; custom types may switch between
    // `link` and `category`.
    let effective_role = if let Some(requested) = body.role.as_deref() {
        let requested = requested.trim();
        if is_system && requested != role_of(&current) {
            return Err(ApiError::bad_request(format!(
                "the usage of the built-in relation type `{key}` is fixed to `{}`",
                role_of(&current)
            )));
        }
        custom_role(Some(requested))?.to_owned()
    } else {
        role_of(&current).to_owned()
    };
    if body.role.is_some() {
        content.insert("role".to_owned(), Value::String(effective_role.clone()));
    }
    if let Some(source_types) = body.source_types {
        content.insert("source_types".to_owned(), type_names_array(&source_types));
    }
    if let Some(target_types) = body.target_types {
        check_role_target_types(&state.db, &effective_role, &target_types).await?;
        content.insert("target_types".to_owned(), type_names_array(&target_types));
    }
    // A role change normalises the target types: `collection` is fixed to its
    // built-in type, `category` keeps only taxonomy types (a stale list such as
    // `["product"]` is dropped, otherwise the role rule could never be met).
    if body.role.is_some() {
        let enforced = role_target_types(&effective_role);
        if !enforced.is_empty() {
            content.insert("target_types".to_owned(), type_names_array(&enforced));
        } else if effective_role == ROLE_CATEGORY {
            let requested = content
                .get("target_types")
                .and_then(Value::as_array)
                .map(|array| {
                    array
                        .iter()
                        .filter_map(Value::as_str)
                        .map(str::to_owned)
                        .collect::<Vec<_>>()
                })
                .unwrap_or_default();
            let allowed = category_target_types(&state.db).await?;
            let kept: Vec<String> = requested
                .into_iter()
                .filter(|name| allowed.iter().any(|t| t == name))
                .collect();
            content.insert("target_types".to_owned(), type_names_array(&kept));
        }
    }
    if let Some(attributes) = body.attributes {
        validate_relation_attributes(&state.db, &attributes).await?;
        content.insert("attributes".to_owned(), attr_names_array(&attributes));
    }

    let mut response = state
        .db
        .inner()
        .query(
            "LET $t = type::record('relation_definition', $key); UPDATE $t MERGE $content; UPDATE $t SET updated_at = time::now(), updated_by = $user; SELECT * FROM $t;",
        )
        .bind(("key", key.clone()))
        .bind(("content", Value::Object(content)))
        .bind(("user", claims.sub.clone()))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(3)?;
    let record = rows
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::not_found(format!("relation type `{key}` not found")))?;
    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &claims.sub,
            action: "relation_definition_updated",
            object_id: Some(&record_id),
            from: None,
            to: None,
            metadata: Some(json!({ "name": key, "role": effective_role })),
        },
    )
    .await;
    Ok(Json(record))
}

/// Deletes a custom relation definition. `system` types are protected; a type
/// still used by references is rejected with 409.
#[utoipa::path(
    delete,
    path = "/relation-definitions/{id}",
    responses(
        (status = 204, description = "Deleted"),
        (status = 400, description = "System type or invalid id", body = Value),
        (status = 403, description = "Forbidden", body = Value),
        (status = 404, description = "Not found", body = Value),
        (status = 409, description = "Still used by references", body = Value)
    )
)]
pub async fn delete_relation_definition(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<StatusCode, ApiError> {
    if !claims.is_admin() {
        return Err(ApiError::forbidden("requires admin role"));
    }
    let key = key_of(&id);
    let current = fetch_relation_definition(&state.db, &key)
        .await?
        .ok_or_else(|| ApiError::not_found(format!("relation type `{key}` not found")))?;
    let record_id = current
        .get("id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    if current
        .get("system")
        .and_then(Value::as_bool)
        .unwrap_or(false)
    {
        return Err(ApiError::bad_request(format!(
            "relation type `{key}` is a system type and cannot be deleted"
        )));
    }
    let used: Vec<Value> = state
        .db
        .inner()
        .query("SELECT id FROM reference WHERE definition_id = $key LIMIT 1;")
        .bind(("key", key.clone()))
        .await?
        .check()?
        .take(0)?;
    if !used.is_empty() {
        return Err(ApiError::conflict(format!(
            "relation type `{key}` is still used by references"
        )));
    }
    state
        .db
        .inner()
        .query("DELETE type::record('relation_definition', $key);")
        .bind(("key", key.clone()))
        .await?
        .check()?;
    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &claims.sub,
            action: "relation_definition_deleted",
            object_id: Some(&record_id),
            from: None,
            to: None,
            metadata: Some(json!({ "name": key })),
        },
    )
    .await;
    Ok(StatusCode::NO_CONTENT)
}

/// Updates a reference's edge metadata (``attributes``). Used by the relations
/// editor to maintain per-edge attribute values.
#[utoipa::path(
    patch,
    path = "/references/{id}",
    request_body = UpdateReferenceBody,
    responses(
        (status = 200, description = "Updated reference", body = Value),
        (status = 400, description = "Invalid payload", body = Value),
        (status = 403, description = "Forbidden", body = Value),
        (status = 404, description = "Reference not found", body = Value)
    )
)]
pub async fn update_reference(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
    Json(body): Json<UpdateReferenceBody>,
) -> Result<Json<Value>, ApiError> {
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
    let reference = fetch_record(&state.db, "reference", key)
        .await?
        .ok_or_else(|| ApiError::not_found(format!("reference `{id}` not found")))?;
    let definition_key = reference
        .get("definition_id")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_owned();
    validate_edge_attributes(&state.db, &definition_key, &body.attributes).await?;

    // Update the edge metadata (a RELATION table; a trailing `SELECT * FROM $r`
    // in the same statement returns no rows — so re-fetch in a separate query).
    state
        .db
        .inner()
        .query("LET $r = type::record('reference', $key); UPDATE $r SET attributes = $attributes;")
        .bind(("key", key.to_owned()))
        .bind(("attributes", body.attributes))
        .await?
        .check()?;
    let record = fetch_record(&state.db, "reference", key)
        .await?
        .ok_or_else(|| ApiError::not_found(format!("reference `{id}` not found")))?;
    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &claims.sub,
            action: "reference_updated",
            object_id: reference
                .get("in")
                .and_then(Value::as_str)
                .map(str::to_owned)
                .as_deref(),
            from: None,
            to: None,
            metadata: Some(json!({ "reference_id": id })),
        },
    )
    .await;
    Ok(Json(record))
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
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(id): Path<String>,
    Query(params): Query<ReferenceListParams>,
) -> Result<Json<Vec<Value>>, ApiError> {
    crate::auth::api_key_or_role(&headers, &state.config, crate::auth::ROLE_EDITOR)?;
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
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(id): Path<String>,
) -> Result<StatusCode, ApiError> {
    crate::auth::api_key_or_role(&headers, &state.config, crate::auth::ROLE_EDITOR)?;
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
    /// of objects assigned to the category subtree, including subcategories).
    #[serde(default)]
    pub count_members: bool,
    /// Category relation type whose members are counted with `count_members`
    /// (default: the built-in primary type). Must have the usage `category`;
    /// an unknown or non-category type is rejected with `400`.
    #[serde(default)]
    pub definition_id: Option<String>,
    /// Which children to return: `all` (default), `composition` (everything
    /// **except** the taxonomy nodes) or `taxonomy` (only taxonomy nodes).
    /// Taxonomy nodes are the nodes of the category/classification trees and
    /// hang under the same root, but they are not part of the product structure.
    #[serde(default)]
    pub scope: Option<String>,
    /// Only children of this object type (e.g. `product`, `category`,
    /// `etim_class`) — one taxonomy at a time instead of every tree.
    #[serde(default, rename = "type")]
    pub type_: Option<String>,
}

/// Which children a caller wants: the whole tree, the composition structure or a
/// taxonomy's nodes.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum ChildScope {
    All,
    Composition,
    Taxonomy,
}

impl ChildScope {
    /// Parses the `scope` parameter (`all` when absent or empty).
    fn parse(value: Option<&str>) -> Result<Self, ApiError> {
        match value.map(str::trim) {
            None | Some("") | Some("all") => Ok(Self::All),
            Some("composition") => Ok(Self::Composition),
            Some("taxonomy") => Ok(Self::Taxonomy),
            Some(other) => Err(ApiError::bad_request(format!(
                "unknown `scope` `{other}` (expected all | composition | taxonomy)"
            ))),
        }
    }
}

/// Lists the direct composition children of an object.
///
/// Each child is returned as its object (`id`/`type`/`status`/`attributes`) plus
/// a `hasChildren` flag so the structure tree can show the expand arrow without
/// loading the whole subtree (lazy loading); `?limit`/`?offset` paginate nodes
/// with many direct children.
///
/// `?scope=composition` returns the product structure (every child **except** the
/// taxonomy nodes) and `?scope=taxonomy` only the nodes of a category or
/// classification tree; `?type=` narrows to one object type. The filter runs in
/// SurrealQL, so paging and counting apply to the filtered set — the universal
/// root carries thousands of taxonomy nodes, and a client-side filter would both
/// truncate the page and (with `count_members`) count subtrees nobody renders.
///
/// With `?count_members=true`, category children also carry a `memberCount` for
/// the category tree's counter — counting the members of the category relation
/// type given by `?definition_id` (default: the built-in primary type), so
/// additional categorisations have their own counter. All counts of a page are
/// computed in **one pass** (see `category_member_counts`).
///
/// Accepts an editor JWT or the `X-Api-Key` service account (the bulk worker
/// walks the tree for `consolidate_inheritance`).
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
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(id): Path<String>,
    Query(params): Query<ChildrenListParams>,
) -> Result<Json<Vec<Value>>, ApiError> {
    crate::auth::api_key_or_role(&headers, &state.config, crate::auth::ROLE_EDITOR)?;
    let key = parse_object_key(&id)?;
    if fetch_object(&state.db, key).await?.is_none() {
        return Err(ApiError::not_found(format!("object `{id}` not found")));
    }

    let scope = ChildScope::parse(params.scope.as_deref())?;
    let node_type = params
        .type_
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_owned);

    // Direct children of the parent (paginated). The type filter is pushed into
    // the SQL so `LIMIT`/`START` count the filtered set — but it must use the
    // **record-link traversal** `in.type`, never `in IN (SELECT VALUE id FROM
    // object WHERE …)`: measured on the universal root (5694 composition edges,
    // 5648 of them taxonomy nodes) the subquery shape needs **31.7 s**, because the
    // planner evaluates that subquery per candidate row, while `in.type …` answers
    // in 0.7 s (the `idx_ref_out_def` index still drives the lookup).
    let mut children_sql = String::from(
        "LET $parent = type::record('object', $key);
         SELECT in AS child FROM reference
         WHERE out = $parent AND definition_id = 'rel_depends_on'",
    );
    let taxonomy = match scope {
        ChildScope::All => None,
        _ => Some(crate::api::object_types::taxonomy_types(&state.db).await?),
    };
    match scope {
        ChildScope::All => {}
        ChildScope::Composition => children_sql.push_str(" AND in.type NOT IN $taxonomy"),
        ChildScope::Taxonomy => children_sql.push_str(" AND in.type IN $taxonomy"),
    }
    if node_type.is_some() {
        children_sql.push_str(" AND in.type = $node_type");
    }
    children_sql.push_str(" ORDER BY in LIMIT $limit START $offset;");

    let mut query = state
        .db
        .inner()
        .query(children_sql)
        .bind(("key", key.to_owned()))
        .bind(("limit", params.limit.unwrap_or(1000).clamp(1, 1000) as i64))
        .bind(("offset", params.offset as i64));
    if let Some(taxonomy) = &taxonomy {
        query = query.bind(("taxonomy", json!(taxonomy)));
    }
    if let Some(node_type) = &node_type {
        query = query.bind(("node_type", node_type.clone()));
    }
    let mut response = query.await?.check()?;
    let child_rows: Vec<Value> = response.take(1)?;
    let child_ids: Vec<String> = child_rows
        .iter()
        .filter_map(|row| row.get("child").and_then(Value::as_str).map(str::to_owned))
        .collect();
    if child_ids.is_empty() {
        return Ok(Json(vec![]));
    }

    // Fetch the child objects (so the client can resolve labels without the full tree).
    // A **record list literal** keeps this at index speed: with `array::map` a
    // 1000-child page took 16.6 s (measured), the literal takes 0.18 s.
    let literal =
        crate::api::keys::record_list_literal("object", child_ids.iter().map(String::as_str));
    let object_sql = format!("SELECT * FROM object WHERE id IN {literal} AND deleted_at IS NONE;");
    let mut response = state.db.inner().query(object_sql).await?.check()?;
    let objects: Vec<Value> = response.take(0)?;

    // Which of the children are themselves parents (have children)?
    let has_sql = format!(
        "SELECT out AS parent FROM reference\n         WHERE definition_id = 'rel_depends_on' AND out IN {literal};"
    );
    let mut response = state.db.inner().query(has_sql).await?.check()?;
    let parent_rows: Vec<Value> = response.take(0)?;
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
        // Category children: add the number of objects assigned through the
        // requested category relation type (default: the primary type) to the
        // category subtree (category tree counter). Every taxonomy node type
        // (`object_type.taxonomy`) carries a counter, so additional
        // categorisations and classification hierarchies show theirs too.
        let definition =
            resolve_category_definition(&state.db, params.definition_id.as_deref()).await?;
        let taxonomy = crate::api::object_types::taxonomy_types(&state.db).await?;
        let taxonomy_children: Vec<String> = result
            .iter()
            .filter(|value| {
                let node_type = value
                    .get("type")
                    .and_then(Value::as_str)
                    .unwrap_or_default();
                taxonomy.iter().any(|entry| entry == node_type)
            })
            .filter_map(|value| value.get("id").and_then(Value::as_str))
            .map(key_of)
            .collect();
        // **One** pass for the whole page: the per-node variant walked every
        // composition edge once per child, which took ~8 s for the root's 100
        // taxonomy children (measured with the full ETIM bundle installed).
        let counts = category_member_counts(&state.db, &taxonomy_children, &definition).await?;
        let counted: Vec<Value> = result
            .into_iter()
            .map(|mut value| {
                let node_key = value
                    .get("id")
                    .and_then(Value::as_str)
                    .map(key_of)
                    .unwrap_or_default();
                if let Some(count) = counts.get(&node_key) {
                    value["memberCount"] = json!(count);
                }
                value
            })
            .collect();
        return Ok(Json(counted));
    }
    Ok(Json(result))
}

/// Maximum number of levels walked when resolving an object's ancestor path
/// (a taxonomy is a hierarchy of classes, not thousands of levels deep).
const MAX_ANCESTOR_DEPTH: usize = 64;

/// Returns the path from the outermost composition ancestor down to the object.
///
/// The tree panels use it to show a search hit **in its hierarchy**: a hit may
/// sit below a class that is far beyond the loaded page (or outside the shown
/// taxonomy roots), and a flat hit list cannot show where it belongs. The list
/// starts at the top-most ancestor and **ends with the object itself**, so the
/// caller can render the whole path without merging its own node.
///
/// Each entry is the object document plus a `hasChildren` flag (same shape as
/// `GET /objects/{id}/children`), so the tree marks expandable nodes lazily. The
/// universal root is not part of the path — it is the parent of every tree's
/// top level, not a node of it.
#[utoipa::path(
    get,
    path = "/objects/{id}/ancestors",
    responses(
        (status = 200, description = "Ancestor path (root-most first, object last)", body = Vec<Value>),
        (status = 400, description = "Invalid id", body = Value),
        (status = 404, description = "Object not found", body = Value)
    )
)]
pub async fn list_object_ancestors(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(id): Path<String>,
) -> Result<Json<Vec<Value>>, ApiError> {
    crate::auth::api_key_or_role(&headers, &state.config, crate::auth::ROLE_EDITOR)?;
    let key = parse_object_key(&id)?;
    if fetch_object(&state.db, key).await?.is_none() {
        return Err(ApiError::not_found(format!("object `{id}` not found")));
    }

    // Walk up the `depends_on` chain (one query per level; taxonomy depth is
    // small). A repeated key means broken data, so the walk stops instead of
    // looping — the path stays bounded either way.
    let mut keys: Vec<String> = vec![key.to_owned()];
    let mut current = key.to_owned();
    for _ in 0..MAX_ANCESTOR_DEPTH {
        match parent_key(&state.db, &current).await? {
            None => break,
            Some(parent) if parent == "object_root" => break,
            Some(parent) => {
                if keys.contains(&parent) {
                    break;
                }
                keys.push(parent.clone());
                current = parent;
            }
        }
    }
    // Root-most first, the requested object last.
    keys.reverse();

    // Record list literals (see `keys::record_list_literal`): the ancestor path is
    // tiny, but the shape must not become the quadratic `array::map` variant.
    let literal = crate::api::keys::record_list_literal("object", keys.iter().map(String::as_str));
    let object_sql = format!("SELECT * FROM object WHERE id IN {literal} AND deleted_at IS NONE;");
    let mut response = state.db.inner().query(object_sql).await?.check()?;
    let objects: Vec<Value> = response.take(0)?;

    // Which of the path nodes are themselves parents (have children)?
    let has_sql = format!(
        "SELECT out AS parent FROM reference\n         WHERE definition_id = 'rel_depends_on' AND out IN {literal};"
    );
    let mut response = state.db.inner().query(has_sql).await?.check()?;
    let parent_rows: Vec<Value> = response.take(0)?;
    let parent_set: HashSet<String> = parent_rows
        .iter()
        .filter_map(|row| row.get("parent").and_then(Value::as_str).map(str::to_owned))
        .collect();

    let by_id: HashMap<String, Value> = objects
        .into_iter()
        .filter_map(|obj| {
            let id = obj.get("id").and_then(Value::as_str)?.to_owned();
            Some((id, obj))
        })
        .collect();
    let result: Vec<Value> = keys
        .iter()
        .filter_map(|node_key| {
            let id = format!("object:{node_key}");
            let mut value = by_id.get(&id)?.clone();
            value["hasChildren"] = json!(parent_set.contains(&id));
            Some(value)
        })
        .collect();
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

/// Returns the bare keys of every object assigned (via the given category
/// relation type) to the given category or any of its composition descendants.
/// Used for the server-side category filter (`GET /objects?category=...`);
/// `definition` selects a specific category relation (default: the built-in
/// primary type, resolved by the caller).
pub async fn category_member_keys(
    db: &Db,
    category_id: &str,
    definition: &str,
) -> Result<Vec<String>, ApiError> {
    let category_keys = subtree_keys(db, category_id).await?;
    // Record list literal, not `array::map` (the subtree of a root node carries
    // thousands of keys — see `keys::record_list_literal`).
    let literal =
        crate::api::keys::record_list_literal("object", category_keys.iter().map(String::as_str));
    let sql = format!(
        "SELECT in AS member FROM reference\n         WHERE definition_id = $def AND out IN {literal};"
    );
    let mut response = db
        .inner()
        .query(sql)
        .bind(("def", definition.to_owned()))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
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

/// Number of objects assigned to each node's composition subtree, for several
/// nodes in **one pass** — the batch variant of [`category_member_keys`].
///
/// Why it exists: a tree level with `count_members=true` asked per child, and
/// every call re-read **all** composition edges and walked one subtree. For the
/// universal root with the full ETIM bundle installed (5648 taxonomy children)
/// that was ~8 s per page of 100 nodes — more than the frontend's page timeout.
/// Here the edges (once) and the assignments (once) are read into maps and every
/// requested subtree is walked in memory (see [`subtree_member_counts`]).
///
/// Returns `{bare node key: member count}`.
pub async fn category_member_counts(
    db: &Db,
    node_ids: &[String],
    definition: &str,
) -> Result<HashMap<String, usize>, ApiError> {
    if node_ids.is_empty() {
        return Ok(HashMap::new());
    }
    // Composition edges (child -> parent or parent -> children), once.
    let mut response = db
        .inner()
        .query(
            "SELECT in AS child, out AS parent FROM reference \
             WHERE definition_id = 'rel_depends_on';",
        )
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    let mut children: HashMap<String, Vec<String>> = HashMap::new();
    for row in &rows {
        let (Some(child), Some(parent)) = (
            row.get("child").and_then(Value::as_str).map(key_of),
            row.get("parent").and_then(Value::as_str).map(key_of),
        ) else {
            continue;
        };
        children.entry(parent).or_default().push(child);
    }
    // Assignments of the requested category relation type, once.
    let mut response = db
        .inner()
        .query("SELECT in AS member, out AS node FROM reference WHERE definition_id = $def;")
        .bind(("def", definition.to_owned()))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    let mut assigned: HashMap<String, Vec<String>> = HashMap::new();
    for row in &rows {
        let (Some(member), Some(node)) = (
            row.get("member").and_then(Value::as_str).map(key_of),
            row.get("node").and_then(Value::as_str).map(key_of),
        ) else {
            continue;
        };
        assigned.entry(node).or_default().push(member);
    }
    Ok(subtree_member_counts(node_ids, &children, &assigned))
}

/// Distinct members per requested subtree, computed over in-memory maps.
///
/// Pure and unit-tested: an iterative walk with a visited set, so a cycle (or a
/// node shared by two branches, which a broken `depends_on` edge could produce)
/// terminates, and a member assigned to several nodes **inside one subtree** is
/// counted once — the same number the per-node variant returned.
pub(crate) fn subtree_member_counts(
    nodes: &[String],
    children: &HashMap<String, Vec<String>>,
    assigned: &HashMap<String, Vec<String>>,
) -> HashMap<String, usize> {
    let mut counts: HashMap<String, usize> = HashMap::with_capacity(nodes.len());
    for node in nodes {
        let mut seen_nodes: HashSet<&str> = HashSet::new();
        let mut members: HashSet<&str> = HashSet::new();
        let mut stack: Vec<&str> = vec![node.as_str()];
        while let Some(current) = stack.pop() {
            if !seen_nodes.insert(current) {
                continue;
            }
            if let Some(found) = assigned.get(current) {
                members.extend(found.iter().map(String::as_str));
            }
            if let Some(next) = children.get(current) {
                stack.extend(next.iter().map(String::as_str));
            }
        }
        counts.insert(node.clone(), members.len());
    }
    counts
}

/// Returns the bare object keys that are members of a materialized collection
/// (`collection_member` relation, `out` = the collection; flat, no hierarchy).
pub async fn collection_member_keys(db: &Db, collection_id: &str) -> Result<Vec<String>, ApiError> {
    let sql = "
        SELECT in AS member FROM reference
        WHERE definition_id = 'rel_collection_member' AND out = type::record('object', $col);
    ";
    let mut response = db
        .inner()
        .query(sql)
        .bind(("col", key_of(collection_id).to_owned()))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
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
    headers: HeaderMap,
    State(state): State<AppState>,
    Path(id): Path<String>,
    Query(params): Query<ReferenceWriteQuery>,
    Json(body): Json<ReparentObject>,
) -> Result<Json<Value>, ApiError> {
    // `api_key_or_role`, not `AuthUser`: the worker re-parents objects for the
    // bulk operation `change_parent` and for `depends_on` references in a MEF
    // import (service account, `X-Api-Key`).
    let user_id = crate::auth::api_key_or_role(&headers, &state.config, crate::auth::ROLE_EDITOR)?;
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
        metadata: json!({ "user_id": user_id.clone() }),
    });
    let outbox_created = crate::event::content(&crate::event::OutboxEvent {
        event_type: crate::event::EVENT_REFERENCE_CREATED,
        object_type: child_type.to_owned(),
        object_id: id.clone(),
        changes: json!({ "parent": body.parent.clone() }),
        metadata: json!({ "user_id": user_id.clone() }),
    });

    // Statements: BEGIN(0), LET child(1), LET parent(2), DELETE(3),
    // RELATE(4), [CREATE outbox deleted(5), CREATE outbox created(6)],
    // SELECT(7|5), COMMIT(8|6). The events are skipped for bulk imports
    // (`?emit_events=false`), like `create_reference` does.
    let emit_events = params.emit_events.unwrap_or(true);
    let event_statements = if emit_events {
        "CREATE outbox_event CONTENT $outbox_deleted;\n        CREATE outbox_event CONTENT $outbox_created;\n        "
    } else {
        ""
    };
    let select_index = if emit_events { 7 } else { 5 };
    let sql = format!(
        "
        BEGIN TRANSACTION;
        LET $child = type::record('object', $child_key);
        LET $parent = type::record('object', $parent_key);
        DELETE reference WHERE in = $child AND definition_id = 'rel_depends_on';
        RELATE $child->reference->$parent SET definition_id = 'rel_depends_on', type = 'depends_on', attributes = {{}};
        {event_statements}SELECT * FROM $child;
        COMMIT TRANSACTION;
    "
    );
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
    let objects: Vec<Value> = response.take(select_index)?;
    let object = objects
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::internal("updated object was not returned"))?;

    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &user_id,
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn child_scope_parses_the_documented_values() {
        assert_eq!(ChildScope::parse(None).expect("default"), ChildScope::All);
        assert_eq!(ChildScope::parse(Some("")).expect("empty"), ChildScope::All);
        assert_eq!(
            ChildScope::parse(Some("all")).expect("all"),
            ChildScope::All
        );
        assert_eq!(
            ChildScope::parse(Some(" composition ")).expect("composition"),
            ChildScope::Composition
        );
        assert_eq!(
            ChildScope::parse(Some("taxonomy")).expect("taxonomy"),
            ChildScope::Taxonomy
        );
        assert!(ChildScope::parse(Some("structure")).is_err());
    }

    /// `parent -> children` for `root -> [a, b]`, `a -> [c]`.
    fn tree() -> HashMap<String, Vec<String>> {
        HashMap::from([
            ("root".to_owned(), vec!["a".to_owned(), "b".to_owned()]),
            ("a".to_owned(), vec!["c".to_owned()]),
        ])
    }

    #[test]
    fn subtree_member_counts_adds_the_descendants() {
        // Two members on `c`, one on `b`, none on `a`/`root` directly.
        let assigned = HashMap::from([
            ("c".to_owned(), vec!["m1".to_owned(), "m2".to_owned()]),
            ("b".to_owned(), vec!["m3".to_owned()]),
        ]);
        let counts = subtree_member_counts(
            &[
                "root".to_owned(),
                "a".to_owned(),
                "b".to_owned(),
                "c".to_owned(),
            ],
            &tree(),
            &assigned,
        );
        assert_eq!(counts.get("root"), Some(&3));
        assert_eq!(counts.get("a"), Some(&2), "only the subtree of `a`");
        assert_eq!(counts.get("b"), Some(&1));
        assert_eq!(counts.get("c"), Some(&2));
    }

    #[test]
    fn subtree_member_counts_counts_a_member_once_per_subtree() {
        // The same object categorised in a node *and* its child: one member.
        let assigned = HashMap::from([
            ("a".to_owned(), vec!["m1".to_owned()]),
            ("c".to_owned(), vec!["m1".to_owned(), "m2".to_owned()]),
        ]);
        let counts = subtree_member_counts(&["a".to_owned(), "c".to_owned()], &tree(), &assigned);
        assert_eq!(counts.get("a"), Some(&2));
        assert_eq!(counts.get("c"), Some(&2));
    }

    #[test]
    fn subtree_member_counts_survives_a_cycle() {
        let children = HashMap::from([
            ("a".to_owned(), vec!["b".to_owned()]),
            ("b".to_owned(), vec!["a".to_owned()]),
        ]);
        let assigned = HashMap::from([("b".to_owned(), vec!["m1".to_owned()])]);
        let counts = subtree_member_counts(&["a".to_owned()], &children, &assigned);
        assert_eq!(counts.get("a"), Some(&1));
    }

    #[test]
    fn role_of_defaults_to_link() {
        assert_eq!(role_of(&json!({ "role": "category" })), ROLE_CATEGORY);
        assert_eq!(role_of(&json!({ "role": "  " })), ROLE_LINK);
        assert_eq!(role_of(&json!({})), ROLE_LINK);
    }

    #[test]
    fn custom_roles_accept_link_and_category_only() {
        assert_eq!(custom_role(None).expect("default role"), ROLE_LINK);
        assert_eq!(
            custom_role(Some("category")).expect("category"),
            ROLE_CATEGORY
        );
        assert!(custom_role(Some("composition")).is_err());
        assert!(custom_role(Some("collection")).is_err());
    }

    #[test]
    fn role_target_types_fix_the_target_side() {
        // Only `collection` is fixed to a built-in type; a categorisation may
        // point at every taxonomy type (resolved at reference creation).
        assert_eq!(
            role_target_types(ROLE_COLLECTION),
            vec!["collection".to_owned()]
        );
        assert!(role_target_types(ROLE_CATEGORY).is_empty());
        assert!(role_target_types(ROLE_LINK).is_empty());
    }

    #[test]
    fn a_category_role_only_accepts_taxonomy_targets() {
        let allowed = vec!["category".to_owned(), "etim_class".to_owned()];

        assert!(check_taxonomy_targets(&[], &allowed).is_ok());
        assert!(check_taxonomy_targets(&["etim_class".to_owned()], &allowed).is_ok());
        assert!(check_taxonomy_targets(&["product".to_owned()], &allowed).is_err());
        // A non-taxonomy type stays invalid even if the list is empty.
        assert!(check_taxonomy_targets(&["product".to_owned()], &[]).is_err());
        // Other roles keep the fixed-target rule.
        assert!(check_taxonomy_targets(&["product".to_owned()], &allowed).is_err());
    }
}
