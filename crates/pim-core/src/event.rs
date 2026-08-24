//! Event system groundwork (Phase 4 preparation for Phase 6).
//!
//! CRUD events follow `plan/detail/integration-eventsystem.md`: every CRUD
//! operation emits an event that is written atomically with the operation into
//! the `outbox_event` table (transactional outbox) and later relayed to NATS
//! JetStream on `events.crud`. This module defines the event types, the outbox
//! status, the JetStream subject, and the outbox write helper that Phase 6
//! will fold into the CRUD transactions.

use serde_json::{Value, json};

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

/// Inserts a `pending` outbox event.
///
/// Phase 6 folds this write into the same transaction as the CRUD operation so
/// the event is atomically persisted with its change; until then it is a
/// standalone insert that establishes the outbox contract.
pub async fn enqueue_outbox_event(db: &Db, event: OutboxEvent) -> Result<(), ApiError> {
    let changes = if event.changes.is_object() {
        event.changes
    } else {
        json!({})
    };
    let metadata = if event.metadata.is_object() {
        event.metadata
    } else {
        json!({})
    };
    let content = json!({
        "event_type": event.event_type,
        "object_type": event.object_type,
        "object_id": event.object_id,
        "changes": changes,
        "metadata": metadata,
        "status": STATUS_PENDING,
    });
    db.inner()
        .query("CREATE outbox_event CONTENT $content;")
        .bind(("content", content))
        .await?
        .check()?;
    Ok(())
}
