//! AI-assistance endpoints (`plan/detail/ki-assistenz.md`, stage 1).
//!
//! Two endpoints, both thin: they check the role, resolve the configuration
//! through the settings snapshot, forward the request to the worker over NATS
//! and write one audit entry. **Nothing is stored** — the suggestion goes back
//! to the editor, where the user reviews it and saves through the normal path.
//!
//! Fail-closed: switched off (`ai_enabled = false`), incomplete provider
//! configuration or an unreachable worker each answer `503` with a message a
//! human can act on. There is no fallback and no effect on any write path.

use axum::Json;
use axum::extract::State;
use serde_json::{Value, json};
use utoipa::ToSchema;

use crate::ai::AiError;
use crate::app::AppState;
use crate::audit::{self, AuditEntry};
use crate::auth::AuthUser;
use crate::error::ApiError;

/// Payload for `POST /ai/generate`.
///
/// `area` decides which system prompt and which context the worker builds; the
/// remaining fields are the values the editor already has on screen.
#[derive(Debug, serde::Deserialize, ToSchema)]
pub struct GenerateRequest {
    /// Target area: `rule` | `computed_attribute` | `search`.
    pub area: String,
    /// `create` (from a description), `edit` (improve an existing script) or
    /// `explain` (prose for the description field).
    pub mode: String,
    /// The user's description of what the rule should do.
    #[serde(default)]
    pub description: String,
    /// Rule type for `area = rule` (`validation`, `computed`, …).
    #[serde(default)]
    pub rule_type: Option<String>,
    /// Object type the rule applies to (`""` = all types).
    #[serde(default)]
    pub object_type: Option<String>,
    /// The current script: the baseline for `edit`, the subject for `explain`.
    #[serde(default)]
    pub current_script: Option<String>,
    /// The attribute the computed-attribute rule writes.
    #[serde(default)]
    pub target_attribute: Option<String>,
    /// Attributes of the type that are **declared as computed** (the only names
    /// `set_value` may write).
    #[serde(default)]
    pub declared_computed: Option<Vec<String>>,
    /// UI language of the user, so the prose comes back in the language the
    /// user reads (the code itself stays English).
    #[serde(default)]
    pub language: Option<String>,
}

/// Payload for `POST /ai/test` — no fields; the core resolves the configuration.
#[derive(Debug, serde::Deserialize, ToSchema)]
pub struct TestRequest {}

/// Maps an `AiError` to an HTTP error (fail-closed, never a silent success).
fn unavailable(error: &AiError) -> ApiError {
    match error {
        AiError::Disabled | AiError::NotConfigured(_) => ApiError::unavailable(error.to_string()),
        AiError::Transport(message) | AiError::BadReply(message) => {
            ApiError::unavailable(format!("AI service unavailable: {message}"))
        }
    }
}

/// Rejects unknown areas and modes before a model call is paid for.
fn validate(request: &GenerateRequest) -> Result<(), ApiError> {
    if !matches!(
        request.area.as_str(),
        "rule" | "computed_attribute" | "search"
    ) {
        return Err(ApiError::bad_request(format!(
            "unknown area `{}` (expected rule, computed_attribute or search)",
            request.area
        )));
    }
    if !matches!(request.mode.as_str(), "create" | "edit" | "explain") {
        return Err(ApiError::bad_request(format!(
            "unknown mode `{}` (expected create, edit or explain)",
            request.mode
        )));
    }
    if request.mode != "explain" && request.description.trim().is_empty() {
        return Err(ApiError::bad_request(
            "`description` is required unless the mode is `explain`",
        ));
    }
    if request.mode == "explain"
        && request
            .current_script
            .as_deref()
            .unwrap_or("")
            .trim()
            .is_empty()
    {
        return Err(ApiError::bad_request(
            "`current_script` is required for the mode `explain`",
        ));
    }
    Ok(())
}

/// Builds a suggestion (rule script or computed-attribute rule) — admin only.
#[utoipa::path(
    post,
    path = "/ai/generate",
    request_body = GenerateRequest,
    responses(
        (status = 200, description = "Suggestion plus its policy check", body = Value),
        (status = 400, description = "Invalid request", body = Value),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Forbidden", body = Value),
        (status = 503, description = "Assistance disabled or worker unavailable", body = Value)
    )
)]
pub async fn generate(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Json(body): Json<GenerateRequest>,
) -> Result<Json<Value>, ApiError> {
    if !claims.is_admin() {
        return Err(ApiError::forbidden("requires admin role"));
    }
    validate(&body)?;

    let request = json!({
        "area": body.area,
        "mode": body.mode,
        "description": body.description,
        "rule_type": body.rule_type,
        "object_type": body.object_type,
        "current_script": body.current_script,
        "target_attribute": body.target_attribute,
        "declared_computed": body.declared_computed,
        "language": body.language,
    });
    let reply = state.ai.generate(request).await.map_err(|error| {
        tracing::warn!(error = %error, "AI generation failed");
        unavailable(&error)
    })?;

    // Audit metadata only: never the prompt, never the script, never object
    // values — just enough to see where a suggestion came from
    // (`plan/detail/ki-assistenz.md` section 6.7).
    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &claims.sub,
            action: "ai_generated",
            object_id: None,
            from: None,
            to: None,
            metadata: Some(json!({
                "area": body.area,
                "mode": body.mode,
                "rule_type": body.rule_type,
                "object_type": body.object_type,
                "ok": reply.get("ok").and_then(Value::as_bool).unwrap_or(false),
                "model": reply.get("model"),
                "attempts": reply.get("attempts"),
                "latency_ms": reply.get("latency_ms"),
            })),
        },
    )
    .await;

    Ok(Json(reply))
}

/// Minimal provider round trip for the settings page — admin only.
#[utoipa::path(
    post,
    path = "/ai/test",
    request_body = TestRequest,
    responses(
        (status = 200, description = "Connection test result", body = Value),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Forbidden", body = Value),
        (status = 503, description = "Assistance disabled or worker unavailable", body = Value)
    )
)]
pub async fn test(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Json(_body): Json<TestRequest>,
) -> Result<Json<Value>, ApiError> {
    if !claims.is_admin() {
        return Err(ApiError::forbidden("requires admin role"));
    }
    let reply = state.ai.test().await.map_err(|error| {
        tracing::warn!(error = %error, "AI connection test failed");
        unavailable(&error)
    })?;
    Ok(Json(reply))
}
