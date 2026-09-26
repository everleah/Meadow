//! System logs and runtime settings handlers (plan section 12).
//!
//! `log_entry` is the append-only table of curated operational entries from
//! all components. `system_setting` persists runtime configuration resolved
//! as: code default < environment < database.

use axum::Json;
use axum::extract::{Path, Query, State};
use axum::http::{HeaderMap, StatusCode};
use serde::Deserialize;
use serde_json::{Map, Value, json};
use utoipa::{IntoParams, ToSchema};

use crate::app::AppState;
use crate::audit::{self, AuditEntry};
use crate::auth::AuthUser;
use crate::db::Db;
use crate::error::ApiError;

const LEVELS: [&str; 5] = ["DEBUG", "INFO", "WARN", "ERROR", "CRITICAL"];
const CATEGORIES: [&str; 5] = ["app", "job", "rule", "auth", "integration"];
const MAX_BATCH_SIZE: usize = 1000;

/// A single curated log entry accepted by `POST /system/logs`.
#[derive(Debug, Deserialize, ToSchema)]
pub struct IngestLogEntry {
    /// Source component (`pim-core`, `pim-worker`, `pim-frontend`).
    pub service: String,
    /// `DEBUG` | `INFO` | `WARN` | `ERROR` | `CRITICAL`.
    pub level: String,
    /// `app` | `job` | `rule` | `auth` | `integration`.
    pub category: String,
    /// Plain-text message (required).
    pub message: String,
    /// Correlation id across services.
    #[serde(default)]
    pub trace_id: Option<String>,
    /// Optional structured context.
    #[serde(default)]
    pub context: Option<Value>,
}

/// Query parameters for `GET /system/logs`.
#[derive(Debug, Deserialize, ToSchema, IntoParams)]
#[into_params(parameter_in = Query)]
pub struct ListLogs {
    /// Filter by service.
    pub service: Option<String>,
    /// Filter by category.
    pub category: Option<String>,
    /// Filter by level.
    pub level: Option<String>,
    /// Inclusive lower bound on `created_at` (ISO-8601).
    pub from: Option<String>,
    /// Inclusive upper bound on `created_at` (ISO-8601).
    pub to: Option<String>,
    /// Substring match on `message`.
    pub q: Option<String>,
    /// Maximum number of results (capped at 1000).
    #[serde(default = "default_limit")]
    pub limit: u32,
    /// Number of results to skip.
    #[serde(default)]
    pub start: u32,
}

/// Payload for `PUT /system/settings/{key}`.
#[derive(Debug, Deserialize, ToSchema)]
pub struct PutSetting {
    /// New value for the setting.
    pub value: Value,
}

/// Query parameters for `GET /system/settings`.
#[derive(Debug, Deserialize, ToSchema, IntoParams)]
#[into_params(parameter_in = Query)]
pub struct SettingsQuery {
    /// When true, each setting is returned as `{ value, source }`
    /// (`default` | `env` | `db`); otherwise a flat `{ key: value }` map.
    #[serde(default)]
    pub include_source: Option<bool>,
}

fn default_limit() -> u32 {
    100
}

fn default_settings() -> Map<String, Value> {
    let mut settings = Map::new();
    settings.insert("log_persist_level".to_owned(), json!("WARN"));
    settings.insert("log_persist_categories".to_owned(), json!(["app"]));
    settings.insert("log_retention_days".to_owned(), json!(30));
    settings.insert("log_rate_limit".to_owned(), json!(1000));
    settings.insert("rules_degrade".to_owned(), json!(false));
    settings.insert("rules_breaker_threshold".to_owned(), json!(3));
    settings.insert("rules_timeout_ms".to_owned(), json!(2000));
    // AI assistance (plan/detail/ki-assistenz.md sections 4 and 4.1). Opt-in:
    // with `ai_enabled = false` the feature is invisible in the UI.
    settings.insert("ai_enabled".to_owned(), json!(false));
    settings.insert("ai_provider".to_owned(), json!("generic"));
    settings.insert("ai_endpoint".to_owned(), json!(""));
    settings.insert("ai_model".to_owned(), json!(""));
    settings.insert("ai_api_key".to_owned(), json!(""));
    settings.insert("ai_temperature".to_owned(), json!(0.2));
    settings.insert("ai_max_output_tokens".to_owned(), json!(2048));
    settings.insert("ai_timeout_ms".to_owned(), json!(30_000));
    settings.insert("ai_max_requests_per_hour".to_owned(), json!(60));
    settings
}

/// Providers the AI client knows how to talk to (`ai_provider`).
const AI_PROVIDERS: [&str; 4] = ["generic", "openai", "azure_openai", "ollama"];

/// Settings whose value must never leave the core in a response.
///
/// A secret is still **stored** (database or environment) and still used
/// internally — responses report `configured` instead of the value
/// (`plan/detail/ki-assistenz.md` section 4).
fn secret_setting(key: &str) -> bool {
    matches!(key, "ai_api_key")
}

/// Whether a setting value counts as set (non-empty string, non-null).
fn is_configured(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::String(text) => !text.trim().is_empty(),
        _ => true,
    }
}

/// Replaces every secret value with `null` — the response-shape half of
/// [`secret_setting`].
fn mask_secrets(settings: &mut Map<String, Value>) {
    let keys: Vec<String> = settings.keys().cloned().collect();
    for key in keys {
        if secret_setting(&key) {
            settings.insert(key, Value::Null);
        }
    }
}

/// The masked form of one secret setting: no value, but the UI can tell
/// whether something is configured and where it comes from.
fn masked_secret(value: &Value, source: &str) -> Value {
    json!({
        "value": Value::Null,
        "configured": is_configured(value),
        "secret": true,
        "source": source,
    })
}

/// Returns the default + environment settings (no DB overrides). Used to seed
/// the runtime settings snapshot at startup.
pub fn initial_settings() -> Map<String, Value> {
    let mut settings = default_settings();
    let keys: Vec<String> = settings.keys().cloned().collect();
    for key in keys {
        if let Some(env_value) = env_setting(&key) {
            settings.insert(key, env_value);
        }
    }
    settings
}

/// Environment override for a known setting (inbetriebnahme level).
fn env_setting(key: &str) -> Option<Value> {
    match key {
        "log_persist_level" => std::env::var("LOG_PERSIST_LEVEL").ok().map(Value::String),
        "log_persist_categories" => std::env::var("LOG_PERSIST_CATEGORIES").ok().map(|value| {
            Value::Array(
                value
                    .split(',')
                    .map(|category| Value::String(category.trim().to_owned()))
                    .collect(),
            )
        }),
        "log_retention_days" => std::env::var("LOG_RETENTION_DAYS")
            .ok()
            .and_then(|value| value.parse::<i64>().ok())
            .map(|value| json!(value)),
        "log_rate_limit" => std::env::var("LOG_RATE_LIMIT")
            .ok()
            .and_then(|value| value.parse::<i64>().ok())
            .map(|value| json!(value)),
        "rules_degrade" => std::env::var("RULES_DEGRADE").ok().map(|value| {
            json!(matches!(
                value.as_str(),
                "1" | "true" | "TRUE" | "yes" | "YES"
            ))
        }),
        "rules_breaker_threshold" => std::env::var("RULES_BREAKER_THRESHOLD")
            .ok()
            .and_then(|value| value.parse::<i64>().ok())
            .map(|value| json!(value)),
        "rules_timeout_ms" => std::env::var("RULES_TIMEOUT_MS")
            .ok()
            .and_then(|value| value.parse::<i64>().ok())
            .map(|value| json!(value)),
        "ai_enabled" => std::env::var("AI_ENABLED").ok().map(|value| {
            json!(matches!(
                value.as_str(),
                "1" | "true" | "TRUE" | "yes" | "YES"
            ))
        }),
        "ai_provider" => std::env::var("AI_PROVIDER").ok().map(Value::String),
        "ai_endpoint" => std::env::var("AI_ENDPOINT").ok().map(Value::String),
        "ai_model" => std::env::var("AI_MODEL").ok().map(Value::String),
        "ai_api_key" => std::env::var("AI_API_KEY").ok().map(Value::String),
        "ai_temperature" => std::env::var("AI_TEMPERATURE")
            .ok()
            .and_then(|value| value.parse::<f64>().ok())
            .map(|value| json!(value)),
        "ai_max_output_tokens" => std::env::var("AI_MAX_OUTPUT_TOKENS")
            .ok()
            .and_then(|value| value.parse::<i64>().ok())
            .map(|value| json!(value)),
        "ai_timeout_ms" => std::env::var("AI_TIMEOUT_MS")
            .ok()
            .and_then(|value| value.parse::<i64>().ok())
            .map(|value| json!(value)),
        "ai_max_requests_per_hour" => std::env::var("AI_MAX_REQUESTS_PER_HOUR")
            .ok()
            .and_then(|value| value.parse::<i64>().ok())
            .map(|value| json!(value)),
        _ => None,
    }
}

fn is_known_setting(key: &str) -> bool {
    default_settings().contains_key(key)
}

fn validate_setting(key: &str, value: &Value) -> Result<(), ApiError> {
    match key {
        "log_persist_level" => match value.as_str() {
            Some(level) if LEVELS.contains(&level) => Ok(()),
            _ => Err(ApiError::bad_request(
                "`log_persist_level` must be one of DEBUG|INFO|WARN|ERROR|CRITICAL",
            )),
        },
        "log_persist_categories" => match value.as_array() {
            Some(items)
                if items.iter().all(|category| {
                    category
                        .as_str()
                        .is_some_and(|category| CATEGORIES.contains(&category))
                }) =>
            {
                Ok(())
            }
            _ => Err(ApiError::bad_request(
                "`log_persist_categories` must be an array of app|job|rule|auth|integration",
            )),
        },
        "log_retention_days" => match value.as_i64() {
            Some(days) if (7..=90).contains(&days) => Ok(()),
            _ => Err(ApiError::bad_request(
                "`log_retention_days` must be an integer between 7 and 90",
            )),
        },
        "log_rate_limit" => match value.as_i64() {
            Some(rate) if rate > 0 => Ok(()),
            _ => Err(ApiError::bad_request(
                "`log_rate_limit` must be a positive integer",
            )),
        },
        "rules_degrade" => match value.as_bool() {
            Some(_) => Ok(()),
            None => Err(ApiError::bad_request("`rules_degrade` must be a boolean")),
        },
        "rules_breaker_threshold" => match value.as_i64() {
            Some(threshold) if threshold > 0 => Ok(()),
            _ => Err(ApiError::bad_request(
                "`rules_breaker_threshold` must be a positive integer",
            )),
        },
        "rules_timeout_ms" => match value.as_i64() {
            Some(ms) if (100..=60_000).contains(&ms) => Ok(()),
            _ => Err(ApiError::bad_request(
                "`rules_timeout_ms` must be between 100 and 60000",
            )),
        },
        "ai_enabled" => match value.as_bool() {
            Some(_) => Ok(()),
            None => Err(ApiError::bad_request("`ai_enabled` must be a boolean")),
        },
        "ai_provider" => match value.as_str() {
            Some(provider) if AI_PROVIDERS.contains(&provider) => Ok(()),
            _ => Err(ApiError::bad_request(
                "`ai_provider` must be one of generic|openai|azure_openai|ollama",
            )),
        },
        "ai_endpoint" | "ai_model" | "ai_api_key" => match value.as_str() {
            Some(_) => Ok(()),
            None => Err(ApiError::bad_request(format!("`{key}` must be a string"))),
        },
        "ai_temperature" => match value.as_f64() {
            Some(temperature) if (0.0..=2.0).contains(&temperature) => Ok(()),
            _ => Err(ApiError::bad_request(
                "`ai_temperature` must be a number between 0 and 2",
            )),
        },
        "ai_max_output_tokens" => match value.as_i64() {
            Some(tokens) if (1..=32_768).contains(&tokens) => Ok(()),
            _ => Err(ApiError::bad_request(
                "`ai_max_output_tokens` must be between 1 and 32768",
            )),
        },
        "ai_timeout_ms" => match value.as_i64() {
            Some(ms) if (1_000..=300_000).contains(&ms) => Ok(()),
            _ => Err(ApiError::bad_request(
                "`ai_timeout_ms` must be between 1000 and 300000",
            )),
        },
        "ai_max_requests_per_hour" => match value.as_i64() {
            Some(limit) if limit >= 0 => Ok(()),
            _ => Err(ApiError::bad_request(
                "`ai_max_requests_per_hour` must be zero or a positive integer",
            )),
        },
        _ => Err(ApiError::bad_request(format!("unknown setting `{key}`"))),
    }
}

fn validate_log_entry(entry: &IngestLogEntry) -> Result<(), ApiError> {
    if entry.service.trim().is_empty() {
        return Err(ApiError::bad_request(
            "log entry `service` must not be empty",
        ));
    }
    if !LEVELS.contains(&entry.level.as_str()) {
        return Err(ApiError::bad_request(format!(
            "invalid log level `{}`",
            entry.level
        )));
    }
    if !CATEGORIES.contains(&entry.category.as_str()) {
        return Err(ApiError::bad_request(format!(
            "invalid log category `{}`",
            entry.category
        )));
    }
    if entry.message.trim().is_empty() {
        return Err(ApiError::bad_request(
            "log entry `message` must not be empty",
        ));
    }
    Ok(())
}

fn entry_content(entry: &IngestLogEntry) -> Value {
    let mut content = Map::new();
    content.insert(
        "service".to_owned(),
        Value::String(entry.service.trim().to_owned()),
    );
    content.insert("level".to_owned(), Value::String(entry.level.clone()));
    content.insert("category".to_owned(), Value::String(entry.category.clone()));
    content.insert(
        "message".to_owned(),
        Value::String(entry.message.trim().to_owned()),
    );
    if let Some(trace_id) = &entry.trace_id {
        content.insert("trace_id".to_owned(), Value::String(trace_id.clone()));
    }
    if let Some(context) = &entry.context {
        content.insert("context".to_owned(), context.clone());
    }
    Value::Object(content)
}

/// Accepts a batch of curated log entries from components.
#[utoipa::path(
    post,
    path = "/system/logs",
    request_body = Vec<IngestLogEntry>,
    responses(
        (status = 202, description = "Accepted batch", body = Value),
        (status = 400, description = "Invalid batch", body = Value),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Forbidden", body = Value)
    )
)]
pub async fn ingest_logs(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(batch): Json<Vec<IngestLogEntry>>,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    // The machine path (the worker ingests logs) and the admin fallback are one catalog check.
    crate::auth::require_permission(&state, &headers, "log.manage")?;
    if batch.is_empty() || batch.len() > MAX_BATCH_SIZE {
        return Err(ApiError::bad_request(format!(
            "batch must contain between 1 and {MAX_BATCH_SIZE} entries"
        )));
    }
    for entry in &batch {
        validate_log_entry(entry)?;
    }

    let entries: Vec<Value> = batch.iter().map(entry_content).collect();
    let sql = "FOR $entry IN $batch { CREATE log_entry CONTENT $entry; };";
    state
        .db
        .inner()
        .query(sql)
        .bind(("batch", Value::Array(entries)))
        .await?
        .check()?;

    Ok((
        StatusCode::ACCEPTED,
        Json(json!({ "accepted": batch.len() })),
    ))
}

/// Lists curated system logs (admin only).
#[utoipa::path(
    get,
    path = "/system/logs",
    params(ListLogs),
    responses(
        (status = 200, description = "Log entries", body = Vec<Value>),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Forbidden", body = Value)
    )
)]
pub async fn list_logs(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Query(params): Query<ListLogs>,
) -> Result<Json<Vec<Value>>, ApiError> {
    claims.require("log.read")?;
    let limit = params.limit.clamp(1, 1000) as i64;
    let start = params.start as i64;

    let mut conditions: Vec<&str> = Vec::new();
    if params.service.is_some() {
        conditions.push("service = $service");
    }
    if params.category.is_some() {
        conditions.push("category = $category");
    }
    if params.level.is_some() {
        conditions.push("level = $level");
    }
    if params.from.is_some() {
        conditions.push("created_at >= type::datetime($from)");
    }
    if params.to.is_some() {
        conditions.push("created_at <= type::datetime($to)");
    }
    if params.q.is_some() {
        conditions.push("message CONTAINS $q");
    }

    let mut sql = String::from("SELECT * FROM log_entry");
    if !conditions.is_empty() {
        sql.push_str(" WHERE ");
        sql.push_str(&conditions.join(" AND "));
    }
    sql.push_str(" ORDER BY created_at DESC LIMIT $limit START $start");

    let mut query = state.db.inner().query(sql);
    if let Some(service) = &params.service {
        query = query.bind(("service", service.clone()));
    }
    if let Some(category) = &params.category {
        query = query.bind(("category", category.clone()));
    }
    if let Some(level) = &params.level {
        query = query.bind(("level", level.clone()));
    }
    if let Some(from) = &params.from {
        query = query.bind(("from", from.clone()));
    }
    if let Some(to) = &params.to {
        query = query.bind(("to", to.clone()));
    }
    if let Some(q) = &params.q {
        query = query.bind(("q", q.clone()));
    }
    let mut response = query
        .bind(("limit", limit))
        .bind(("start", start))
        .await?
        .check()?;
    let logs: Vec<Value> = response.take(0)?;
    Ok(Json(logs))
}

/// Reads all runtime settings with their resolved values (admin only).
#[utoipa::path(
    get,
    path = "/system/settings",
    params(SettingsQuery),
    responses(
        (status = 200, description = "Resolved settings", body = Value),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Forbidden", body = Value)
    )
)]
pub async fn list_settings(
    State(state): State<AppState>,
    headers: HeaderMap,
    Query(params): Query<SettingsQuery>,
) -> Result<Json<Value>, ApiError> {
    // The machine path (the worker reads the runtime settings) and the admin UI share one check.
    crate::auth::require_permission(&state, &headers, "log.read")?;
    if params.include_source.unwrap_or(false) {
        resolve_settings_detailed(&state.db).await.map(Json)
    } else {
        // The flat form carries no source, so a secret can only be blanked out.
        let mut resolved = resolve_settings(&state.db).await?;
        if let Value::Object(map) = &mut resolved {
            mask_secrets(map);
        }
        Ok(Json(resolved))
    }
}

/// Sets a runtime setting, persisting it in the database (admin only).
#[utoipa::path(
    put,
    path = "/system/settings/{key}",
    request_body = PutSetting,
    responses(
        (status = 200, description = "Updated setting", body = Value),
        (status = 400, description = "Invalid key or value", body = Value),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Forbidden", body = Value)
    )
)]
pub async fn put_setting(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(key): Path<String>,
    Json(body): Json<PutSetting>,
) -> Result<Json<Value>, ApiError> {
    claims.require("log.manage")?;
    if !is_known_setting(&key) {
        return Err(ApiError::bad_request(format!("unknown setting `{key}`")));
    }
    validate_setting(&key, &body.value)?;
    let user_id = claims.sub.clone();

    // One write, or the record conflicts with itself: the former form
    // (`BEGIN; LET $rec; UPSERT $rec CONTENT; UPDATE $rec SET …; SELECT …; COMMIT`)
    // wrote the same record twice in one transaction, so the **second** save of
    // any setting answered `409 concurrent write conflict` (found 2026-09-19 while
    // verifying the AI settings, which are saved one after another).
    let sql = "
        UPSERT type::record('system_setting', $key)
            SET key = $key, value = $value, updated_at = time::now(), updated_by = $user
            RETURN AFTER;
    ";
    let mut response = state
        .db
        .inner()
        .query(sql)
        .bind(("key", key.clone()))
        .bind(("value", body.value.clone()))
        .bind(("user", user_id.clone()))
        .await?
        .check()?;
    let settings: Vec<Value> = response.take(0)?;
    let setting = settings
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::internal("updated setting was not returned"))?;

    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &user_id,
            action: "system_setting_updated",
            object_id: None,
            from: None,
            to: Some(json!({ "key": key, "value": body.value })),
            metadata: None,
        },
    )
    .await;

    refresh_settings_snapshot(&state).await?;
    Ok(Json(setting))
}

/// Resets a runtime setting to its default (deletes the DB entry).
#[utoipa::path(
    delete,
    path = "/system/settings/{key}",
    responses(
        (status = 204, description = "Setting reset"),
        (status = 400, description = "Unknown key", body = Value),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Forbidden", body = Value)
    )
)]
pub async fn delete_setting(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(key): Path<String>,
) -> Result<StatusCode, ApiError> {
    claims.require("log.manage")?;
    if !is_known_setting(&key) {
        return Err(ApiError::bad_request(format!("unknown setting `{key}`")));
    }
    let user_id = claims.sub.clone();

    let sql = "
        LET $rec = type::record('system_setting', $key);
        DELETE $rec;
    ";
    state
        .db
        .inner()
        .query(sql)
        .bind(("key", key.clone()))
        .await?
        .check()?;

    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &user_id,
            action: "system_setting_deleted",
            object_id: None,
            from: None,
            to: None,
            metadata: Some(json!({ "key": key })),
        },
    )
    .await;

    refresh_settings_snapshot(&state).await?;
    Ok(StatusCode::NO_CONTENT)
}

/// Resolves all known settings as code default < environment < database.
async fn resolve_settings(db: &Db) -> Result<Value, ApiError> {
    let mut resolved = default_settings();
    let keys: Vec<String> = resolved.keys().cloned().collect();
    for key in keys {
        if let Some(env_value) = env_setting(&key) {
            resolved.insert(key, env_value);
        }
    }

    let sql = "SELECT * FROM system_setting;";
    let mut response = db.inner().query(sql).await?.check()?;
    let rows: Vec<Value> = response.take(0)?;
    for row in rows {
        let Some(key) = row.get("key").and_then(Value::as_str) else {
            continue;
        };
        let Some(value) = row.get("value") else {
            continue;
        };
        if resolved.contains_key(key) {
            resolved.insert(key.to_owned(), value.clone());
        }
    }

    Ok(Value::Object(resolved))
}

/// Refreshes the in-memory runtime settings snapshot from the database.
/// Called after a settings write/reset so runtime consumers (e.g. the rule
/// client) see the new value immediately.
pub async fn refresh_settings_snapshot(state: &AppState) -> Result<(), ApiError> {
    let resolved = resolve_settings(&state.db).await?;
    if let Value::Object(map) = resolved {
        let mut guard = state
            .settings
            .write()
            .map_err(|_| ApiError::internal("settings snapshot lock poisoned"))?;
        *guard = map;
    }
    Ok(())
}

/// Resolves all settings as `{ key: { value, source } }` (source: default|env|db).
async fn resolve_settings_detailed(db: &Db) -> Result<Value, ApiError> {
    let mut result = Map::new();
    for (key, default_value) in default_settings() {
        let mut value = default_value;
        let mut source = "default".to_owned();
        if let Some(env_value) = env_setting(&key) {
            value = env_value;
            source = "env".to_owned();
        }
        let entry = if secret_setting(&key) {
            masked_secret(&value, &source)
        } else {
            json!({ "value": value.clone(), "source": source })
        };
        result.insert(key.clone(), entry);
    }

    let sql = "SELECT * FROM system_setting;";
    let mut response = db.inner().query(sql).await?.check()?;
    let rows: Vec<Value> = response.take(0)?;
    for row in rows {
        let Some(key) = row.get("key").and_then(Value::as_str) else {
            continue;
        };
        let Some(value) = row.get("value") else {
            continue;
        };
        if result.contains_key(key) {
            let entry = if secret_setting(key) {
                masked_secret(value, "db")
            } else {
                json!({ "value": value.clone(), "source": "db" })
            };
            result.insert(key.to_owned(), entry);
        }
    }

    Ok(Value::Object(result))
}

/// Resolves the configured log-retention window in days (default 30).
pub(crate) async fn resolve_retention_days(db: &Db) -> Result<i64, ApiError> {
    let settings = resolve_settings(db).await?;
    let days = settings
        .get("log_retention_days")
        .and_then(Value::as_i64)
        .unwrap_or(30);
    Ok(days)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn setting_level_validation() {
        assert!(validate_setting("log_persist_level", &json!("WARN")).is_ok());
        assert!(validate_setting("log_persist_level", &json!("NOPE")).is_err());
        assert!(validate_setting("log_persist_level", &json!(42)).is_err());
    }

    #[test]
    fn setting_retention_days_range() {
        assert!(validate_setting("log_retention_days", &json!(30)).is_ok());
        assert!(validate_setting("log_retention_days", &json!(7)).is_ok());
        assert!(validate_setting("log_retention_days", &json!(90)).is_ok());
        assert!(validate_setting("log_retention_days", &json!(6)).is_err());
        assert!(validate_setting("log_retention_days", &json!(200)).is_err());
    }

    #[test]
    fn setting_categories_and_unknown_key() {
        assert!(validate_setting("log_persist_categories", &json!(["app", "job"])).is_ok());
        assert!(validate_setting("log_persist_categories", &json!(["nope"])).is_err());
        assert!(validate_setting("log_rate_limit", &json!(1000)).is_ok());
        assert!(validate_setting("unknown_setting", &json!(1)).is_err());
    }

    #[test]
    fn setting_ai_validation() {
        assert!(validate_setting("ai_enabled", &json!(true)).is_ok());
        assert!(validate_setting("ai_enabled", &json!("yes")).is_err());
        assert!(validate_setting("ai_provider", &json!("azure_openai")).is_ok());
        assert!(validate_setting("ai_provider", &json!("anthropic")).is_err());
        assert!(validate_setting("ai_endpoint", &json!("https://api.deepseek.com/v1")).is_ok());
        assert!(validate_setting("ai_endpoint", &json!(42)).is_err());
        assert!(validate_setting("ai_model", &json!("deepseek-chat")).is_ok());
        assert!(validate_setting("ai_temperature", &json!(0.2)).is_ok());
        assert!(validate_setting("ai_temperature", &json!(2)).is_ok());
        assert!(validate_setting("ai_temperature", &json!(2.5)).is_err());
        assert!(validate_setting("ai_max_output_tokens", &json!(2048)).is_ok());
        assert!(validate_setting("ai_max_output_tokens", &json!(0)).is_err());
        assert!(validate_setting("ai_timeout_ms", &json!(30_000)).is_ok());
        assert!(validate_setting("ai_timeout_ms", &json!(200)).is_err());
        assert!(validate_setting("ai_max_requests_per_hour", &json!(0)).is_ok());
        assert!(validate_setting("ai_max_requests_per_hour", &json!(-1)).is_err());
    }

    #[test]
    fn the_api_key_is_secret_and_never_serialised() {
        assert!(secret_setting("ai_api_key"));
        assert!(!secret_setting("ai_endpoint"));
        // The value is gone from the response, the source and `configured` stay.
        let entry = masked_secret(&json!("sk-secret"), "db");
        assert_eq!(entry.get("value"), Some(&Value::Null));
        assert_eq!(entry.get("configured"), Some(&json!(true)));
        assert_eq!(entry.get("secret"), Some(&json!(true)));
        assert_eq!(entry.get("source"), Some(&json!("db")));
        assert!(!entry.to_string().contains("sk-secret"));
        // An unset key is reported as not configured (the UI hides the buttons).
        let unset = masked_secret(&json!("  "), "default");
        assert_eq!(unset.get("configured"), Some(&json!(false)));

        let mut flat = Map::new();
        flat.insert("ai_api_key".to_owned(), json!("sk-secret"));
        flat.insert("ai_model".to_owned(), json!("deepseek-chat"));
        mask_secrets(&mut flat);
        assert_eq!(flat.get("ai_api_key"), Some(&Value::Null));
        assert_eq!(flat.get("ai_model"), Some(&json!("deepseek-chat")));
    }

    fn entry(level: &str, category: &str, message: &str) -> IngestLogEntry {
        IngestLogEntry {
            service: "pim-core".to_owned(),
            level: level.to_owned(),
            category: category.to_owned(),
            message: message.to_owned(),
            trace_id: None,
            context: None,
        }
    }

    #[test]
    fn log_entry_validation() {
        assert!(validate_log_entry(&entry("INFO", "app", "hello")).is_ok());
        assert!(validate_log_entry(&entry("NOPE", "app", "hello")).is_err());
        assert!(validate_log_entry(&entry("INFO", "nope", "hello")).is_err());
        assert!(validate_log_entry(&entry("INFO", "app", "   ")).is_err());
        assert!(validate_log_entry(&entry("INFO", "app", "")).is_err());
    }
}
