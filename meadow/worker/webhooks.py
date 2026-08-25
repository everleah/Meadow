"""Webhook outbound connector: delivers CRUD events to a middleware over HTTP.

Subscribes to `events.crud` (stream `EVENTS`), filters events, and delivers
matching events to a configured endpoint with **at-least-once** semantics:
delivery failures are retried via NATS redelivery (negative-ack) and finally
dead-lettered to a dedicated DLQ stream (manual cleanup, like the job queue).
The middleware must deduplicate on `event_id` (redelivery is normal).

When coalescing is enabled, events are buffered per `object_id` + `event_type`
within a fixed window and only the **last** event is sent (with `coalesced` and
`bundled_count` flags); the buffered messages are acknowledged on flush.
Configuration is read from environment variables (`WEBHOOK_URL`, …) in the MVP;
persisting connector configuration is a later step.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any

import httpx
import nats
import nats.js.api as jsapi
from pydantic import BaseModel, Field

from meadow.worker.log_sender import LogSender

logger = logging.getLogger(__name__)

#: NATS stream/subject that carries CRUD events (published by the core relay).
EVENTS_STREAM = "EVENTS"
EVENTS_SUBJECT = "events.crud"
EVENTS_DURABLE = "event-webhook"
#: Dead-letter stream for events that exhausted all delivery attempts.
EVENTS_DLQ_STREAM = "EVENTS_DLQ"
EVENTS_DLQ_SUBJECT = "events.dlq.webhook"

#: Ack-wait window before JetStream redelivers an unacked event.
WEBHOOK_ACK_WAIT_S = 300.0
#: Maximum delivery attempts before dead-lettering.
WEBHOOK_MAX_DELIVER = 5
#: Delay before a failed event is redelivered after a negative ack.
WEBHOOK_NAK_DELAY_S = 5.0


class WebhookFilter(BaseModel):
    """Event selection rules (all lists empty = match everything)."""

    event_types: list[str] = Field(default_factory=list)
    object_types: list[str] = Field(default_factory=list)
    attribute_changes: list[str] = Field(default_factory=list)


class WebhookCoalesce(BaseModel):
    """Coalescing: per `object_id` + `event_type`, only the last event is sent."""

    enabled: bool = False
    window_seconds: float = 30.0


class WebhookConfig(BaseModel):
    """Configuration of one webhook endpoint."""

    url: str
    method: str = "POST"
    headers: dict[str, str] = Field(default_factory=dict)
    filter: WebhookFilter = Field(default_factory=WebhookFilter)
    coalesce: WebhookCoalesce = Field(default_factory=WebhookCoalesce)
    timeout_s: float = 30.0


def matches_filter(event: dict[str, Any], config: WebhookConfig) -> bool:
    """Whether an event passes the connector's filter."""
    event_filter = config.filter
    if event_filter.event_types and event.get("event_type") not in event_filter.event_types:
        return False
    if event_filter.object_types and event.get("object_type") not in event_filter.object_types:
        return False
    if event_filter.attribute_changes:
        changed = _changed_attribute_names(event)
        if not changed.intersection(event_filter.attribute_changes):
            return False
    return True


def _changed_attribute_names(event: dict[str, Any]) -> set[str]:
    """Collects the attribute names touched by an event's `changes` object."""
    names: set[str] = set()
    changes = event.get("changes")
    if not isinstance(changes, dict):
        return names
    attributes = changes.get("attributes")
    if isinstance(attributes, dict):
        names.update(str(key) for key in attributes)
    if "status" in changes:
        names.add("status")
    return names


def _num_delivered(msg: Any) -> int:
    metadata = getattr(msg, "metadata", None)
    num = getattr(metadata, "num_delivered", 0)
    return int(num) if num else 1


def _coalesce_key(event: dict[str, Any]) -> tuple[str, str]:
    """The coalescing key: one buffer slot per object + event type."""
    return (str(event.get("object_id") or ""), str(event.get("event_type") or ""))


class _CoalesceBuffer:
    """Holds the latest event per key until the flush loop drains it."""

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def add(self, key: tuple[str, str], event: dict[str, Any], msg: Any) -> None:
        async with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self._entries[key] = {"event": event, "msgs": [msg]}
            else:
                entry["event"] = event
                entry["msgs"].append(msg)

    async def drain(self) -> list[dict[str, Any]]:
        async with self._lock:
            entries = list(self._entries.values())
            self._entries.clear()
        return entries


async def _ensure_dlq_stream(js) -> None:
    """Ensures the webhook dead-letter stream exists (idempotent)."""
    try:
        await js.stream_info(EVENTS_DLQ_STREAM)
    except nats.js.errors.NotFoundError:
        await js.add_stream(name=EVENTS_DLQ_STREAM, subjects=[EVENTS_DLQ_SUBJECT])


async def _dead_letter(js, event: dict[str, Any], error: str, num_delivered: int) -> None:
    """Publishes a failed event to the webhook DLQ stream."""
    await _ensure_dlq_stream(js)
    payload = {
        "event_id": event.get("event_id"),
        "event_type": event.get("event_type"),
        "object_id": event.get("object_id"),
        "event": event,
        "error": error,
        "num_delivered": num_delivered,
    }
    await js.publish(EVENTS_DLQ_SUBJECT, json.dumps(payload).encode())


async def _deliver(
    client: httpx.AsyncClient,
    config: WebhookConfig,
    event: dict[str, Any],
) -> None:
    """Sends one event to the middleware; raises on a non-2xx response."""
    response = await client.request(
        config.method,
        config.url,
        json=event,
        headers=config.headers or None,
        timeout=config.timeout_s,
    )
    response.raise_for_status()


async def _ack_many(msgs: list[Any]) -> None:
    for msg in msgs:
        await msg.ack()


async def _nak_many(msgs: list[Any], delay: float) -> None:
    for msg in msgs:
        await msg.nak(delay=delay)


async def _term_many(msgs: list[Any]) -> None:
    for msg in msgs:
        await msg.term()


async def _deliver_or_retry(
    client: httpx.AsyncClient,
    js,
    config: WebhookConfig,
    msg: Any,
    event: dict[str, Any],
    max_deliver: int,
    nak_delay: float,
) -> None:
    """Delivers one event; acks, naks (retry), or dead-letters + terms."""
    num_delivered = _num_delivered(msg)
    try:
        await _deliver(client, config, event)
        await msg.ack()
    except Exception as exc:  # noqa: BLE001 - transient delivery failure
        logger.warning(
            "webhook delivery failed for %s (attempt %d): %s",
            event.get("event_id"),
            num_delivered,
            exc,
        )
        if num_delivered >= max_deliver:
            try:
                await _dead_letter(js, event, str(exc), num_delivered)
            except Exception:  # noqa: BLE001 - fall back to a retry
                logger.exception("webhook dead-letter publish failed")
                await msg.nak(delay=nak_delay)
                return
            await msg.term()
        else:
            await msg.nak(delay=nak_delay)


async def _deliver_coalesced(
    client: httpx.AsyncClient,
    js,
    config: WebhookConfig,
    entry: dict[str, Any],
    max_deliver: int,
    nak_delay: float,
) -> None:
    """Delivers the latest buffered event and settles all messages for its key."""
    msgs: list[Any] = entry["msgs"]
    latest = dict(entry["event"])
    latest["coalesced"] = len(msgs) > 1
    latest["bundled_count"] = len(msgs)

    num_delivered = _num_delivered(msgs[-1])
    try:
        await _deliver(client, config, latest)
        await _ack_many(msgs)
    except Exception as exc:  # noqa: BLE001 - transient delivery failure
        logger.warning(
            "webhook coalesced delivery failed for %s (attempt %d): %s",
            latest.get("event_id"),
            num_delivered,
            exc,
        )
        if num_delivered >= max_deliver:
            try:
                await _dead_letter(js, latest, str(exc), num_delivered)
            except Exception:  # noqa: BLE001 - fall back to a retry
                logger.exception("webhook dead-letter publish failed")
                await _nak_many(msgs, nak_delay)
                return
            await _term_many(msgs)
        else:
            await _nak_many(msgs, nak_delay)


async def _flush_loop(
    client: httpx.AsyncClient,
    js,
    config: WebhookConfig,
    buffer: _CoalesceBuffer,
    max_deliver: int,
    nak_delay: float,
) -> None:
    """Periodically delivers the buffered coalesced events."""
    window = max(config.coalesce.window_seconds, 0.1)
    while True:
        await asyncio.sleep(window)
        for entry in await buffer.drain():
            await _deliver_coalesced(client, js, config, entry, max_deliver, nak_delay)


async def serve_webhooks(
    nats_url: str,
    config: WebhookConfig,
    *,
    subject: str = EVENTS_SUBJECT,
    stream: str = EVENTS_STREAM,
    durable: str = EVENTS_DURABLE,
    ack_wait: float = WEBHOOK_ACK_WAIT_S,
    max_deliver: int = WEBHOOK_MAX_DELIVER,
    nak_delay: float = WEBHOOK_NAK_DELAY_S,
    log_sender: LogSender | None = None,
) -> None:
    """Consumes CRUD events and delivers matching ones to the webhook endpoint.

    Runs until cancelled; intended to be started as a separate asyncio task so
    long webhook deliveries never block job processing.
    """
    nc = await nats.connect(nats_url)
    js = nc.jetstream()
    consumer_config = jsapi.ConsumerConfig(ack_wait=ack_wait, max_deliver=max_deliver)
    psub = await js.pull_subscribe(
        subject,
        durable=durable,
        stream=stream,
        config=consumer_config,
    )
    if log_sender is not None:
        log_sender.enqueue(
            service="pim-worker",
            level="INFO",
            category="integration",
            message=f"webhook consumer subscribed to {subject} -> {config.url}",
        )
    logger.info("webhook consumer subscribed to %r -> %s", subject, config.url)

    async with httpx.AsyncClient() as client:
        flusher: asyncio.Task | None = None
        if config.coalesce.enabled:
            buffer = _CoalesceBuffer()
            flusher = asyncio.create_task(
                _flush_loop(client, js, config, buffer, max_deliver, nak_delay)
            )
        else:
            buffer = None
        try:
            while True:
                try:
                    msgs = await psub.fetch(1, timeout=5)
                except TimeoutError:
                    continue
                for msg in msgs:
                    await _handle_message(client, js, config, msg, max_deliver, nak_delay, buffer)
        finally:
            if flusher is not None:
                flusher.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await flusher
            await nc.close()


async def _handle_message(
    client: httpx.AsyncClient,
    js,
    config: WebhookConfig,
    msg: Any,
    max_deliver: int,
    nak_delay: float,
    buffer: _CoalesceBuffer | None = None,
) -> None:
    """Handles one event message: filter, buffer (coalesce) or deliver."""
    try:
        event = json.loads(msg.data)
    except json.JSONDecodeError, TypeError:
        logger.error("invalid event payload; terminating message")
        await msg.term()
        return

    if not matches_filter(event, config):
        await msg.ack()
        return

    if buffer is not None:
        await buffer.add(_coalesce_key(event), event, msg)
        return

    await _deliver_or_retry(client, js, config, msg, event, max_deliver, nak_delay)


__all__ = [
    "EVENTS_DLQ_STREAM",
    "EVENTS_DLQ_SUBJECT",
    "EVENTS_DURABLE",
    "EVENTS_STREAM",
    "EVENTS_SUBJECT",
    "WebhookCoalesce",
    "WebhookConfig",
    "WebhookFilter",
    "matches_filter",
    "serve_webhooks",
]
