//! Object CRUD handlers.
//!
//! Every object is part of the composition tree: creating an object also
//! creates its `depends_on` edge to the universal root.

use axum::Json;
use axum::extract::{Path, Query, State};
use axum::http::StatusCode;
use serde::Deserialize;
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

/// Query parameters for listing objects.
#[derive(Debug, Deserialize, ToSchema, IntoParams)]
#[into_params(parameter_in = Query)]
pub struct ListObjects {
    /// Filter by object type.
    #[serde(rename = "type")]
    #[param(rename = "type")]
    pub object_type: Option<String>,
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
    responses(
        (status = 201, description = "Created object", body = Value),
        (status = 400, description = "Invalid payload", body = Value),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Forbidden", body = Value),
        (status = 500, description = "Internal error", body = Value)
    )
)]
pub async fn create_object(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Json(body): Json<CreateObject>,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    if !claims.is_editor() {
        return Err(ApiError::forbidden("requires editor role"));
    }
    if body.object_type.trim().is_empty() {
        return Err(ApiError::bad_request("`type` must not be empty"));
    }
    let CreateObject {
        object_type,
        attributes,
        status,
    } = body;
    let object_type = object_type.trim().to_owned();
    let user_id = claims.sub.clone();

    let Value::Object(mut attributes) = attributes else {
        return Err(ApiError::bad_request("`attributes` must be an object"));
    };
    keys::normalize_key_attributes(&state.db, &object_type, &mut attributes, None).await?;

    let content = json!({
        "type": object_type,
        "attributes": Value::Object(attributes),
        "status": status,
        "version": 1,
        "updated_by": user_id.clone(),
    });

    // Statements: BEGIN(0), LET(1), CREATE(2), RELATE(3), SELECT(4), COMMIT(5).
    let sql = "
        BEGIN TRANSACTION;
        LET $obj = type::record('object', rand::ulid());
        CREATE $obj CONTENT $content;
        RELATE $obj->reference->object:object_root SET definition_id = 'rel_depends_on', type = 'depends_on', attributes = {};
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
    let objects: Vec<Value> = response.take(4)?;
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
    // Statements: LET(0), SELECT(1).
    let sql = "
        LET $obj = type::record('object', $key);
        SELECT * FROM $obj;
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

    if let Some(attributes) = object.get_mut("attributes").and_then(Value::as_object_mut) {
        if let Some(locale) = locale {
            crate::language::resolve_attributes(&state.db, locale, attributes).await?;
        }
        if params.resolve_inheritance == Some(true) {
            crate::inheritance::resolve_inheritance(&state.db, key, locale, attributes).await?;
        }
    }

    Ok(Json(object))
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

    let mut sql = String::from("SELECT * FROM object");
    if params.object_type.is_some() {
        sql.push_str(" WHERE type = $type");
    }
    sql.push_str(" ORDER BY created_at DESC LIMIT $limit START $start");

    let mut query = state.db.inner().query(sql);
    if let Some(object_type) = &params.object_type {
        query = query.bind(("type", object_type.clone()));
    }
    let mut response = query
        .bind(("limit", limit))
        .bind(("start", start))
        .await?
        .check()?;
    let objects: Vec<Value> = response.take(0)?;
    Ok(Json(objects))
}

/// Partially updates an object's attributes and/or status.
#[utoipa::path(
    patch,
    path = "/objects/{id}",
    request_body = UpdateObject,
    responses(
        (status = 200, description = "Updated object", body = Value),
        (status = 400, description = "Invalid id or payload", body = Value),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Forbidden", body = Value),
        (status = 404, description = "Object not found", body = Value)
    )
)]
pub async fn update_object(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
    Json(body): Json<UpdateObject>,
) -> Result<Json<Value>, ApiError> {
    if !claims.is_editor() {
        return Err(ApiError::forbidden("requires editor role"));
    }
    let key = parse_key(&id, TABLE)?;
    let user_id = claims.sub.clone();

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

    if changes.is_empty() {
        return Ok(Json(current));
    }

    // Statements: BEGIN(0), LET(1), UPDATE MERGE(2), UPDATE SET(3), SELECT(4), COMMIT(5).
    let sql = "
        BEGIN TRANSACTION;
        LET $obj = type::record('object', $key);
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
        .bind(("changes", Value::Object(changes)))
        .bind(("user", user_id.clone()))
        .await?
        .check()?;
    let objects: Vec<Value> = response.take(4)?;
    let object = objects
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::not_found(format!("object `{id}` not found")))?;

    let status_changed = changed_fields.contains(&"status");
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
            object_id: Some(&id),
            from,
            to,
            metadata,
        },
    )
    .await;

    Ok(Json(object))
}
