# Meadow PIM — MEF & Event System Integration

This document is the **integration contract** for external systems (middleware, ERP, webhooks, analytics). It describes:

- the **Meadow Exchange Format (MEF)** used to import/export data, and
- the **event system** that streams changes to consumers.

The authoritative technical plan lives in `plan/meadow-pim-architektur.md` and `plan/detail/integration-eventsystem.md`; this page is the practical, audience-facing reference.

## 1. Meadow Exchange Format (MEF)

MEF is a generic JSON exchange format that maps Meadow PIM core entities (objects and references). It is the single exchange format for the inbound/outbound connectors.

### 1.1 Document structure

```json
{
  "format_version": "1.0",
  "source": "middleware",
  "created_at": "2026-08-14T10:00:00Z",
  "objects": [
    {
      "id": "object_123",
      "type": "product",
      "attributes": {
        "name": { "en": "Example product", "de": "Beispielprodukt" },
        "price": 19.99,
        "width": { "value": 12.5, "unit": "mm" }
      },
      "status": "draft"
    }
  ],
  "references": [
    {
      "in": "object_123",
      "out": "object_456",
      "type": "related",
      "attributes": { "relation_strength": 0.8 }
    }
  ]
}
```

| Field | Type | Description |
| --- | --- | --- |
| `format_version` | `string` | Format version (currently `"1.0"`). |
| `source` | `string` | Source-system label (e.g. `"middleware"`, `"meadow"`). |
| `created_at` | `string` | ISO-8601 UTC timestamp of document creation. |
| `objects` | `array` | Objects to import/export (see below). |
| `references` | `array` | Standalone references between objects (see below). |

### 1.2 Object entry

| Field | Type | Description |
| --- | --- | --- |
| `id` | `string` | External identifier. Optional: an upsert/duplicate key; also becomes the **source reference** for idempotent dedup. Meadow's own export writes the core record id (`object:<ulid>`) here — re-importing such a document with `?match_by_id=true` updates those objects instead of creating duplicates. |
| `type` | `string` | Object type (e.g. `product`, `category`). |
| `attributes` | `object` | Attribute values keyed by attribute name. |
| `status` | `string` | Workflow status (`draft` unless specified). |

**Attribute values:**

- **Scalar** values are passed as-is (e.g. `"price": 19.99`, `"sku": "ABC-123"`).
- **Multilingual** values are nested objects keyed by language code (fallback per the language tree), e.g. `"name": { "en": "...", "de": "..." }`.
- **Quantified** values (number + unit) are `{ "value": <number>, "unit": "<code>" }`. On import the worker normalizes them to the canonical SI unit; on export they are re-emitted in the display unit.

### 1.3 Reference entry

| Field | Type | Description |
| --- | --- | --- |
| `in` / `out` | `string` | The two objects' `id`s. |
| `type` | `string` | Relation-definition **name** (e.g. `related`, `category_member`). The worker maps it to the core definition key `rel_<name>`. |
| `attributes` | `object` | Optional relation attributes (e.g. `position`). |

**`depends_on` (composition):** a new object is attached to the universal root when it
is created and an object has exactly one composition parent, so a `depends_on`
reference is applied as a **re-parenting** (`PATCH /objects/{id}/parent`) instead of a
second edge. That is how a MEF document expresses the composition tree (variants under
their product, vehicle generations under their model).

### 1.4 Semantics

- **Upsert by business key:** if `match_by_key` is set (e.g. `sku`), an existing object of the same type with the same key value is updated instead of created.
- **Idempotent dedup:** objects without a key are matched by their **source reference** — the MEF `id` plus the document `source` — against `GET /objects/by-source`, so re-importing the same record updates rather than duplicates.
- **Superset behavior:** attributes not present in the MEF document are left unchanged on update / empty on create.
- **Self-contained references:** both endpoints of a reference must be part of the document (`objects`), because the worker resolves `in`/`out` through the ids it just imported. A document may repeat an object that another document owns — the second import updates it.
- **Validation:** the core only validates the document shallowly (`objects`/`references` must be arrays); full processing happens in the worker.
- **Dates:** all timestamps are ISO-8601 with timezone, normalized to UTC on receipt.

### 1.5 Inbound (import)

1. Submit a MEF document to `POST /imports` (JSON body, or `multipart/form-data` with a `file` field; upload limit 100 MB).
2. The core stores the document in `import_document` and queues an `inbound_import` job; the response is `202 Accepted` with a `job_run_id`. The caller never blocks.
3. The worker (`meadow/worker/imports.py`) fetches the document, runs active `normalization` rules, converts quantified values to SI, upserts objects by business key or source reference, creates references, and reports progress at the `job_run`.

```bash
curl -X POST "$PIM_CORE_URL/imports?match_by_key=sku&source=erp" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  --data @mef-document.json
```

### 1.6 Outbound (export)

The `json_export` connector produces MEF documents from selected objects (`filter.type` / `filter.status`). The result is placed in the `job_run.result` as `{ "connector": "json_export", "exported": N, "document": { ... } }`.

## 2. Event System

Every CRUD operation in the core emits an **event** describing the change. Events are written **atomically** with the change into a transactional outbox and relayed to NATS JetStream, where consumers (webhooks, middleware, analytics) subscribe.

### 2.1 Event payload

```json
{
  "event_id": "outbox_event:<key>",
  "timestamp": "2026-08-14T10:00:00Z",
  "event_type": "object.updated",
  "object_type": "product",
  "object_id": "object:01H...",
  "changes": {
    "attributes.price": { "old": 19.99, "new": 24.99 }
  },
  "metadata": {
    "user_id": "user:admin",
    "request_id": "req_456"
  }
}
```

| Field | Type | Description |
| --- | --- | --- |
| `event_id` | `string` | Unique event id (the outbox record id). Use for **deduplication** (at-least-once delivery). |
| `timestamp` | `string` | ISO-8601 UTC time the event was created. |
| `event_type` | `string` | One of the event types below. |
| `object_type` | `string` | Business type of the affected object. |
| `object_id` | `string` | Full record id of the affected object (e.g. `object:<ulid>`). |
| `changes` | `object` | Structured old/new diff (e.g. `attributes.price`). |
| `metadata` | `object` | Context (`user_id`, `request_id`, …). No secrets/PII. |

### 2.2 Event types

| Event | Emitted on |
| --- | --- |
| `object.created` | A new object is created. |
| `object.updated` | Object attributes or status change. |
| `object.deleted` | An object is **soft** deleted. |
| `reference.created` | A reference is created. |
| `reference.deleted` | A reference is removed. |
| `attribute_definition.created` / `updated` / `deleted` | Attribute-definition changes. |
| `import.completed` | Summary event of a bulk import with `emit_events=false` (instead of per-object events). |

### 2.3 Transport & reliability

- **Transactional outbox:** the event is written into the `outbox_event` table **in the same SurrealDB transaction** as the change. The core never depends on NATS for the write to succeed.
- **Relay:** a background task (`event.rs::run_relay`) publishes `pending` outbox entries to NATS JetStream on subject **`events.crud`** (stream `EVENTS`, WorkQueue retention) every `EVENT_RELAY_INTERVAL_S` (default 5 s), then marks them `published`.
- **Outage behavior:** if NATS is unreachable, the relay logs a warning and events stay `pending`; writes keep succeeding. Once NATS returns, pending events are delivered. The outbox is the source; the stream is only the transport.
- **Ordering:** event order is preserved **per object** (`object_id`); globally the stream is unordered (multiple worker consumers).
- **Delivery contract:** **at-least-once**. Consumers must deduplicate by `event_id` (redelivery after timeout or failure is normal).
- **Retention:** the `EVENTS` stream respects `EVENTS_STREAM_MAX_AGE_DAYS` / `EVENTS_STREAM_MAX_BYTES`; acknowledged messages are removed (WorkQueue).

### 2.4 Consuming events

Consumers (e.g. the webhook worker, `meadow/worker/webhooks.py`) subscribe to `events.crud` with a durable consumer, apply per-connector filters (`event_types`, `object_types`, `attribute_changes`), optionally coalesce per `object_id` + `event_type`, deliver over HTTP with retry, and dead-letter to `events.dlq.webhook` on persistent failure.

To consume directly:

```bash
nats consumer create events CRUD --subject events.crud --ack explicit --deliver last
nats sub events.crud --consumer CRUD
```

```python
# nats-py: subscribe to events.crud (JetStream), ack only after processing
import asyncio, json
import nats

async def on_msg(msg):
    event = json.loads(msg.data)
    if event.get("event_id") in processed:      # deduplicate by event_id
        await msg.ack()
        return
    ...process...
    await msg.ack()

async def main():
    nc = await nats.connect("nats://localhost:4222")
    js = nc.jetstream()
    sub = await js.subscribe("events.crud", ordered=True)
    async for msg in sub.messages:
        await on_msg(msg)

asyncio.run(main())
```

## 3. Configuration reference

| Env | Default | Purpose |
| --- | --- | --- |
| `EVENTS_NATS_URL` | `nats://localhost:4222` | NATS URL for the event relay. |
| `EVENT_RELAY_INTERVAL_S` | `5` | Outbox poll interval (seconds). |
| `EVENTS_STREAM_MAX_AGE_DAYS` | `30` | `EVENTS` stream retention (days). |
| `EVENTS_STREAM_MAX_BYTES` | `1073741824` | `EVENTS` stream max bytes. |
