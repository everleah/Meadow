//! Group management handlers (admin/superadmin only).

use axum::Json;
use axum::extract::{Path, Query, State};
use axum::http::StatusCode;
use serde::Deserialize;
use serde_json::Value;
use utoipa::{IntoParams, ToSchema};

use crate::app::AppState;
use crate::auth::AuthUser;
use crate::error::ApiError;

const TABLE: &str = "group";

/// Payload for creating a group.
#[derive(Debug, Deserialize, ToSchema)]
pub struct CreateGroup {
    pub name: String,
    /// Additional granted permissions.
    #[serde(default)]
    pub grant_permissions: Vec<String>,
    /// Explicitly denied permissions.
    #[serde(default)]
    pub deny_permissions: Vec<String>,
    /// ABAC data scope (see plan section 9).
    #[serde(default = "default_object")]
    pub scope: Value,
    /// UI branding.
    #[serde(default = "default_object")]
    pub branding: Value,
}

fn default_object() -> Value {
    Value::Object(serde_json::Map::new())
}

/// Payload for a partial group update.
#[derive(Debug, Deserialize, ToSchema)]
pub struct UpdateGroup {
    #[serde(default)]
    pub name: Option<String>,
    #[serde(default)]
    pub grant_permissions: Option<Vec<String>>,
    #[serde(default)]
    pub deny_permissions: Option<Vec<String>>,
    #[serde(default)]
    pub scope: Option<Value>,
    #[serde(default)]
    pub branding: Option<Value>,
}

/// Query parameters for listing groups.
#[derive(Debug, Deserialize, ToSchema, IntoParams)]
#[into_params(parameter_in = Query)]
pub struct ListGroups {
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

fn require_admin(claims: &crate::auth::Claims) -> Result<(), ApiError> {
    if claims.is_admin() {
        Ok(())
    } else {
        Err(ApiError::forbidden("requires admin role"))
    }
}

/// Creates a new group.
#[utoipa::path(
    post,
    path = "/groups",
    request_body = CreateGroup,
    responses(
        (status = 201, description = "Created group", body = Value),
        (status = 403, description = "Forbidden", body = Value)
    )
)]
pub async fn create_group(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Json(body): Json<CreateGroup>,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    require_admin(&claims)?;
    if body.name.trim().is_empty() {
        return Err(ApiError::bad_request("`name` must not be empty"));
    }
    let content = serde_json::json!({
        "name": body.name,
        "grant_permissions": body.grant_permissions,
        "deny_permissions": body.deny_permissions,
        "scope": body.scope,
        "branding": body.branding,
    });

    let sql = "
        LET $g = type::record('group', rand::ulid());
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
        .ok_or_else(|| ApiError::internal("created group was not returned"))?;
    Ok((StatusCode::CREATED, Json(group)))
}

/// Lists groups.
#[utoipa::path(
    get,
    path = "/groups",
    params(ListGroups),
    responses((status = 200, description = "Groups", body = Vec<Value>))
)]
pub async fn list_groups(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Query(params): Query<ListGroups>,
) -> Result<Json<Vec<Value>>, ApiError> {
    require_admin(&claims)?;
    let limit = params.limit.clamp(1, 1000) as i64;
    let start = params.start as i64;
    let sql = "SELECT * FROM group ORDER BY name ASC LIMIT $limit START $start";
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

/// Loads a single group by id.
#[utoipa::path(
    get,
    path = "/groups/{id}",
    responses(
        (status = 200, description = "Group", body = Value),
        (status = 404, description = "Not found", body = Value)
    )
)]
pub async fn get_group(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<Json<Value>, ApiError> {
    require_admin(&claims)?;
    let key = parse_key(&id, TABLE)?;
    let sql = "
        LET $g = type::record('group', $key);
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
        .ok_or_else(|| ApiError::not_found(format!("group `{id}` not found")))?;
    Ok(Json(group))
}

/// Partially updates a group.
#[utoipa::path(
    patch,
    path = "/groups/{id}",
    request_body = UpdateGroup,
    responses(
        (status = 200, description = "Updated group", body = Value),
        (status = 404, description = "Not found", body = Value)
    )
)]
pub async fn update_group(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
    Json(body): Json<UpdateGroup>,
) -> Result<Json<Value>, ApiError> {
    require_admin(&claims)?;
    let key = parse_key(&id, TABLE)?;

    let mut changes = serde_json::Map::new();
    if let Some(name) = body.name {
        changes.insert("name".to_owned(), Value::String(name));
    }
    if let Some(grant_permissions) = body.grant_permissions {
        changes.insert(
            "grant_permissions".to_owned(),
            Value::Array(grant_permissions.into_iter().map(Value::String).collect()),
        );
    }
    if let Some(deny_permissions) = body.deny_permissions {
        changes.insert(
            "deny_permissions".to_owned(),
            Value::Array(deny_permissions.into_iter().map(Value::String).collect()),
        );
    }
    if let Some(scope) = body.scope {
        changes.insert("scope".to_owned(), scope);
    }
    if let Some(branding) = body.branding {
        changes.insert("branding".to_owned(), branding);
    }
    let changes = Value::Object(changes);

    let sql = "
        LET $g = type::record('group', $key);
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
        .ok_or_else(|| ApiError::not_found(format!("group `{id}` not found")))?;
    Ok(Json(group))
}

/// Deletes a group.
#[utoipa::path(
    delete,
    path = "/groups/{id}",
    responses(
        (status = 204, description = "Deleted"),
        (status = 404, description = "Not found", body = Value)
    )
)]
pub async fn delete_group(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<StatusCode, ApiError> {
    require_admin(&claims)?;
    let key = parse_key(&id, TABLE)?;
    let sql = "
        LET $g = type::record('group', $key);
        DELETE $g;
    ";
    state
        .db
        .inner()
        .query(sql)
        .bind(("key", key))
        .await?
        .check()?;
    Ok(StatusCode::NO_CONTENT)
}
