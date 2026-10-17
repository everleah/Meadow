"""The suggestion dialog only offers modes that can work right now.

Found in a browser check (2026-10-17): a new rule has an empty editor, yet the mode
list still offered `explain`; the click ended in `current_script is required for the
mode explain` (`crates/pim-core/src/api/ai.rs`). The page test for the users page
(`tests/test_users_page.py`) drives the dialog itself; this file holds the rule the
list is built from.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from meadow.frontend.ai_assist import MODES, offered_modes, stream_request_id  # noqa: E402


def test_an_empty_editor_does_not_offer_explain() -> None:
    assert offered_modes(MODES, lambda: "") == ("create", "edit")
    assert offered_modes(MODES, lambda: "   \n") == ("create", "edit")
    assert offered_modes(MODES, None) == ("create", "edit")


def test_a_filled_editor_keeps_every_mode() -> None:
    assert offered_modes(MODES, lambda: "def rule(ctx):\n    return None") == MODES


def test_the_caller_can_still_withhold_modes() -> None:
    """`users.py` restricts the list to groups, which have no prose field."""
    assert offered_modes(("create", "edit"), lambda: '{"permissions": []}') == (
        "create",
        "edit",
    )


def test_a_stop_clicked_before_the_first_event_waits_for_the_id() -> None:
    """The id is the core's first stream event — the click can come earlier."""
    state: dict[str, object] = {"id": "", "running": True}

    async def scenario() -> str:
        async def arrive() -> None:
            await asyncio.sleep(0.1)
            state["id"] = "01J9STREAM"

        task = asyncio.create_task(arrive())
        request_id = await stream_request_id(state)
        await task
        return request_id

    assert asyncio.run(scenario()) == "01J9STREAM"


def test_a_stop_after_the_run_does_not_wait() -> None:
    state: dict[str, object] = {"id": "", "running": False}
    started = time.monotonic()
    assert asyncio.run(stream_request_id(state)) == ""
    assert time.monotonic() - started < 0.2
