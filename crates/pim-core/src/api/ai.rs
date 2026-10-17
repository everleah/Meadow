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
use axum::extract::{Query, State};
use serde::Deserialize;
use serde_json::{Value, json};
use utoipa::{IntoParams, ToSchema};

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
    /// Target area: `rule` | `computed_attribute` | `search` | `connector` |
    /// `export_profile` | `job` | `import_mapping` | `group_permissions`.
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
    /// Concrete target of a JSON-configuration area: the **connector type** for
    /// `area = connector` (`webhook`, `json_import`, `json_export`) and the
    /// **job type** for `area = job` (`inbound_import`, `bulk_operation`, …).
    #[serde(default)]
    pub config_kind: Option<String>,
    /// The columns of the uploaded file for `area = import_mapping` — name plus a
    /// **shape** hint (`text, 6 characters, all values different`), never a value.
    #[serde(default)]
    pub columns: Option<Vec<ColumnSpec>>,
    /// The workflow states the scope card offers for `area = group_permissions` — the
    /// editor's own option list (`display.OBJECT_STATUSES`). The suggestion may only name
    /// a state the card can show, and a scope with an unknown status matches nothing
    /// silently, so the list travels with the request like `declared_computed` does.
    #[serde(default)]
    pub statuses: Option<Vec<String>>,
    /// What the suggestion is for — the stable identity the editor is writing: a
    /// record id while editing, `new` for an unsaved artefact, or the attribute
    /// name for a computed attribute.
    ///
    /// It is the **history key** of the audit entry (`GET /ai/history`) and never
    /// reaches the model: the prompt is built from the fields above, and the value
    /// is an identifier, not object data.
    #[serde(default)]
    pub target: Option<String>,
}

/// One column of an uploaded file: the header and what its values look like.
#[derive(Debug, serde::Deserialize, serde::Serialize, ToSchema)]
pub struct ColumnSpec {
    /// The header as it appears in the file (the key of the mapping).
    pub name: String,
    /// A value-free description of the cells (`number, 4 characters`).
    #[serde(default)]
    pub hint: String,
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
        "rule"
            | "computed_attribute"
            | "search"
            | "connector"
            | "export_profile"
            | "job"
            | "import_mapping"
            | "group_permissions"
    ) {
        return Err(ApiError::bad_request(format!(
            "unknown area `{}` (expected rule, computed_attribute, search, connector, \
             export_profile, job, import_mapping or group_permissions)",
            request.area
        )));
    }
    if matches!(request.area.as_str(), "connector" | "job")
        && request.config_kind.as_deref().unwrap_or("").is_empty()
    {
        let what = if request.area == "connector" {
            "the connector type"
        } else {
            "the job type"
        };
        return Err(ApiError::bad_request(format!(
            "`config_kind` ({what}) is required for the area `{}`",
            request.area
        )));
    }
    // A job type the queue would reject cannot be suggested: the assistant builds
    // the parameters for it, and `POST /jobs` would refuse the result.
    if request.area == "job"
        && let Some(job_type) = request.config_kind.as_deref()
        && !crate::api::jobs::is_known_job_type(job_type)
    {
        return Err(ApiError::bad_request(format!(
            "unknown job type `{job_type}` (expected {})",
            crate::api::jobs::VALID_JOB_TYPES.join(", ")
        )));
    }
    // The mapping area is about the columns of *this* file: without them the worker
    // could only guess, and the request would cost a model call before failing.
    if request.area == "import_mapping"
        && request
            .columns
            .as_ref()
            .is_none_or(|columns| columns.is_empty())
    {
        return Err(ApiError::bad_request(
            "`columns` (the column headers of the file) is required for the area `import_mapping`",
        ));
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
    claims.require("ai.use")?;
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
        "config_kind": body.config_kind,
        "columns": body.columns,
        "statuses": body.statuses,
    });
    let reply = state.ai.generate(request).await.map_err(|error| {
        tracing::warn!(error = %error, "AI generation failed");
        unavailable(&error)
    })?;

    // Audit metadata only: never the prompt, never the script, never object
    // values — just enough to see where a suggestion came from and to list the
    // suggestions of one target (`plan/detail/ki-assistenz.md` section 6.7).
    let tokens = reply
        .get("usage")
        .and_then(|usage| usage.get("total_tokens"))
        .and_then(Value::as_i64);
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
                "target": body.target,
                "rule_type": body.rule_type,
                "object_type": body.object_type,
                "ok": reply.get("ok").and_then(Value::as_bool).unwrap_or(false),
                "model": reply.get("model"),
                "attempts": reply.get("attempts"),
                "latency_ms": reply.get("latency_ms"),
                "tokens": tokens,
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
    claims.require("ai.use")?;
    let reply = state.ai.test().await.map_err(|error| {
        tracing::warn!(error = %error, "AI connection test failed");
        unavailable(&error)
    })?;
    Ok(Json(reply))
}

/// Default and maximum number of history entries `GET /ai/history` returns.
const HISTORY_DEFAULT: u32 = 20;
const HISTORY_MAX: u32 = 100;

/// Query parameters for `GET /ai/history`.
#[derive(Debug, Deserialize, ToSchema, IntoParams)]
#[into_params(parameter_in = Query)]
pub struct HistoryParams {
    /// Area of the suggestions (`rule`, `computed_attribute`, `search`, …).
    pub area: String,
    /// What the suggestions were asked for (`new`, a record id, an attribute name).
    pub target: String,
    /// Maximum number of entries (default 20, capped at 100).
    #[serde(default)]
    pub limit: Option<u32>,
}

/// The recorded suggestions for one target — **metadata only**.
///
/// Reads the `ai_generated` audit entries the write path already writes: area,
/// mode, target, model, attempts, latency, token count and whether the suggestion
/// passed the checks. No prompt, no script, no object values — the same rule the
/// audit entry follows (`plan/detail/ki-assistenz.md` §6.7), so a history is safe
/// to show to anyone who may ask for suggestions at all (`ai.use`).
#[utoipa::path(
    get,
    path = "/ai/history",
    params(HistoryParams),
    responses(
        (status = 200, description = "Recorded suggestions, newest first", body = Value),
        (status = 400, description = "Invalid payload", body = Value),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Forbidden", body = Value)
    )
)]
pub async fn history(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Query(params): Query<HistoryParams>,
) -> Result<Json<Value>, ApiError> {
    claims.require("ai.use")?;
    let (area, target) = history_key(&params.area, &params.target)?;
    let limit = params.limit.unwrap_or(HISTORY_DEFAULT).min(HISTORY_MAX);
    let mut response = state
        .db
        .inner()
        .query(
            "SELECT timestamp, user_id, metadata FROM audit_log \
             WHERE action = 'ai_generated' AND metadata.area = $area \
             AND metadata.target = $target ORDER BY timestamp DESC LIMIT $limit;",
        )
        .bind(("area", area))
        .bind(("target", target))
        .bind(("limit", limit))
        .await?
        .check()?;
    let entries: Vec<Value> = response.take(0)?;
    Ok(Json(json!({ "entries": entries })))
}

/// The trimmed `(area, target)` of a history request; both are required.
fn history_key(area: &str, target: &str) -> Result<(String, String), ApiError> {
    let area = area.trim();
    let target = target.trim();
    if area.is_empty() || target.is_empty() {
        return Err(ApiError::bad_request(
            "`area` and `target` are required (the history is per target)",
        ));
    }
    Ok((area.to_owned(), target.to_owned()))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn request(area: &str, config_kind: Option<&str>) -> GenerateRequest {
        GenerateRequest {
            area: area.to_owned(),
            mode: "create".to_owned(),
            description: "something to build".to_owned(),
            rule_type: None,
            object_type: None,
            current_script: None,
            target_attribute: None,
            declared_computed: None,
            language: None,
            config_kind: config_kind.map(str::to_owned),
            columns: None,
            statuses: None,
            target: None,
        }
    }

    #[test]
    fn a_history_request_needs_area_and_target() {
        assert_eq!(
            history_key(" rule ", " business_rule:sku ").expect("trimmed"),
            ("rule".to_owned(), "business_rule:sku".to_owned())
        );
        assert!(history_key("", "new").is_err());
        assert!(history_key("rule", "   ").is_err());
    }

    fn message(error: ApiError) -> String {
        error.message().to_owned()
    }

    #[test]
    fn a_json_area_needs_its_concrete_target() {
        // Without the type the worker cannot build the schema projection, and the
        // request would cost a model call before failing.
        assert!(
            message(validate(&request("connector", None)).unwrap_err()).contains("connector type")
        );
        assert!(message(validate(&request("job", None)).unwrap_err()).contains("job type"));
        assert!(validate(&request("connector", Some("webhook"))).is_ok());
        assert!(validate(&request("job", Some("bulk_operation"))).is_ok());
        // The other areas carry their target in `object_type`.
        assert!(validate(&request("rule", None)).is_ok());
        assert!(validate(&request("search", None)).is_ok());
        assert!(validate(&request("group_permissions", None)).is_ok());
    }

    #[test]
    fn a_job_area_rejects_a_type_the_queue_would_reject() {
        let error = message(validate(&request("job", Some("send_emails"))).unwrap_err());
        assert!(error.contains("unknown job type `send_emails`"));
        // …and the message names the types that work, so a caller can correct it.
        assert!(error.contains("inbound_import"));
        // A connector type is checked by the connector schema, not here.
        assert!(validate(&request("connector", Some("whatever"))).is_ok());
    }

    #[test]
    fn the_unknown_area_message_names_every_area() {
        let error = message(validate(&request("calendar", None)).unwrap_err());
        for area in [
            "rule",
            "computed_attribute",
            "search",
            "connector",
            "export_profile",
            "job",
            "import_mapping",
            "group_permissions",
        ] {
            assert!(error.contains(area), "`{area}` missing in: {error}");
        }
    }

    #[test]
    fn the_mapping_area_needs_the_columns_of_the_file() {
        let mut mapping = request("import_mapping", None);
        let error = message(validate(&mapping).unwrap_err());
        assert!(error.contains("`columns`"));
        // An empty list is as useless as no list at all.
        mapping.columns = Some(Vec::new());
        assert!(validate(&mapping).is_err());
        mapping.columns = Some(vec![ColumnSpec {
            name: "Kunden-Nr".to_owned(),
            hint: "text, 6 characters".to_owned(),
        }]);
        assert!(validate(&mapping).is_ok());
        // The other areas do not need columns.
        assert!(validate(&request("rule", None)).is_ok());
    }

    #[test]
    fn the_group_area_works_without_extra_input() {
        // The area describes a group document: the permission catalog and the scope schema
        // come from the core, the workflow states from the editor. A description is enough
        // (the editor sends the current matrix as `current_script` in the `edit` mode).
        assert!(validate(&request("group_permissions", None)).is_ok());

        let mut edit = request("group_permissions", None);
        edit.mode = "edit".to_owned();
        assert!(validate(&edit).is_ok());
    }
}
