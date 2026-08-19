//! Attribute definition CRUD handlers.

use axum::Json;
use axum::extract::{Path, Query, State};
use axum::http::StatusCode;
use serde::Deserialize;
use serde_json::Value;
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
    /// Optional attribute group id.
    #[serde(default)]
    pub group_id: Option<String>,
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
    pub group_id: Option<String>,
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
        group_id,
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

    validate_key_definition(&definition_type, multilingual, is_key)?;
    // Business keys are never inheritable (an inherited identity would
    // duplicate). `is_key: true` forces `inheritable: false`.
    let inheritable = if is_key { false } else { inheritable };

    let mut content = serde_json::Map::new();
    content.insert("name".to_owned(), Value::String(name));
    content.insert("label".to_owned(), label);
    content.insert("type".to_owned(), Value::String(definition_type));
    if let Some(group_id) = group_id {
        content.insert("group_id".to_owned(), Value::String(group_id));
    }
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

    // Statements: BEGIN(0), LET(1), CREATE(2), SELECT(3), COMMIT(4).
    let sql = "
        BEGIN TRANSACTION;
        LET $obj = type::record('attribute_definition', rand::ulid());
        CREATE $obj CONTENT $content;
        SELECT * FROM $obj;
        COMMIT TRANSACTION;
    ";
    let mut response = state
        .db
        .inner()
        .query(sql)
        .bind(("content", content))
        .await?
        .check()?;
    let definitions: Vec<Value> = response.take(3)?;
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
    if let Some(group_id) = body.group_id {
        changes.insert("group_id".to_owned(), Value::String(group_id));
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
    if let Some(constraints) = body.constraints {
        changes.insert("constraints".to_owned(), constraints);
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

    let changes = Value::Object(changes);

    // Statements: BEGIN(0), LET(1), UPDATE MERGE(2), UPDATE SET(3), SELECT(4), COMMIT(5).
    let sql = "
        BEGIN TRANSACTION;
        LET $obj = type::record('attribute_definition', $key);
        UPDATE $obj MERGE $changes;
        UPDATE $obj SET version = version + 1, updated_at = time::now(), updated_by = $user;
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
        .await?
        .check()?;
    let definitions: Vec<Value> = response.take(4)?;
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
