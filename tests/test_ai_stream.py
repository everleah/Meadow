"""The streamed suggestion: the `text/event-stream` framing of the core.

`POST /ai/generate/stream` frames every event as one `data:` line
(`crates/pim-core/src/api/ai.rs`, `plan/detail/ki-assistenz.md` §13). The dialog
reads a stream it did not create, so the client must be tolerant: separators,
comments and a malformed piece may not end an otherwise healthy answer.
"""

from __future__ import annotations

from meadow.frontend.api import sse_json


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
