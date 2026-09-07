//! Object-type registry (Phase 9): the managed set of business object types.
//!
//! Every object belongs to exactly one type (its `type` string). Object types
//! are **flat** (no type inheritance): common properties are provided by
//! attribute definitions with `applicable_types: []` (empty = all), type
//! specific capabilities by attribute definitions scoped to that type. Each
//! type may declare `key_attributes` (the unique + mandatory business-key
//! attribute names for that type; empty = no key) and an optional
//! `label_attribute` (the attribute used as the display name).

use axum::Json;
use axum::extract::{Path, Query, State};
use axum::http::StatusCode;
use serde::Deserialize;
use serde_json::{Map, Value, json};
use utoipa::{IntoParams, ToSchema};

use crate::app::AppState;
use crate::audit::{self, AuditEntry};
use crate::auth::AuthUser;
use crate::db::Db;
use crate::error::ApiError;

/// Query parameters for listing object types.
#[derive(Debug, Deserialize, ToSchema, IntoParams)]
#[into_params(parameter_in = Query)]
pub struct ListObjectTypes {
    #[serde(default = "default_limit")]
    pub limit: u32,
    #[serde(default)]
    pub start: u32,
}

fn default_limit() -> u32 {
    1000
}

/// Payload for creating an object type.
#[derive(Debug, Deserialize, ToSchema)]
pub struct CreateObjectType {
    /// Type code (e.g. `product`), maps to the object `type`.
    pub name: String,
    /// Multilingual label.
    #[serde(default)]
    pub label: Option<Value>,
    #[serde(default)]
    pub description: Option<String>,
    /// Attribute names that are the unique + mandatory business keys.
    #[serde(default)]
    pub key_attributes: Option<Vec<String>>,
    /// Attribute name used as the display label.
    #[serde(default)]
    pub label_attribute: Option<String>,
}

/// Payload for updating an object type.
#[derive(Debug, Deserialize, ToSchema)]
pub struct UpdateObjectType {
    #[serde(default)]
    pub label: Option<Value>,
    #[serde(default)]
    pub description: Option<String>,
    #[serde(default)]
    pub key_attributes: Option<Vec<String>>,
    #[serde(default)]
    pub label_attribute: Option<Option<String>>,
}

/// Loads a single object-type record by name.
pub(crate) async fn fetch_type(db: &Db, name: &str) -> Result<Option<Value>, ApiError> {
    let mut response = db
        .inner()
        .query("SELECT * FROM object_type WHERE name = $name LIMIT 1;")
        .bind(("name", name.to_owned()))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    Ok(rows.into_iter().next())
}

/// Returns the key_attributes names for a type from the registry (empty if the
/// type is not registered or has no keys).
pub(crate) async fn key_attributes_for(
    db: &Db,
    object_type: &str,
) -> Result<Vec<String>, ApiError> {
    let Some(record) = fetch_type(db, object_type).await? else {
        return Ok(Vec::new());
    };
    Ok(record
        .get("key_attributes")
        .and_then(Value::as_array)
        .map(|arr| {
            arr.iter()
                .filter_map(Value::as_str)
                .map(str::to_owned)
                .collect()
        })
        .unwrap_or_default())
}

/// Validates that a key/label attribute reference is `text` (and not
/// multilingual) when the attribute already exists. Unknown attribute names are
/// allowed as forward references (the key becomes active once the attribute is
/// created).
async fn validate_attribute_references(
    db: &Db,
    key_attributes: &[String],
    label_attribute: Option<&str>,
) -> Result<(), ApiError> {
    let mut names = key_attributes.to_vec();
    if let Some(label) = label_attribute {
        names.push(label.to_owned());
    }
    names.sort();
    names.dedup();
    for name in names {
        let rows: Vec<Value> = db
            .inner()
            .query(
                "SELECT type, multilingual FROM attribute_definition WHERE name = $name LIMIT 1;",
            )
            .bind(("name", name.clone()))
            .await?
            .check()?
            .take(0)?;
        if let Some(definition) = rows.into_iter().next() {
            let definition_type = definition
                .get("type")
                .and_then(Value::as_str)
                .unwrap_or_default();
            let multilingual = definition
                .get("multilingual")
                .and_then(Value::as_bool)
                .unwrap_or(false);
            if definition_type != "text" || multilingual {
                return Err(ApiError::bad_request(format!(
                    "attribute `{name}` is not eligible as a key/label: business keys and \
                     labels must be `text` and not multilingual"
                )));
            }
        }
    }
    Ok(())
}

/// Lists all object types.
#[utoipa::path(
    get,
    path = "/object-types",
    params(ListObjectTypes),
    responses((status = 200, description = "Object types", body = Vec<Value>))
)]
pub async fn list_object_types(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Query(params): Query<ListObjectTypes>,
) -> Result<Json<Vec<Value>>, ApiError> {
    if !claims.is_editor() {
        return Err(ApiError::forbidden("requires editor role"));
    }
    let limit = params.limit.clamp(1, 1000) as i64;
    let start = params.start as i64;
    let mut response = state
        .db
        .inner()
        .query("SELECT * FROM object_type ORDER BY name LIMIT $limit START $start;")
        .bind(("limit", limit))
        .bind(("start", start))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    Ok(Json(rows))
}

/// Gets a single object type.
#[utoipa::path(
    get,
    path = "/object-types/{name}",
    responses((status = 200, description = "Object type", body = Value))
)]
pub async fn get_object_type(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(name): Path<String>,
) -> Result<Json<Value>, ApiError> {
    if !claims.is_editor() {
        return Err(ApiError::forbidden("requires editor role"));
    }
    let object_type = fetch_type(&state.db, &name)
        .await?
        .ok_or_else(|| ApiError::not_found(format!("object type `{name}` not found")))?;
    Ok(Json(object_type))
}

/// Creates an object type.
#[utoipa::path(
    post,
    path = "/object-types",
    request_body = CreateObjectType,
    responses((status = 201, description = "Created", body = Value))
)]
pub async fn create_object_type(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Json(body): Json<CreateObjectType>,
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
        .query("SELECT id FROM object_type WHERE name = $name LIMIT 1")
        .bind(("name", name.clone()))
        .await?
        .check()?
        .take(0)?;
    if !existing.is_empty() {
        return Err(ApiError::conflict(format!(
            "object type `{name}` already exists"
        )));
    }

    let key_attributes = body.key_attributes.unwrap_or_default();
    let label_attribute = body
        .label_attribute
        .as_deref()
        .map(str::trim)
        .filter(|v| !v.is_empty());
    validate_attribute_references(&state.db, &key_attributes, label_attribute).await?;

    let mut attributes = Map::new();
    attributes.insert("name".to_owned(), Value::String(name.clone()));
    if let Some(label) = body.label.clone() {
        attributes.insert("label".to_owned(), label);
    } else {
        attributes.insert("label".to_owned(), json!({ "en": name }));
    }
    if let Some(description) = body
        .description
        .as_deref()
        .map(str::trim)
        .filter(|v| !v.is_empty())
    {
        attributes.insert(
            "description".to_owned(),
            Value::String(description.to_owned()),
        );
    }
    attributes.insert(
        "key_attributes".to_owned(),
        Value::Array(
            key_attributes
                .iter()
                .map(|v| Value::String(v.clone()))
                .collect(),
        ),
    );
    attributes.insert("system".to_owned(), Value::Bool(false));
    if let Some(la) = label_attribute {
        attributes.insert("label_attribute".to_owned(), Value::String(la.to_owned()));
    }

    let mut response = state
        .db
        .inner()
        .query(
            "LET $t = type::record('object_type', $key); CREATE $t CONTENT $content; SELECT * FROM $t;",
        )
        .bind(("key", name.clone()))
        .bind(("content", Value::Object(attributes)))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(2)?;
    let record = rows
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::internal("created object type was not returned"))?;
    let record_id = record
        .get("id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &claims.sub,
            action: "object_type_created",
            object_id: Some(&record_id),
            from: None,
            to: None,
            metadata: Some(json!({ "name": name })),
        },
    )
    .await;
    Ok((StatusCode::CREATED, Json(record)))
}

/// Updates an object type.
#[utoipa::path(
    patch,
    path = "/object-types/{name}",
    request_body = UpdateObjectType,
    responses((status = 200, description = "Updated", body = Value))
)]
pub async fn update_object_type(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(name): Path<String>,
    Json(body): Json<UpdateObjectType>,
) -> Result<Json<Value>, ApiError> {
    if !claims.is_admin() {
        return Err(ApiError::forbidden("requires admin role"));
    }
    let current = fetch_type(&state.db, &name)
        .await?
        .ok_or_else(|| ApiError::not_found(format!("object type `{name}` not found")))?;
    let mut content = Map::new();

    if let Some(label) = body.label {
        content.insert("label".to_owned(), label);
    }
    if let Some(description) = body.description {
        match description.trim() {
            "" => {
                content.remove("description");
            }
            value => {
                content.insert("description".to_owned(), Value::String(value.to_owned()));
            }
        }
    }
    if let Some(key_attributes) = body.key_attributes {
        let la = current
            .get("label_attribute")
            .and_then(Value::as_str)
            .map(str::to_owned);
        validate_attribute_references(&state.db, &key_attributes, la.as_deref()).await?;
        content.insert(
            "key_attributes".to_owned(),
            Value::Array(
                key_attributes
                    .iter()
                    .map(|v| Value::String(v.clone()))
                    .collect(),
            ),
        );
    }
    if let Some(label_attribute) = body.label_attribute {
        match label_attribute.as_deref().map(str::trim) {
            Some(value) if !value.is_empty() => {
                content.insert(
                    "label_attribute".to_owned(),
                    Value::String(value.to_owned()),
                );
            }
            _ => {
                content.remove("label_attribute");
            }
        }
    }

    let mut response = state
        .db
        .inner()
        .query(
            "LET $t = type::record('object_type', $key); UPDATE $t MERGE $content; UPDATE $t SET updated_at = time::now(), updated_by = $user; SELECT * FROM $t;",
        )
        .bind(("key", name.clone()))
        .bind(("content", Value::Object(content)))
        .bind(("user", claims.sub.clone()))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(3)?;
    let record = rows
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::not_found(format!("object type `{name}` not found")))?;
    let record_id = record
        .get("id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &claims.sub,
            action: "object_type_updated",
            object_id: Some(&record_id),
            from: None,
            to: None,
            metadata: Some(json!({ "name": name })),
        },
    )
    .await;
    Ok(Json(record))
}

/// Deletes an object type.
#[utoipa::path(
    delete,
    path = "/object-types/{name}",
    responses((status = 204, description = "Deleted"))
)]
pub async fn delete_object_type(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(name): Path<String>,
) -> Result<StatusCode, ApiError> {
    if !claims.is_admin() {
        return Err(ApiError::forbidden("requires admin role"));
    }
    let current = fetch_type(&state.db, &name)
        .await?
        .ok_or_else(|| ApiError::not_found(format!("object type `{name}` not found")))?;
    // System (seeded/default) types are protected and cannot be deleted.
    if current
        .get("system")
        .and_then(Value::as_bool)
        .unwrap_or(false)
    {
        return Err(ApiError::bad_request(format!(
            "object type `{name}` is a system type and cannot be deleted"
        )));
    }
    let record_id = current
        .get("id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    // Refuse to delete a type still used by objects.
    let used: Vec<Value> = state
        .db
        .inner()
        .query("SELECT id FROM object WHERE type = $type AND deleted_at IS NONE LIMIT 1")
        .bind(("type", name.clone()))
        .await?
        .check()?
        .take(0)?;
    if !used.is_empty() {
        return Err(ApiError::conflict(format!(
            "object type `{name}` is still used by objects"
        )));
    }
    state
        .db
        .inner()
        .query("DELETE type::record('object_type', $key);")
        .bind(("key", name.clone()))
        .await?
        .check()?;
    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &claims.sub,
            action: "object_type_deleted",
            object_id: Some(&record_id),
            from: None,
            to: None,
            metadata: Some(json!({ "name": name })),
        },
    )
    .await;
    Ok(StatusCode::NO_CONTENT)
}

/// Ensures an object type is registered (auto-registers unknown types so the
/// registry stays complete). Best-effort: a registry write failure never fails
/// the object write.
pub(crate) async fn ensure_registered(db: &Db, object_type: &str) -> Result<(), ApiError> {
    if object_type.is_empty() {
        return Ok(());
    }
    if fetch_type(db, object_type).await?.is_some() {
        return Ok(());
    }
    let existing: Vec<Value> = db
        .inner()
        .query("SELECT id FROM object_type WHERE name = $name LIMIT 1")
        .bind(("name", object_type.to_owned()))
        .await?
        .check()?
        .take(0)?;
    if !existing.is_empty() {
        return Ok(());
    }
    db.inner()
        .query("LET $t = type::record('object_type', $key); CREATE $t CONTENT $content;")
        .bind(("key", object_type.to_owned()))
        .bind((
            "content",
            json!({
                "name": object_type,
                "label": { "en": object_type },
                "key_attributes": [],
                "system": false,
            }),
        ))
        .await?
        .check()?;
    Ok(())
}
