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
    let content = content(entry);
    let sql = "CREATE audit_log CONTENT $content;";
    db.inner()
        .query(sql)
        .bind(("content", content))
        .await?
        .check()?;
    Ok(())
}

/// Builds the `audit_log` content of one entry (fields without a value are
/// omitted so the schema defaults apply).
pub fn content(entry: AuditEntry<'_>) -> Value {
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
    Value::Object(content)
}

/// Persists many audit entries in **one** statement.
///
/// The batch upsert writes one entry per object (the audit contract of a single
/// object write stays identical), but pays a single round trip for the whole
/// batch (`plan/detail/demodaten-generator.md` §4.3).
pub async fn record_many(db: &Db, entries: Vec<Value>) -> Result<(), ApiError> {
    if entries.is_empty() {
        return Ok(());
    }
    db.inner()
        .query("FOR $entry IN $entries { CREATE audit_log CONTENT $entry; };")
        .bind(("entries", entries))
        .await?
        .check()?;
    Ok(())
}

/// Writes a batch of audit entries and logs a warning instead of failing the
/// caller.
pub async fn record_many_best_effort(db: &Db, entries: Vec<Value>) {
    if let Err(error) = record_many(db, entries).await {
        tracing::warn!(?error, "failed to write audit log entries");
    }
}

/// Writes an audit entry and logs a warning instead of failing the caller.
pub async fn record_best_effort(db: &Db, entry: AuditEntry<'_>) {
    if let Err(error) = record(db, entry).await {
        tracing::warn!(?error, "failed to write audit log entry");
    }
}
