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

fn default_limit() -> u32 {
    100
}

fn default_settings() -> Map<String, Value> {
    let mut settings = Map::new();
    settings.insert("log_persist_level".to_owned(), json!("WARN"));
    settings.insert("log_persist_categories".to_owned(), json!(["app"]));
    settings.insert("log_retention_days".to_owned(), json!(30));
    settings.insert("log_rate_limit".to_owned(), json!(1000));
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
    // Machine-to-machine API key takes precedence; admin JWT is a fallback
    // for development when no API key is configured.
    let api_key_ok = match &state.config.log_ingest_api_key {
        Some(expected) => headers
            .get("x-api-key")
            .and_then(|value| value.to_str().ok())
            .is_some_and(|provided| provided == expected),
        None => false,
    };
    let jwt_ok = headers
        .get(axum::http::header::AUTHORIZATION)
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.strip_prefix("Bearer "))
        .and_then(|token| crate::auth::verify(&state.config.jwt_secret, token).ok())
        .is_some_and(|claims| claims.is_admin());
    if !api_key_ok && !jwt_ok {
        return Err(ApiError::forbidden("requires API key or admin role"));
    }
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
    if !claims.is_admin() {
        return Err(ApiError::forbidden("requires admin role"));
    }
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
    responses(
        (status = 200, description = "Resolved settings", body = Value),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Forbidden", body = Value)
    )
)]
pub async fn list_settings(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
) -> Result<Json<Value>, ApiError> {
    if !claims.is_admin() {
        return Err(ApiError::forbidden("requires admin role"));
    }
    resolve_settings(&state.db).await.map(Json)
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
    if !claims.is_admin() {
        return Err(ApiError::forbidden("requires admin role"));
    }
    if !is_known_setting(&key) {
        return Err(ApiError::bad_request(format!("unknown setting `{key}`")));
    }
    validate_setting(&key, &body.value)?;
    let user_id = claims.sub.clone();

    // Statements: BEGIN(0), LET(1), UPSERT(2), UPDATE(3), SELECT(4), COMMIT(5).
    let sql = "
        BEGIN TRANSACTION;
        LET $rec = type::record('system_setting', $key);
        UPSERT $rec CONTENT $content;
        UPDATE $rec SET updated_at = time::now(), updated_by = $user;
        SELECT * FROM $rec;
        COMMIT TRANSACTION;
    ";
    let content = json!({ "key": key, "value": body.value });
    let mut response = state
        .db
        .inner()
        .query(sql)
        .bind(("key", key.clone()))
        .bind(("content", content))
        .bind(("user", user_id.clone()))
        .await?
        .check()?;
    let settings: Vec<Value> = response.take(4)?;
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
    if !claims.is_admin() {
        return Err(ApiError::forbidden("requires admin role"));
    }
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
