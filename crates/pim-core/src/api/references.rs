//! Reference (graph edge) handlers.
//!
//! References are native SurrealDB RELATION records: `in` = source, `out` =
//! target (see `plan/detail/relationen.md`).

use std::collections::{HashMap, HashSet};
use std::sync::Arc;

use axum::Json;
use axum::extract::{Path, Query, State};
use axum::http::{HeaderMap, StatusCode};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, json};
use utoipa::{IntoParams, ToSchema};

use crate::app::AppState;
use crate::audit::{self, AuditEntry};
use crate::auth::AuthUser;
use crate::db::Db;
use crate::error::ApiError;

pub(crate) const COMPOSITION_DEFINITION: &str = "rel_depends_on";

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

pub(crate) async fn fetch_relation_definition(
    db: &Db,
    key: &str,
) -> Result<Option<Value>, ApiError> {
    fetch_record(db, "relation_definition", key).await
}

/// Whether an edge with exactly these endpoints and this relation definition
/// already exists.
///
/// Uses the `(out, definition_id)` index and filters the source afterwards, so the
/// lookup stays cheap for hub objects with many edges. Both endpoints are rendered
/// as **record literals**: with bound records the planner falls back to a full
/// `TableScan` over all 275 863 edges (see `keys::record_literal`); the bound
/// *definition* stays a filter on top of the index scan.
async fn edge_exists(
    db: &Db,
    source_key: &str,
    target_key: &str,
    definition_id: &str,
) -> Result<bool, ApiError> {
    let (Some(source), Some(target)) = (
        crate::api::keys::record_literal("object", source_key),
        crate::api::keys::record_literal("object", target_key),
    ) else {
        return Ok(false);
    };
    let sql = format!(
        "SELECT count() AS n FROM reference
          WHERE in = {source} AND out = {target} AND definition_id = $definition_id GROUP ALL;"
    );
    let mut response = db
        .inner()
        .query(sql)
        .bind(("definition_id", definition_id.to_owned()))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
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
pub(crate) fn check_type_restriction(
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
    let user_id = crate::auth::require_permission(&state, &headers, "reference.create")?;
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

    // Composition and categorisation edges feed the dashboard aggregation.
    state.cache.invalidate_stats();
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
    crate::auth::require_permission(&state, &headers, "attribute_definition.read")?;
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
    claims.require("attribute_definition.manage")?;
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
    // A relation definition's role decides which edges count as categorisation.
    state.cache.invalidate_stats();
    state.cache.invalidate_relation_definitions().await;
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
    claims.require("attribute_definition.manage")?;
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
    // A role change reclassifies the edges of that relation.
    state.cache.invalidate_stats();
    state.cache.invalidate_relation_definitions().await;
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
    claims.require("attribute_definition.manage")?;
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
    state.cache.invalidate_stats();
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
    claims.require("reference.create")?;
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
    state.cache.invalidate_stats();
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
    crate::auth::require_permission(&state, &headers, "reference.read")?;
    let key = parse_object_key(&id)?;
    if fetch_object(&state.db, key).await?.is_none() {
        return Err(ApiError::not_found(format!("object `{id}` not found")));
    }
    // The object is rendered as a **record literal** in both comparisons: with a
    // bound record SurrealDB scans all edges (`TableScan`); the literal turns it
    // into a `UnionIndexScan` over `idx_ref_in`/`idx_ref_out` (see
    // `keys::record_literal`). This runs on every object delete, so the difference
    // grows with the reference table.
    let Some(source) = crate::api::keys::record_literal("object", key) else {
        return Err(ApiError::bad_request(format!("invalid object id `{id}`")));
    };
    let sql = format!("SELECT * FROM reference WHERE in = {source} OR out = {source};");
    let mut response = state.db.inner().query(sql).await?.check()?;
    let references: Vec<Value> = response.take(0)?;

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
///
/// The composition edge is **not** removable here: it is the object's parent
/// link, and deleting it leaves the object without a parent — invisible in the
/// structure tree and unreachable for the delete cascade, which is exactly the
/// state 81 demo variants were found in on 2026-09-17 (their edges had been
/// removed without any trace, because an edge delete by id writes neither an
/// audit entry nor an event). A parent link is replaced by re-parenting
/// (`PATCH /objects/{id}/parent`) or removed together with the object.
#[utoipa::path(
    delete,
    path = "/references/{id}",
    responses(
        (status = 204, description = "Deleted"),
        (status = 400, description = "Invalid id or composition edge", body = Value),
        (status = 404, description = "Reference not found", body = Value)
    )
)]
pub async fn delete_reference(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(id): Path<String>,
) -> Result<StatusCode, ApiError> {
    crate::auth::require_permission(&state, &headers, "reference.delete")?;
    let (table, key) = id
        .split_once(':')
        .ok_or_else(|| ApiError::bad_request(format!("invalid reference id `{id}`")))?;
    if table != "reference" {
        return Err(ApiError::bad_request(format!(
            "id `{id}` does not reference table `reference`"
        )));
    }
    let Some(edge) = fetch_record(&state.db, "reference", key).await? else {
        return Err(ApiError::not_found(format!("reference `{id}` not found")));
    };
    if is_composition_edge_value(&edge) {
        return Err(ApiError::bad_request(
            "the composition edge is changed by re-parenting and removed with the object",
        ));
    }
    let sql = "DELETE type::record('reference', $key);";
    state
        .db
        .inner()
        .query(sql)
        .bind(("key", key))
        .await?
        .check()?;
    state.cache.invalidate_stats();
    Ok(StatusCode::NO_CONTENT)
}

/// Whether a loaded `reference` record is the composition edge (`rel_depends_on`).
fn is_composition_edge_value(edge: &Value) -> bool {
    edge.get("definition_id")
        .and_then(Value::as_str)
        .map(|definition| definition.trim_matches('`') == COMPOSITION_DEFINITION)
        .unwrap_or(false)
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
    let caller = crate::auth::require_caller(&state, &headers, "reference.read")?;
    let key = parse_object_key(&id)?;
    require_visible_object(&state, &caller, &id, key).await?;

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
    //
    // The parent is rendered as a **record literal** (see `keys::record_literal`):
    // bound as a param the same page needs **245 ms** on the `perf` dataset — the
    // planner scans the whole `rel_depends_on` partition (70 314 edges) and sorts
    // it — while the literal drives `idx_ref_out_def` and answers in **23 ms**.
    let Some(parent) = crate::api::keys::record_literal("object", key) else {
        return Err(ApiError::bad_request(format!("invalid object id `{id}`")));
    };
    let mut children_sql = format!(
        "SELECT in AS child FROM reference
         WHERE out = {parent} AND definition_id = 'rel_depends_on'"
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
        .bind(("limit", params.limit.unwrap_or(1000).clamp(1, 1000) as i64))
        .bind(("offset", params.offset as i64));
    if let Some(taxonomy) = &taxonomy {
        query = query.bind(("taxonomy", json!(taxonomy)));
    }
    if let Some(node_type) = &node_type {
        query = query.bind(("node_type", node_type.clone()));
    }
    let mut response = query.await?.check()?;
    let child_rows: Vec<Value> = response.take(0)?;
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
    let caller = crate::auth::require_caller(&state, &headers, "reference.read")?;
    let key = parse_object_key(&id)?;
    require_visible_object(&state, &caller, &id, key).await?;

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
    claims.require("reference.read")?;
    let caller = crate::auth::Caller::User(Box::new(claims));
    // The tree carries the **ids** of the whole composition structure, so a scoped caller sees
    // the nodes inside their scope — and the paths to them: the ancestors of a visible object are
    // part of that object (the taxonomy above a product is not in the scope, and without the path
    // the caller would look at an empty tree). `in.` renders the scope against the edge's child,
    // since this query reads `reference` and not `object`. An unrestricted caller runs the query
    // without a condition and without the second lookup.
    let scope = crate::scope::for_caller_on(&state, &caller, "in.").await?;
    let sql =
        "SELECT in AS child, out AS parent FROM reference WHERE definition_id = 'rel_depends_on';";
    let mut response = state.db.inner().query(sql).await?.check()?;
    let rows: Vec<Value> = response.take(0)?;

    let mut children: HashMap<String, Vec<String>> = HashMap::new();
    let mut parents: HashMap<String, String> = HashMap::new();
    for row in &rows {
        let child = row.get("child").and_then(Value::as_str).map(str::to_owned);
        let parent = row.get("parent").and_then(Value::as_str).map(str::to_owned);
        if let (Some(child), Some(parent)) = (child, parent) {
            children
                .entry(parent.clone())
                .or_default()
                .push(child.clone());
            parents.insert(child, parent);
        }
    }

    if let Some(clause) = &scope {
        // Which of the composition nodes are inside the scope? One query on the child side of
        // the edge, so it returns the visible nodes and not the catalogue.
        let sql = format!(
            "SELECT in AS child FROM reference \
                 WHERE definition_id = 'rel_depends_on' AND {};",
            clause.conditions.join(" AND ")
        );
        let mut request = state.db.inner().query(sql);
        for (name, value) in &clause.binds {
            request = request.bind((name.as_str(), value.clone()));
        }
        let mut response = request.await?.check()?;
        let rows: Vec<Value> = response.take(0)?;
        let visible: HashSet<String> = rows
            .iter()
            .filter_map(|row| row.get("child").and_then(Value::as_str))
            .map(str::to_owned)
            .collect();
        // The visible nodes plus every step on the way to them. A parent already kept needs
        // no second walk: its own path is in the set as well.
        let mut keep = visible.clone();
        for node in &visible {
            let mut current = node.clone();
            while let Some(parent) = parents.get(&current) {
                if !keep.insert(parent.clone()) {
                    break;
                }
                current = parent.clone();
            }
        }
        // Edges outside that set disappear, so the walk below never reaches them.
        for kids in children.values_mut() {
            kids.retain(|child| keep.contains(child));
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

/// Rejects an object the caller may not read (`404`), for the structural views.
///
/// The anchor check of `GET /objects/{id}/children` and `…/ancestors`: a caller may read the
/// structure of an object they may read and learns nothing about one they may not — the same
/// `404` `GET /objects/{id}` answers, so the id does not become an oracle. An unrestricted caller
/// adds no condition at all (rule R3).
async fn require_visible_object(
    state: &AppState,
    caller: &crate::auth::Caller,
    id: &str,
    key: &str,
) -> Result<Value, ApiError> {
    let mut conditions: Vec<String> = vec!["deleted_at IS NONE".to_owned()];
    let mut binds: Vec<(String, Value)> = Vec::new();
    crate::scope::apply(
        crate::scope::for_caller(state, caller).await?,
        &mut conditions,
        &mut binds,
    );
    let sql = format!(
        "SELECT * FROM type::record('object', $key) WHERE {};",
        conditions.join(" AND ")
    );
    let mut request = state.db.inner().query(sql).bind(("key", key.to_owned()));
    for (name, value) in binds {
        request = request.bind((name, value));
    }
    let mut response = request.await?.check()?;
    let rows: Vec<Value> = response.take(0)?;
    rows.into_iter()
        .next()
        .ok_or_else(|| ApiError::not_found(format!("object `{id}` not found")))
}

/// Returns every descendant (and itself) of a composition node as bare object
/// keys, breadth-first. Used for the server-side subtree filter
/// (`GET /objects?ancestor=...`) that replaces client-side tree traversal once
/// the structure tree is lazy-loaded.
///
/// Loads the whole `rel_depends_on` edge set; callers that need several subtrees
/// (batch delete) load it once via [`composition_children`] and use
/// [`subtree_keys_in`].
pub async fn subtree_keys(db: &Db, root_id: &str) -> Result<Vec<String>, ApiError> {
    let children = composition_children(db).await?;
    Ok(subtree_keys_in(&children, root_id))
}

/// Loads the composition tree as a parent -> children adjacency map.
///
/// One query for the whole edge set; a per-root `WHERE out = …` query looks
/// cheap but repeats the traversal for every root — for a batch that is N full
/// scans of `reference` where one is enough
/// (`plan/detail/demodaten-generator.md` §4.3).
pub async fn composition_children(db: &Db) -> Result<HashMap<String, Vec<String>>, ApiError> {
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
    Ok(children)
}

/// Returns `root_id` and every composition descendant as bare object keys,
/// using a preloaded adjacency map (see [`composition_children`]).
pub fn subtree_keys_in(children: &HashMap<String, Vec<String>>, root_id: &str) -> Vec<String> {
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
    result.iter().map(|id| key_of(id)).collect()
}

/// Returns the bare keys of the objects **below** `root_id` in the composition
/// tree (children, grandchildren, …), breadth-first with **one indexed query per
/// level**.
///
/// Use this instead of [`subtree_keys`] when a single object is inspected on a hot
/// path: `subtree_keys` loads the whole `rel_depends_on` edge set (~12 k rows on a
/// demo-sized dataset, ~1–2 MB JSON) on every call, which cost ~300 ms per write
/// when the inherited-computed cascade asked for a leaf's descendants
/// (`plan/detail/demodaten-generator.md` §4.3). Here a leaf costs one small
/// indexed query, and a node with a subtree costs a handful of level queries
/// instead of one table scan.
pub async fn descendant_keys(
    db: &Db,
    root_id: &str,
    max_depth: usize,
) -> Result<Vec<String>, ApiError> {
    let mut result: Vec<String> = Vec::new();
    let mut seen: HashSet<String> = HashSet::from([key_of(root_id)]);
    let mut level: Vec<String> = vec![key_of(root_id)];
    for _ in 0..max_depth {
        if level.is_empty() {
            break;
        }
        let literal =
            crate::api::keys::record_list_literal("object", level.iter().map(String::as_str));
        if literal == "[]" {
            break;
        }
        let mut response = db
            .inner()
            .query(format!(
                "SELECT VALUE in FROM reference \
                 WHERE out IN {literal} AND definition_id = 'rel_depends_on';"
            ))
            .await?
            .check()?;
        let rows: Vec<Value> = response.take(0)?;
        let mut next: Vec<String> = Vec::new();
        for row in rows {
            let Some(key) = row.as_str().map(key_of) else {
                continue;
            };
            if seen.insert(key.clone()) {
                result.push(key.clone());
                next.push(key);
            }
        }
        level = next;
    }
    Ok(result)
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

/// Bare keys of every object that is categorised in a dimension — the value of
/// the `uncategorized` filter's `NOT IN` list.
///
/// The whole counterpart of [`category_member_keys`]: that one walks a subtree,
/// this one asks the definition directly (one flat read of the relation rows).
pub async fn categorised_object_keys(db: &Db, definition: &str) -> Result<Vec<String>, ApiError> {
    let mut response = db
        .inner()
        .query("SELECT in AS member FROM reference WHERE definition_id = $def;")
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
    // The catalog check keeps the worker's machine path: it re-parents objects for the
    // bulk operation `change_parent` and for `depends_on` references in a MEF import
    // (service account, `X-Api-Key`).
    let user_id = crate::auth::require_permission(&state, &headers, "reference.create")?;
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

    // Re-parenting moves an object inside the composition tree.
    state.cache.invalidate_stats();
    Ok(Json(object))
}

/// Upper bound of rows per `POST /references/batch` call (`references` +
/// `reparents` together).
pub const MAX_BATCH_REFERENCE_ROWS: usize = 500;

/// One edge to create (mirrors [`CreateReference`]).
#[derive(Debug, Deserialize, ToSchema)]
pub struct BatchReferenceItem {
    /// Source object id (`object:<ulid>`).
    pub source: String,
    /// Target object id (`object:<ulid>`).
    pub target: String,
    /// `relation_definition` record key (e.g. `rel_fits_vehicle`).
    pub definition_id: String,
    /// Edge metadata.
    #[serde(default = "default_object")]
    pub attributes: Value,
}

/// One object to move in the composition tree (`PATCH /objects/{id}/parent`).
#[derive(Debug, Deserialize, ToSchema)]
pub struct BatchReparentItem {
    /// Object to move (`object:<ulid>`).
    pub source: String,
    /// Its new composition parent (`object:<ulid>`).
    pub parent: String,
}

/// Payload of `POST /references/batch`.
#[derive(Debug, Deserialize, ToSchema)]
pub struct BatchReferenceRequest {
    /// Edges to create.
    #[serde(default)]
    pub references: Vec<BatchReferenceItem>,
    /// Composition moves. The invariant allows exactly one parent edge, so a
    /// `depends_on` relationship is applied as a re-parent — exactly like a MEF
    /// `depends_on` reference.
    #[serde(default)]
    pub reparents: Vec<BatchReparentItem>,
}

/// Per-entry outcome of a batch reference write.
#[derive(Debug, Serialize, ToSchema)]
pub struct BatchReferenceItemResult {
    /// `reference` or `reparent`.
    pub kind: String,
    /// Position inside the request's `references`/`reparents` array.
    pub index: usize,
    pub source: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub target: Option<String>,
    /// `created`, `existing`, `reparented` or `failed`.
    pub action: String,
    /// Failure message (only for `failed`).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

/// Result of `POST /references/batch`.
#[derive(Debug, Serialize, ToSchema)]
pub struct BatchReferenceResponse {
    pub created: usize,
    pub existing: usize,
    pub reparented: usize,
    pub failed: usize,
    pub results: Vec<BatchReferenceItemResult>,
}

/// An entry of [`BatchReferenceRequest::references`] that passed preparation.
struct PreparedBatchEdge {
    index: usize,
    /// Id as it came in (`object:<ulid>`) — used for the lock key and the event.
    source_id: String,
    target_id: String,
    source_key: String,
    target_key: String,
    definition_id: String,
    definition_name: String,
    attributes: Value,
    source_type: String,
    /// Whether the edge is the composition relation (a single parent edge).
    composition: bool,
}

/// An entry of [`BatchReferenceRequest::reparents`] that passed preparation.
struct PreparedBatchReparent {
    index: usize,
    source_id: String,
    parent_id: String,
    source_key: String,
    old_parent_id: Option<String>,
    source_type: String,
}

/// Creates many references and/or re-parents many objects with **one**
/// transaction.
///
/// `POST /references` costs one HTTP request plus a write path (lookups,
/// transaction, audit, outbox) **per edge**; an import spends its time there
/// instead of in the database — measured 2026-09-16: the reference phase of a
/// 7 992-object chunk ran at **11.5 edges/s** while the same chunk's objects went
/// through `POST /objects/batch-upsert` at ~141 objects/s
/// (`plan/detail/demodaten-generator.md` §4.3). This endpoint amortises exactly
/// those round trips:
///
/// - all relation definitions and all involved objects are read in **one** query
///   each (instead of two lookups per edge),
/// - existing edges are resolved with **one** query per batch and reported as
///   `existing` (idempotency without relying on the `409` message),
/// - missing objects, role violations, edge-attribute violations, cycles and
///   objects without a composition parent are reported **per entry** — one bad
///   entry never fails the batch,
/// - the edges of the batch are locked as one critical section
///   (`crate::key_lock`, the same key the single endpoint uses),
/// - all edges, all re-parents and all events go into **one** transaction.
///
/// Semantics stay those of the single endpoints: `depends_on` is applied as a
/// re-parent (see `PATCH /objects/{id}/parent`), everything else is a new edge.
#[utoipa::path(
    post,
    path = "/references/batch",
    request_body = BatchReferenceRequest,
    params(ReferenceWriteQuery),
    responses(
        (status = 200, description = "Per-entry results", body = BatchReferenceResponse),
        (status = 400, description = "Invalid payload", body = Value),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Forbidden", body = Value),
        (status = 413, description = "Too many rows", body = Value)
    )
)]
pub async fn batch_create_references(
    State(state): State<AppState>,
    headers: HeaderMap,
    Query(params): Query<ReferenceWriteQuery>,
    Json(body): Json<BatchReferenceRequest>,
) -> Result<Json<BatchReferenceResponse>, ApiError> {
    let user_id = crate::auth::require_permission(&state, &headers, "reference.create")?;
    let total = body.references.len() + body.reparents.len();
    if total == 0 {
        return Err(ApiError::bad_request(
            "`references` and `reparents` are both empty",
        ));
    }
    if total > MAX_BATCH_REFERENCE_ROWS {
        return Err(ApiError::payload_too_large(format!(
            "batch exceeds the limit of {MAX_BATCH_REFERENCE_ROWS} rows \
             (`references` + `reparents`)"
        )));
    }
    let emit_events = params.emit_events.unwrap_or(true);
    let started = std::time::Instant::now();

    // ---- preparation (no database access) ----------------------------------
    let mut results: Vec<BatchReferenceItemResult> = Vec::with_capacity(total);
    let mut edges: Vec<Option<PreparedBatchEdge>> = Vec::with_capacity(body.references.len());
    let mut moves: Vec<Option<PreparedBatchReparent>> = Vec::with_capacity(body.reparents.len());
    for (index, item) in body.references.iter().enumerate() {
        let source_id = item.source.trim().to_owned();
        let target_id = item.target.trim().to_owned();
        let definition_id = item.definition_id.trim().to_owned();
        let mut failure: Option<String> = None;
        if definition_id.is_empty() {
            failure = Some("entry without `definition_id`".to_owned());
        }
        if !item.attributes.is_object() {
            failure.get_or_insert_with(|| "`attributes` must be an object".to_owned());
        }
        let keys = match (parse_object_key(&source_id), parse_object_key(&target_id)) {
            (Ok(source_key), Ok(target_key)) => {
                if source_key == target_key {
                    failure.get_or_insert_with(|| "source and target must differ".to_owned());
                }
                Some((source_key.to_owned(), target_key.to_owned()))
            }
            (Err(error), _) | (_, Err(error)) => {
                failure.get_or_insert(error.message().to_owned());
                None
            }
        };
        match (failure, keys) {
            (None, Some((source_key, target_key))) => {
                edges.push(Some(PreparedBatchEdge {
                    index,
                    source_id,
                    target_id,
                    source_key,
                    target_key,
                    composition: definition_id == COMPOSITION_DEFINITION,
                    definition_id,
                    definition_name: String::new(),
                    attributes: item.attributes.clone(),
                    source_type: String::new(),
                }));
            }
            (failure, _) => {
                results.push(reference_result(
                    "reference",
                    index,
                    &source_id,
                    Some(target_id),
                    "failed",
                    failure,
                ));
                edges.push(None);
            }
        }
    }
    for (index, item) in body.reparents.iter().enumerate() {
        let source_id = item.source.trim().to_owned();
        let parent_id = item.parent.trim().to_owned();
        let source_key = match (parse_object_key(&source_id), parse_object_key(&parent_id)) {
            (Ok(source_key), Ok(parent_key)) => {
                if source_key == parent_key {
                    results.push(reference_result(
                        "reparent",
                        index,
                        &source_id,
                        Some(parent_id.clone()),
                        "failed",
                        Some("an object cannot be its own parent".to_owned()),
                    ));
                    moves.push(None);
                    continue;
                }
                source_key.to_owned()
            }
            (Err(error), _) | (_, Err(error)) => {
                results.push(reference_result(
                    "reparent",
                    index,
                    &source_id,
                    Some(parent_id),
                    "failed",
                    Some(error.message().to_owned()),
                ));
                moves.push(None);
                continue;
            }
        };
        moves.push(Some(PreparedBatchReparent {
            index,
            source_id,
            parent_id,
            source_key,
            old_parent_id: None,
            source_type: String::new(),
        }));
    }

    // ---- one lookup per resource instead of per entry ----------------------
    let definitions = load_relation_definitions(&state.db).await?;
    let mut object_keys: Vec<String> = Vec::new();
    let mut composition_children: Vec<String> = Vec::new();
    let mut new_parents: Vec<String> = Vec::new();
    for edge in edges.iter().flatten() {
        object_keys.push(edge.source_key.clone());
        object_keys.push(edge.target_key.clone());
        if edge.composition {
            composition_children.push(edge.source_id.clone());
            new_parents.push(edge.target_id.clone());
        }
    }
    for item in moves.iter().flatten() {
        object_keys.push(item.source_key.clone());
        object_keys.push(key_of(&item.parent_id));
        composition_children.push(item.source_id.clone());
        new_parents.push(item.parent_id.clone());
    }
    let composition_definition = definitions.get(COMPOSITION_DEFINITION).cloned();
    let types = load_object_types(&state.db, &object_keys).await?;
    // The current composition parent (single-parent invariant, no-op detection)
    // and the parent chain of every **new parent** (cycle check) — one query each.
    let parents = load_composition_parents(&state.db, &composition_children).await?;
    let chain = load_parent_chain(&state.db, &new_parents).await?;
    let category_targets: Option<Vec<String>> = if edges.iter().flatten().any(|edge| {
        definitions
            .get(&edge.definition_id)
            .map(|definition| role_of(definition) == ROLE_CATEGORY)
            .unwrap_or(false)
    }) {
        Some(category_target_types(&state.db).await?)
    } else {
        None
    };

    // ---- validate every entry ----------------------------------------------
    let mut existing: HashSet<String> = HashSet::new();
    let mut batch_seen: HashSet<String> = HashSet::new();
    // New composition parent of every source the batch touches (edge *or* move),
    // so a cycle inside the batch is caught as well (the database does not know
    // those assignments yet).
    let mut batch_parents: HashMap<String, String> = HashMap::new();
    for entry in edges.iter_mut() {
        // `take`: what stays `None` is not written (the entry is reported and
        // dropped); survivors are put back for the write step.
        let Some(edge) = entry.as_mut() else {
            continue;
        };
        let Some(definition) = definitions.get(&edge.definition_id) else {
            results.push(reference_result(
                "reference",
                edge.index,
                &edge.source_id,
                Some(edge.target_id.clone()),
                "failed",
                Some(format!(
                    "unknown relation definition `{}`",
                    edge.definition_id
                )),
            ));
            *entry = None;
            continue;
        };
        edge.definition_name = definition
            .get("name")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_owned();
        let source_type = types.get(&edge.source_key).cloned();
        let target_type = types.get(&edge.target_key).cloned();
        let mut failure = if source_type.is_none() {
            Some("source object not found".to_owned())
        } else if target_type.is_none() {
            Some("target object not found".to_owned())
        } else {
            let source_type = source_type.as_deref().unwrap_or_default();
            let target_type = target_type.as_deref().unwrap_or_default();
            check_type_restriction(definition, source_type, target_type)
                .and_then(|()| {
                    check_edge_role(
                        definition,
                        &edge.definition_id,
                        target_type,
                        category_targets.as_deref(),
                    )
                })
                .and_then(|()| validate_edge_attribute_names(definition, &edge.attributes))
                .err()
                .map(|error| error.message().to_owned())
        };
        if failure.is_none() && edge.composition {
            // A composition edge obeys the single-parent invariant exactly like
            // `POST /references` (identical messages); `batch_parents` catches a
            // second composition edge for the same source *inside* the batch.
            let has_parent = parents.contains_key(&edge.source_key)
                || batch_parents.contains_key(&edge.source_key);
            failure = if has_parent {
                Some("source object already has a composition parent; use re-parenting".to_owned())
            } else if creates_cycle(&chain, &batch_parents, &edge.source_key, &edge.target_key) {
                Some("would create a cycle".to_owned())
            } else {
                None
            };
            if failure.is_none() {
                batch_parents.insert(edge.source_key.clone(), edge.target_key.clone());
            }
        }
        if failure.is_none() {
            edge.source_type = source_type.unwrap_or_default();
            // Duplicates inside the batch would be invisible to the database
            // (both writes would "succeed").
            let key = format!(
                "{}\u{1}{}\u{1}{}",
                edge.definition_id, edge.source_id, edge.target_id
            );
            if !batch_seen.insert(key) {
                failure = Some("duplicate edge inside the batch".to_owned());
            }
        }
        if let Some(message) = failure {
            results.push(reference_result(
                "reference",
                edge.index,
                &edge.source_id,
                Some(edge.target_id.clone()),
                "failed",
                Some(message),
            ));
            *entry = None;
        }
    }
    // One query for every edge the batch touches.
    existing.extend(load_existing_edges(&state.db, edges.iter().flatten()).await?);

    let mut reparent_rows: Vec<Value> = Vec::new();
    for entry in moves.iter_mut() {
        let Some(item) = entry.as_mut() else {
            continue;
        };
        let Some(source_type) = types.get(&item.source_key).cloned() else {
            results.push(reference_result(
                "reparent",
                item.index,
                &item.source_id,
                Some(item.parent_id.clone()),
                "failed",
                Some("object not found".to_owned()),
            ));
            *entry = None;
            continue;
        };
        item.source_type = source_type;
        let Some(definition) = composition_definition.as_ref() else {
            results.push(reference_result(
                "reparent",
                item.index,
                &item.source_id,
                Some(item.parent_id.clone()),
                "failed",
                Some("composition definition is missing".to_owned()),
            ));
            *entry = None;
            continue;
        };
        let child_type = item.source_type.clone();
        let parent_key = key_of(&item.parent_id);
        let Some(parent_type) = types.get(&parent_key).cloned() else {
            results.push(reference_result(
                "reparent",
                item.index,
                &item.source_id,
                Some(item.parent_id.clone()),
                "failed",
                Some("parent object not found".to_owned()),
            ));
            *entry = None;
            continue;
        };
        let Some(old_parent_id) = parents.get(&item.source_key).cloned() else {
            // Identical message to `PATCH /objects/{id}/parent`.
            results.push(reference_result(
                "reparent",
                item.index,
                &item.source_id,
                Some(item.parent_id.clone()),
                "failed",
                Some("object has no composition parent".to_owned()),
            ));
            *entry = None;
            continue;
        };
        if let Err(error) = check_type_restriction(definition, &child_type, &parent_type) {
            results.push(reference_result(
                "reparent",
                item.index,
                &item.source_id,
                Some(item.parent_id.clone()),
                "failed",
                Some(error.message().to_owned()),
            ));
            *entry = None;
            continue;
        }
        if creates_cycle(&chain, &batch_parents, &item.source_key, &parent_key) {
            results.push(reference_result(
                "reparent",
                item.index,
                &item.source_id,
                Some(item.parent_id.clone()),
                "failed",
                Some("would create a cycle".to_owned()),
            ));
            *entry = None;
            continue;
        }
        // Moving an object to the parent it already has changes nothing.
        if old_parent_id == item.parent_id {
            results.push(reference_result(
                "reparent",
                item.index,
                &item.source_id,
                Some(item.parent_id.clone()),
                "existing",
                None,
            ));
            *entry = None;
            continue;
        }
        item.old_parent_id = Some(old_parent_id);
        batch_parents.insert(item.source_key.clone(), parent_key.clone());
        reparent_rows.push(json!({
            "source": item.source_key,
            "parent": parent_key,
        }));
    }

    // ---- one critical section for the whole batch ---------------------------
    let mut lock_names: Vec<String> = Vec::new();
    for edge in edges.iter().flatten() {
        lock_names.push(
            [
                "edge",
                edge.definition_id.as_str(),
                edge.source_id.as_str(),
                edge.target_id.as_str(),
            ]
            .join("\u{1}"),
        );
    }
    for item in moves.iter().flatten() {
        lock_names.push(format!("reparent\u{1}{}", item.source_id));
    }
    let _guards = state.key_locks.lock_keys(&lock_names).await;

    // A parallel writer may have created "(some of) the edges between the lookup
    // and the lock, so re-check the existing ones with the lock held — the same
    // order as the single endpoint (lock, then check, then write).
    if !edges.iter().all(Option::is_none) {
        existing.extend(load_existing_edges(&state.db, edges.iter().flatten()).await?);
    }

    // ---- classify and write -----------------------------------------------
    let mut edge_rows: Vec<Value> = Vec::new();
    let mut events: Vec<Value> = Vec::new();
    let mut audits: Vec<Value> = Vec::new();
    let mut created_count = 0usize;
    let mut existing_count = 0usize;
    let mut reparented_count = 0usize;
    for entry in edges.iter_mut() {
        // `take` again: an entry is consumed once it is either reported as
        // `existing` or put into the write rows.
        let Some(edge) = entry.take() else {
            continue;
        };
        let key = format!(
            "{}\u{1}{}\u{1}{}",
            edge.definition_id, edge.source_id, edge.target_id
        );
        if existing.contains(&key) {
            existing_count += 1;
            results.push(reference_result(
                "reference",
                edge.index,
                &edge.source_id,
                Some(edge.target_id.clone()),
                "existing",
                None,
            ));
            continue;
        }
        edge_rows.push(json!({
            "source": edge.source_key,
            "target": edge.target_key,
            "definition_id": edge.definition_id,
            "definition_name": edge.definition_name,
            "attributes": edge.attributes,
        }));
        if emit_events {
            events.push(crate::event::content(&crate::event::OutboxEvent {
                event_type: crate::event::EVENT_REFERENCE_CREATED,
                object_type: edge.source_type.clone(),
                object_id: edge.source_id.clone(),
                changes: json!({
                    "target": edge.target_id.clone(),
                    "definition_id": edge.definition_id.clone(),
                }),
                metadata: json!({ "user_id": user_id.clone() }),
            }));
        }
        audits.push(audit::content(AuditEntry {
            user_id: &user_id,
            action: "reference_created",
            object_id: Some(&edge.source_id),
            from: None,
            to: None,
            metadata: Some(json!({
                "target": edge.target_id,
                "definition_id": edge.definition_id,
            })),
        }));
        created_count += 1;
        results.push(reference_result(
            "reference",
            edge.index,
            &edge.source_id,
            Some(edge.target_id.clone()),
            "created",
            None,
        ));
    }
    for item in moves.iter().flatten() {
        let old_parent_id = item.old_parent_id.clone().unwrap_or_default();
        if emit_events {
            events.push(crate::event::content(&crate::event::OutboxEvent {
                event_type: crate::event::EVENT_REFERENCE_DELETED,
                object_type: item.source_type.clone(),
                object_id: item.source_id.clone(),
                changes: json!({ "parent": old_parent_id.clone() }),
                metadata: json!({ "user_id": user_id.clone() }),
            }));
            events.push(crate::event::content(&crate::event::OutboxEvent {
                event_type: crate::event::EVENT_REFERENCE_CREATED,
                object_type: item.source_type.clone(),
                object_id: item.source_id.clone(),
                changes: json!({ "parent": item.parent_id.clone() }),
                metadata: json!({ "user_id": user_id.clone() }),
            }));
        }
        audits.push(audit::content(AuditEntry {
            user_id: &user_id,
            action: "reference_reparented",
            object_id: Some(&item.source_id),
            from: Some(json!({ "parent": old_parent_id })),
            to: Some(json!({ "parent": item.parent_id.clone() })),
            metadata: None,
        }));
        reparented_count += 1;
        results.push(reference_result(
            "reparent",
            item.index,
            &item.source_id,
            Some(item.parent_id.clone()),
            "reparented",
            None,
        ));
    }

    // ---- one transaction for the whole batch -------------------------------
    if !edge_rows.is_empty() || !reparent_rows.is_empty() {
        let mut sql = String::from("BEGIN TRANSACTION;\n");
        if !edge_rows.is_empty() {
            // `RELATE` needs records, not keys: `RELATE type::record(…)->…` does
            // not parse ("Unexpected token `::`") although `/sql` accepts it, so
            // the loop binds both endpoints first (`objects.rs` uses the same
            // form for the root edges).
            sql.push_str(
                "FOR $row IN $edges { LET $src = type::record('object', $row.source); \
                 LET $tgt = type::record('object', $row.target); \
                 RELATE $src->reference->$tgt SET definition_id = $row.definition_id, \
                 type = $row.definition_name, attributes = $row.attributes; };\n",
            );
        }
        if !reparent_rows.is_empty() {
            // The direction of a relation record is immutable, so re-parenting
            // deletes the old edge and creates the new one (identical to
            // `reparent_object`).
            sql.push_str(
                "FOR $row IN $reparents { LET $child = type::record('object', $row.source); \
                 LET $parent = type::record('object', $row.parent); \
                 DELETE reference WHERE in = $child AND definition_id = 'rel_depends_on'; \
                 RELATE $child->reference->$parent SET definition_id = 'rel_depends_on', \
                 type = 'depends_on', attributes = {}; };\n",
            );
        }
        if !events.is_empty() {
            sql.push_str("FOR $e IN $events { CREATE outbox_event CONTENT $e; };\n");
        }
        sql.push_str("COMMIT TRANSACTION;");
        if let Err(error) = state
            .db
            .inner()
            .query(sql)
            .bind(("edges", edge_rows))
            .bind(("reparents", reparent_rows))
            .bind(("events", events))
            .await
            .and_then(|response| response.check())
        {
            // The transaction is all-or-nothing: report every entry it contained
            // as failed so a caller's retry pass re-applies exactly those rows.
            let message = error.message().to_owned();
            for result in results.iter_mut() {
                if matches!(result.action.as_str(), "created" | "reparented") {
                    result.action = "failed".to_owned();
                    result.error = Some(message.clone());
                }
            }
            created_count = 0;
            reparented_count = 0;
        }
    }
    if !audits.is_empty() {
        audit::record_many_best_effort(&state.db, audits).await;
    }
    if created_count > 0 || reparented_count > 0 {
        // Composition and categorisation edges feed the dashboard aggregation.
        state.cache.invalidate_stats();
    }
    tracing::debug!(
        total_ms = started.elapsed().as_secs_f64() * 1000.0,
        created = created_count,
        existing = existing_count,
        reparented = reparented_count,
        "batch references"
    );

    let failed = results
        .iter()
        .filter(|result| result.action == "failed")
        .count();
    // Report in request order: `references` first, then `reparents`, each by its
    // own index — the entries are validated in two passes, so the raw order would
    // mix failures and successes of the same array.
    results.sort_by(|left, right| {
        (left.kind != "reference", left.index).cmp(&(right.kind != "reference", right.index))
    });
    Ok(Json(BatchReferenceResponse {
        created: created_count,
        existing: existing_count,
        reparented: reparented_count,
        failed,
        results,
    }))
}

/// Builds one per-entry result.
fn reference_result(
    kind: &str,
    index: usize,
    source: &str,
    target: Option<String>,
    action: &str,
    error: Option<String>,
) -> BatchReferenceItemResult {
    BatchReferenceItemResult {
        kind: kind.to_owned(),
        index,
        source: source.to_owned(),
        target,
        action: action.to_owned(),
        error,
    }
}

/// Loads every relation definition once, keyed by record key (without backticks).
async fn load_relation_definitions(db: &Db) -> Result<HashMap<String, Value>, ApiError> {
    let mut response = db
        .inner()
        .query("SELECT *, type::string(id) AS record_id FROM relation_definition;")
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    let mut definitions = HashMap::with_capacity(rows.len());
    for row in rows {
        if let Some(id) = row.get("record_id").and_then(Value::as_str) {
            definitions.insert(key_of(id).trim_matches('`').to_owned(), row);
        }
    }
    Ok(definitions)
}

/// Loads the object type of every object the batch touches (one query).
async fn load_object_types(db: &Db, keys: &[String]) -> Result<HashMap<String, String>, ApiError> {
    let mut wanted: Vec<String> = keys.to_vec();
    wanted.sort_unstable();
    wanted.dedup();
    let mut types = HashMap::with_capacity(wanted.len());
    if wanted.is_empty() {
        return Ok(types);
    }
    let literal =
        crate::api::keys::record_list_literal("object", wanted.iter().map(String::as_str));
    let mut response = db
        .inner()
        .query(format!(
            "SELECT type::string(id) AS id, type FROM object \
             WHERE id IN {literal} AND deleted_at IS NONE;"
        ))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    for row in rows {
        if let (Some(id), Some(object_type)) = (
            row.get("id").and_then(Value::as_str),
            row.get("type").and_then(Value::as_str),
        ) {
            types.insert(key_of(id), object_type.to_owned());
        }
    }
    Ok(types)
}

/// The keys of the edges of `edges` that already exist, as
/// `definition_id\u{1}source\u{1}target` (one query for the whole batch).
async fn load_existing_edges<'a>(
    db: &Db,
    edges: impl Iterator<Item = &'a PreparedBatchEdge>,
) -> Result<HashSet<String>, ApiError> {
    let mut wanted: Vec<&PreparedBatchEdge> = edges.collect();
    if wanted.is_empty() {
        return Ok(HashSet::new());
    }
    wanted.sort_by(|a, b| a.definition_id.cmp(&b.definition_id));
    let mut found: HashSet<String> = HashSet::new();
    let mut start = 0;
    while start < wanted.len() {
        let definition = wanted[start].definition_id.clone();
        let mut end = start;
        while end < wanted.len() && wanted[end].definition_id == definition {
            end += 1;
        }
        let group = &wanted[start..end];
        let sources: Vec<String> = group.iter().map(|edge| edge.source_id.clone()).collect();
        let targets: Vec<String> = group.iter().map(|edge| edge.target_id.clone()).collect();
        let source_literal =
            crate::api::keys::record_list_literal("object", sources.iter().map(String::as_str));
        let target_literal =
            crate::api::keys::record_list_literal("object", targets.iter().map(String::as_str));
        let mut response = db
            .inner()
            .query(format!(
                "SELECT type::string(in) AS source, type::string(out) AS target FROM reference \
                 WHERE definition_id = $definition AND in IN {source_literal} \
                 AND out IN {target_literal};"
            ))
            .bind(("definition", definition.clone()))
            .await?
            .check()?;
        let rows: Vec<Value> = response.take(0)?;
        for row in rows {
            if let (Some(source), Some(target)) = (
                row.get("source").and_then(Value::as_str),
                row.get("target").and_then(Value::as_str),
            ) {
                found.insert(format!("{definition}\u{1}{source}\u{1}{target}"));
            }
        }
        start = end;
    }
    Ok(found)
}

/// The current composition parent of every object the batch moves (one query),
/// keyed by the child's record key and mapping to the parent's **record id**
/// (`object:<ulid>`, like the single endpoint reports it).
async fn load_composition_parents(
    db: &Db,
    child_ids: &[String],
) -> Result<HashMap<String, String>, ApiError> {
    let mut children: Vec<String> = child_ids.to_vec();
    children.sort_unstable();
    children.dedup();
    let mut parents = HashMap::with_capacity(children.len());
    if children.is_empty() {
        return Ok(parents);
    }
    let literal =
        crate::api::keys::record_list_literal("object", children.iter().map(String::as_str));
    let mut response = db
        .inner()
        .query(format!(
            "SELECT type::string(in) AS child, type::string(out) AS parent FROM reference \
             WHERE definition_id = 'rel_depends_on' AND in IN {literal};"
        ))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    for row in rows {
        if let (Some(child), Some(parent)) = (
            row.get("child").and_then(Value::as_str),
            row.get("parent").and_then(Value::as_str),
        ) {
            parents.insert(key_of(child), parent.to_owned());
        }
    }
    Ok(parents)
}

/// Current composition parent (`child key → parent key`) of every new parent of
/// the batch plus, walking upwards, of all of their ancestors (one query per
/// level) — the data the cycle check walks.
async fn load_parent_chain(
    db: &Db,
    parent_ids: &[String],
) -> Result<HashMap<String, String>, ApiError> {
    let mut chain: HashMap<String, String> = HashMap::new();
    let mut frontier: Vec<String> = parent_ids.iter().map(|id| key_of(id)).collect();
    frontier.sort_unstable();
    frontier.dedup();
    let mut visited: HashSet<String> = frontier.iter().cloned().collect();
    let mut levels = 0;
    while !frontier.is_empty() && levels < MAX_ANCESTOR_DEPTH {
        levels += 1;
        let literal =
            crate::api::keys::record_list_literal("object", frontier.iter().map(String::as_str));
        let mut response = db
            .inner()
            .query(format!(
                "SELECT type::string(in) AS child, type::string(out) AS parent FROM reference \
                 WHERE definition_id = 'rel_depends_on' AND in IN {literal};"
            ))
            .await?
            .check()?;
        let rows: Vec<Value> = response.take(0)?;
        let mut next: Vec<String> = Vec::new();
        for row in rows {
            let (Some(child), Some(parent)) = (
                row.get("child").and_then(Value::as_str),
                row.get("parent").and_then(Value::as_str),
            ) else {
                continue;
            };
            let child = key_of(child).to_owned();
            let parent = key_of(parent).to_owned();
            chain.insert(child, parent.clone());
            // The universal root is the parent of every top-level node, not part
            // of a path — and it has no parent of its own.
            if parent != "object_root" && visited.insert(parent.clone()) {
                next.push(parent);
            }
        }
        frontier = next;
    }
    Ok(chain)
}

/// Whether adding the composition edge **source → target** would close a cycle.
///
/// Walks **up from the new parent** (`target`): the edge is a cycle exactly when
/// the source is already above the target, i.e. the target sits below the source
/// today. Each level prefers the **batch** assignment (those are not in the
/// database yet) and falls back to the stored parent, so chains inside the batch
/// are covered as well. A self-edge counts as a cycle.
///
/// The previous version collected the new parents **and** their ancestors into a
/// set and compared the *source* against it. Because the new parents themselves
/// were part of that set, every source that happened to be a parent elsewhere in
/// the same batch was rejected: the model import of the perf dataset lost 10
/// category edges with "would create a cycle"— although both ends were siblings
/// (found live 2026-09-16, `demo` never hit it because it ran through the
/// single-write path).
fn creates_cycle(
    chain: &HashMap<String, String>,
    batch_parents: &HashMap<String, String>,
    source_key: &str,
    target_key: &str,
) -> bool {
    if source_key == target_key {
        return true;
    }
    let mut current = target_key.to_owned();
    for _ in 0..MAX_ANCESTOR_DEPTH {
        let Some(parent) = batch_parents.get(&current).or_else(|| chain.get(&current)) else {
            return false;
        };
        if parent == source_key {
            return true;
        }
        if parent == "object_root" {
            return false;
        }
        current = parent.clone();
    }
    false
}

/// Validates the edge-metadata keys against the already loaded definition
/// (the batch pendant of [`validate_edge_attributes`], without a query).
fn validate_edge_attribute_names(definition: &Value, attributes: &Value) -> Result<(), ApiError> {
    if !attributes.is_object() && !attributes.is_null() {
        return Err(ApiError::bad_request("`attributes` must be an object"));
    }
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

/// The role rule of a relation type against the batch's preloaded taxonomy
/// types (the batch pendant of the block in [`create_reference`]).
fn check_edge_role(
    definition: &Value,
    definition_id: &str,
    target_type: &str,
    category_targets: Option<&[String]>,
) -> Result<(), ApiError> {
    let role = role_of(definition);
    if role == ROLE_CATEGORY {
        let allowed = category_targets.unwrap_or(&[]);
        if !allowed.iter().any(|name| name == target_type) {
            return Err(ApiError::bad_request(format!(
                "relation type `{}` is a {} relation: the target must be an object of a taxonomy \
                 type (allowed: {})",
                definition_id_of(definition, definition_id),
                role,
                allowed.join(", ")
            )));
        }
        return Ok(());
    }
    for required in role_target_types(role) {
        if target_type != required {
            return Err(ApiError::bad_request(format!(
                "relation type `{}` is a {} relation: the target must be an object of type \
                 `{required}`",
                definition_id_of(definition, definition_id),
                role
            )));
        }
    }
    Ok(())
}

/// Upper bound of object ids per `POST /references/query` call.
pub const MAX_QUERY_REFERENCE_IDS: usize = 500;

/// Default page size (rows) of `POST /references/query`.
const DEFAULT_REFERENCE_PAGE: usize = 5_000;

/// Upper bound of rows per `POST /references/query` page.
pub const MAX_REFERENCE_PAGE: usize = 50_000;

/// Payload of `POST /references/query`.
#[derive(Debug, Deserialize, ToSchema)]
pub struct ReferenceQueryRequest {
    /// Objects whose edges should be read (`object:<ulid>`), at most
    /// [`MAX_QUERY_REFERENCE_IDS`] per call.
    pub ids: Vec<String>,
    /// Rows per page (default 5 000, max 50 000).
    #[serde(default)]
    pub limit: Option<usize>,
    /// Rows to skip (paging through a result larger than `limit`).
    #[serde(default)]
    pub start: Option<usize>,
}

/// Result of `POST /references/query`.
#[derive(Debug, Serialize, ToSchema)]
pub struct ReferenceQueryResponse {
    /// Raw edge rows (`id`, `definition_id`, `in`, `out`, `attributes`).
    pub references: Vec<Value>,
    /// `true` when the page is full — another call with `start += limit` may
    /// return more rows.
    pub truncated: bool,
}

/// Reads many references in **one** request (incoming and outgoing).
///
/// `GET /objects/{id}/references` answers per object and enriches every row with
/// both endpoint objects (two extra lookups per edge, plus a label resolution):
/// for an export over thousands of objects that is thousands of round trips for
/// projections the export does not need. This endpoint returns the **raw** edges
/// of up to [`MAX_QUERY_REFERENCE_IDS`] objects in one query and pages the rows
/// (`limit`/`start`, `ORDER BY id`), so a hub node (a category with thousands of
/// members) cannot blow up the response.
///
/// The id list is rendered as a **record list literal** — a bound key list would
/// be evaluated per candidate row (see [`crate::api::keys::record_list_literal`]).
#[utoipa::path(
    post,
    path = "/references/query",
    request_body = ReferenceQueryRequest,
    responses(
        (status = 200, description = "Raw reference rows", body = ReferenceQueryResponse),
        (status = 400, description = "Invalid payload", body = Value),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Forbidden", body = Value),
        (status = 413, description = "Too many ids", body = Value)
    )
)]
pub async fn query_references(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<ReferenceQueryRequest>,
) -> Result<Json<ReferenceQueryResponse>, ApiError> {
    crate::auth::require_permission(&state, &headers, "reference.read")?;
    if body.ids.is_empty() {
        return Err(ApiError::bad_request("`ids` must not be empty"));
    }
    if body.ids.len() > MAX_QUERY_REFERENCE_IDS {
        return Err(ApiError::payload_too_large(format!(
            "`ids` exceeds the limit of {MAX_QUERY_REFERENCE_IDS} objects"
        )));
    }
    let limit = body
        .limit
        .unwrap_or(DEFAULT_REFERENCE_PAGE)
        .clamp(1, MAX_REFERENCE_PAGE) as i64;
    let start = body.start.unwrap_or(0) as i64;
    let keys: Vec<String> = body
        .ids
        .iter()
        .map(|id| parse_object_key(id).map(str::to_owned))
        .collect::<Result<_, _>>()?;
    let literal = crate::api::keys::record_list_literal("object", keys.iter().map(String::as_str));
    let sql = format!(
        "SELECT id, definition_id, in, out, attributes FROM reference \
         WHERE in IN {literal} OR out IN {literal} ORDER BY id LIMIT $limit START $start;"
    );
    let mut response = state
        .db
        .inner()
        .query(sql)
        .bind(("limit", limit))
        .bind(("start", start))
        .await?
        .check()?;
    let references: Vec<Value> = response.take(0)?;
    let truncated = references.len() as i64 >= limit;
    Ok(Json(ReferenceQueryResponse {
        references,
        truncated,
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_the_composition_edge_is_protected_from_a_delete_by_id() {
        // The composition edge is the parent link: it is re-parented or removed
        // with the object, never detached (a parent-less object is invisible in
        // the structure tree and unreachable for the delete cascade — 81 demo
        // variants were found in that state on 2026-09-17).
        assert!(is_composition_edge_value(&json!({
            "definition_id": "rel_depends_on"
        })));
        assert!(is_composition_edge_value(&json!({
            "definition_id": "`rel_depends_on`"
        })));
        assert!(!is_composition_edge_value(&json!({
            "definition_id": "rel_category_member"
        })));
        assert!(!is_composition_edge_value(&json!({ "type": "depends_on" })));
    }

    #[test]
    fn batch_role_check_mirrors_the_single_endpoint() {
        // A categorisation may only point at a taxonomy type.
        let category = json!({ "name": "category_member", "role": "category" });
        let targets = vec!["category".to_owned(), "etim_class".to_owned()];
        assert!(
            check_edge_role(
                &category,
                "rel_category_member",
                "etim_class",
                Some(&targets)
            )
            .is_ok()
        );
        let error = check_edge_role(&category, "rel_category_member", "product", Some(&targets))
            .expect_err("a product is not a taxonomy type");
        assert!(error.message().contains("taxonomy"));

        // A collection membership is fixed to `collection`.
        let collection = json!({ "name": "collection_member", "role": "collection" });
        assert!(check_edge_role(&collection, "rel_collection_member", "collection", None).is_ok());
        let error = check_edge_role(&collection, "rel_collection_member", "product", None)
            .expect_err("a product is not a collection");
        assert!(
            error
                .message()
                .contains("must be an object of type `collection`")
        );

        // A plain link has no target rule.
        let link = json!({ "name": "related", "role": "link" });
        assert!(check_edge_role(&link, "rel_related", "product", None).is_ok());
    }

    #[test]
    fn batch_edge_attributes_must_be_declared_for_the_relation_type() {
        let declared = json!({ "name": "alternative_to", "attributes": ["match_type"] });
        assert!(validate_edge_attribute_names(&declared, &json!({"match_type": "oe"})).is_ok());
        // An empty value is allowed (the template only lists the allowed keys).
        assert!(validate_edge_attribute_names(&declared, &json!({})).is_ok());
        let error = validate_edge_attribute_names(&declared, &json!({"quality_tier": "a"}))
            .expect_err("`quality_tier` is not declared");
        assert!(error.message().contains("not declared"));

        // No template: arbitrary metadata is allowed (built-in types).
        let open = json!({ "name": "related" });
        assert!(validate_edge_attribute_names(&open, &json!({"anything": 1})).is_ok());
        let error = validate_edge_attribute_names(&open, &json!("nope"))
            .expect_err("attributes must be an object");
        assert!(error.message().contains("must be an object"));
    }

    #[test]
    fn batch_cycle_check_walks_up_from_the_new_parent() {
        // `s` and `t` are children of the universal root, `deep` sits below `t`.
        let chain: HashMap<String, String> = HashMap::from([
            ("s".to_owned(), "object_root".to_owned()),
            ("t".to_owned(), "object_root".to_owned()),
            ("deep".to_owned(), "t".to_owned()),
        ]);
        let empty: HashMap<String, String> = HashMap::new();
        // The false positive of the old check: `s` is a parent of `deep`, and the
        // model import created `s → t` — nothing cyclic, both are siblings.
        assert!(!creates_cycle(&chain, &empty, "s", "t"));
        assert!(!creates_cycle(&chain, &empty, "deep", "s"));
        // `deep` is below `t`: re-parenting `t` under `deep` closes the cycle.
        assert!(creates_cycle(&chain, &empty, "t", "deep"));
        // `deep → t` is the parent it already has (a no-op, not a cycle).
        assert!(!creates_cycle(&chain, &empty, "deep", "t"));
        // A self-edge is always a cycle.
        assert!(creates_cycle(&chain, &empty, "s", "s"));
        // Unknown objects end the walk at the universal root.
        assert!(!creates_cycle(&chain, &empty, "other", "t"));
    }

    #[test]
    fn batch_cycle_check_sees_assignments_of_the_same_batch() {
        // The batch moves `t` below `s` first; creating `s → t` afterwards closes
        // the cycle even though the database knows nothing about it yet.
        let chain: HashMap<String, String> = HashMap::from([
            ("s".to_owned(), "object_root".to_owned()),
            ("t".to_owned(), "object_root".to_owned()),
        ]);
        let batch: HashMap<String, String> = HashMap::from([("t".to_owned(), "s".to_owned())]);
        assert!(creates_cycle(&chain, &batch, "s", "t"));
        // The other direction stays valid: `s` may go under `t`.
        assert!(!creates_cycle(&chain, &HashMap::new(), "t", "s"));
        // A longer in-batch chain is followed as well (`t → u → s`).
        let batch2: HashMap<String, String> = HashMap::from([
            ("t".to_owned(), "u".to_owned()),
            ("u".to_owned(), "s".to_owned()),
        ]);
        assert!(creates_cycle(&chain, &batch2, "s", "t"));
    }

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
