//! Periodic log-retention cleanup (Phase 5, step 8).
//!
//! Deletes `log_entry` rows older than `log_retention_days` (resolved from
//! system settings: code default < environment < database) on a fixed interval
//! (`LOG_CLEANUP_INTERVAL_S`, default 3600).

use std::time::Duration;

use serde_json::Value;

use crate::api::system::resolve_retention_days;
use crate::config::Config;
use crate::db::Db;
use crate::error::ApiError;

/// Deletes stale log entries and returns the number of deleted rows.
pub(crate) async fn cleanup_once(db: &Db) -> Result<u64, ApiError> {
    let days = resolve_retention_days(db).await?;

    let count_sql = "
        SELECT count() FROM log_entry
        WHERE created_at < (time::now() - duration::from_days($days))
        GROUP ALL;
    ";
    let mut response = db
        .inner()
        .query(count_sql)
        .bind(("days", days))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    let count = rows
        .first()
        .and_then(|row| row.get("count"))
        .and_then(|value| value.as_u64().or_else(|| value.as_i64().map(|n| n as u64)))
        .unwrap_or(0);

    if count == 0 {
        return Ok(0);
    }

    let delete_sql = "
        DELETE FROM log_entry
        WHERE created_at < (time::now() - duration::from_days($days));
    ";
    db.inner()
        .query(delete_sql)
        .bind(("days", days))
        .await?
        .check()?;
    Ok(count)
}

/// Runs the cleanup loop forever.
pub async fn run(db: Db, config: Config) {
    let interval = Duration::from_secs(config.log_cleanup_interval_seconds);
    tracing::info!(
        interval_seconds = config.log_cleanup_interval_seconds,
        "log cleanup started"
    );
    loop {
        match cleanup_once(&db).await {
            Ok(0) => {}
            Ok(deleted) => tracing::info!(deleted, "log cleanup removed stale entries"),
            Err(error) => tracing::warn!(error = %error.message(), "log cleanup failed"),
        }
        tokio::time::sleep(interval).await;
    }
}
