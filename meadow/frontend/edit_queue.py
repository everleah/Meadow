"""Sequential editing: a queue of objects reviewed one after another.

The grid starts a queue for the selected rows; the product form then shows a
progress banner with "Save & next" while the current object is part of it. The
queue lives in the per-user storage (`app.storage.user`), so it survives the
navigation between the objects — no new endpoint and no server-side state.

The helpers that only need the id list are pure functions, so they are unit
tested (`next_object_id`, `position_of`).
"""

from __future__ import annotations

from nicegui import app

#: User-storage key of the queue (kept across a logout, see `auth.SESSION_KEYS`).
QUEUE_KEY = "edit_queue"


def next_object_id(ids: list[str], object_id: str) -> str | None:
    """Returns the queue entry **after** ``object_id`` (``None`` = last one)."""
    position = position_of(ids, object_id)
    if position is None or position + 1 >= len(ids):
        return None
    return ids[position + 1]


def position_of(ids: list[str], object_id: str) -> int | None:
    """Returns the zero-based position of ``object_id`` in the queue."""
    try:
        return ids.index(str(object_id))
    except ValueError:
        return None


def start_queue(ids: list[str], origin: str) -> None:
    """Stores a new queue (``origin`` is where the flow returns to)."""
    app.storage.user[QUEUE_KEY] = {"ids": [str(i) for i in ids], "origin": origin}


def stop_queue() -> None:
    """Removes the queue (leaving sequential editing)."""
    app.storage.user.pop(QUEUE_KEY, None)


def queue_ids() -> list[str]:
    """The ids of the active queue (empty when none is active)."""
    queue = app.storage.user.get(QUEUE_KEY) or {}
    return [str(entry) for entry in queue.get("ids") or []]


def queue_origin() -> str:
    """The URL the sequential flow returns to (falls back to the grid)."""
    queue = app.storage.user.get(QUEUE_KEY) or {}
    return str(queue.get("origin") or "/products")


def is_active(object_id: str) -> bool:
    """Whether ``object_id`` is part of the active queue."""
    return position_of(queue_ids(), object_id) is not None
