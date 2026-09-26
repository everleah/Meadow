//! JWT issuing/verification, password hashing, and the authenticated-user extractor.

use argon2::{
    Argon2,
    password_hash::{PasswordHash, PasswordHasher, PasswordVerifier, SaltString, rand_core::OsRng},
};
use axum::extract::FromRequestParts;
use axum::http::{HeaderMap, header, request::Parts};
use jsonwebtoken::{Algorithm, DecodingKey, EncodingKey, Header, Validation, decode, encode};
use serde::{Deserialize, Serialize};
use utoipa::ToSchema;

use crate::app::AppState;
use crate::config::Config;
use crate::error::ApiError;

/// Roles defined by the plan (section 9).
pub const ROLE_SUPERADMIN: &str = "superadmin";
pub const ROLE_ADMIN: &str = "admin";
pub const ROLE_EDITOR: &str = "editor";
pub const ROLE_VIEWER: &str = "viewer";

/// The acting user in an impersonation session (plan: `impersonator` claim).
#[derive(Debug, Clone, Serialize, Deserialize, ToSchema)]
pub struct Impersonator {
    pub sub: String,
    pub username: String,
}

/// JWT claims (plan: `sub`, `username`, `roles[]`, `groups[]`, `perms[]`,
/// `auth_version`, `token_version`, `exp`).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Claims {
    pub sub: String,
    pub username: String,
    pub roles: Vec<String>,
    /// Group record ids the user belongs to (direct memberships and their parents).
    #[serde(default)]
    pub groups: Vec<String>,
    /// The effective permissions as bit words ([`crate::permissions::PermissionSet`]).
    ///
    /// They are resolved **once** at login (`crate::rbac::resolve`) and travel with the
    /// token, so a permission check costs a bit test instead of a query — the request
    /// path must not pay for authorization (see the performance rules in
    /// `plan/detail/benutzer-gruppen-rechte.md`).
    #[serde(default)]
    pub perms: Vec<u64>,
    /// The access version the token was issued with.
    ///
    /// A token whose version differs from [`crate::cache::MetaCache::auth_version`] is
    /// rejected: every change to permissions, groups, memberships or passwords raises the
    /// stored counter, so a revoked right takes effect at once instead of after the
    /// token's TTL. **`0` is never a valid version** — a token without this claim is
    /// answered with `401` and the session logs in again.
    #[serde(default)]
    pub auth_version: u64,
    pub token_version: u64,
    pub exp: usize,
    /// Set when a superadmin impersonates another user (see plan section 9).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub impersonator: Option<Impersonator>,
}

impl Claims {
    /// Whether the token carries the given role.
    pub fn has_role(&self, role: &str) -> bool {
        self.roles.iter().any(|r| r == role)
    }

    /// Whether the token has `admin` or `superadmin` privileges.
    pub fn is_admin(&self) -> bool {
        self.has_role(ROLE_ADMIN) || self.has_role(ROLE_SUPERADMIN)
    }

    /// Whether the token has `editor` privileges or above.
    pub fn is_editor(&self) -> bool {
        self.has_role(ROLE_EDITOR) || self.is_admin()
    }

    /// The effective permissions of this token (a bit test, no I/O).
    pub fn permissions(&self) -> crate::permissions::PermissionSet {
        crate::permissions::PermissionSet::from_words(&self.perms)
    }

    /// Whether the token carries this permission.
    ///
    /// Stage B moves the call sites onto this check; the role checks above stay until
    /// then, which keeps the behaviour of users without groups identical.
    pub fn can(&self, permission: &str) -> bool {
        self.permissions().can(permission)
    }

    /// Rejects the request unless the token carries this permission.
    ///
    /// The message names the missing permission instead of "insufficient role": the matrix in
    /// the admin UI and this answer then speak about the same thing, and a support question
    /// ("why can't I?") is answered by the group editor rather than by reading the role list.
    pub fn require(&self, permission: &str) -> Result<(), ApiError> {
        if self.can(permission) {
            return Ok(());
        }
        Err(ApiError::forbidden(format!(
            "missing permission `{permission}`"
        )))
    }
}

/// Hashes a password with argon2id.
pub fn hash_password(password: &str) -> Result<String, argon2::password_hash::Error> {
    let salt = SaltString::generate(OsRng);
    Argon2::default()
        .hash_password(password.as_bytes(), &salt)
        .map(|hash| hash.to_string())
}

/// Verifies a password against an argon2 hash.
pub fn verify_password(password: &str, hash: &str) -> bool {
    PasswordHash::new(hash)
        .map(|parsed| {
            Argon2::default()
                .verify_password(password.as_bytes(), &parsed)
                .is_ok()
        })
        .unwrap_or(false)
}

/// Minimum length of a new password (characters).
pub const MIN_PASSWORD_LENGTH: usize = 8;

/// Validates a new password: minimum length and difference from `current`.
///
/// Used by the self-service change (`POST /auth/password`) and by the admin
/// paths (`POST /users`, `PATCH /users/{id}` with a new password).
pub fn validate_new_password(current: &str, new: &str) -> Result<(), String> {
    if new.chars().count() < MIN_PASSWORD_LENGTH {
        return Err(format!(
            "password must be at least {MIN_PASSWORD_LENGTH} characters long"
        ));
    }
    if !current.is_empty() && new == current {
        return Err("new password must differ from the current one".to_owned());
    }
    Ok(())
}

/// Issues a signed HS256 token.
pub fn issue(
    secret: &str,
    ttl_seconds: u64,
    claims: Claims,
) -> Result<String, jsonwebtoken::errors::Error> {
    let exp = jsonwebtoken::get_current_timestamp() as usize + ttl_seconds as usize;
    let mut claims = claims;
    claims.exp = exp;
    encode(
        &Header::default(),
        &claims,
        &EncodingKey::from_secret(secret.as_bytes()),
    )
}

/// Verifies a token and returns its claims.
pub fn verify(secret: &str, token: &str) -> Result<Claims, jsonwebtoken::errors::Error> {
    let data = decode::<Claims>(
        token,
        &DecodingKey::from_secret(secret.as_bytes()),
        &Validation::new(Algorithm::HS256),
    )?;
    Ok(data.claims)
}

/// Who is asking: the machine-to-machine caller (the worker's API key) or a user session.
///
/// The distinction matters where a decision depends on the **person** rather than on the
/// role — the ABAC scope is the first such case (`crate::scope`): the worker writes and reads
/// the whole catalogue by design, a user session is restricted by the groups in its token.
pub enum Caller {
    /// The worker (or another machine) with the configured API key: no claims, no scope.
    System,
    /// A verified user session.
    User(Box<Claims>),
}

impl Caller {
    /// The claims of a user session (`None` for the machine caller).
    pub fn claims(&self) -> Option<&Claims> {
        match self {
            Caller::System => None,
            Caller::User(claims) => Some(claims),
        }
    }

    /// The acting user id, for audit entries (`system` for the machine caller).
    pub fn user_id(&self) -> &str {
        match self {
            Caller::System => "system",
            Caller::User(claims) => &claims.sub,
        }
    }
}

/// Reads the caller of a request from its headers.
///
/// The API key wins over a bearer token: a machine call is never a person's session. This is
/// the one place that turns headers into an identity, so a new endpoint cannot invent its own
/// rule about which header counts (`api_key_or_role` and the `AuthUser` extractor both build
/// on it).
pub fn caller(headers: &HeaderMap, config: &Config) -> Result<Caller, ApiError> {
    if let Some(expected) = &config.log_ingest_api_key {
        let provided = headers
            .get("x-api-key")
            .and_then(|value| value.to_str().ok())
            .unwrap_or_default();
        if provided == expected {
            return Ok(Caller::System);
        }
    }
    let token = headers
        .get(header::AUTHORIZATION)
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.strip_prefix("Bearer "))
        .ok_or_else(|| ApiError::unauthorized("missing bearer token"))?;
    let claims = verify(&config.jwt_secret, token)
        .map_err(|_| ApiError::unauthorized("invalid or expired token"))?;
    Ok(Caller::User(Box::new(claims)))
}

/// Extracted authenticated user (from the `Authorization: Bearer` header).
pub struct AuthUser(pub Claims);

/// Rejects a session whose access version is behind the stored one.
///
/// Every change to the access model (permissions, groups, memberships, passwords) raises the
/// counter, so a revoked right cannot survive in an already issued token. `0` never matches
/// (see [`Claims::auth_version`]). The message is deliberately the one the frontend already
/// maps to "session expired → log in again" (`meadow/frontend/api.py`), because that is
/// exactly the action the caller has to take.
pub fn check_version(claims: &Claims, cache: &crate::cache::MetaCache) -> Result<(), ApiError> {
    if claims.auth_version != cache.auth_version() {
        return Err(ApiError::unauthorized("invalid or expired token"));
    }
    Ok(())
}

/// Reads the caller of an authenticated request and rejects a stale session.
///
/// The one-liner for handlers that need to know *who* asks: the machine API key
/// ([`Caller::System`]) or a user session whose access version is still current. Handlers that
/// only need a role keep using [`api_key_or_role`]; handlers that need the identity itself — the
/// ABAC scope is the first such case (`crate::scope`) — use this.
pub fn authenticated(state: &AppState, headers: &HeaderMap) -> Result<Caller, ApiError> {
    let caller = caller(headers, &state.config)?;
    if let Some(claims) = caller.claims() {
        check_version(claims, &state.cache)?;
    }
    Ok(caller)
}

/// Authorizes a request as a machine call (the configured API key, which holds every right) or
/// as a user session carrying `permission`, and answers the acting user id (`system` for the
/// machine).
///
/// This is the replacement for the role-based `api_key_or_role` (stage B): the requirement comes
/// from the **catalog** (`crate::permissions`), so an endpoint cannot invent its own rule, and a
/// session whose access version has moved on is rejected here as well — the API-key/bearer gap
/// the stage-A docs listed. The API key needs no version check: a machine has no session.
pub fn require_permission(
    state: &AppState,
    headers: &HeaderMap,
    permission: &str,
) -> Result<String, ApiError> {
    Ok(require_caller(state, headers, permission)?
        .user_id()
        .to_owned())
}

/// The same check, answering **who** asks.
///
/// Handlers that need the identity itself — the ABAC scope of a view (`crate::scope`) — use this
/// instead of [`require_permission`]; both go through one rule, so an endpoint cannot end up with
/// a weaker check than its neighbour.
pub fn require_caller(
    state: &AppState,
    headers: &HeaderMap,
    permission: &str,
) -> Result<Caller, ApiError> {
    let caller = caller(headers, &state.config)?;
    if let Caller::User(claims) = &caller {
        check_version(claims, &state.cache)?;
        claims.require(permission)?;
    }
    Ok(caller)
}

/// Authorizes a request as either a machine-to-machine caller (matching the
/// `X-Api-Key`) or a JWT bearer carrying at least `required_role`.
///
/// Returns the acting user id: `system` for the API-key path (workers such as
/// the MEF import connector write without a person login) or the JWT `sub`.
/// This is used by worker-facing write endpoints (`POST /objects`,
/// `PATCH /objects/{id}`, `POST /references`) so the import connector can
/// persist objects through the core with the same privileges as an editor.
pub fn api_key_or_role(
    headers: &HeaderMap,
    config: &Config,
    required_role: &str,
) -> Result<String, ApiError> {
    let claims = match caller(headers, config)? {
        Caller::System => return Ok("system".to_owned()),
        Caller::User(claims) => claims,
    };
    let allowed = match required_role {
        // `viewer` is the base role: any authenticated token qualifies.
        ROLE_VIEWER => true,
        ROLE_EDITOR => claims.is_editor(),
        ROLE_ADMIN | ROLE_SUPERADMIN => claims.is_admin(),
        other => claims.has_role(other),
    };
    if !allowed {
        return Err(ApiError::forbidden("insufficient role"));
    }
    Ok(claims.sub)
}

impl FromRequestParts<AppState> for AuthUser {
    type Rejection = ApiError;

    async fn from_request_parts(
        parts: &mut Parts,
        state: &AppState,
    ) -> Result<Self, Self::Rejection> {
        let token = parts
            .headers
            .get(header::AUTHORIZATION)
            .and_then(|value| value.to_str().ok())
            .and_then(|value| value.strip_prefix("Bearer "))
            .ok_or_else(|| ApiError::unauthorized("missing bearer token"))?;

        let claims = verify(&state.config.jwt_secret, token)
            .map_err(|_| ApiError::unauthorized("invalid or expired token"))?;
        check_version(&claims, &state.cache)?;

        Ok(AuthUser(claims))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample_claims() -> Claims {
        Claims {
            sub: "user:admin".to_owned(),
            username: "admin".to_owned(),
            roles: vec![ROLE_SUPERADMIN.to_owned()],
            groups: vec!["group:webshop".to_owned()],
            perms: crate::permissions::role_defaults(crate::permissions::Role::Superadmin)
                .words()
                .to_vec(),
            auth_version: 1,
            token_version: 1,
            exp: 0,
            impersonator: None,
        }
    }

    #[test]
    fn password_hash_and_verify() {
        let hash = hash_password("secret").expect("hash should succeed");
        assert!(verify_password("secret", &hash));
        assert!(!verify_password("wrong", &hash));
    }

    #[test]
    fn jwt_roundtrip() {
        let claims = sample_claims();
        let token = issue("test-secret", 3600, claims.clone()).expect("issue should succeed");
        let decoded = verify("test-secret", &token).expect("verify should succeed");
        assert_eq!(decoded.sub, claims.sub);
        assert_eq!(decoded.username, claims.username);
        assert_eq!(decoded.roles, claims.roles);
        assert_eq!(decoded.groups, claims.groups);
        assert_eq!(decoded.auth_version, claims.auth_version);
        assert_eq!(decoded.token_version, claims.token_version);
        assert!(decoded.exp > 0);
        assert!(decoded.has_role(ROLE_SUPERADMIN));
        assert!(decoded.is_admin());
        // The permissions travel with the token and are read back as a bit set.
        assert!(decoded.can("system.manage"));
        assert!(decoded.can("object.read"));
    }

    #[test]
    fn jwt_rejects_wrong_secret() {
        let token = issue("secret-a", 3600, sample_claims()).expect("issue should succeed");
        assert!(verify("secret-b", &token).is_err());
    }
}
