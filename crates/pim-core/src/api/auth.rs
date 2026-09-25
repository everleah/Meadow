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
use crate::rbac;

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

/// Self-service password change payload.
#[derive(Debug, Deserialize, ToSchema)]
pub struct ChangePasswordRequest {
    pub current_password: String,
    pub new_password: String,
}

/// Current-user response.
#[derive(Debug, Serialize, ToSchema)]
pub struct MeResponse {
    pub sub: String,
    pub username: String,
    pub roles: Vec<String>,
    /// The groups of this session (direct memberships and their parent groups).
    #[serde(default)]
    pub groups: Vec<String>,
    /// The effective permissions as keys — the UI hides menus and actions with them.
    ///
    /// Resolved once at login and carried by the token, so this answer costs no query and
    /// cannot drift from what the core enforces.
    #[serde(default)]
    pub permissions: Vec<String>,
    /// Whether the user still has to set their own password (the UI shows a
    /// hint/banner then). Absent `must_change_password` counts as `true`.
    pub must_change_password: bool,
    /// When the user last changed their own password (absent for seeded or
    /// admin-set passwords).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub password_changed_at: Option<String>,
    /// Present when the session impersonates another user (superadmin).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub impersonator: Option<Impersonator>,
}

/// Builds the claims of one session: the roles plus the **resolved** access.
///
/// The permission set and the group list are computed here — **once per login** — and
/// travel with the token, so the request path never repeats the resolution (the
/// performance rules of `plan/detail/benutzer-gruppen-rechte.md`). `impersonator` is set
/// for an impersonation session (the acting superadmin); the access is that of the
/// impersonated user.
async fn claims_for(
    state: &AppState,
    sub: String,
    username: String,
    roles: Vec<String>,
    token_version: u64,
    impersonator: Option<Impersonator>,
) -> Result<Claims, ApiError> {
    let access = rbac::resolve(&state.db, &sub, &roles).await?;
    Ok(Claims {
        sub,
        username,
        roles,
        groups: access.groups,
        perms: access.permissions.words().to_vec(),
        auth_version: state.cache.auth_version(),
        token_version,
        exp: 0,
        impersonator,
    })
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

    let claims = claims_for(
        &state,
        id,
        body.username.clone(),
        roles,
        token_version,
        None,
    )
    .await?;
    let token = auth::issue(
        &state.config.jwt_secret,
        state.config.jwt_ttl_seconds,
        claims,
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
pub async fn me(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
) -> Result<Json<MeResponse>, ApiError> {
    let key = user_key(&claims.sub)?;
    let user = fetch_user(&state, key)
        .await?
        .ok_or_else(|| ApiError::unauthorized("invalid session"))?;

    // Computed before the response moves the claim fields out of `claims`.
    let permissions: Vec<String> = claims
        .permissions()
        .keys()
        .into_iter()
        .map(str::to_owned)
        .collect();
    Ok(Json(MeResponse {
        sub: claims.sub,
        username: claims.username,
        roles: claims.roles,
        groups: claims.groups,
        permissions,
        must_change_password: must_change_password(&user),
        password_changed_at: user
            .get("password_changed_at")
            .and_then(Value::as_str)
            .map(str::to_owned),
        impersonator: claims.impersonator,
    }))
}

/// Whether the user still has to set their own password (`NONE` counts as yes).
pub(crate) fn must_change_password(user: &Value) -> bool {
    user.get("must_change_password")
        .and_then(Value::as_bool)
        .unwrap_or(true)
}

/// Splits the JWT `sub` claim (`user:<key>`) into the record key.
fn user_key(sub: &str) -> Result<&str, ApiError> {
    let (table, key) = sub
        .split_once(':')
        .ok_or_else(|| ApiError::unauthorized("invalid session subject"))?;
    if table != "user" || key.is_empty() {
        return Err(ApiError::unauthorized("invalid session subject"));
    }
    Ok(key)
}

/// Loads a user record (`None` when it does not exist).
async fn fetch_user(state: &AppState, key: &str) -> Result<Option<Value>, ApiError> {
    let sql = "
        LET $u = type::record('user', $key);
        SELECT * FROM $u;
    ";
    let mut response = state
        .db
        .inner()
        .query(sql)
        .bind(("key", key.to_owned()))
        .await?
        .check()?;
    let users: Vec<Value> = response.take(1)?;
    Ok(users.into_iter().next())
}

/// Changes the authenticated user's own password.
///
/// Requires the current password, validates the new one, stores a fresh argon2
/// hash, records `password_changed_at` and bumps `token_version`, and returns a
/// **new token** so the caller's session continues seamlessly.
#[utoipa::path(
    post,
    path = "/auth/password",
    request_body = ChangePasswordRequest,
    responses(
        (status = 200, description = "New JWT token", body = LoginResponse),
        (status = 400, description = "Invalid current or new password", body = Value),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Impersonated session", body = Value),
        (status = 404, description = "User not found", body = Value)
    )
)]
pub async fn change_password(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Json(body): Json<ChangePasswordRequest>,
) -> Result<Json<LoginResponse>, ApiError> {
    if claims.impersonator.is_some() {
        return Err(ApiError::forbidden(
            "cannot change the password of an impersonated session",
        ));
    }
    let key = user_key(&claims.sub)?;
    let user = fetch_user(&state, key)
        .await?
        .ok_or_else(|| ApiError::not_found("user not found"))?;

    let current_hash = user
        .get("password_hash")
        .and_then(Value::as_str)
        .unwrap_or_default();
    if !auth::verify_password(&body.current_password, current_hash) {
        return Err(ApiError::bad_request("current password is incorrect"));
    }
    auth::validate_new_password(&body.current_password, &body.new_password)
        .map_err(ApiError::bad_request)?;
    let new_hash = auth::hash_password(&body.new_password)
        .map_err(|error| ApiError::internal(error.to_string()))?;
    // A new password ends every session issued before it — the session of the caller
    // included, which is why the fresh token below is built **after** this bump (it must
    // carry the new version, otherwise it would be rejected by its own rule).
    rbac::invalidate_sessions(&state.db, &state.cache).await?;
    let sql = "
        LET $u = type::record('user', $key);
        UPDATE $u SET password_hash = $hash,
            password_changed_at = time::now(),
            must_change_password = false,
            token_version = token_version + 1,
            updated_at = time::now();
        SELECT * FROM $u;
    ";
    let mut response = state
        .db
        .inner()
        .query(sql)
        .bind(("key", key.to_owned()))
        .bind(("hash", new_hash))
        .await?
        .check()?;
    let users: Vec<Value> = response.take(1)?;
    let updated = users
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::internal("updated user was not returned"))?;

    let username = updated
        .get("username")
        .and_then(Value::as_str)
        .unwrap_or(&claims.username)
        .to_owned();
    let roles = updated
        .get("roles")
        .and_then(Value::as_array)
        .map(|array| {
            array
                .iter()
                .filter_map(Value::as_str)
                .map(str::to_owned)
                .collect::<Vec<_>>()
        })
        .unwrap_or_else(|| claims.roles.clone());
    let token_version = updated
        .get("token_version")
        .and_then(Value::as_u64)
        .unwrap_or(0);

    let token = auth::issue(
        &state.config.jwt_secret,
        state.config.jwt_ttl_seconds,
        claims_for(
            &state,
            claims.sub.clone(),
            username,
            roles,
            token_version,
            None,
        )
        .await?,
    )
    .map_err(|error| ApiError::internal(error.to_string()))?;

    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &claims.sub,
            action: "password_changed",
            object_id: Some(&claims.sub),
            from: None,
            to: None,
            metadata: None,
        },
    )
    .await;

    Ok(Json(LoginResponse {
        token,
        token_type: "bearer".to_owned(),
    }))
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
        claims_for(
            &state,
            target_id.clone(),
            username.clone(),
            roles,
            token_version,
            Some(Impersonator {
                sub: claims.sub.clone(),
                username: claims.username.clone(),
            }),
        )
        .await?,
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn password_change_rejects_short_and_identical_passwords() {
        assert!(auth::validate_new_password("secret-pass", "another-pass").is_ok());
        assert!(auth::validate_new_password("secret-pass", "short").is_err());
        assert!(auth::validate_new_password("secret-pass", "secret-pass").is_err());
        // Length is counted in characters, not bytes.
        assert!(auth::validate_new_password("secret-pass", "pässwört").is_ok());
        // No comparison when the current password is unknown (admin paths).
        assert!(auth::validate_new_password("", "short").is_err());
        assert!(auth::validate_new_password("", "long-enough").is_ok());
    }

    #[test]
    fn user_key_splits_the_subject() {
        assert_eq!(user_key("user:abc123").unwrap(), "abc123");
        assert!(user_key("object:abc123").is_err());
        assert!(user_key("user:").is_err());
        assert!(user_key("no-colon").is_err());
    }
}
