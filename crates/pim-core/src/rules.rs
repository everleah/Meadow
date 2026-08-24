//! Synchronous business-rule evaluation via NATS request/reply (Phase 4).
//!
//! The core stays the database authority: it loads the applicable `active`
//! `business_rule` records, builds the resolved candidate view, sends a
//! request to the Python worker over NATS (`rules.evaluate`), and enforces the
//! outcome in the write path (fail-closed). See
//! `plan/detail/business-rules.md`.

use std::collections::{HashMap, HashSet};
use std::time::Duration;

use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

use crate::config::Config;
use crate::db::Db;
use crate::error::ApiError;

/// A single validation error or warning from a rule.
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct RuleIssue {
    pub attr: String,
    pub code: String,
    pub message: String,
}

/// The outcome returned by the Python worker.
#[derive(Debug, Clone, Default, Deserialize, Serialize)]
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
    breaker_threshold: u64,
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
            breaker_threshold: config.rules_breaker_threshold,
        }
    }

    /// Whether a transport failure should degrade to "skip rules".
    pub fn degrade(&self) -> bool {
        self.degrade
    }

    /// Consecutive rule failures before the circuit breaker disables a rule.
    pub fn breaker_threshold(&self) -> u64 {
        self.breaker_threshold
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
        Ok(outcome) => {
            if let Err(error) =
                account_failures(db, &rules, &outcome, client.breaker_threshold()).await
            {
                tracing::warn!(?error, "failed to update rule failure counters");
            }
            Ok(outcome)
        }
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

/// Updates consecutive-failure counters after an evaluation and auto-disables
/// rules that reach the threshold (circuit breaker). Returns the names of the
/// rules that were auto-disabled.
async fn account_failures(
    db: &Db,
    rules: &[RuleSpec],
    outcome: &RuleOutcome,
    threshold: u64,
) -> Result<Vec<String>, ApiError> {
    let failed: HashSet<&str> = outcome.failed.iter().map(String::as_str).collect();
    let mut auto_disabled = Vec::new();

    for rule in rules {
        if failed.contains(rule.name.as_str()) {
            // Increment the consecutive-failure counter (statement 0), then
            // read back the new counter and state (statement 1).
            let sql = "
                UPDATE business_rule
                SET failure_count = IF failure_count IS NONE THEN 1 ELSE failure_count + 1 END
                WHERE name = $name;
                SELECT id, failure_count, state FROM business_rule WHERE name = $name;
            ";
            let mut response = db
                .inner()
                .query(sql)
                .bind(("name", rule.name.clone()))
                .await?
                .check()?;
            let rows: Vec<Value> = response.take(1)?;
            let row = rows.into_iter().next();
            let count = row
                .as_ref()
                .and_then(|r| r.get("failure_count"))
                .and_then(Value::as_u64)
                .unwrap_or(0);
            let state = row
                .as_ref()
                .and_then(|r| r.get("state"))
                .and_then(Value::as_str)
                .unwrap_or("");
            let rule_id = row
                .as_ref()
                .and_then(|r| r.get("id"))
                .and_then(Value::as_str)
                .unwrap_or("");

            if state == "active" && count >= threshold {
                let disable_sql = "
                    UPDATE business_rule
                    SET state = 'disabled', failure_count = 0, version = version + 1, updated_at = time::now(), updated_by = 'system:circuit-breaker'
                    WHERE name = $name AND state = 'active';
                ";
                db.inner()
                    .query(disable_sql)
                    .bind(("name", rule.name.clone()))
                    .await?
                    .check()?;
                auto_disabled.push(rule.name.clone());

                // High-priority alert: audit entry + system log entry.
                crate::audit::record_best_effort(
                    db,
                    crate::audit::AuditEntry {
                        user_id: "system:circuit-breaker",
                        action: "rule_auto_disabled",
                        object_id: if rule_id.is_empty() {
                            None
                        } else {
                            Some(rule_id)
                        },
                        from: Some(Value::String("active".to_owned())),
                        to: Some(Value::String("disabled".to_owned())),
                        metadata: Some(json!({
                            "rule": rule.name,
                            "consecutive_failures": count,
                            "threshold": threshold,
                        })),
                    },
                )
                .await;
                record_rule_log(
                    db,
                    "ERROR",
                    &format!(
                        "rule `{}` auto-disabled by circuit breaker after {} consecutive failures",
                        rule.name, count
                    ),
                    json!({ "rule": rule.name, "consecutive_failures": count }),
                )
                .await;
            }
        } else {
            // Success resets the counter (no-op when it is already zero).
            let sql = "UPDATE business_rule SET failure_count = 0 WHERE name = $name AND failure_count > 0;";
            db.inner()
                .query(sql)
                .bind(("name", rule.name.clone()))
                .await?
                .check()?;
        }
    }
    Ok(auto_disabled)
}

/// Writes a system log entry for rule events (best-effort).
async fn record_rule_log(db: &Db, level: &str, message: &str, context: Value) {
    let content = json!({
        "service": "pim-core",
        "level": level,
        "category": "rule",
        "message": message,
        "context": context,
    });
    let result = db
        .inner()
        .query("CREATE log_entry CONTENT $content;")
        .bind(("content", content))
        .await
        .and_then(|response| response.check());
    if let Err(error) = result {
        tracing::warn!(?error, "failed to write rule log entry");
    }
}

/// Validates computed values against declared `computed` attribute definitions
/// and returns them for merging (fail-closed on undeclared writes).
pub async fn resolve_computed(
    db: &Db,
    computed: &serde_json::Map<String, Value>,
) -> Result<serde_json::Map<String, Value>, ApiError> {
    if computed.is_empty() {
        return Ok(serde_json::Map::new());
    }

    let names: Vec<Value> = computed
        .keys()
        .map(|name| Value::String(name.clone()))
        .collect();
    let sql = "SELECT name, computed FROM attribute_definition WHERE name IN $names";
    let mut response = db
        .inner()
        .query(sql)
        .bind(("names", Value::Array(names)))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;

    let mut declared: HashMap<String, bool> = HashMap::new();
    for row in rows {
        if let (Some(name), Some(is_computed)) = (
            row.get("name").and_then(Value::as_str),
            row.get("computed").and_then(Value::as_bool),
        ) {
            declared.insert(name.to_owned(), is_computed);
        }
    }

    validate_declared(computed, &declared)?;
    Ok(computed.clone())
}

/// Pure check: every computed key must be declared as `computed: true`.
fn validate_declared(
    computed: &serde_json::Map<String, Value>,
    declared: &HashMap<String, bool>,
) -> Result<(), ApiError> {
    for name in computed.keys() {
        match declared.get(name) {
            Some(true) => {}
            Some(false) => {
                return Err(ApiError::bad_request(format!(
                    "computed rule wrote `{name}`, which is not declared as `computed`"
                )));
            }
            None => {
                return Err(ApiError::bad_request(format!(
                    "computed rule wrote undeclared attribute `{name}`"
                )));
            }
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn declared(map: &[(&str, bool)]) -> HashMap<String, bool> {
        map.iter()
            .map(|(name, flag)| (name.to_string(), *flag))
            .collect()
    }

    fn computed(keys: &[&str]) -> serde_json::Map<String, Value> {
        keys.iter()
            .map(|name| (name.to_string(), Value::Null))
            .collect()
    }

    #[test]
    fn computed_accepts_declared_attribute() {
        let result = validate_declared(
            &computed(&["gross_price"]),
            &declared(&[("gross_price", true)]),
        );
        assert!(result.is_ok());
    }

    #[test]
    fn computed_rejects_undeclared_attribute() {
        let result = validate_declared(&computed(&["unknown"]), &HashMap::new());
        assert!(result.is_err());
    }

    #[test]
    fn computed_rejects_non_computed_attribute() {
        let result = validate_declared(&computed(&["sku"]), &declared(&[("sku", false)]));
        assert!(result.is_err());
    }
}
