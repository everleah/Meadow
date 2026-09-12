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
    /// Reference to a reusable `enum_set:<key>` for `enum` attributes (Phase 9).
    #[serde(default)]
    pub enum_set_id: Option<String>,
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
    /// Reference to a reusable `enum_set:<key>` for `enum` attributes.
    #[serde(default)]
    pub enum_set_id: Option<Option<String>>,
    #[serde(default)]
    pub default_value: Option<Value>,
    #[serde(default)]
    pub description: Option<String>,
    /// When removing an enum value that is still in use, a migration map
    /// `{ old_value: new_value }` re-assigns the objects before the removal.
    #[serde(default)]
    pub enum_remap: Option<serde_json::Map<String, Value>>,
    /// Not supported here: the data type changes through
    /// `POST /attribute-definitions/{id}/type-change` (it migrates the values).
    #[serde(default, rename = "type")]
    pub definition_type: Option<String>,
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
        enum_set_id,
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
    if let Some(set_id) = enum_set_id {
        let set_ref = validate_enum_set(&state.db, &set_id).await?;
        content.insert("enum_set_id".to_owned(), Value::String(set_ref));
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
    // A type change migrates every existing value, so it has its own endpoint;
    // silently ignoring `type` in a PATCH would look like a successful change.
    if body.definition_type.is_some() {
        return Err(ApiError::bad_request(
            "the data type cannot be patched: use `POST /attribute-definitions/{id}/type-change` \
             so the existing values are migrated",
        ));
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
    let mut clear_enum_set = false;
    if let Some(enum_set_id) = body.enum_set_id {
        match enum_set_id
            .as_ref()
            .map(|s| s.trim())
            .filter(|s| !s.is_empty())
        {
            Some(set_id) => {
                let set_ref = validate_enum_set(&state.db, set_id).await?;
                changes.insert("enum_set_id".to_owned(), Value::String(set_ref));
            }
            None => {
                clear_enum_set = true;
            }
        }
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

    // A rename must carry the class features over: `category_attribute`
    // references the attribute by its (stable) name.
    let previous_name = existing
        .get("name")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    let renamed_to = changes
        .get("name")
        .and_then(Value::as_str)
        .map(str::to_owned)
        .filter(|new_name| !new_name.is_empty() && new_name != &previous_name);
    if let Some(new_name) = renamed_to {
        state
            .db
            .inner()
            .query("UPDATE category_attribute SET attribute = $new WHERE attribute = $old;")
            .bind(("old", previous_name))
            .bind(("new", new_name))
            .await?
            .check()?;
    }

    let outbox = crate::event::content(&crate::event::OutboxEvent {
        event_type: crate::event::EVENT_ATTRIBUTE_DEFINITION_UPDATED,
        object_type: String::new(),
        object_id: id.clone(),
        changes: Value::Object(changes.clone()),
        metadata: json!({ "user_id": user_id.clone() }),
    });
    let changes = Value::Object(changes);

    // Clear an enum-set reference with `SET ... = NONE` because `MERGE` cannot
    // carry the distinct `NONE` value (JSON `null` would be rejected by the
    // `option<string>` field). Statement indices below shift when present.
    let clear_sql = if clear_enum_set {
        "UPDATE $obj SET enum_set_id = NONE;"
    } else {
        ""
    };
    let select_index = if clear_enum_set { 6 } else { 5 };

    // Statements (no clear): BEGIN(0), LET(1), UPDATE MERGE(2), UPDATE SET(3),
    // CREATE outbox(4), SELECT(5), COMMIT(6).
    let sql = format!(
        "
        BEGIN TRANSACTION;
        LET $obj = type::record('attribute_definition', $key);
        UPDATE $obj MERGE $changes;
        {clear_sql}
        UPDATE $obj SET version = version + 1, updated_at = time::now(), updated_by = $user;
        CREATE outbox_event CONTENT $outbox;
        SELECT * FROM $obj;
        COMMIT TRANSACTION;
        "
    );
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
    let definitions: Vec<Value> = response.take(select_index)?;
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

/// Normalizes a raw enum-set identifier to a full `enum_set:<key>` record id
/// and verifies the set exists. Returns the normalized record-id string to
/// store on `attribute_definition.enum_set_id`.
async fn validate_enum_set(db: &crate::db::Db, raw: &str) -> Result<String, ApiError> {
    let key = raw
        .trim()
        .split_once(':')
        .map(|(_, k)| k.to_owned())
        .unwrap_or_else(|| raw.trim().to_owned());
    if key.is_empty() {
        return Err(ApiError::bad_request("`enum_set_id` must not be empty"));
    }
    let set_ref = format!("enum_set:{key}");
    let mut response = db
        .inner()
        .query("SELECT id FROM enum_set WHERE id = type::record('enum_set', $key) LIMIT 1;")
        .bind(("key", key.clone()))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    if rows.is_empty() {
        return Err(ApiError::bad_request(format!(
            "enum set `{set_ref}` does not exist"
        )));
    }
    Ok(set_ref)
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

/// Payload for `POST /attribute-definitions/{id}/type-change`.
#[derive(Debug, Deserialize, ToSchema)]
pub struct ChangeAttributeType {
    /// New data type (`text`, `url`, `number`, `boolean`, `date`, `enum`,
    /// `reference`).
    #[serde(rename = "type")]
    pub definition_type: String,
    /// Target enum set (required when switching **to** `enum`).
    #[serde(default)]
    pub enum_set_id: Option<String>,
    /// Canonical unit for the new `number` type (optional).
    #[serde(default)]
    pub unit_id: Option<String>,
    /// Display unit for the new `number` type (optional).
    #[serde(default)]
    pub display_unit_id: Option<String>,
    /// Apply even though some values cannot be converted: those values are
    /// removed instead of blocking the change.
    #[serde(default)]
    pub drop_incompatible: bool,
}

/// Query parameters for `POST /attribute-definitions/{id}/type-change`.
#[derive(Debug, Deserialize, ToSchema, IntoParams)]
#[into_params(parameter_in = Query)]
pub struct TypeChangeParams {
    /// Only compute and return the report; change nothing.
    #[serde(default)]
    pub dry_run: bool,
}

/// Every object that carries a value for the attribute (`id` + raw value).
///
/// Soft-deleted objects are skipped: they are invisible everywhere else and
/// keep their value only until the data is purged.
async fn load_attribute_values(
    db: &crate::db::Db,
    name: &str,
) -> Result<Vec<(String, Value)>, ApiError> {
    let mut response = db
        .inner()
        .query(
            "SELECT VALUE {id: type::string(id), value: attributes[$name]} FROM object \
             WHERE deleted_at IS NONE AND attributes[$name] IS NOT NONE;",
        )
        .bind(("name", name.to_owned()))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    Ok(rows
        .into_iter()
        .filter_map(|row| {
            let id = row.get("id").and_then(Value::as_str)?.to_owned();
            Some((id, row.get("value").cloned().unwrap_or(Value::Null)))
        })
        .collect())
}

/// Option codes of an enum set (`enum_set.options` → `enum_option.code`).
async fn enum_option_codes(db: &crate::db::Db, set_ref: &str) -> Result<Vec<String>, ApiError> {
    let key = parse_key(set_ref, "enum_set")?;
    let mut response = db
        .inner()
        .query(
            "LET $set = type::record('enum_set', $key); \
             SELECT VALUE code FROM enum_option WHERE id IN ($set.options ?? []);",
        )
        .bind(("key", key.to_owned()))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(1)?;
    let codes: Vec<String> = rows
        .into_iter()
        .filter_map(|code| code.as_str().map(str::to_owned))
        .collect();
    Ok(codes)
}

/// Changes the data type of an attribute definition and migrates its values.
///
/// The transition must be allowed by the compatibility matrix
/// (`attribute_definition_types`); every existing value is converted, and a
/// value that cannot be converted blocks the change (`409` with samples) unless
/// `drop_incompatible` removes it. With `?dry_run=true` only the report is
/// computed. Definition update and value migration run in one transaction, so a
/// failure never leaves values with a definition that no longer matches.
#[utoipa::path(
    post,
    path = "/attribute-definitions/{id}/type-change",
    request_body = ChangeAttributeType,
    params(TypeChangeParams),
    responses(
        (status = 200, description = "Conversion report (applied or dry run)", body = Value),
        (status = 400, description = "Unsupported target type or missing modifier", body = Value),
        (status = 404, description = "Attribute definition not found", body = Value),
        (status = 409, description = "Computed/key attribute or incompatible values", body = Value)
    )
)]
pub async fn change_attribute_type(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
    Query(params): Query<TypeChangeParams>,
    Json(body): Json<ChangeAttributeType>,
) -> Result<Json<Value>, ApiError> {
    if !claims.is_admin() {
        return Err(ApiError::forbidden("requires admin role"));
    }
    let key = parse_key(&id, TABLE)?;
    let user_id = claims.sub.clone();
    let target = body.definition_type.trim().to_owned();

    let definition = load_definition(&state.db, key).await?;
    let name = definition
        .get("name")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    let source = definition
        .get("type")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    if target == source {
        return Err(ApiError::bad_request(format!(
            "attribute `{name}` already has type `{source}`"
        )));
    }
    if definition
        .get("computed")
        .and_then(Value::as_bool)
        .unwrap_or(false)
    {
        return Err(ApiError::conflict(format!(
            "attribute `{name}` is computed: adapt the rule first, then change the type"
        )));
    }
    if definition
        .get("is_key")
        .and_then(Value::as_bool)
        .unwrap_or(false)
        && target != "text"
    {
        return Err(ApiError::conflict(format!(
            "attribute `{name}` is a business key (`is_key`): remove `is_key` first, \
             keys must stay `text`"
        )));
    }

    // The target enum set is required and must exist; its codes validate the
    // values. Units are passed through like in `create_attribute_definition`.
    let enum_set = match (&body.enum_set_id, target.as_str()) {
        (Some(raw), "enum") => Some(validate_enum_set(&state.db, raw).await?),
        (None, "enum") => {
            return Err(ApiError::bad_request(
                "switching to `enum` requires `enum_set_id`",
            ));
        }
        _ => None,
    };
    if !crate::attribute_definition_types::is_allowed_transition(
        &source,
        &target,
        enum_set.is_some(),
    ) {
        let allowed = crate::attribute_definition_types::allowed_targets(&source);
        return Err(ApiError::bad_request(format!(
            "`{source}` → `{target}` is not supported (allowed targets: {})",
            allowed.join(", ")
        )));
    }
    let codes = match &enum_set {
        Some(set_ref) => Some(enum_option_codes(&state.db, set_ref).await?),
        None => None,
    };

    let values = load_attribute_values(&state.db, &name).await?;
    let plan = crate::attribute_definition_types::plan_conversion(
        &source,
        &target,
        &values,
        codes.as_deref(),
    );

    // Report: the same shape for the dry run and the applied change.
    let samples: Vec<Value> = plan
        .incompatible
        .iter()
        .take(10)
        .map(|problem| {
            json!({
                "object": problem.object_id,
                "value": problem.value,
                "reason": problem.reason,
            })
        })
        .collect();
    let mut report = json!({
        "name": name,
        "from": source,
        "to": target,
        "checked": plan.checked,
        "convertible": plan.convertible(),
        "incompatible": plan.incompatible.len(),
        "samples": samples,
        "dropped": if body.drop_incompatible && !params.dry_run {
            plan.dropped.len()
        } else {
            0
        },
        "dry_run": params.dry_run,
    });

    // The dry run always reports what *would* happen (the caller shows the
    // sample list and asks whether the values may be dropped); an applied
    // change without `drop_incompatible` is rejected instead.
    if !plan.incompatible.is_empty() && !body.drop_incompatible && !params.dry_run {
        return Err(ApiError::conflict(format!(
            "{} of {} value(s) of `{name}` cannot be converted to `{target}` \
             (use `drop_incompatible` to remove them): {}",
            plan.incompatible.len(),
            plan.checked,
            plan.incompatible
                .iter()
                .take(3)
                .map(|problem| format!("{} ({})", problem.object_id, problem.reason))
                .collect::<Vec<_>>()
                .join("; ")
        )));
    }

    if params.dry_run {
        return Ok(Json(report));
    }

    // Definition changes: new type plus the target modifiers. Leaving `enum`
    // clears the set (and the legacy option list), leaving `number` clears the
    // units — `MERGE` cannot write `NONE`, so those fields are `UNSET` after it.
    let mut changes = serde_json::Map::new();
    changes.insert("type".to_owned(), Value::String(target.clone()));
    if let Some(set_ref) = &enum_set {
        changes.insert("enum_set_id".to_owned(), Value::String(set_ref.clone()));
    }
    if let Some(unit) = body.unit_id.filter(|unit| !unit.trim().is_empty()) {
        changes.insert("unit_id".to_owned(), Value::String(unit));
    }
    if let Some(unit) = body.display_unit_id.filter(|unit| !unit.trim().is_empty()) {
        changes.insert("display_unit_id".to_owned(), Value::String(unit));
    }
    let mut clear_fields: Vec<&str> = Vec::new();
    if source == "enum" && target != "enum" {
        clear_fields.push("enum_set_id");
        clear_fields.push("constraints");
    }
    if crate::attribute_definition_types::is_numeric(&source)
        && !crate::attribute_definition_types::is_numeric(&target)
    {
        clear_fields.push("unit_id");
        clear_fields.push("display_unit_id");
    }
    let unset_sql = if clear_fields.is_empty() {
        String::new()
    } else {
        format!("UPDATE $obj UNSET {};", clear_fields.join(", "))
    };

    let rows: Vec<Value> = plan
        .converted
        .iter()
        .map(|(object_id, value)| {
            let key = object_id
                .split_once(':')
                .map(|(_, k)| k)
                .unwrap_or(object_id);
            json!({ "key": key, "value": value })
        })
        .collect();
    let drops: Vec<String> = plan
        .dropped
        .iter()
        .map(|object_id| {
            object_id
                .split_once(':')
                .map(|(_, k)| k)
                .unwrap_or(object_id)
                .to_owned()
        })
        .collect();
    let outbox = crate::event::content(&crate::event::OutboxEvent {
        event_type: crate::event::EVENT_ATTRIBUTE_DEFINITION_UPDATED,
        object_type: String::new(),
        object_id: id.clone(),
        changes: json!({
            "type": {"from": source, "to": target},
            "converted": plan.convertible(),
            "dropped": drops.len(),
        }),
        metadata: json!({ "user_id": user_id.clone() }),
    });

    // Statements: BEGIN(0), LET(1), UPDATE MERGE(2), [UNSET](3), UPDATE SET,
    // FOR converted, FOR dropped, CREATE outbox, SELECT, COMMIT. The `UNSET`
    // statement only exists when fields must be cleared.
    let sql = format!(
        "
        BEGIN TRANSACTION;
        LET $obj = type::record('attribute_definition', $key);
        UPDATE $obj MERGE $changes;
        {unset_sql}
        UPDATE $obj SET version = version + 1, updated_at = time::now(), updated_by = $user;
        FOR $row IN $rows {{ UPDATE type::record('object', $row.key) SET attributes[$name] = $row.value; }};
        FOR $drop IN $drops {{ UPDATE type::record('object', $drop) SET attributes[$name] = NONE; }};
        CREATE outbox_event CONTENT $outbox;
        SELECT * FROM $obj;
        COMMIT TRANSACTION;
        "
    );
    let select_index = if unset_sql.is_empty() { 7 } else { 8 };
    let mut response = state
        .db
        .inner()
        .query(sql)
        .bind(("key", key.to_owned()))
        .bind(("changes", Value::Object(changes)))
        .bind(("user", user_id.clone()))
        .bind(("name", name.clone()))
        .bind(("rows", Value::Array(rows)))
        .bind(("drops", json!(drops)))
        .bind(("outbox", outbox))
        .await?
        .check()?;
    let definitions: Vec<Value> = response.take(select_index)?;
    let updated = definitions
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::not_found(format!("attribute definition `{id}` not found")))?;

    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &user_id,
            action: "attribute_definition_type_changed",
            object_id: Some(&id),
            from: None,
            to: None,
            metadata: Some(json!({
                "name": name,
                "from": source,
                "to": target,
                "converted": plan.convertible(),
                "dropped": plan.dropped.len(),
            })),
        },
    )
    .await;
    // The data type feeds the search catalog, the `url` validation and the
    // multilingual handling — all cached.
    state.cache.invalidate_attribute_definitions().await;

    if let Some(object) = report.as_object_mut() {
        object.insert("definition".to_owned(), updated);
    }
    Ok(Json(report))
}

/// Loads one attribute definition by record key.
async fn load_definition(db: &crate::db::Db, key: &str) -> Result<Value, ApiError> {
    let mut response = db
        .inner()
        .query("SELECT * FROM type::record('attribute_definition', $key);")
        .bind(("key", key.to_owned()))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    rows.into_iter()
        .next()
        .ok_or_else(|| ApiError::not_found(format!("attribute definition `{key}` not found")))
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

    // Count objects that already carry a value for this attribute. Soft-deleted
    // objects do not block the deletion — they are invisible everywhere else
    // (object lists, completeness, enum usage) and keep their value only until
    // the data is purged.
    let count_sql = format!(
        "SELECT count() FROM object WHERE deleted_at IS NONE \
         AND attributes.{name} IS NOT NONE GROUP ALL;"
    );
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

    // Class features (Stage B) reference the attribute by name; the definition is
    // gone, so drop the dangling feature rows of every class.
    state
        .db
        .inner()
        .query("DELETE category_attribute WHERE attribute = $name;")
        .bind(("name", name.clone()))
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
