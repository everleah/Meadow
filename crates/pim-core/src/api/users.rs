//! User management handlers (admin/superadmin only).

use axum::Json;
use axum::extract::{Path, Query, State};
use axum::http::StatusCode;
use serde::Deserialize;
use serde_json::{Value, json};
use utoipa::{IntoParams, ToSchema};

use crate::app::AppState;
use crate::auth::{self, AuthUser, ROLE_SUPERADMIN};
use crate::db::Db;
use crate::error::ApiError;
use crate::rbac;

const TABLE: &str = "user";
const VALID_ROLES: [&str; 4] = ["superadmin", "admin", "editor", "viewer"];

/// Payload for creating a user.
#[derive(Debug, Deserialize, ToSchema)]
pub struct CreateUser {
    pub username: String,
    pub email: String,
    pub password: String,
    /// Roles (subset of `superadmin`, `admin`, `editor`, `viewer`).
    #[serde(default)]
    pub roles: Vec<String>,
    #[serde(default = "default_locale")]
    pub locale: String,
    #[serde(default = "default_status")]
    pub status: String,
    /// Whether the user must set their own password on first login (default
    /// `true`; uncheck it when the password is final, e.g. integration accounts).
    #[serde(default = "default_must_change_password")]
    pub must_change_password: bool,
}

fn default_locale() -> String {
    "en".to_owned()
}

fn default_status() -> String {
    "active".to_owned()
}

fn default_must_change_password() -> bool {
    true
}

/// Payload for a partial user update.
#[derive(Debug, Deserialize, ToSchema)]
pub struct UpdateUser {
    #[serde(default)]
    pub email: Option<String>,
    #[serde(default)]
    pub password: Option<String>,
    #[serde(default)]
    pub roles: Option<Vec<String>>,
    #[serde(default)]
    pub status: Option<String>,
    #[serde(default)]
    pub locale: Option<String>,
    /// Whether the user must set their own password (see `CreateUser`).
    #[serde(default)]
    pub must_change_password: Option<bool>,
}

/// Query parameters for listing users.
#[derive(Debug, Deserialize, ToSchema, IntoParams)]
#[into_params(parameter_in = Query)]
pub struct ListUsers {
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

/// Loads a single user without its password hash.
async fn fetch_user(db: &Db, key: &str) -> Result<Option<Value>, ApiError> {
    let sql = "
        LET $u = type::record('user', $key);
        SELECT * OMIT password_hash FROM $u;
    ";
    let mut response = db.inner().query(sql).bind(("key", key)).await?.check()?;
    let users: Vec<Value> = response.take(1)?;
    Ok(users.into_iter().next())
}

/// Counts active `superadmin` users.
async fn active_superadmin_count(db: &Db) -> Result<u64, ApiError> {
    let mut response = db
        .inner()
        .query("SELECT count() FROM user WHERE roles CONTAINS 'superadmin' AND status = 'active' GROUP ALL")
        .await?
        .check()?;
    let counts: Vec<Value> = response.take(0)?;
    Ok(counts
        .first()
        .and_then(|entry| entry.get("count"))
        .and_then(Value::as_u64)
        .unwrap_or(0))
}

/// Whether a user record is an active superadmin.
fn is_active_superadmin(user: &Value) -> bool {
    let superadmin = user
        .get("roles")
        .and_then(Value::as_array)
        .map(|roles| {
            roles
                .iter()
                .any(|role| role.as_str() == Some(ROLE_SUPERADMIN))
        })
        .unwrap_or(false);
    let active = user.get("status").and_then(Value::as_str) == Some("active");
    superadmin && active
}

/// Creates a new user.
#[utoipa::path(
    post,
    path = "/users",
    request_body = CreateUser,
    responses(
        (status = 201, description = "Created user", body = Value),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Forbidden", body = Value),
        (status = 400, description = "Invalid payload", body = Value)
    )
)]
pub async fn create_user(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Json(body): Json<CreateUser>,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    claims.require("user.manage")?;
    if body.username.trim().is_empty() || body.email.trim().is_empty() || body.password.is_empty() {
        return Err(ApiError::bad_request(
            "username, email and password are required",
        ));
    }
    auth::validate_new_password("", &body.password).map_err(ApiError::bad_request)?;
    for role in &body.roles {
        if !VALID_ROLES.contains(&role.as_str()) {
            return Err(ApiError::bad_request(format!("unknown role `{role}`")));
        }
    }

    let password_hash = auth::hash_password(&body.password)
        .map_err(|error| ApiError::internal(error.to_string()))?;
    let content = json!({
        "username": body.username,
        "email": body.email,
        "password_hash": password_hash,
        "auth_provider": "local",
        "roles": body.roles,
        "status": body.status,
        "token_version": 1,
        "must_change_password": body.must_change_password,
        "locale": body.locale,
    });

    let sql = "
        LET $u = type::record('user', rand::ulid());
        CREATE $u CONTENT $content;
        SELECT * OMIT password_hash FROM $u;
    ";
    let mut response = state
        .db
        .inner()
        .query(sql)
        .bind(("content", content))
        .await?
        .check()?;
    let users: Vec<Value> = response.take(2)?;
    let user = users
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::internal("created user was not returned"))?;
    Ok((StatusCode::CREATED, Json(user)))
}

/// Lists users.
#[utoipa::path(
    get,
    path = "/users",
    params(ListUsers),
    responses((status = 200, description = "Users", body = Vec<Value>))
)]
pub async fn list_users(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Query(params): Query<ListUsers>,
) -> Result<Json<Vec<Value>>, ApiError> {
    claims.require("user.read")?;
    let limit = params.limit.clamp(1, 1000) as i64;
    let start = params.start as i64;
    let sql =
        "SELECT * OMIT password_hash FROM user ORDER BY username ASC LIMIT $limit START $start";
    let mut response = state
        .db
        .inner()
        .query(sql)
        .bind(("limit", limit))
        .bind(("start", start))
        .await?
        .check()?;
    let users: Vec<Value> = response.take(0)?;
    Ok(Json(users))
}

/// Loads a single user by id.
#[utoipa::path(
    get,
    path = "/users/{id}",
    responses(
        (status = 200, description = "User", body = Value),
        (status = 404, description = "Not found", body = Value)
    )
)]
pub async fn get_user(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<Json<Value>, ApiError> {
    claims.require("user.read")?;
    let key = parse_key(&id, TABLE)?;
    let user = fetch_user(&state.db, key)
        .await?
        .ok_or_else(|| ApiError::not_found(format!("user `{id}` not found")))?;
    Ok(Json(user))
}

/// Partially updates a user.
#[utoipa::path(
    patch,
    path = "/users/{id}",
    request_body = UpdateUser,
    responses(
        (status = 200, description = "Updated user", body = Value),
        (status = 404, description = "Not found", body = Value)
    )
)]
pub async fn update_user(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
    Json(body): Json<UpdateUser>,
) -> Result<Json<Value>, ApiError> {
    claims.require("user.manage")?;
    let key = parse_key(&id, TABLE)?;
    let existing = fetch_user(&state.db, key)
        .await?
        .ok_or_else(|| ApiError::not_found(format!("user `{id}` not found")))?;

    // Protect the last active superadmin from losing its role.
    if let Some(new_roles) = &body.roles
        && !new_roles.iter().any(|role| role == ROLE_SUPERADMIN)
        && is_active_superadmin(&existing)
        && active_superadmin_count(&state.db).await? == 1
    {
        return Err(ApiError::forbidden(
            "cannot remove the superadmin role from the last active superadmin",
        ));
    }

    let mut changes = serde_json::Map::new();
    if let Some(email) = body.email {
        changes.insert("email".to_owned(), Value::String(email));
    }
    let password_changed = if let Some(password) = body.password {
        if password.is_empty() {
            return Err(ApiError::bad_request("password must not be empty"));
        }
        auth::validate_new_password("", &password).map_err(ApiError::bad_request)?;
        let hash = auth::hash_password(&password)
            .map_err(|error| ApiError::internal(error.to_string()))?;
        changes.insert("password_hash".to_owned(), Value::String(hash));
        // An admin-set password is not the user's own choice: the marker is
        // cleared below (so the UI keeps hinting at "password never changed")
        // and the token version is bumped (invalidates older tokens once
        // enforcement is in place).
        let version = existing
            .get("token_version")
            .and_then(Value::as_u64)
            .unwrap_or(0);
        changes.insert("token_version".to_owned(), Value::from(version + 1));
        // A handed-over password normally has to be replaced by the user; an
        // explicit `must_change_password` in the request wins.
        changes.insert(
            "must_change_password".to_owned(),
            Value::Bool(body.must_change_password.unwrap_or(true)),
        );
        true
    } else {
        false
    };
    if let Some(roles) = body.roles {
        for role in &roles {
            if !VALID_ROLES.contains(&role.as_str()) {
                return Err(ApiError::bad_request(format!("unknown role `{role}`")));
            }
        }
        changes.insert(
            "roles".to_owned(),
            Value::Array(roles.into_iter().map(Value::String).collect()),
        );
    }
    if let Some(status) = body.status {
        changes.insert("status".to_owned(), Value::String(status));
    }
    if let Some(locale) = body.locale {
        changes.insert("locale".to_owned(), Value::String(locale));
    }
    // Explicit wins over the default set by the password branch above, and works
    // on its own (clear/re-arm the requirement without touching the password).
    if let Some(must_change) = body.must_change_password {
        changes.insert("must_change_password".to_owned(), Value::Bool(must_change));
    }
    let changes = Value::Object(changes);

    // Roles, status and password are the access of a user: the sessions issued before
    // this change have to be renewed (fail-closed). Bumped before the write — a failed
    // write then costs an unnecessary login, the other order would keep a revoked right
    // alive in an issued token.
    rbac::invalidate_sessions(&state.db, &state.cache).await?;
    // `password_changed_at` is an `option<datetime>`: MERGE cannot write a null
    // there, so an admin reset unsets the field in an extra statement.
    let mut sql = String::from(
        "
        LET $u = type::record('user', $key);
        UPDATE $u MERGE $changes;
    ",
    );
    let mut select_index = 2;
    if password_changed {
        sql.push_str("UPDATE $u UNSET password_changed_at;\n");
        select_index = 3;
    }
    sql.push_str(
        "
        UPDATE $u SET updated_at = time::now();
        SELECT * OMIT password_hash FROM $u;
    ",
    );

    let mut response = state
        .db
        .inner()
        .query(&sql)
        .bind(("key", key))
        .bind(("changes", changes))
        .await?
        .check()?;
    let users: Vec<Value> = response.take(select_index)?;
    let user = users
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::not_found(format!("user `{id}` not found")))?;

    if password_changed {
        crate::audit::record_best_effort(
            &state.db,
            crate::audit::AuditEntry {
                user_id: &claims.sub,
                action: "user_password_reset",
                object_id: Some(&id),
                from: None,
                to: None,
                metadata: None,
            },
        )
        .await;
    }
    Ok(Json(user))
}

/// Deletes a user.
#[utoipa::path(
    delete,
    path = "/users/{id}",
    responses(
        (status = 204, description = "Deleted"),
        (status = 403, description = "Forbidden", body = Value),
        (status = 404, description = "Not found", body = Value)
    )
)]
pub async fn delete_user(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<StatusCode, ApiError> {
    claims.require("user.manage")?;
    let key = parse_key(&id, TABLE)?;
    let existing = fetch_user(&state.db, key)
        .await?
        .ok_or_else(|| ApiError::not_found(format!("user `{id}` not found")))?;

    if is_active_superadmin(&existing) && active_superadmin_count(&state.db).await? == 1 {
        return Err(ApiError::forbidden(
            "cannot delete the last active superadmin",
        ));
    }

    // A deleted user's token must not survive.
    rbac::invalidate_sessions(&state.db, &state.cache).await?;
    let sql = "
        LET $u = type::record('user', $key);
        DELETE $u;
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
