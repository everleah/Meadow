//! Attribute group management handlers (admin/superadmin only).

use axum::Json;
use axum::extract::{Path, Query, State};
use axum::http::StatusCode;
use serde::Deserialize;
use serde_json::Value;
use utoipa::{IntoParams, ToSchema};

use crate::app::AppState;
use crate::auth::AuthUser;
use crate::error::ApiError;

const TABLE: &str = "attribute_group";

/// Payload for creating an attribute group.
#[derive(Debug, Deserialize, ToSchema)]
pub struct CreateAttributeGroup {
    pub name: String,
    /// Multilingual label.
    #[serde(default = "default_object")]
    pub label: Value,
    /// Sort position in the UI.
    #[serde(default)]
    pub position: i64,
    /// Object types the group applies to (empty = derive from members).
    #[serde(default)]
    pub applicable_types: Vec<String>,
}

fn default_object() -> Value {
    Value::Object(serde_json::Map::new())
}

/// Payload for a partial attribute group update.
#[derive(Debug, Deserialize, ToSchema)]
pub struct UpdateAttributeGroup {
    #[serde(default)]
    pub name: Option<String>,
    #[serde(default)]
    pub label: Option<Value>,
    #[serde(default)]
    pub position: Option<i64>,
    #[serde(default)]
    pub applicable_types: Option<Vec<String>>,
}

/// Query parameters for listing attribute groups.
#[derive(Debug, Deserialize, ToSchema, IntoParams)]
#[into_params(parameter_in = Query)]
pub struct ListAttributeGroups {
    #[serde(default = "default_limit")]
    pub limit: u32,
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

/// Creates a new attribute group.
#[utoipa::path(
    post,
    path = "/attribute-groups",
    request_body = CreateAttributeGroup,
    responses(
        (status = 201, description = "Created attribute group", body = Value),
        (status = 403, description = "Forbidden", body = Value),
        (status = 400, description = "Invalid payload", body = Value)
    )
)]
pub async fn create_attribute_group(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Json(body): Json<CreateAttributeGroup>,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    claims.require("attribute_group.manage")?;
    if body.name.trim().is_empty() {
        return Err(ApiError::bad_request("`name` must not be empty"));
    }
    let content = serde_json::json!({
        "name": body.name,
        "label": body.label,
        "position": body.position,
        "applicable_types": body.applicable_types,
    });

    let sql = "
        LET $g = type::record('attribute_group', rand::ulid());
        CREATE $g CONTENT $content;
        SELECT * FROM $g;
    ";
    let mut response = state
        .db
        .inner()
        .query(sql)
        .bind(("content", content))
        .await?
        .check()?;
    let groups: Vec<Value> = response.take(2)?;
    let group = groups
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::internal("created attribute group was not returned"))?;
    Ok((StatusCode::CREATED, Json(group)))
}

/// Lists attribute groups.
///
/// Read access is `attribute_definition.read` (viewer+), not admin-only: the groups are
/// display metadata — the product form renders one section per group and the export
/// wizard groups its attribute selection by them. Writes need `attribute_group.manage`.
#[utoipa::path(
    get,
    path = "/attribute-groups",
    params(ListAttributeGroups),
    responses(
        (status = 200, description = "Attribute groups", body = Vec<Value>),
        (status = 403, description = "Forbidden", body = Value)
    )
)]
pub async fn list_attribute_groups(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Query(params): Query<ListAttributeGroups>,
) -> Result<Json<Vec<Value>>, ApiError> {
    claims.require("attribute_definition.read")?;
    let limit = params.limit.clamp(1, 1000) as i64;
    let start = params.start as i64;
    let sql = "SELECT * FROM attribute_group ORDER BY position ASC LIMIT $limit START $start";
    let mut response = state
        .db
        .inner()
        .query(sql)
        .bind(("limit", limit))
        .bind(("start", start))
        .await?
        .check()?;
    let groups: Vec<Value> = response.take(0)?;
    Ok(Json(groups))
}

/// Loads a single attribute group by id.
#[utoipa::path(
    get,
    path = "/attribute-groups/{id}",
    responses(
        (status = 200, description = "Attribute group", body = Value),
        (status = 404, description = "Not found", body = Value)
    )
)]
pub async fn get_attribute_group(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<Json<Value>, ApiError> {
    claims.require("attribute_definition.read")?;
    let key = parse_key(&id, TABLE)?;
    let sql = "
        LET $g = type::record('attribute_group', $key);
        SELECT * FROM $g;
    ";
    let mut response = state
        .db
        .inner()
        .query(sql)
        .bind(("key", key))
        .await?
        .check()?;
    let groups: Vec<Value> = response.take(1)?;
    let group = groups
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::not_found(format!("attribute group `{id}` not found")))?;
    Ok(Json(group))
}

/// Partially updates an attribute group.
#[utoipa::path(
    patch,
    path = "/attribute-groups/{id}",
    request_body = UpdateAttributeGroup,
    responses(
        (status = 200, description = "Updated attribute group", body = Value),
        (status = 404, description = "Not found", body = Value)
    )
)]
pub async fn update_attribute_group(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
    Json(body): Json<UpdateAttributeGroup>,
) -> Result<Json<Value>, ApiError> {
    claims.require("attribute_group.manage")?;
    let key = parse_key(&id, TABLE)?;

    let mut changes = serde_json::Map::new();
    if let Some(name) = body.name {
        changes.insert("name".to_owned(), Value::String(name));
    }
    if let Some(label) = body.label {
        changes.insert("label".to_owned(), label);
    }
    if let Some(position) = body.position {
        changes.insert("position".to_owned(), Value::from(position));
    }
    if let Some(applicable_types) = body.applicable_types {
        changes.insert(
            "applicable_types".to_owned(),
            Value::Array(applicable_types.into_iter().map(Value::String).collect()),
        );
    }
    let changes = Value::Object(changes);

    let sql = "
        LET $g = type::record('attribute_group', $key);
        UPDATE $g MERGE $changes;
        SELECT * FROM $g;
    ";
    let mut response = state
        .db
        .inner()
        .query(sql)
        .bind(("key", key))
        .bind(("changes", changes))
        .await?
        .check()?;
    let groups: Vec<Value> = response.take(2)?;
    let group = groups
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::not_found(format!("attribute group `{id}` not found")))?;
    Ok(Json(group))
}

/// Deletes an attribute group.
#[utoipa::path(
    delete,
    path = "/attribute-groups/{id}",
    responses(
        (status = 204, description = "Deleted"),
        (status = 404, description = "Not found", body = Value)
    )
)]
pub async fn delete_attribute_group(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<StatusCode, ApiError> {
    claims.require("attribute_group.manage")?;
    let key = parse_key(&id, TABLE)?;
    let gid_full = format!("{TABLE}:{key}");
    let sql = "
        LET $g = type::record('attribute_group', $key);
        UPDATE attribute_definition
            SET group_ids = array::difference(group_ids ?? [], [$gid])
            WHERE $gid IN group_ids;
        DELETE $g;
    ";
    state
        .db
        .inner()
        .query(sql)
        .bind(("key", key))
        .bind(("gid", gid_full))
        .await?
        .check()?;
    Ok(StatusCode::NO_CONTENT)
}
