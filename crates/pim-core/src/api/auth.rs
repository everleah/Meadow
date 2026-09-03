//! Authentication handlers: login, current-user and impersonation.

use axum::Json;
use axum::extract::{Path, State};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use utoipa::ToSchema;

use crate::app::AppState;
use crate::audit::{self, AuditEntry};
use crate::auth::{self, AuthUser, Claims, Impersonator};
use crate::error::ApiError;

/// Login payload.
#[derive(Debug, Deserialize, ToSchema)]
pub struct LoginRequest {
    pub username: String,
    pub password: String,
}

/// Login response carrying the signed JWT.
#[derive(Debug, Serialize, ToSchema)]
pub struct LoginResponse {
    pub token: String,
    pub token_type: String,
}

/// Current-user response.
#[derive(Debug, Serialize, ToSchema)]
pub struct MeResponse {
    pub sub: String,
    pub username: String,
    pub roles: Vec<String>,
    /// Present when the session impersonates another user (superadmin).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub impersonator: Option<Impersonator>,
}

/// Exchanges username/password for a JWT.
#[utoipa::path(
    post,
    path = "/auth/login",
    request_body = LoginRequest,
    responses(
        (status = 200, description = "JWT token", body = LoginResponse),
        (status = 401, description = "Invalid credentials", body = Value),
        (status = 403, description = "Account suspended", body = Value)
    )
)]
pub async fn login(
    State(state): State<AppState>,
    Json(body): Json<LoginRequest>,
) -> Result<Json<LoginResponse>, ApiError> {
    if body.username.trim().is_empty() || body.password.is_empty() {
        return Err(ApiError::unauthorized("invalid credentials"));
    }

    let sql = "SELECT * FROM user WHERE username = $username LIMIT 1";
    let mut response = state
        .db
        .inner()
        .query(sql)
        .bind(("username", body.username.clone()))
        .await?
        .check()?;
    let users: Vec<Value> = response.take(0)?;
    let user = users
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::unauthorized("invalid credentials"))?;

    let id = user
        .get("id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    let password_hash = user
        .get("password_hash")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let status = user
        .get("status")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let roles = user
        .get("roles")
        .and_then(Value::as_array)
        .map(|array| {
            array
                .iter()
                .filter_map(Value::as_str)
                .map(str::to_owned)
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    let token_version = user
        .get("token_version")
        .and_then(Value::as_u64)
        .unwrap_or(0);

    if status != "active" {
        return Err(ApiError::forbidden("account is suspended"));
    }
    if !auth::verify_password(&body.password, password_hash) {
        return Err(ApiError::unauthorized("invalid credentials"));
    }

    let token = auth::issue(
        &state.config.jwt_secret,
        state.config.jwt_ttl_seconds,
        Claims {
            sub: id,
            username: body.username.clone(),
            roles,
            token_version,
            exp: 0,
            impersonator: None,
        },
    )
    .map_err(|error| ApiError::internal(error.to_string()))?;

    Ok(Json(LoginResponse {
        token,
        token_type: "bearer".to_owned(),
    }))
}

/// Returns the current authenticated user's claims.
#[utoipa::path(
    get,
    path = "/auth/me",
    responses(
        (status = 200, description = "Current user", body = MeResponse),
        (status = 401, description = "Unauthorized", body = Value)
    )
)]
pub async fn me(AuthUser(claims): AuthUser) -> Json<MeResponse> {
    Json(MeResponse {
        sub: claims.sub,
        username: claims.username,
        roles: claims.roles,
        impersonator: claims.impersonator,
    })
}

/// "Login as user" for superadmin: issues a JWT for the target user with the
/// target's roles and an `impersonator` claim (the acting superadmin).
#[utoipa::path(
    post,
    path = "/auth/impersonate/{user_id}",
    responses(
        (status = 200, description = "Impersonation token", body = LoginResponse),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Forbidden", body = Value),
        (status = 400, description = "Invalid request", body = Value),
        (status = 404, description = "User not found", body = Value)
    )
)]
pub async fn impersonate_user(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<Json<LoginResponse>, ApiError> {
    if !claims.has_role(auth::ROLE_SUPERADMIN) {
        return Err(ApiError::forbidden("requires superadmin role"));
    }
    let (table, key) = id
        .split_once(':')
        .ok_or_else(|| ApiError::bad_request(format!("invalid id `{id}`")))?;
    if table != "user" {
        return Err(ApiError::bad_request(format!(
            "id `{id}` does not reference table `user`"
        )));
    }

    let sql = "
        LET $u = type::record('user', $key);
        SELECT * FROM $u;
    ";
    let mut response = state
        .db
        .inner()
        .query(sql)
        .bind(("key", key))
        .await?
        .check()?;
    let users: Vec<Value> = response.take(1)?;
    let user = users
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::not_found(format!("user `{id}` not found")))?;

    let target_id = user
        .get("id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    let username = user
        .get("username")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    let status = user
        .get("status")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let roles = user
        .get("roles")
        .and_then(Value::as_array)
        .map(|array| {
            array
                .iter()
                .filter_map(Value::as_str)
                .map(str::to_owned)
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    let token_version = user
        .get("token_version")
        .and_then(Value::as_u64)
        .unwrap_or(0);

    if target_id == claims.sub {
        return Err(ApiError::bad_request("cannot impersonate yourself"));
    }
    if status != "active" {
        return Err(ApiError::bad_request(format!(
            "user `{username}` is not active"
        )));
    }

    let token = auth::issue(
        &state.config.jwt_secret,
        state.config.jwt_ttl_seconds,
        Claims {
            sub: target_id.clone(),
            username: username.clone(),
            roles,
            token_version,
            exp: 0,
            impersonator: Some(Impersonator {
                sub: claims.sub.clone(),
                username: claims.username.clone(),
            }),
        },
    )
    .map_err(|error| ApiError::internal(error.to_string()))?;

    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &claims.sub,
            action: "user_impersonated",
            object_id: Some(&target_id),
            from: None,
            to: None,
            metadata: Some(json!({ "username": username })),
        },
    )
    .await;

    Ok(Json(LoginResponse {
        token,
        token_type: "bearer".to_owned(),
    }))
}
