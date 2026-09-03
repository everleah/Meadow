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

/// JWT claims (plan: `sub`, `username`, `roles[]`, `token_version`, `exp`).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Claims {
    pub sub: String,
    pub username: String,
    pub roles: Vec<String>,
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

/// Extracted authenticated user (from the `Authorization: Bearer` header).
pub struct AuthUser(pub Claims);

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
    if let Some(expected) = &config.log_ingest_api_key {
        let provided = headers
            .get("x-api-key")
            .and_then(|value| value.to_str().ok())
            .unwrap_or_default();
        if provided == expected {
            return Ok("system".to_owned());
        }
    }
    let token = headers
        .get(header::AUTHORIZATION)
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.strip_prefix("Bearer "))
        .ok_or_else(|| ApiError::unauthorized("missing bearer token"))?;
    let claims = verify(&config.jwt_secret, token)
        .map_err(|_| ApiError::unauthorized("invalid or expired token"))?;
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
        assert_eq!(decoded.token_version, claims.token_version);
        assert!(decoded.exp > 0);
        assert!(decoded.has_role(ROLE_SUPERADMIN));
        assert!(decoded.is_admin());
    }

    #[test]
    fn jwt_rejects_wrong_secret() {
        let token = issue("secret-a", 3600, sample_claims()).expect("issue should succeed");
        assert!(verify("secret-b", &token).is_err());
    }
}
