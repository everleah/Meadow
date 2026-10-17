"""Tests for the webhook outbound connector (Phase 6)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import nats

from meadow.worker.webhooks import (
    WebhookConfig,
    WebhookRetry,
    _CoalesceBuffer,
    _config_signature,
    _Connector,
    _deliver_coalesced,
    _DeliveryTracker,
    _handle_message,
    load_failure_message,
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


def test_a_rejected_credential_names_the_value_that_has_to_match() -> None:
    """A standing configuration problem is explained, not dumped as a traceback."""
    request = httpx.Request("GET", "http://127.0.0.1:8080/connectors")
    error = httpx.HTTPStatusError(
        "401", request=request, response=httpx.Response(401, request=request)
    )

    message = load_failure_message(error)

    assert "401" in message
    assert "LOG_INGEST_API_KEY" in message
    assert "keeping the current config" in message


def test_other_http_failures_report_the_status() -> None:
    """A server error is a different story than a rejected key."""
    request = httpx.Request("GET", "http://127.0.0.1:8080/connectors")
    error = httpx.HTTPStatusError(
        "500", request=request, response=httpx.Response(500, request=request)
    )

    assert load_failure_message(error) == (
        "core answered HTTP 500 while loading webhook connectors"
    )


def test_transport_errors_keep_their_type_and_message() -> None:
    """A broken connection is not a credential problem and says what happened."""
    message = load_failure_message(httpx.ConnectError("connection refused"))

    assert message.startswith("ConnectError: ")
    assert "connection refused" in message


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
    await _handle_message(client, _FakeJs(), [connector()], msg, tracker())
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
    await _handle_message(client, _FakeJs(), [_Connector(config=cfg)], msg, tracker())
    await client.aclose()
    assert msg.acked is True
    assert delivered == []


async def test_delivery_failure_naks_for_retry() -> None:
    async def transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    msg = _FakeMsg(json.dumps(event()).encode())
    await _handle_message(client, _FakeJs(), [connector()], msg, tracker())
    await client.aclose()
    assert msg.nakked is True
    assert msg.acked is False


async def test_final_delivery_failure_dead_letters_and_terminates() -> None:
    async def transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    js = _FakeJs()
    msg = _FakeMsg(json.dumps(event()).encode(), num_delivered=5)
    await _handle_message(client, js, [connector()], msg, tracker())
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
    await _handle_message(client, _FakeJs(), [one, two], msg, tracker())
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
    await _handle_message(client, _FakeJs(), [coalesced], msg, tracker())
    await client.aclose()

    assert delivered == []
    assert msg.acked is False
    assert msg.nakked is False
    assert coalesced.buffer is not None
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
    await _deliver_coalesced(client, _FakeJs(), coalesced, entry, tr)
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


def test_config_signature_covers_the_retry_policy() -> None:
    original = config(url="https://one.example", key="one")
    changed = config(url="https://one.example", key="one", retry={"max_attempts": 2})
    assert _config_signature([original]) != _config_signature([changed])


def test_retry_policy_defaults_and_clamping() -> None:
    assert config().retry.max_attempts == 5
    # A list since Stage 3: one wait per further attempt, the last one repeats.
    assert config().retry.backoff_seconds == [5.0]
    assert config(retry={"max_attempts": 0}).retry.max_attempts == 1
    assert config(retry={"max_attempts": 99}).retry.max_attempts == 10
    assert config(retry={"backoff_seconds": 0}).retry.backoff_seconds == [0.1]
    assert config(retry={"backoff_seconds": []}).retry.backoff_seconds == [5.0]


def test_retry_policy_accepts_a_single_stored_number() -> None:
    """The older form (one number) keeps its meaning: the same wait every time."""
    retry = WebhookRetry.model_validate({"max_attempts": 3, "backoff_seconds": 120})
    assert retry.backoff_seconds == [120.0]


def test_the_backoff_list_keeps_its_entries_and_clamps_them() -> None:
    retry = WebhookRetry.model_validate({"backoff_seconds": [30, 60, 120]})
    assert retry.backoff_seconds == [30.0, 60.0, 120.0]
    assert WebhookRetry.model_validate({"backoff_seconds": [0, 60]}).backoff_seconds == [
        0.1,
        60.0,
    ]


async def test_connector_attempt_limit_governs_dead_lettering() -> None:
    """A connector with `max_attempts: 5` dead-letters on the fifth attempt."""

    async def transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    js = _FakeJs()
    msg = _FakeMsg(json.dumps(event()).encode(), num_delivered=5)
    await _handle_message(client, js, [connector(retry={"max_attempts": 5})], msg, tracker())
    await client.aclose()
    assert msg.terminated is True
    assert len(js.published) == 1


async def test_strict_connector_dead_letters_earlier() -> None:
    async def transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    js = _FakeJs()
    msg = _FakeMsg(json.dumps(event()).encode(), num_delivered=2)
    await _handle_message(client, js, [connector(retry={"max_attempts": 2})], msg, tracker())
    await client.aclose()
    assert msg.terminated is True
    assert len(js.published) == 1


async def test_payload_mode_object_adds_the_object_to_the_event() -> None:
    """`payload_mode: object`: the event stays the envelope, the object is added.

    Keeping the event envelope is what lets the middleware deduplicate on
    `event_id` — redelivery is normal with at-least-once.
    """
    sent: list[dict[str, Any]] = []

    async def transport(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200)

    async def load_object(object_id: str) -> dict[str, Any]:
        return {"id": object_id, "attributes": {"name": "Loaded"}}

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    msg = _FakeMsg(json.dumps(event()).encode())
    await _handle_message(
        client,
        _FakeJs(),
        [_Connector(config=config(payload_mode="object"), load_object=load_object)],
        msg,
        tracker(),
    )
    await client.aclose()
    assert sent[0]["event_id"] == "outbox_event:1"
    assert sent[0]["event_type"] == "object.updated"
    assert sent[0]["object"]["attributes"]["name"] == "Loaded"
    assert msg.acked is True


async def test_payload_mode_object_keeps_the_event_for_a_deleted_object() -> None:
    sent: list[dict[str, Any]] = []

    async def transport(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200)

    async def load_object(object_id: str) -> dict[str, Any]:
        raise AssertionError("a deleted object must not be fetched")

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    msg = _FakeMsg(json.dumps(event(event_type="object.deleted")).encode())
    await _handle_message(
        client,
        _FakeJs(),
        [_Connector(config=config(payload_mode="object"), load_object=load_object)],
        msg,
        tracker(),
    )
    await client.aclose()
    assert "object" not in sent[0]
    assert sent[0]["event_type"] == "object.deleted"


async def test_a_vanished_object_does_not_block_the_delivery() -> None:
    """A `404` on the object read means "gone": the event is sent, not retried."""
    sent: list[dict[str, Any]] = []

    async def transport(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200)

    async def load_object(object_id: str) -> dict[str, Any]:
        request = httpx.Request("GET", f"http://core/objects/{object_id}")
        return httpx.Response(404, request=request).raise_for_status()  # type: ignore[return-value]

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    msg = _FakeMsg(json.dumps(event()).encode())
    await _handle_message(
        client,
        _FakeJs(),
        [_Connector(config=config(payload_mode="object"), load_object=load_object)],
        msg,
        tracker(),
    )
    await client.aclose()
    assert "object" not in sent[0]
    assert msg.acked is True


async def test_a_failing_object_read_is_retried() -> None:
    """Any other fetch failure raises: the delivery retries instead of degrading."""
    sent: list[dict[str, Any]] = []

    async def transport(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200)

    async def load_object(object_id: str) -> dict[str, Any]:
        request = httpx.Request("GET", f"http://core/objects/{object_id}")
        return httpx.Response(500, request=request).raise_for_status()  # type: ignore[return-value]

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    msg = _FakeMsg(json.dumps(event()).encode())
    await _handle_message(
        client,
        _FakeJs(),
        [_Connector(config=config(payload_mode="object"), load_object=load_object)],
        msg,
        tracker(),
    )
    await client.aclose()
    assert sent == []
    assert msg.nakked is True


async def test_the_connector_status_is_reported_only_when_it_changes() -> None:
    """A webhook fires constantly; the list needs "healthy", not every delivery."""
    reports: list[tuple[str, bool, str | None]] = []

    async def report(connector_id: str, ok: bool, error: str | None) -> None:
        reports.append((connector_id, ok, error))

    async def transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    connector = _Connector(config=config(id="connector:webhook"), report_status=report)
    for _ in range(3):
        msg = _FakeMsg(json.dumps(event()).encode())
        await _handle_message(client, _FakeJs(), [connector], msg, tracker())
    await client.aclose()
    assert reports == [("connector:webhook", True, None)]


async def test_a_failure_and_the_recovery_are_reported_but_not_every_retry() -> None:
    reports: list[tuple[str, bool, str | None]] = []
    failing = {"now": True}

    async def report(connector_id: str, ok: bool, error: str | None) -> None:
        reports.append((connector_id, ok, error))

    async def transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500 if failing["now"] else 200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    connector = _Connector(config=config(id="connector:webhook"), report_status=report)
    for _ in range(2):
        await _handle_message(
            client, _FakeJs(), [connector], _FakeMsg(json.dumps(event()).encode()), tracker()
        )
    failing["now"] = False
    await _handle_message(
        client, _FakeJs(), [connector], _FakeMsg(json.dumps(event()).encode()), tracker()
    )
    await client.aclose()
    assert [report[1] for report in reports] == [False, True]
    assert "500" in str(reports[0][2])


async def test_a_failing_status_report_is_retried_on_the_next_delivery() -> None:
    attempts: list[bool] = []

    async def report(connector_id: str, ok: bool, error: str | None) -> None:
        attempts.append(ok)
        if len(attempts) == 1:
            raise httpx.ConnectError("core not reachable")

    async def transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    connector = _Connector(config=config(id="connector:webhook"), report_status=report)
    for _ in range(2):
        await _handle_message(
            client, _FakeJs(), [connector], _FakeMsg(json.dumps(event()).encode()), tracker()
        )
    await client.aclose()
    assert attempts == [True, True]


async def test_connector_backoff_is_used_for_the_redelivery_delay() -> None:
    async def transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    cfg = config(retry={"max_attempts": 5, "backoff_seconds": 120})
    msg = _FakeMsg(json.dumps(event()).encode())
    await _handle_message(client, _FakeJs(), [_Connector(config=cfg)], msg, tracker())
    await client.aclose()
    assert msg.nakked is True
    assert msg.nak_delay == 120


async def test_the_backoff_list_steps_through_the_attempts() -> None:
    """`[5, 30, 120]`: the first retry waits 5 s, the third (and every later) 120 s."""

    async def transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    cfg = config(retry={"max_attempts": 5, "backoff_seconds": [5, 30, 120]})
    first = _FakeMsg(json.dumps(event()).encode(), num_delivered=1)
    await _handle_message(client, _FakeJs(), [_Connector(config=cfg)], first, tracker())
    third = _FakeMsg(json.dumps(event()).encode(), num_delivered=3)
    await _handle_message(client, _FakeJs(), [_Connector(config=cfg)], third, tracker())
    await client.aclose()
    assert first.nak_delay == 5
    assert third.nak_delay == 120


async def test_shared_message_uses_the_longest_requested_backoff() -> None:
    async def transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    fast = connector(url="https://fast.example", retry={"backoff_seconds": 10})
    slow = connector(url="https://slow.example", retry={"backoff_seconds": 90})
    msg = _FakeMsg(json.dumps(event()).encode())
    await _handle_message(client, _FakeJs(), [fast, slow], msg, tracker())
    await client.aclose()
    assert msg.nakked is True
    assert msg.nak_delay == 90


async def test_coalesced_delivery_uses_the_connector_attempt_limit() -> None:
    async def transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    js = _FakeJs()
    cramped = _Connector(
        config=config(coalesce={"enabled": True}, retry={"max_attempts": 1}),
    )
    msg = _FakeMsg(json.dumps(event()).encode())
    entry = {"event": event(), "msgs": [msg]}
    tr = tracker()
    await tr.open(msg, 1)
    await _deliver_coalesced(client, js, cramped, entry, tr)
    await client.aclose()
    assert msg.terminated is True
    assert len(js.published) == 1
