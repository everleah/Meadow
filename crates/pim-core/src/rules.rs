//! Synchronous business-rule evaluation via NATS request/reply (Phase 4).
//!
//! The core stays the database authority: it loads the applicable `active`
//! `business_rule` records, builds the resolved candidate view, sends a
//! request to the Python worker over NATS (`rules.evaluate`), and enforces the
//! outcome in the write path (fail-closed). See
//! `plan/detail/business-rules.md`.

use std::collections::{HashMap, HashSet};
use std::sync::{Arc, RwLock};
use std::time::Duration;

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, json};

use crate::app::AppState;
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
#[derive(Debug, Clone, Serialize)]
pub struct RuleSpec {
    pub name: String,
    #[serde(rename = "type")]
    pub rule_type: String,
    pub script: String,
}

/// Additional context for a rule evaluation (besides object, mode, language).
///
/// Set for channel-specific release transitions (`release` table): the Python
/// contract exposes them as `ctx.channel` / `ctx.release_status`, while
/// `obj.status` keeps the **global** object status. Object status transitions
/// use `EvaluationContext::default()`.
///
/// Set for object writes with class features (Stage B): `ctx.missing_features`
/// lists the mandatory features of the object's taxonomy classes that are not
/// set — a rule can use it to block a transition (e.g. "publish only with a
/// complete ETIM class").
#[derive(Debug, Clone, Copy, Default)]
pub struct EvaluationContext<'a> {
    /// Channel whose release is being changed.
    pub channel: Option<&'a str>,
    /// Target state of that channel release.
    pub release_status: Option<&'a str>,
    /// Unset mandatory class features (empty when the object has no classes).
    pub missing_features: Option<&'a [String]>,
}

/// What to evaluate for a write operation (rule set, mode, actor, context).
#[derive(Debug, Clone, Copy)]
pub struct WriteEvaluation<'a> {
    /// Rule types to run (`validation`, `computed`, `workflow_guard`, …).
    pub rule_types: &'a [&'a str],
    /// Evaluation mode (`save`, `transition`, `import`, `bulk`).
    pub mode: &'a str,
    /// Working language of the resolved view, if any.
    pub language: Option<&'a str>,
    /// Triggering user or system.
    pub actor: Option<&'a str>,
    /// Channel-release context (default for plain object writes).
    pub context: EvaluationContext<'a>,
}

impl<'a> WriteEvaluation<'a> {
    /// A write evaluation without channel context.
    pub fn new(
        rule_types: &'a [&'a str],
        mode: &'a str,
        language: Option<&'a str>,
        actor: Option<&'a str>,
    ) -> Self {
        Self {
            rule_types,
            mode,
            language,
            actor,
            context: EvaluationContext::default(),
        }
    }
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
///
/// Connection details come from `Config` (startup); the operational knobs
/// (`timeout`, `degrade`, `breaker_threshold`) are read at runtime from the
/// shared settings snapshot, so they can be tuned on the system-settings page
/// without a restart.
#[derive(Debug, Clone)]
pub struct RulesClient {
    nats_url: String,
    subject: String,
    compile_subject: String,
    settings: Arc<RwLock<Map<String, Value>>>,
}

impl RulesClient {
    /// Builds the client from runtime configuration + the shared settings snapshot.
    pub fn from_config(config: &Config, settings: Arc<RwLock<Map<String, Value>>>) -> Self {
        Self {
            nats_url: config.rules_nats_url.clone(),
            subject: config.rules_subject.clone(),
            compile_subject: config.rules_compile_subject.clone(),
            settings,
        }
    }

    /// The evaluation request timeout (ms), read at runtime.
    fn timeout(&self) -> Duration {
        let ms = self
            .settings
            .read()
            .map(|guard| {
                guard
                    .get("rules_timeout_ms")
                    .and_then(Value::as_u64)
                    .unwrap_or(2000)
            })
            .unwrap_or(2000);
        Duration::from_millis(ms)
    }

    /// Whether a transport failure should degrade to "skip rules".
    pub fn degrade(&self) -> bool {
        self.settings
            .read()
            .map(|guard| {
                guard
                    .get("rules_degrade")
                    .and_then(Value::as_bool)
                    .unwrap_or(false)
            })
            .unwrap_or(false)
    }

    /// Consecutive rule failures before the circuit breaker disables a rule.
    pub fn breaker_threshold(&self) -> u64 {
        self.settings
            .read()
            .map(|guard| {
                guard
                    .get("rules_breaker_threshold")
                    .and_then(Value::as_u64)
                    .unwrap_or(3)
            })
            .unwrap_or(3)
    }

    /// Evaluates the given rules against the candidate object view.
    pub async fn evaluate(
        &self,
        rules: &[RuleSpec],
        object: &Value,
        mode: &str,
        language: Option<&str>,
        actor: Option<&str>,
        context: EvaluationContext<'_>,
    ) -> Result<RuleOutcome, EvaluateError> {
        let payload = evaluation_payload(rules, object, mode, language, actor, context);
        let bytes = serde_json::to_vec(&payload)
            .map_err(|error| EvaluateError::BadReply(error.to_string()))?;

        let client = async_nats::connect(&self.nats_url)
            .await
            .map_err(|error| EvaluateError::Transport(error.to_string()))?;
        let request = async_nats::Request::new()
            .payload(bytes.into())
            .timeout(Some(self.timeout()));
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
            .timeout(Some(self.timeout()));
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

/// The NATS request payload for one rule evaluation.
///
/// The shape is the worker's contract (`meadow/worker/protocol.py`,
/// `EvaluationRequest`): `missing_features` is a **list**, never `null` — a
/// serialised `None` made the worker reject the whole request
/// (`missing_features: Input should be a valid array`), which silently broke
/// every Rule Lab run (`dry-run`/`test-batch` pass `EvaluationContext::default()`)
/// while the object write path kept working, because that one always supplies a
/// list. Found 2026-09-19 while testing an AI suggestion in the lab; the unit
/// test below pins the field's shape.
fn evaluation_payload(
    rules: &[RuleSpec],
    object: &Value,
    mode: &str,
    language: Option<&str>,
    actor: Option<&str>,
    context: EvaluationContext<'_>,
) -> Value {
    json!({
        "rules": rules,
        "object": object,
        "mode": mode,
        "language": language,
        "actor": actor,
        "channel": context.channel,
        "release_status": context.release_status,
        "missing_features": context.missing_features.unwrap_or(&[]),
    })
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
    cache: &crate::cache::MetaCache,
    db: &Db,
    client: &RulesClient,
    object_view: &Value,
    evaluation: WriteEvaluation<'_>,
) -> Result<RuleOutcome, ApiError> {
    let WriteEvaluation {
        rule_types,
        mode,
        language,
        actor,
        context,
    } = evaluation;
    let object_type = object_view
        .get("type")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let rules = cache.active_rules(db, rule_types, object_type).await?;
    if rules.is_empty() {
        return Ok(RuleOutcome::default());
    }
    // Class features (Stage B): resolve the unset mandatory class features once
    // the rules are known (so the extra queries only run when rules exist).
    let missing_features = match context.missing_features {
        Some(names) => names.to_vec(),
        None => missing_class_features(cache, db, object_view).await?,
    };
    let context = EvaluationContext {
        missing_features: Some(&missing_features),
        ..context
    };
    match client
        .evaluate(&rules, object_view, mode, language, actor, context)
        .await
    {
        Ok(outcome) => {
            if let Err(error) =
                account_failures(db, &rules, &outcome, client.breaker_threshold()).await
            {
                tracing::warn!(?error, "failed to update rule failure counters");
            }
            persist_outcome_logs(db, &outcome).await;
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

/// Unset mandatory class features of the candidate object (Stage B).
///
/// `object_view` is the resolved candidate view sent to the rules, so a value
/// that is set (own or inherited) counts as satisfied. Objects without an id
/// (create) have no classes yet.
async fn missing_class_features(
    cache: &crate::cache::MetaCache,
    db: &Db,
    object_view: &Value,
) -> Result<Vec<String>, ApiError> {
    let Some(id) = object_view
        .get("id")
        .and_then(Value::as_str)
        .filter(|id| !id.is_empty())
    else {
        return Ok(Vec::new());
    };
    let required =
        crate::class_features::class_required_names(cache, db, crate::class_features::key_of(id))
            .await?;
    if required.is_empty() {
        return Ok(Vec::new());
    }
    let attributes = object_view.get("attributes").and_then(Value::as_object);
    Ok(required
        .into_iter()
        .filter(|name| {
            attributes
                .and_then(|map| map.get(name.as_str()))
                .map(Value::is_null)
                .unwrap_or(true)
        })
        .collect())
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

/// Persists rule warnings and `log()` output from an evaluation as curated log
/// entries (best-effort; a failed write must not fail the save).
async fn persist_outcome_logs(db: &Db, outcome: &RuleOutcome) {
    if outcome.warnings.is_empty() && outcome.logs.is_empty() {
        return;
    }
    for warning in &outcome.warnings {
        record_rule_log(
            db,
            "WARN",
            &format!(
                "validation warning: {}: {} ({})",
                warning.attr, warning.message, warning.code
            ),
            json!({ "attr": warning.attr, "code": warning.code }),
        )
        .await;
    }
    for entry in &outcome.logs {
        let message = entry.get("message").and_then(Value::as_str).unwrap_or("");
        if message.is_empty() {
            continue;
        }
        let level = match entry.get("level").and_then(Value::as_str) {
            Some("DEBUG") => "DEBUG",
            Some("WARN") | Some("WARNING") => "WARN",
            Some("ERROR") => "ERROR",
            Some("CRITICAL") => "CRITICAL",
            _ => "INFO",
        };
        record_rule_log(db, level, message, json!({})).await;
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

/// An active `computed` rule that reads inherited values (a cascade rule).
///
/// When one of these reads a value from an ancestor, an ancestor save must
/// schedule a recompute over the affected descendants (eventual consistency).
#[derive(Debug, Clone)]
pub struct InheritedComputedRule {
    pub rule_id: String,
    pub name: String,
    pub rule_type: String,
    pub script: String,
    pub applies_to: Vec<String>,
}

/// Returns the active `computed` rules whose attribute definitions are marked
/// `computed_reads_inherited: true`.
/// Loads the rules that compute a value from inherited data (cached by
/// [`crate::cache::MetaCache::inherited_computed_rules`]).
pub async fn load_inherited_computed_rules(
    db: &Db,
) -> Result<Vec<InheritedComputedRule>, ApiError> {
    let defs: Vec<Value> = db
        .inner()
        .query(
            "SELECT computed_rule FROM attribute_definition \
             WHERE computed = true AND computed_reads_inherited = true;",
        )
        .await?
        .check()?
        .take(0)?;
    let mut names: Vec<String> = Vec::new();
    for def in defs {
        if let Some(rule_name) = def.get("computed_rule").and_then(Value::as_str)
            && !names.iter().any(|n| n == rule_name)
        {
            names.push(rule_name.to_owned());
        }
    }
    if names.is_empty() {
        return Ok(Vec::new());
    }
    let rows: Vec<Value> = db
        .inner()
        .query(
            "SELECT * FROM business_rule \
             WHERE name IN $names AND type = 'computed' AND state = 'active';",
        )
        .bind(("names", names))
        .await?
        .check()?
        .take(0)?;
    let mut result = Vec::new();
    for rule in rows {
        let Some(rule_id) = rule.get("id").and_then(Value::as_str) else {
            continue;
        };
        let Some(name) = rule.get("name").and_then(Value::as_str) else {
            continue;
        };
        let Some(script) = rule.get("script").and_then(Value::as_str) else {
            continue;
        };
        let rule_type = rule
            .get("type")
            .and_then(Value::as_str)
            .unwrap_or("computed")
            .to_owned();
        let applies_to = rule
            .get("applies_to")
            .and_then(Value::as_array)
            .map(|types| {
                types
                    .iter()
                    .filter_map(|t| t.as_str().map(str::to_owned))
                    .collect()
            })
            .unwrap_or_default();
        result.push(InheritedComputedRule {
            rule_id: rule_id.to_owned(),
            name: name.to_owned(),
            rule_type,
            script: script.to_owned(),
            applies_to,
        });
    }
    Ok(result)
}

/// Queues a `business_rule` recompute job per affected inherited-computed rule
/// over the saved object's descendants (best-effort, never inline).
///
/// Called from the write path **after** the object is persisted, so the
/// recompute is eventual-consistent with the ancestor change. Locale is
/// threaded through so the worker resolves the view consistently with the
/// write path.
/// Depth bound for the descendant walk of the inherited-computed cascade. The
/// composition tree is shallow (make → model → generation → variant in the demo
/// data); the walk is cycle-safe on top of this bound.
const MAX_CASCADE_DEPTH: usize = 64;

pub async fn queue_inherited_cascade(
    state: &AppState,
    root_id: &str,
    locale: Option<&str>,
    actor: &str,
) -> Result<(), ApiError> {
    let rules = state.cache.inherited_computed_rules(&state.db).await?;
    if rules.is_empty() {
        return Ok(());
    }
    // Descendants level by level instead of `subtree_keys`: that helper loads the
    // **whole** `rel_depends_on` edge set per call, which cost ~300 ms on every
    // attribute PATCH of a demo-sized dataset — even for a leaf without children
    // (`plan/detail/demodaten-generator.md` §4.3).
    let descendants: Vec<String> =
        crate::api::references::descendant_keys(&state.db, root_id, MAX_CASCADE_DEPTH).await?;
    if descendants.is_empty() {
        return Ok(());
    }
    // Load the descendant object types so each rule can be filtered by its
    // `applies_to` before a recompute is scheduled. The keys are rendered as a
    // record list literal: `id IN array::map($keys, …)` is evaluated per row and
    // quadratic (`crate::api::keys::record_list_literal`).
    let literal =
        crate::api::keys::record_list_literal("object", descendants.iter().map(String::as_str));
    let records: Vec<Value> = state
        .db
        .inner()
        .query(format!(
            "SELECT type::string(id) AS id, type FROM object WHERE id IN {literal} AND deleted_at IS NONE;"
        ))
        .await?
        .check()?
        .take(0)?;
    let mut type_by_key: HashMap<String, String> = HashMap::new();
    for record in &records {
        if let (Some(id), Some(ty)) = (
            record.get("id").and_then(Value::as_str),
            record.get("type").and_then(Value::as_str),
        ) {
            let bare = id.strip_prefix("object:").unwrap_or(id);
            type_by_key.insert(bare.to_owned(), ty.to_owned());
        }
    }
    for rule in rules.iter() {
        // Filter on the bare keys, then hand the worker full `object:` ids
        // (the recompute/worker loads via `GET /objects/{id}`).
        let matches: Vec<String> = descendants
            .iter()
            .filter(|key| {
                rule.applies_to.is_empty()
                    || type_by_key
                        .get(*key)
                        .is_some_and(|ty| rule.applies_to.contains(ty))
            })
            .map(|key| format!("object:{key}"))
            .collect();
        if matches.is_empty() {
            continue;
        }
        let mut parameters = json!({
            "rule_id": rule.rule_id,
            "rule": { "name": rule.name, "type": rule.rule_type, "script": rule.script },
            "applies_to": rule.applies_to,
            "object_ids": matches,
        });
        if let Some(locale) = locale {
            parameters["locale"] = Value::String(locale.to_owned());
        }
        let result = crate::api::jobs::queue_run(
            &state.db,
            &state.config,
            None,
            "business_rule",
            parameters,
            "cascade",
            actor,
        )
        .await;
        if let Err(error) = result {
            tracing::warn!(
                ?error,
                "failed to enqueue inherited-computed cascade for rule {}",
                rule.name
            );
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn spec() -> RuleSpec {
        RuleSpec {
            name: "check_sku".to_owned(),
            rule_type: "validation".to_owned(),
            script: "def run(obj, ctx, result):\n    pass\n".to_owned(),
        }
    }

    fn object() -> Value {
        json!({ "id": "object:1", "type": "product", "status": "draft", "attributes": {} })
    }

    /// The Rule Lab passes `EvaluationContext::default()`; a `null` there made
    /// the worker reject the whole request, so the list is pinned here.
    #[test]
    fn the_evaluation_payload_always_carries_a_feature_list() {
        let payload = evaluation_payload(
            &[spec()],
            &object(),
            "save",
            None,
            None,
            EvaluationContext::default(),
        );
        assert_eq!(payload.get("missing_features"), Some(&json!([])));
        assert!(payload["missing_features"].is_array());
        // Channel context stays optional; absent means `null`, which the worker
        // models as `str | None`.
        assert_eq!(payload.get("channel"), Some(&Value::Null));
        assert_eq!(payload.get("release_status"), Some(&Value::Null));

        let names = vec!["EF000123".to_owned()];
        let with_features = evaluation_payload(
            &[spec()],
            &object(),
            "transition",
            Some("de"),
            Some("user:admin"),
            EvaluationContext {
                channel: Some("web"),
                release_status: Some("published"),
                missing_features: Some(&names),
            },
        );
        assert_eq!(with_features["missing_features"], json!(["EF000123"]));
        assert_eq!(with_features["channel"], json!("web"));
        assert_eq!(with_features["language"], json!("de"));
    }

    /// The keys are the worker's `EvaluationRequest` fields — a rename here has
    /// to break a test, not a live evaluation.
    #[test]
    fn the_evaluation_payload_uses_the_worker_field_names() {
        let payload = evaluation_payload(
            &[spec()],
            &object(),
            "import",
            None,
            None,
            EvaluationContext::default(),
        );
        let mut keys: Vec<&str> = payload
            .as_object()
            .expect("payload is an object")
            .keys()
            .map(String::as_str)
            .collect();
        keys.sort_unstable();
        assert_eq!(
            keys,
            vec![
                "actor",
                "channel",
                "language",
                "missing_features",
                "mode",
                "object",
                "release_status",
                "rules",
            ]
        );
        // The rule spec keeps its `type` key (the worker's `EvaluationRule`).
        assert_eq!(payload["rules"][0]["type"], json!("validation"));
        assert_eq!(payload["rules"][0]["name"], json!("check_sku"));
    }

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
