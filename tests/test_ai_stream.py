"""The streamed suggestion, end to end: core framing, worker abort, frontend reading.

`POST /ai/generate/stream` frames every event as one `data:` line
(`crates/pim-core/src/api/ai.rs`, `plan/detail/ki-assistenz.md` §13). The dialog
reads a stream it did not create, so the client must be tolerant: separators,
comments and a malformed piece may not end an otherwise healthy answer.

The worker half matters for the **history**: a stream the user stopped is marked
`cancelled`, so the history can say "cancelled" instead of "rejected" and no caller
has to match on the error text.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from meadow.ai.provider import ChatReply  # noqa: E402 - after the path fix
from meadow.frontend.api import sse_json  # noqa: E402 - after the path fix
from meadow.worker import ai_service  # noqa: E402 - after the path fix
from meadow.worker.ai_service import JsonCache  # noqa: E402 - after the path fix
from meadow.worker.protocol import AiGenerateEnvelope  # noqa: E402 - after the path fix


def test_a_data_line_carries_the_event() -> None:
    assert sse_json('data: {"type":"delta","text":"Hallo"}') == {
        "type": "delta",
        "text": "Hallo",
    }
    assert sse_json('data:{"type":"done","reply":{"ok":true}}') == {
        "type": "done",
        "reply": {"ok": True},
    }


def test_framing_and_broken_pieces_are_skipped() -> None:
    assert sse_json("") is None
    assert sse_json(": keep-alive") is None
    assert sse_json("event: ping") is None
    assert sse_json("data: not json") is None
    # A JSON scalar is not an event.
    assert sse_json("data: 42") is None


class _EmptyCache(JsonCache):
    """A cache that answers without the core (`JsonCache.get` would fetch)."""

    def __init__(self) -> None:
        super().__init__("http://127.0.0.1:1/none", None, label="test")

    async def get(self) -> Any:
        return []


class _CancelledPublisher:
    """The NATS half of a stream the user stopped before the first piece."""

    def __init__(self) -> None:
        self.cancelled = asyncio.Event()
        self.cancelled.set()

    async def publish(self, text: str) -> None:
        raise AssertionError(f"nothing may be published after a cancel: {text!r}")

    async def close(self) -> None:
        return None


def test_a_stopped_stream_answers_with_the_cancel_marker(monkeypatch) -> None:
    publisher = _CancelledPublisher()

    async def open_stream(nats_url: str, request_id: str, subject: str) -> _CancelledPublisher:
        return publisher

    async def answer(config: Any, messages: list[dict[str, str]], **kwargs: Any) -> ChatReply:
        return ChatReply(
            text="The rule checks the gross price.",
            model="m",
            latency_ms=5,
            usage={},
        )

    monkeypatch.setattr(ai_service, "_open_stream", open_stream)
    monkeypatch.setattr(ai_service, "chat_stream", answer)

    cache = _EmptyCache()
    envelope = AiGenerateEnvelope(
        config={
            "provider": "generic",
            "endpoint": "https://api.example.com/v1",
            "model": "some-model",
            "api_key": "sk-test",
        },
        request={
            "area": "rule",
            "mode": "explain",
            "description": "what does this rule do?",
            "current_script": "def run(obj, ctx, result):\n    return None\n",
        },
        request_id="01J9STREAM",
        stream_subject="ai.stream.01J9STREAM",
    )
    reply = asyncio.run(
        ai_service.handle_generate(
            envelope,
            core_url="http://127.0.0.1:1",
            api_key=None,
            nats_url="nats://127.0.0.1:1",
            vocabulary=cache,
            search_catalog=cache,
            connector_types=cache,
            export_profile_schema=cache,
            job_schema=cache,
            object_types=cache,
            languages=cache,
            permission_catalog=cache,
            state=ai_service.ServiceState(),
        )
    )
    assert reply["ok"] is False
    # The marker the audit copies and the history reads (`crates/pim-core/src/api/ai.rs`).
    assert reply["cancelled"] is True
    assert reply["errors"] == ["cancelled by the user"]
