//! Synchronous business-rule evaluation via NATS request/reply (Phase 4).
//!
//! The core stays the database authority: it loads the applicable `active`
//! `business_rule` records, builds the resolved candidate view, sends a
//! request to the Python worker over NATS (`rules.evaluate`), and enforces the
//! outcome in the write path (fail-closed). See
//! `plan/detail/business-rules.md`.

use std::time::Duration;

use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

use crate::config::Config;
use crate::db::Db;
use crate::error::ApiError;

/// A single validation error or warning from a rule.
#[derive(Debug, Clone, Deserialize)]
pub struct RuleIssue {
    pub attr: String,
    pub code: String,
    pub message: String,
}

/// The outcome returned by the Python worker.
#[derive(Debug, Clone, Default, Deserialize)]
pub struct RuleOutcome {
    #[serde(default)]
    pub errors: Vec<RuleIssue>,
    #[serde(default)]
    pub warnings: Vec<RuleIssue>,
    #[serde(default)]
    pub computed: Value,
    #[serde(default)]
    pub allowed: Option<bool>,
    #[serde(default)]
    pub deny_reason: Option<String>,
    #[serde(default)]
    pub failed: Vec<String>,
    #[serde(default)]
    pub logs: Vec<Value>,
}

impl RuleOutcome {
    /// Whether any blocking rule failed (timeout, exception, compile error).
    pub fn has_failures(&self) -> bool {
        !self.failed.is_empty()
    }

    /// Returns the computed values as a JSON object (empty when none).
    pub fn computed_object(&self) -> serde_json::Map<String, Value> {
        self.computed.as_object().cloned().unwrap_or_default()
    }
}

/// A rule to evaluate: its script plus metadata, sent to the Python worker.
#[derive(Debug, Serialize)]
pub struct RuleSpec {
    pub name: String,
    #[serde(rename = "type")]
    pub rule_type: String,
    pub script: String,
}

/// Failure modes of a rule-evaluation request.
#[derive(Debug)]
pub enum EvaluateError {
    /// NATS connection or timeout failure (fail-closed, or degrade mode).
    Transport(String),
    /// The worker replied with an unparseable payload.
    BadReply(String),
}

impl std::fmt::Display for EvaluateError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            EvaluateError::Transport(message) => write!(f, "{message}"),
            EvaluateError::BadReply(message) => write!(f, "invalid reply: {message}"),
        }
    }
}

/// Stateless NATS client for the synchronous rule-evaluation call.
#[derive(Debug, Clone)]
pub struct RulesClient {
    nats_url: String,
    subject: String,
    compile_subject: String,
    timeout: Duration,
    degrade: bool,
}

impl RulesClient {
    /// Builds the client from runtime configuration.
    pub fn from_config(config: &Config) -> Self {
        Self {
            nats_url: config.rules_nats_url.clone(),
            subject: config.rules_subject.clone(),
            compile_subject: config.rules_compile_subject.clone(),
            timeout: Duration::from_millis(config.rules_timeout_ms),
            degrade: config.rules_degrade,
        }
    }

    /// Whether a transport failure should degrade to "skip rules".
    pub fn degrade(&self) -> bool {
        self.degrade
    }

    /// Evaluates the given rules against the candidate object view.
    pub async fn evaluate(
        &self,
        rules: &[RuleSpec],
        object: &Value,
        mode: &str,
        language: Option<&str>,
        actor: Option<&str>,
    ) -> Result<RuleOutcome, EvaluateError> {
        let payload = json!({
            "rules": rules,
            "object": object,
            "mode": mode,
            "language": language,
            "actor": actor,
        });
        let bytes = serde_json::to_vec(&payload)
            .map_err(|error| EvaluateError::BadReply(error.to_string()))?;

        let client = async_nats::connect(&self.nats_url)
            .await
            .map_err(|error| EvaluateError::Transport(error.to_string()))?;
        let request = async_nats::Request::new()
            .payload(bytes.into())
            .timeout(Some(self.timeout));
        let reply = client
            .send_request(self.subject.clone(), request)
            .await
            .map_err(|error| EvaluateError::Transport(error.to_string()))?;

        serde_json::from_slice(&reply.payload)
            .map_err(|error| EvaluateError::BadReply(error.to_string()))
    }

    /// Validates a rule script with the Python worker.
    ///
    /// Returns the list of compile errors (empty = valid).
    pub async fn compile_script(&self, script: &str) -> Result<Vec<String>, EvaluateError> {
        let payload = json!({ "script": script });
        let bytes = serde_json::to_vec(&payload)
            .map_err(|error| EvaluateError::BadReply(error.to_string()))?;

        let client = async_nats::connect(&self.nats_url)
            .await
            .map_err(|error| EvaluateError::Transport(error.to_string()))?;
        let request = async_nats::Request::new()
            .payload(bytes.into())
            .timeout(Some(self.timeout));
        let reply = client
            .send_request(self.compile_subject.clone(), request)
            .await
            .map_err(|error| EvaluateError::Transport(error.to_string()))?;

        let value: Value = serde_json::from_slice(&reply.payload)
            .map_err(|error| EvaluateError::BadReply(error.to_string()))?;
        let ok = value
            .get("ok")
            .and_then(Value::as_bool)
            .ok_or_else(|| EvaluateError::BadReply("compile reply missing `ok`".to_owned()))?;
        if ok {
            Ok(Vec::new())
        } else {
            let errors = value
                .get("errors")
                .and_then(Value::as_array)
                .map(|items| {
                    items
                        .iter()
                        .filter_map(Value::as_str)
                        .map(str::to_owned)
                        .collect()
                })
                .unwrap_or_else(|| vec!["unknown compile error".to_owned()]);
            Ok(errors)
        }
    }
}

/// Loads the `active` rules of the given types that apply to `object_type`.
pub async fn load_active_rules(
    db: &Db,
    rule_types: &[&str],
    object_type: &str,
) -> Result<Vec<RuleSpec>, ApiError> {
    let sql = "SELECT * FROM business_rule WHERE state = 'active'";
    let mut response = db.inner().query(sql).await?.check()?;
    let rows: Vec<Value> = response.take(0)?;

    let mut specs = Vec::new();
    for row in rows {
        let Some(rule_type) = row.get("type").and_then(Value::as_str) else {
            continue;
        };
        if !rule_types.contains(&rule_type) {
            continue;
        }
        let applies = row
            .get("applies_to")
            .and_then(Value::as_array)
            .map(|types| types.is_empty() || types.iter().any(|t| t.as_str() == Some(object_type)))
            .unwrap_or(true);
        if !applies {
            continue;
        }
        let Some(name) = row.get("name").and_then(Value::as_str) else {
            continue;
        };
        let Some(script) = row.get("script").and_then(Value::as_str) else {
            continue;
        };
        specs.push(RuleSpec {
            name: name.to_owned(),
            rule_type: rule_type.to_owned(),
            script: script.to_owned(),
        });
    }
    Ok(specs)
}

/// Loads and evaluates the applicable active rules for a write operation.
///
/// Returns an empty outcome when no rules apply or when a transport failure
/// degrades to "skip rules".
pub async fn evaluate_for_write(
    db: &Db,
    client: &RulesClient,
    object_view: &Value,
    rule_types: &[&str],
    mode: &str,
    language: Option<&str>,
    actor: Option<&str>,
) -> Result<RuleOutcome, ApiError> {
    let object_type = object_view
        .get("type")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let rules = load_active_rules(db, rule_types, object_type).await?;
    if rules.is_empty() {
        return Ok(RuleOutcome::default());
    }
    match client
        .evaluate(&rules, object_view, mode, language, actor)
        .await
    {
        Ok(outcome) => Ok(outcome),
        Err(error) => {
            if client.degrade() {
                tracing::warn!(?error, "rule evaluation degraded; skipping rules");
                Ok(RuleOutcome::default())
            } else {
                Err(ApiError::unavailable(format!(
                    "rule evaluation unavailable: {error}"
                )))
            }
        }
    }
}

/// Enforces the outcome of a blocking rule evaluation (fail-closed).
pub fn enforce(outcome: &RuleOutcome) -> Result<(), ApiError> {
    if outcome.has_failures() {
        return Err(ApiError::internal(format!(
            "rule(s) {} failed",
            outcome.failed.join(", ")
        )));
    }
    if !outcome.errors.is_empty() {
        let message = outcome
            .errors
            .iter()
            .map(|issue| format!("{}: {} ({})", issue.attr, issue.message, issue.code))
            .collect::<Vec<_>>()
            .join("; ");
        return Err(ApiError::bad_request(format!(
            "validation failed: {message}"
        )));
    }
    if outcome.allowed == Some(false) {
        return Err(ApiError::bad_request(format!(
            "transition denied: {}",
            outcome.deny_reason.as_deref().unwrap_or("not allowed")
        )));
    }
    Ok(())
}
