//! Event system (Phase 4 groundwork, Phase 6 relay).
//!
//! CRUD events follow `plan/detail/integration-eventsystem.md`: every CRUD
//! operation emits an event that is written atomically with the operation into
//! the `outbox_event` table (transactional outbox) and later relayed to NATS
//! JetStream on `events.crud`. This module defines the event types, the outbox
//! status, the JetStream subject, the outbox content helper used inside CRUD
//! transactions, and the background relay.

use std::time::Duration;

use bytes::Bytes;
use serde_json::{Value, json};

use crate::config::Config;
use crate::db::Db;
use crate::error::ApiError;

/// Event types (see `plan/detail/integration-eventsystem.md`).
pub const EVENT_OBJECT_CREATED: &str = "object.created";
pub const EVENT_OBJECT_UPDATED: &str = "object.updated";
pub const EVENT_OBJECT_DELETED: &str = "object.deleted";
pub const EVENT_REFERENCE_CREATED: &str = "reference.created";
pub const EVENT_REFERENCE_DELETED: &str = "reference.deleted";
pub const EVENT_ATTRIBUTE_DEFINITION_CREATED: &str = "attribute_definition.created";
pub const EVENT_ATTRIBUTE_DEFINITION_UPDATED: &str = "attribute_definition.updated";
pub const EVENT_ATTRIBUTE_DEFINITION_DELETED: &str = "attribute_definition.deleted";

/// Outbox delivery status.
pub const STATUS_PENDING: &str = "pending";
pub const STATUS_PUBLISHED: &str = "published";

/// JetStream subject the relay publishes CRUD events to (Phase 6).
pub const EVENT_SUBJECT: &str = "events.crud";

/// A CRUD event to be written to the outbox.
pub struct OutboxEvent {
    /// One of the `EVENT_*` constants.
    pub event_type: &'static str,
    /// Business type of the affected object (e.g. `product`).
    pub object_type: String,
    /// Record id of the affected object (e.g. `object:<ulid>`).
    pub object_id: String,
    /// Structured old/new diff (e.g. `{ "attributes.price": { "old": ..., "new": ... } }`).
    pub changes: Value,
    /// Context such as `user_id`/`request_id`.
    pub metadata: Value,
}

/// Builds the `outbox_event` content for an event (status `pending`).
///
/// CRUD handlers embed this content into their transaction via
/// `CREATE outbox_event CONTENT $outbox;` so the event is written atomically
/// with its change.
pub fn content(event: &OutboxEvent) -> Value {
    let changes = if event.changes.is_object() {
        event.changes.clone()
    } else {
        json!({})
    };
    let metadata = if event.metadata.is_object() {
        event.metadata.clone()
    } else {
        json!({})
    };
    json!({
        "event_type": event.event_type,
        "object_type": event.object_type.clone(),
        "object_id": event.object_id.clone(),
        "changes": changes,
        "metadata": metadata,
        "status": STATUS_PENDING,
    })
}

/// Name of the JetStream stream that carries CRUD events.
pub const EVENTS_STREAM: &str = "EVENTS";
/// Subject pattern of the events stream.
pub const EVENTS_STREAM_SUBJECT: &str = "events.crud";

/// Ensures the `EVENTS` stream exists (WorkQueue retention with limits).
async fn ensure_events_stream(
    js: &async_nats::jetstream::Context,
    config: &Config,
) -> Result<(), ApiError> {
    js.get_or_create_stream(async_nats::jetstream::stream::Config {
        name: EVENTS_STREAM.to_owned(),
        subjects: vec![EVENTS_STREAM_SUBJECT.to_owned()],
        retention: async_nats::jetstream::stream::RetentionPolicy::WorkQueue,
        max_age: Duration::from_secs(config.events_stream_max_age_days * 86_400),
        max_bytes: config.events_stream_max_bytes,
        ..Default::default()
    })
    .await
    .map(|_| ())
    .map_err(|error| ApiError::unavailable(format!("events stream setup failed: {error}")))
}

/// Publishes one pending outbox event to JetStream and marks it published.
async fn publish_pending(
    db: &Db,
    js: &async_nats::jetstream::Context,
    row: &Value,
) -> Result<(), ApiError> {
    let event_id = row
        .get("id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    let payload = json!({
        "event_id": event_id,
        "timestamp": row.get("created_at").cloned().unwrap_or(Value::Null),
        "event_type": row.get("event_type").cloned().unwrap_or(Value::Null),
        "object_type": row.get("object_type").cloned().unwrap_or(Value::Null),
        "object_id": row.get("object_id").cloned().unwrap_or(Value::Null),
        "changes": row.get("changes").cloned().unwrap_or_else(|| json!({})),
        "metadata": row.get("metadata").cloned().unwrap_or_else(|| json!({})),
    });
    let bytes: Bytes = serde_json::to_vec(&payload)
        .map_err(|error| ApiError::internal(format!("failed to encode event: {error}")))?
        .into();
    js.publish(EVENTS_STREAM_SUBJECT, bytes)
        .await
        .map_err(|error| ApiError::unavailable(format!("event publish failed: {error}")))?
        .await
        .map_err(|error| ApiError::unavailable(format!("event publish ack failed: {error}")))?;

    let Some(key) = row
        .get("id")
        .and_then(Value::as_str)
        .and_then(|id| id.split_once(':').map(|(_, key)| key))
    else {
        return Ok(());
    };
    db.inner()
        .query(
            "UPDATE type::record('outbox_event', $key) SET status = 'published', published_at = time::now();",
        )
        .bind(("key", key.to_owned()))
        .await?
        .check()?;
    Ok(())
}

/// One relay cycle: publishes all pending events, returning how many were sent.
async fn relay_cycle(db: &Db, config: &Config) -> Result<u64, ApiError> {
    let sql =
        "SELECT * FROM outbox_event WHERE status = 'pending' ORDER BY created_at LIMIT $batch;";
    let mut response = db
        .inner()
        .query(sql)
        .bind(("batch", 500_i64))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    if rows.is_empty() {
        return Ok(0);
    }

    let client = async_nats::connect(&config.events_nats_url)
        .await
        .map_err(|error| ApiError::unavailable(format!("event relay connect failed: {error}")))?;
    let js = async_nats::jetstream::new(client);
    ensure_events_stream(&js, config).await?;

    // Publish sequentially to preserve per-object event order.
    for row in &rows {
        publish_pending(db, &js, row).await?;
    }
    Ok(rows.len() as u64)
}

/// Runs the event relay forever, polling the outbox on a fixed interval.
pub async fn run_relay(db: Db, config: Config) {
    let interval = Duration::from_secs(config.event_relay_interval_seconds);
    tracing::info!(
        interval_seconds = config.event_relay_interval_seconds,
        "event relay started"
    );
    loop {
        match relay_cycle(&db, &config).await {
            Ok(0) => {}
            Ok(published) => tracing::debug!(published, "event relay published events"),
            Err(error) => tracing::warn!(error = %error.message(), "event relay cycle failed"),
        }
        tokio::time::sleep(interval).await;
    }
}
