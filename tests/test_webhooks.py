"""Tests for the webhook outbound connector (Phase 6)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import nats

from meadow.worker.webhooks import (
    WebhookConfig,
    _handle_message,
    matches_filter,
)


def config(**overrides: Any) -> WebhookConfig:
    payload: dict[str, Any] = {"url": "https://middleware.example/events"}
    payload.update(overrides)
    return WebhookConfig.model_validate(payload)


def event(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "event_id": "outbox_event:1",
        "timestamp": "2026-08-25T10:00:00Z",
        "event_type": "object.updated",
        "object_type": "product",
        "object_id": "object:1",
        "changes": {"attributes": {"price": 24.99}, "status": "draft"},
        "metadata": {"user_id": "user:1"},
    }
    base.update(overrides)
    return base


def test_matches_all_by_default() -> None:
    assert matches_filter(event(), config()) is True


def test_filter_event_types() -> None:
    cfg = config(filter={"event_types": ["object.created"]})
    assert matches_filter(event(event_type="object.updated"), cfg) is False
    assert matches_filter(event(event_type="object.created"), cfg) is True


def test_filter_object_types() -> None:
    cfg = config(filter={"object_types": ["category"]})
    assert matches_filter(event(object_type="product"), cfg) is False
    assert matches_filter(event(object_type="category"), cfg) is True


def test_filter_attribute_changes() -> None:
    cfg = config(filter={"attribute_changes": ["price"]})
    assert matches_filter(event(), cfg) is True
    cfg = config(filter={"attribute_changes": ["ean"]})
    assert matches_filter(event(), cfg) is False
    cfg = config(filter={"attribute_changes": ["status"]})
    assert matches_filter(event(), cfg) is True


class _FakeMsg:
    def __init__(self, data: bytes, *, num_delivered: int = 1) -> None:
        self.data = data
        self.metadata = _FakeMetadata(num_delivered)
        self.acked = False
        self.nakked = False
        self.nak_delay: float | None = None
        self.terminated = False

    async def ack(self) -> None:
        self.acked = True

    async def nak(self, delay: float | None = None) -> None:
        self.nakked = True
        self.nak_delay = delay

    async def term(self) -> None:
        self.terminated = True


class _FakeMetadata:
    def __init__(self, num_delivered: int) -> None:
        self.num_delivered = num_delivered


class _FakeJs:
    def __init__(self) -> None:
        self.published: list[tuple[str, bytes]] = []
        self._streams: set[str] = set()

    async def stream_info(self, name: str) -> None:
        if name not in self._streams:
            raise nats.js.errors.NotFoundError

    async def add_stream(self, name: str, subjects: list[str]) -> None:
        self._streams.add(name)

    async def publish(self, subject: str, payload: bytes) -> None:
        self.published.append((subject, payload))


async def test_deliver_success_acks() -> None:
    async def transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    msg = _FakeMsg(json.dumps(event()).encode())
    await _handle_message(client, _FakeJs(), config(), msg, 5, 1.0)
    await client.aclose()
    assert msg.acked is True
    assert msg.nakked is False


async def test_filtered_out_event_is_acked_without_delivery() -> None:
    delivered: list[httpx.Request] = []

    async def transport(request: httpx.Request) -> httpx.Response:
        delivered.append(request)
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    cfg = config(filter={"event_types": ["object.created"]})
    msg = _FakeMsg(json.dumps(event()).encode())
    await _handle_message(client, _FakeJs(), cfg, msg, 5, 1.0)
    await client.aclose()
    assert msg.acked is True
    assert delivered == []


async def test_delivery_failure_naks_for_retry() -> None:
    async def transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    msg = _FakeMsg(json.dumps(event()).encode())
    await _handle_message(client, _FakeJs(), config(), msg, 5, 1.0)
    await client.aclose()
    assert msg.nakked is True
    assert msg.acked is False


async def test_final_delivery_failure_dead_letters_and_terminates() -> None:
    async def transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    js = _FakeJs()
    msg = _FakeMsg(json.dumps(event()).encode(), num_delivered=5)
    await _handle_message(client, js, config(), msg, 5, 1.0)
    await client.aclose()
    assert msg.terminated is True
    assert msg.nakked is False
    assert len(js.published) == 1
    subject, payload = js.published[0]
    assert subject == "events.dlq.webhook"
    assert json.loads(payload)["event_id"] == "outbox_event:1"
