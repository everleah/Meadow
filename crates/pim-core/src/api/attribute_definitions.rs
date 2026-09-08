//! Attribute definition CRUD handlers.

use axum::Json;
use axum::extract::{Path, Query, State};
use axum::http::StatusCode;
use serde::Deserialize;
use serde_json::{Value, json};
use utoipa::{IntoParams, ToSchema};

use crate::api::keys::validate_key_definition;
use crate::app::AppState;
use crate::audit::{self, AuditEntry};
use crate::auth::AuthUser;
use crate::error::ApiError;

const TABLE: &str = "attribute_definition";

/// Payload for creating an attribute definition.
#[derive(Debug, Deserialize, ToSchema)]
pub struct CreateAttributeDefinition {
    /// Unique attribute name (e.g. `price`, `width`).
    pub name: String,
    /// Multilingual label.
    #[serde(default = "default_object")]
    pub label: Value,
    /// Attribute data type: `text`, `number`, `boolean`, `date`, `enum`, `reference`, ...
    #[serde(rename = "type")]
    pub definition_type: String,
    /// Attribute groups (many-to-many; full group record ids).
    #[serde(default)]
    pub group_ids: Vec<String>,
    /// Whether the value may be inherited along `depends_on`.
    #[serde(default)]
    pub inheritable: bool,
    /// Whether this is a business key (e.g. SKU).
    #[serde(default)]
    pub is_key: bool,
    /// Object types the attribute applies to (empty = all).
    #[serde(default)]
    pub applicable_types: Vec<String>,
    /// Object types for which the attribute is required.
    #[serde(default)]
    pub required_types: Vec<String>,
    /// Whether the value is computed by a rule.
    #[serde(default)]
    pub computed: bool,
    /// Name of the `computed` business rule.
    #[serde(default)]
    pub computed_rule: Option<String>,
    /// Input attributes the formula reads.
    #[serde(default)]
    pub dependencies: Vec<String>,
    /// Whether the formula reads inherited values.
    #[serde(default)]
    pub computed_reads_inherited: bool,
    /// Whether the attribute is language-specific.
    #[serde(default)]
    pub multilingual: bool,
    /// Canonical unit id for numeric values.
    #[serde(default)]
    pub unit_id: Option<String>,
    /// Default display unit id.
    #[serde(default)]
    pub display_unit_id: Option<String>,
    /// Validation constraints (e.g. `{ "min": 0, "max": 10, "enum_values": [...] }`).
    #[serde(default)]
    pub constraints: Option<Value>,
    /// Default value.
    #[serde(default)]
    pub default_value: Option<Value>,
    /// Optional description.
    #[serde(default)]
    pub description: Option<String>,
}

/// Payload for a partial attribute definition update (the data type is
/// intentionally immutable in Phase 1; type changes are a later phase).
#[derive(Debug, Deserialize, ToSchema)]
pub struct UpdateAttributeDefinition {
    #[serde(default)]
    pub name: Option<String>,
    #[serde(default)]
    pub label: Option<Value>,
    #[serde(default)]
    pub group_ids: Option<Vec<String>>,
    #[serde(default)]
    pub inheritable: Option<bool>,
    #[serde(default)]
    pub is_key: Option<bool>,
    #[serde(default)]
    pub applicable_types: Option<Vec<String>>,
    #[serde(default)]
    pub required_types: Option<Vec<String>>,
    #[serde(default)]
    pub computed: Option<bool>,
    #[serde(default)]
    pub computed_rule: Option<String>,
    #[serde(default)]
    pub dependencies: Option<Vec<String>>,
    #[serde(default)]
    pub computed_reads_inherited: Option<bool>,
    #[serde(default)]
    pub multilingual: Option<bool>,
    #[serde(default)]
    pub unit_id: Option<String>,
    #[serde(default)]
    pub display_unit_id: Option<String>,
    #[serde(default)]
    pub constraints: Option<Value>,
    #[serde(default)]
    pub default_value: Option<Value>,
    #[serde(default)]
    pub description: Option<String>,
    /// When removing an enum value that is still in use, a migration map
    /// `{ old_value: new_value }` re-assigns the objects before the removal.
    #[serde(default)]
    pub enum_remap: Option<serde_json::Map<String, Value>>,
}

/// Query parameters for listing attribute definitions.
#[derive(Debug, Deserialize, ToSchema, IntoParams)]
#[into_params(parameter_in = Query)]
pub struct ListAttributeDefinitions {
    /// Filter by attribute data type.
    #[serde(rename = "type")]
    #[param(rename = "type")]
    pub definition_type: Option<String>,
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

fn default_object() -> Value {
    Value::Object(serde_json::Map::new())
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

/// Creates a new attribute definition.
#[utoipa::path(
    post,
    path = "/attribute-definitions",
    request_body = CreateAttributeDefinition,
    responses(
        (status = 201, description = "Created attribute definition", body = Value),
        (status = 400, description = "Invalid payload", body = Value),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Forbidden", body = Value)
    )
)]
pub async fn create_attribute_definition(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Json(body): Json<CreateAttributeDefinition>,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    if !claims.is_admin() {
        return Err(ApiError::forbidden("requires admin role"));
    }
    if body.name.trim().is_empty() {
        return Err(ApiError::bad_request("`name` must not be empty"));
    }
    if body.definition_type.trim().is_empty() {
        return Err(ApiError::bad_request("`type` must not be empty"));
    }
    let user_id = claims.sub.clone();
    let CreateAttributeDefinition {
        name,
        label,
        definition_type,
        group_ids,
        inheritable,
        is_key,
        applicable_types,
        required_types,
        computed,
        computed_rule,
        dependencies,
        computed_reads_inherited,
        multilingual,
        unit_id,
        display_unit_id,
        constraints,
        default_value,
        description,
    } = body;

    // Reject duplicate names with a clean 409 instead of a transaction error.
    let existing: Vec<Value> = state
        .db
        .inner()
        .query("SELECT id FROM attribute_definition WHERE name = $name")
        .bind(("name", name.clone()))
        .await?
        .check()?
        .take(0)?;
    if !existing.is_empty() {
        return Err(ApiError::conflict(format!(
            "attribute `{name}` already exists"
        )));
    }

    validate_key_definition(&definition_type, multilingual, is_key)?;
    // Business keys are never inheritable (an inherited identity would
    // duplicate). `is_key: true` forces `inheritable: false`.
    let inheritable = if is_key { false } else { inheritable };

    let mut content = serde_json::Map::new();
    content.insert("name".to_owned(), Value::String(name));
    content.insert("label".to_owned(), label);
    content.insert("type".to_owned(), Value::String(definition_type));
    content.insert(
        "group_ids".to_owned(),
        Value::Array(group_ids.into_iter().map(Value::String).collect()),
    );
    content.insert("inheritable".to_owned(), Value::Bool(inheritable));
    content.insert("is_key".to_owned(), Value::Bool(is_key));
    content.insert(
        "applicable_types".to_owned(),
        Value::Array(applicable_types.into_iter().map(Value::String).collect()),
    );
    content.insert(
        "required_types".to_owned(),
        Value::Array(required_types.into_iter().map(Value::String).collect()),
    );
    content.insert("computed".to_owned(), Value::Bool(computed));
    if let Some(computed_rule) = computed_rule {
        content.insert("computed_rule".to_owned(), Value::String(computed_rule));
    }
    content.insert(
        "dependencies".to_owned(),
        Value::Array(dependencies.into_iter().map(Value::String).collect()),
    );
    content.insert(
        "computed_reads_inherited".to_owned(),
        Value::Bool(computed_reads_inherited),
    );
    content.insert("multilingual".to_owned(), Value::Bool(multilingual));
    if let Some(unit_id) = unit_id {
        content.insert("unit_id".to_owned(), Value::String(unit_id));
    }
    if let Some(display_unit_id) = display_unit_id {
        content.insert("display_unit_id".to_owned(), Value::String(display_unit_id));
    }
    if let Some(constraints) = constraints {
        content.insert("constraints".to_owned(), constraints);
    }
    if let Some(default_value) = default_value {
        content.insert("default_value".to_owned(), default_value);
    }
    if let Some(description) = description {
        content.insert("description".to_owned(), Value::String(description));
    }
    content.insert("version".to_owned(), Value::from(1));
    content.insert("updated_by".to_owned(), Value::String(user_id.clone()));
    let content = Value::Object(content);
    let changes = json!({ "name": content.get("name").cloned().unwrap_or(Value::Null) });

    // Statements: BEGIN(0), LET(1), CREATE(2), CREATE outbox(3), SELECT(4), COMMIT(5).
    let sql = "
        BEGIN TRANSACTION;
        LET $obj = type::record('attribute_definition', rand::ulid());
        CREATE $obj CONTENT $content;
        CREATE outbox_event SET event_type = 'attribute_definition.created', object_type = '', object_id = type::string($obj.id), changes = $changes, metadata = $metadata, status = 'pending';
        SELECT * FROM $obj;
        COMMIT TRANSACTION;
    ";
    let mut response = state
        .db
        .inner()
        .query(sql)
        .bind(("content", content))
        .bind(("changes", changes))
        .bind(("metadata", json!({ "user_id": user_id.clone() })))
        .await?
        .check()?;
    let definitions: Vec<Value> = response.take(4)?;
    let definition = definitions
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::internal("created attribute definition was not returned"))?;
    let definition_id = definition
        .get("id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &user_id,
            action: "attribute_definition_created",
            object_id: Some(&definition_id),
            from: None,
            to: None,
            metadata: None,
        },
    )
    .await;
    state.cache.invalidate_attribute_definitions().await;
    Ok((StatusCode::CREATED, Json(definition)))
}

/// Loads a single attribute definition by id.
#[utoipa::path(
    get,
    path = "/attribute-definitions/{id}",
    responses(
        (status = 200, description = "Attribute definition", body = Value),
        (status = 400, description = "Invalid id", body = Value),
        (status = 404, description = "Not found", body = Value)
    )
)]
pub async fn get_attribute_definition(
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<Json<Value>, ApiError> {
    let key = parse_key(&id, TABLE)?;
    // Statements: LET(0), SELECT(1).
    let sql = "
        LET $obj = type::record('attribute_definition', $key);
        SELECT * FROM $obj;
    ";
    let mut response = state
        .db
        .inner()
        .query(sql)
        .bind(("key", key))
        .await?
        .check()?;
    let definitions: Vec<Value> = response.take(1)?;
    let definition = definitions
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::not_found(format!("attribute definition `{id}` not found")))?;
    Ok(Json(definition))
}

/// Lists attribute definitions, optionally filtered by data type.
#[utoipa::path(
    get,
    path = "/attribute-definitions",
    params(ListAttributeDefinitions),
    responses((status = 200, description = "Attribute definitions", body = Vec<Value>))
)]
pub async fn list_attribute_definitions(
    State(state): State<AppState>,
    Query(params): Query<ListAttributeDefinitions>,
) -> Result<Json<Vec<Value>>, ApiError> {
    let limit = params.limit.clamp(1, 1000) as i64;
    let start = params.start as i64;

    let mut sql = String::from("SELECT * FROM attribute_definition");
    if params.definition_type.is_some() {
        sql.push_str(" WHERE type = $type");
    }
    sql.push_str(" ORDER BY name ASC LIMIT $limit START $start");

    let mut query = state.db.inner().query(sql);
    if let Some(definition_type) = &params.definition_type {
        query = query.bind(("type", definition_type.clone()));
    }
    let mut response = query
        .bind(("limit", limit))
        .bind(("start", start))
        .await?
        .check()?;
    let definitions: Vec<Value> = response.take(0)?;
    Ok(Json(definitions))
}

/// Partially updates an attribute definition.
#[utoipa::path(
    patch,
    path = "/attribute-definitions/{id}",
    request_body = UpdateAttributeDefinition,
    responses(
        (status = 200, description = "Updated attribute definition", body = Value),
        (status = 400, description = "Invalid id or payload", body = Value),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Forbidden", body = Value),
        (status = 404, description = "Not found", body = Value)
    )
)]
pub async fn update_attribute_definition(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
    Json(body): Json<UpdateAttributeDefinition>,
) -> Result<Json<Value>, ApiError> {
    if !claims.is_admin() {
        return Err(ApiError::forbidden("requires admin role"));
    }
    let key = parse_key(&id, TABLE)?;
    let user_id = claims.sub.clone();

    // Load the current definition to validate the merged business-key
    // constraints (the data type is immutable and not part of the payload).
    let existing_sql = "
        LET $obj = type::record('attribute_definition', $key);
        SELECT * FROM $obj;
    ";
    let mut existing_response = state
        .db
        .inner()
        .query(existing_sql)
        .bind(("key", key))
        .await?
        .check()?;
    let existing_rows: Vec<Value> = existing_response.take(1)?;
    let existing = existing_rows
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::not_found(format!("attribute definition `{id}` not found")))?;
    let existing_type = existing
        .get("type")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let existing_is_key = existing
        .get("is_key")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let existing_multilingual = existing
        .get("multilingual")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let existing_inheritable = existing
        .get("inheritable")
        .and_then(Value::as_bool)
        .unwrap_or(false);

    let mut changes = serde_json::Map::new();
    if let Some(name) = body.name {
        changes.insert("name".to_owned(), Value::String(name));
    }
    if let Some(label) = body.label {
        changes.insert("label".to_owned(), label);
    }
    if let Some(group_ids) = body.group_ids {
        changes.insert(
            "group_ids".to_owned(),
            Value::Array(group_ids.into_iter().map(Value::String).collect()),
        );
    }
    if let Some(inheritable) = body.inheritable {
        changes.insert("inheritable".to_owned(), Value::Bool(inheritable));
    }
    if let Some(is_key) = body.is_key {
        changes.insert("is_key".to_owned(), Value::Bool(is_key));
    }
    if let Some(applicable_types) = body.applicable_types {
        changes.insert(
            "applicable_types".to_owned(),
            Value::Array(applicable_types.into_iter().map(Value::String).collect()),
        );
    }
    if let Some(required_types) = body.required_types {
        changes.insert(
            "required_types".to_owned(),
            Value::Array(required_types.into_iter().map(Value::String).collect()),
        );
    }
    if let Some(computed) = body.computed {
        changes.insert("computed".to_owned(), Value::Bool(computed));
    }
    if let Some(computed_rule) = body.computed_rule {
        changes.insert("computed_rule".to_owned(), Value::String(computed_rule));
    }
    if let Some(dependencies) = body.dependencies {
        changes.insert(
            "dependencies".to_owned(),
            Value::Array(dependencies.into_iter().map(Value::String).collect()),
        );
    }
    if let Some(computed_reads_inherited) = body.computed_reads_inherited {
        changes.insert(
            "computed_reads_inherited".to_owned(),
            Value::Bool(computed_reads_inherited),
        );
    }
    if let Some(multilingual) = body.multilingual {
        changes.insert("multilingual".to_owned(), Value::Bool(multilingual));
    }
    if let Some(unit_id) = body.unit_id {
        changes.insert("unit_id".to_owned(), Value::String(unit_id));
    }
    if let Some(display_unit_id) = body.display_unit_id {
        changes.insert("display_unit_id".to_owned(), Value::String(display_unit_id));
    }
    let new_constraints: Option<Value> = body.constraints;
    if let Some(constraints_value) = new_constraints.as_ref() {
        changes.insert("constraints".to_owned(), constraints_value.clone());
    }
    if let Some(default_value) = body.default_value {
        changes.insert("default_value".to_owned(), default_value);
    }
    if let Some(description) = body.description {
        changes.insert("description".to_owned(), Value::String(description));
    }

    let effective_is_key = body.is_key.unwrap_or(existing_is_key);
    let effective_multilingual = body.multilingual.unwrap_or(existing_multilingual);
    let effective_inheritable = body.inheritable.unwrap_or(existing_inheritable);
    validate_key_definition(existing_type, effective_multilingual, effective_is_key)?;
    if effective_is_key && effective_inheritable {
        // Business keys are never inheritable: force `inheritable: false`.
        changes.insert("inheritable".to_owned(), Value::Bool(false));
    }

    if changes.is_empty() {
        return Ok(Json(existing));
    }

    // --- Enum value management (Phase 9) ------------------------------------
    // Removing an enum option is only allowed when no object uses it; otherwise
    // an explicit re-map (`enum_remap`) migrates those objects first, else the
    // update is blocked with 409.
    if new_constraints.is_some() && existing_type == "enum" {
        let name = existing
            .get("name")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_owned();
        let old_values: Vec<String> = existing
            .get("constraints")
            .and_then(|c| c.get("enum_values"))
            .and_then(Value::as_array)
            .map(|a| {
                a.iter()
                    .filter_map(Value::as_str)
                    .map(str::to_owned)
                    .collect()
            })
            .unwrap_or_default();
        let new_values: Vec<String> = new_constraints
            .as_ref()
            .and_then(|c| c.get("enum_values"))
            .and_then(Value::as_array)
            .map(|a| {
                a.iter()
                    .filter_map(Value::as_str)
                    .map(str::to_owned)
                    .collect()
            })
            .unwrap_or_default();
        let new_set: std::collections::HashSet<String> = new_values.iter().cloned().collect();
        for old in &old_values {
            if new_set.contains(old) {
                continue;
            }
            let used = count_enum_usage(&state.db, &name, old, existing_multilingual).await?;
            if used <= 0 {
                continue;
            }
            let target = body
                .enum_remap
                .as_ref()
                .and_then(|m| m.get(old))
                .and_then(Value::as_str)
                .map(str::trim)
                .filter(|s| !s.is_empty())
                .map(str::to_owned);
            match target {
                Some(target) => {
                    if existing_multilingual {
                        return Err(ApiError::bad_request(
                            "re-mapping multilingual enum values is not supported yet",
                        ));
                    }
                    state
                        .db
                        .inner()
                        .query(
                            "UPDATE object SET attributes[$name] = $target \
                             WHERE deleted_at IS NONE AND attributes[$name] = $old;",
                        )
                        .bind(("name", name.clone()))
                        .bind(("old", old.clone()))
                        .bind(("target", target))
                        .await?
                        .check()?;
                }
                None => {
                    return Err(ApiError::conflict(format!(
                        "enum value `{old}` is still used by {used} object(s); re-map it or keep it"
                    )));
                }
            }
        }
    }

    let outbox = crate::event::content(&crate::event::OutboxEvent {
        event_type: crate::event::EVENT_ATTRIBUTE_DEFINITION_UPDATED,
        object_type: String::new(),
        object_id: id.clone(),
        changes: Value::Object(changes.clone()),
        metadata: json!({ "user_id": user_id.clone() }),
    });
    let changes = Value::Object(changes);

    // Statements: BEGIN(0), LET(1), UPDATE MERGE(2), UPDATE SET(3),
    // CREATE outbox(4), SELECT(5), COMMIT(6).
    let sql = "
        BEGIN TRANSACTION;
        LET $obj = type::record('attribute_definition', $key);
        UPDATE $obj MERGE $changes;
        UPDATE $obj SET version = version + 1, updated_at = time::now(), updated_by = $user;
        CREATE outbox_event CONTENT $outbox;
        SELECT * FROM $obj;
        COMMIT TRANSACTION;
    ";
    let mut response = state
        .db
        .inner()
        .query(sql)
        .bind(("key", key))
        .bind(("changes", changes))
        .bind(("user", user_id.clone()))
        .bind(("outbox", outbox))
        .await?
        .check()?;
    let definitions: Vec<Value> = response.take(5)?;
    let definition = definitions
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::not_found(format!("attribute definition `{id}` not found")))?;

    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &user_id,
            action: "attribute_definition_updated",
            object_id: Some(&id),
            from: None,
            to: None,
            metadata: None,
        },
    )
    .await;
    state.cache.invalidate_attribute_definitions().await;

    Ok(Json(definition))
}

/// Returns the objects that currently carry a given enum value for an attribute.
async fn find_enum_usage(
    db: &crate::db::Db,
    attr_name: &str,
    value: &str,
    multilingual: bool,
    limit: i64,
) -> Result<Vec<Value>, ApiError> {
    let sql = if multilingual {
        "SELECT id, type FROM object WHERE deleted_at IS NONE \
         AND array::contains(object::values(attributes[$name] ?? {}), $value) \
         LIMIT $limit;"
    } else {
        "SELECT id, type FROM object WHERE deleted_at IS NONE \
         AND attributes[$name] = $value LIMIT $limit;"
    };
    let mut response = db
        .inner()
        .query(sql)
        .bind(("name", attr_name.to_owned()))
        .bind(("value", value.to_owned()))
        .bind(("limit", limit))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    Ok(rows)
}

/// Counts the objects that carry a given enum value for an attribute.
async fn count_enum_usage(
    db: &crate::db::Db,
    attr_name: &str,
    value: &str,
    multilingual: bool,
) -> Result<i64, ApiError> {
    let sql = if multilingual {
        "SELECT count() AS count FROM object WHERE deleted_at IS NONE \
         AND array::contains(object::values(attributes[$name] ?? {}), $value) GROUP ALL;"
    } else {
        "SELECT count() AS count FROM object WHERE deleted_at IS NONE \
         AND attributes[$name] = $value GROUP ALL;"
    };
    let mut response = db
        .inner()
        .query(sql)
        .bind(("name", attr_name.to_owned()))
        .bind(("value", value.to_owned()))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    Ok(rows
        .first()
        .and_then(|row| row.get("count"))
        .and_then(Value::as_i64)
        .unwrap_or(0))
}

/// Query parameters for `GET /attribute-definitions/{id}/enum-usage`.
#[derive(Debug, Deserialize, ToSchema, IntoParams)]
#[into_params(parameter_in = Query)]
pub struct EnumUsageParams {
    /// The enum value to look up.
    pub value: String,
    /// Maximum number of usage rows to return.
    #[serde(default = "default_usage_limit")]
    pub limit: u32,
}

fn default_usage_limit() -> u32 {
    50
}

/// Reports how many objects use a given enum value for an attribute (Phase 9).
/// Used by the enum-option management to decide whether a value can be removed
/// or must be re-mapped first.
#[utoipa::path(
    get,
    path = "/attribute-definitions/{id}/enum-usage",
    params(EnumUsageParams),
    responses(
        (status = 200, description = "Usage for an enum value", body = Value),
        (status = 403, description = "Forbidden", body = Value),
        (status = 404, description = "Not found", body = Value)
    )
)]
pub async fn enum_value_usage(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
    Query(params): Query<EnumUsageParams>,
) -> Result<Json<Value>, ApiError> {
    if !claims.is_editor() {
        return Err(ApiError::forbidden("requires editor role"));
    }
    let key = parse_key(&id, TABLE)?;
    let definition = fetch_definition(&state.db, key).await?;
    let Some(definition) = definition else {
        return Err(ApiError::not_found(format!(
            "attribute definition `{id}` not found"
        )));
    };
    let name = definition
        .get("name")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    if name.is_empty() {
        return Err(ApiError::bad_request("attribute name is empty"));
    }
    let multilingual = definition
        .get("multilingual")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let limit = params.limit.clamp(1, 1000) as i64;
    let items = find_enum_usage(&state.db, &name, &params.value, multilingual, limit).await?;
    let count = count_enum_usage(&state.db, &name, &params.value, multilingual).await?;
    Ok(Json(json!({
        "value": params.value,
        "count": count,
        "items": items,
    })))
}

/// Loads a single `attribute_definition` record by key.
async fn fetch_definition(db: &crate::db::Db, key: &str) -> Result<Option<Value>, ApiError> {
    let sql = "LET $obj = type::record('attribute_definition', $key); SELECT * FROM $obj;";
    let mut response = db
        .inner()
        .query(sql)
        .bind(("key", key.to_owned()))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(1)?;
    Ok(rows.into_iter().next())
}

/// Query parameters for deleting an attribute definition.
#[derive(Debug, Deserialize, ToSchema, IntoParams)]
#[into_params(parameter_in = Query)]
pub struct DeleteAttributeDefinition {
    /// Also remove the attribute value from all objects (asynchronously).
    #[serde(default)]
    pub force: bool,
}

/// Deletes an attribute definition (admin/superadmin).
#[utoipa::path(
    delete,
    path = "/attribute-definitions/{id}",
    params(DeleteAttributeDefinition),
    responses(
        (status = 204, description = "Deleted"),
        (status = 409, description = "Attribute in use", body = Value),
        (status = 404, description = "Not found", body = Value)
    )
)]
pub async fn delete_attribute_definition(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
    Query(params): Query<DeleteAttributeDefinition>,
) -> Result<StatusCode, ApiError> {
    if !claims.is_admin() {
        return Err(ApiError::forbidden("requires admin role"));
    }
    let key = parse_key(&id, TABLE)?;
    let user_id = claims.sub.clone();

    // Load the definition name (for the usage check and value removal).
    let def_sql = "
        LET $obj = type::record('attribute_definition', $key);
        SELECT * FROM $obj;
    ";
    let mut def_resp = state
        .db
        .inner()
        .query(def_sql)
        .bind(("key", key))
        .await?
        .check()?;
    let defs: Vec<Value> = def_resp.take(1)?;
    let definition = defs
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::not_found(format!("attribute definition `{id}` not found")))?;
    let name = definition
        .get("name")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();

    // Count objects that already carry a value for this attribute.
    let count_sql =
        format!("SELECT count() FROM object WHERE attributes.{name} IS NOT NONE GROUP ALL;");
    let mut count_resp = state.db.inner().query(&count_sql).await?.check()?;
    let count_rows: Vec<Value> = count_resp.take(0)?;
    let used = count_rows
        .first()
        .and_then(|row| row.get("count"))
        .and_then(Value::as_i64)
        .unwrap_or(0);

    if used > 0 && !params.force {
        return Err(ApiError::conflict(format!(
            "attribute `{name}` is in use by {used} object(s)"
        )));
    }

    // Delete the definition.
    let del_sql = "
        LET $obj = type::record('attribute_definition', $key);
        DELETE $obj;
    ";
    state
        .db
        .inner()
        .query(del_sql)
        .bind(("key", key))
        .await?
        .check()?;

    // Force: clear the value on affected objects asynchronously.
    if used > 0 && params.force {
        let ids_sql = format!("SELECT id FROM object WHERE attributes.{name} IS NOT NONE;");
        let mut ids_resp = state.db.inner().query(&ids_sql).await?.check()?;
        let obj_rows: Vec<Value> = ids_resp.take(0)?;
        let object_ids: Vec<String> = obj_rows
            .iter()
            .filter_map(|row| row.get("id").and_then(Value::as_str).map(String::from))
            .collect();
        let parameters = json!({
            "op": "clear_attribute",
            "object_ids": object_ids,
            "parameters": { "attribute": name },
        });
        let _ = crate::api::jobs::queue_run(
            &state.db,
            &state.config,
            None,
            "bulk_operation",
            parameters,
            "manual",
            &user_id,
        )
        .await?;
    }

    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &user_id,
            action: "attribute_definition_deleted",
            object_id: Some(&id),
            from: None,
            to: None,
            metadata: None,
        },
    )
    .await;
    state.cache.invalidate_attribute_definitions().await;
    Ok(StatusCode::NO_CONTENT)
}
