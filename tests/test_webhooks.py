"""Tests for the webhook outbound connector (Phase 6)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import nats

from meadow.worker.webhooks import (
    WebhookConfig,
    _CoalesceBuffer,
    _config_signature,
    _Connector,
    _deliver_coalesced,
    _DeliveryTracker,
    _handle_message,
    matches_filter,
)


def config(**overrides: Any) -> WebhookConfig:
    payload: dict[str, Any] = {"url": "https://middleware.example/events"}
    payload.update(overrides)
    return WebhookConfig.model_validate(payload)


def connector(**overrides: Any) -> _Connector:
    return _Connector(config=config(**overrides))


def tracker() -> _DeliveryTracker:
    return _DeliveryTracker(nak_delay=1.0)


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
    await _handle_message(client, _FakeJs(), [connector()], msg, 5, tracker())
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
    await _handle_message(client, _FakeJs(), [_Connector(config=cfg)], msg, 5, tracker())
    await client.aclose()
    assert msg.acked is True
    assert delivered == []


async def test_delivery_failure_naks_for_retry() -> None:
    async def transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    msg = _FakeMsg(json.dumps(event()).encode())
    await _handle_message(client, _FakeJs(), [connector()], msg, 5, tracker())
    await client.aclose()
    assert msg.nakked is True
    assert msg.acked is False


async def test_final_delivery_failure_dead_letters_and_terminates() -> None:
    async def transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    js = _FakeJs()
    msg = _FakeMsg(json.dumps(event()).encode(), num_delivered=5)
    await _handle_message(client, js, [connector()], msg, 5, tracker())
    await client.aclose()
    assert msg.terminated is True
    assert msg.nakked is False
    assert len(js.published) == 1
    subject, payload = js.published[0]
    assert subject == "events.dlq.webhook"
    body = json.loads(payload)
    assert body["event_id"] == "outbox_event:1"
    assert body["connector"] == "webhook"


async def test_fan_out_delivers_to_all_matching_connectors() -> None:
    delivered: list[httpx.Request] = []

    async def transport(request: httpx.Request) -> httpx.Response:
        delivered.append(request)
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    one = connector(url="https://one.example/events")
    two = connector(url="https://two.example/events")
    msg = _FakeMsg(json.dumps(event()).encode())
    await _handle_message(client, _FakeJs(), [one, two], msg, 5, tracker())
    await client.aclose()

    assert [str(request.url) for request in delivered] == [
        "https://one.example/events",
        "https://two.example/events",
    ]
    assert msg.acked is True


async def test_coalesce_buffer_last_wins() -> None:
    buffer = _CoalesceBuffer()
    key = ("object:1", "object.updated")
    msg1 = _FakeMsg(json.dumps(event(event_id="outbox_event:1")).encode())
    msg2 = _FakeMsg(json.dumps(event(event_id="outbox_event:2")).encode())
    await buffer.add(key, event(event_id="outbox_event:1"), msg1)
    await buffer.add(key, event(event_id="outbox_event:2"), msg2)

    entries = await buffer.drain()
    assert len(entries) == 1
    assert entries[0]["event"]["event_id"] == "outbox_event:2"
    assert [m.data for m in entries[0]["msgs"]] == [msg1.data, msg2.data]


async def test_handle_message_buffers_when_coalescing() -> None:
    delivered: list[httpx.Request] = []

    async def transport(request: httpx.Request) -> httpx.Response:
        delivered.append(request)
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    coalesced = _Connector(
        config=config(coalesce={"enabled": True, "window_seconds": 30}),
        buffer=_CoalesceBuffer(),
    )
    msg = _FakeMsg(json.dumps(event()).encode())
    await _handle_message(client, _FakeJs(), [coalesced], msg, 5, tracker())
    await client.aclose()

    assert delivered == []
    assert msg.acked is False
    assert msg.nakked is False
    entries = await coalesced.buffer.drain()
    assert len(entries) == 1


async def test_deliver_coalesced_sends_latest_with_flags_and_acks_all() -> None:
    delivered: list[httpx.Request] = []

    async def transport(request: httpx.Request) -> httpx.Response:
        delivered.append(request)
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    coalesced = _Connector(config=config(coalesce={"enabled": True}))
    msg1 = _FakeMsg(json.dumps(event(event_id="outbox_event:1")).encode())
    msg2 = _FakeMsg(json.dumps(event(event_id="outbox_event:2")).encode())
    entry = {"event": event(event_id="outbox_event:2"), "msgs": [msg1, msg2]}

    tr = tracker()
    await tr.open(msg1, 1)
    await tr.open(msg2, 1)
    await _deliver_coalesced(client, _FakeJs(), coalesced, entry, 5, tr)
    await client.aclose()

    assert len(delivered) == 1
    body = json.loads(delivered[0].content)
    assert body["event_id"] == "outbox_event:2"
    assert body["coalesced"] is True
    assert body["bundled_count"] == 2
    assert msg1.acked is True
    assert msg2.acked is True


def test_config_signature_is_order_independent() -> None:
    one = config(url="https://one.example", key="one")
    two = config(url="https://two.example", key="two")
    assert _config_signature([one, two]) == _config_signature([two, one])


def test_config_signature_changes_on_reconfiguration() -> None:
    original = config(url="https://one.example", key="one")
    changed = config(url="https://one.example/changed", key="one")
    assert _config_signature([original]) != _config_signature([changed])
