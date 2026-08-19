//! Audit log recording (plan section 13).
//!
//! The audit log is the durable, domain-level change history (who/what/when).
//! It is written directly from the core's CRUD operations, independent of the
//! event system. Entries are written best-effort: a failed audit write must
//! not fail the underlying operation, so callers log a warning instead.

use serde_json::{Map, Value};

use crate::db::Db;
use crate::error::ApiError;

/// A single audit log entry.
pub struct AuditEntry<'a> {
    /// Triggering user or system identifier (record id, e.g. `user:admin`).
    pub user_id: &'a str,
    /// Change kind, e.g. `object_created`, `object_updated`, `status_changed`.
    pub action: &'a str,
    /// Affected object record id (optional).
    pub object_id: Option<&'a str>,
    /// Old value(s) (optional).
    pub from: Option<Value>,
    /// New value(s) (optional).
    pub to: Option<Value>,
    /// Additional structured context (optional).
    pub metadata: Option<Value>,
}

/// Persists a single audit log entry. Fields without a value are omitted so
/// that schema defaults (e.g. the `timestamp`) apply.
pub async fn record(db: &Db, entry: AuditEntry<'_>) -> Result<(), ApiError> {
    let mut content = Map::new();
    content.insert("action".to_owned(), Value::String(entry.action.to_owned()));
    content.insert(
        "user_id".to_owned(),
        Value::String(entry.user_id.to_owned()),
    );
    if let Some(object_id) = entry.object_id {
        content.insert("object_id".to_owned(), Value::String(object_id.to_owned()));
    }
    if let Some(from) = entry.from {
        content.insert("from".to_owned(), from);
    }
    if let Some(to) = entry.to {
        content.insert("to".to_owned(), to);
    }
    if let Some(metadata) = entry.metadata {
        content.insert("metadata".to_owned(), metadata);
    }

    let sql = "CREATE audit_log CONTENT $content;";
    db.inner()
        .query(sql)
        .bind(("content", Value::Object(content)))
        .await?
        .check()?;
    Ok(())
}

/// Writes an audit entry and logs a warning instead of failing the caller.
pub async fn record_best_effort(db: &Db, entry: AuditEntry<'_>) {
    if let Err(error) = record(db, entry).await {
        tracing::warn!(?error, "failed to write audit log entry");
    }
}
