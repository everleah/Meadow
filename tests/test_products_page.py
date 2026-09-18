"""Page-level tests for `/products` — the net for the `products_page` refactor.

`products_page` in `meadow/frontend/grid.py` is a single ~2700-line page builder
(`plan/detail/ai-code-quality.md` §4.5), so moving sections around needs a check
that the page's *behaviour* still holds — not only that a helper still returns its
value. These tests drive the real page through NiceGUI's user simulation: the app
is booted from `meadow/frontend/__main__.py`, i.e. with the real page
registration, the real shell and the real middleware.

Two things are deliberately replaced:

- the core client (`FakeCore`): this is a page test, not an integration test, and
  the browser talks to no core here;
- the auth middleware: the login flow has its own coverage, and these tests are
  about the page a logged-in user sees.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncGenerator, Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from nicegui import ui
from nicegui.testing import User, user_simulation

from meadow.frontend.i18n import t

EN = "en"
#: The real frontend entry point: booting it registers every page, the shell and the
#: auth middleware, exactly as a browser sees them.
MAIN_FILE = Path(__file__).resolve().parents[1] / "meadow" / "frontend" / "__main__.py"


class FakeCore:
    """Stands in for `CoreClient`: records every call and answers with fixed data."""

    #: Calls the page made, as `(method name, keyword arguments)`.
    calls: list[tuple[str, dict[str, Any]]] = []
    #: Rows to answer with, per method name (missing names answer with `[]`).
    data: dict[str, list[dict]] = {}

    def __init__(self, base_url: str, **kwargs: Any) -> None:
        self.base_url = base_url
        self.timeout = kwargs.get("timeout", 10.0)

    def __getattr__(self, name: str) -> Callable[..., Awaitable[Any]]:
        """Answers every client method: record the call, return the fixed data."""

        async def call(*args: Any, **kwargs: Any) -> Any:
            FakeCore.calls.append((name, kwargs))
            if name in FakeCore.data:
                return FakeCore.data[name]
            # Like the real client: listers answer lists, everything else a record.
            return [] if name.startswith("list_") else {}

        return call

    @classmethod
    def reset(cls, data: dict[str, list[dict]] | None = None) -> None:
        """Clears the recording and sets the answers for the next test."""
        cls.calls = []
        cls.data = data or {}


def _drop_frontend_modules() -> None:
    """Forgets the frontend modules so the simulation imports them fresh.

    Other test modules import them at collection time; the simulation then builds a
    new app while the cached `@ui.page` decorators never run again, so the pages
    would stay unregistered (every `open` would answer 404).
    """
    for name in [name for name in sys.modules if name.startswith("meadow.frontend")]:
        del sys.modules[name]


@pytest.fixture
async def products_user(monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[User]:
    """Boots the real app for one test, with a stub core and no auth redirect."""
    _drop_frontend_modules()
    async with user_simulation(main_file=MAIN_FILE) as user:
        from meadow.frontend import auth, grid, layout

        FakeCore.reset()
        monkeypatch.setattr(grid, "CoreClient", FakeCore)
        monkeypatch.setattr(layout, "CoreClient", FakeCore)

        async def allow(  # the middleware calls `self.dispatch(request, call_next)`
            self: Any, request: Any, call_next: Callable[[Any], Awaitable[Any]]
        ) -> Any:
            return await call_next(request)

        monkeypatch.setattr(auth.AuthMiddleware, "dispatch", allow)
        yield user


async def test_products_page_without_filter_only_loads_the_catalogs(
    products_user: User,
) -> None:
    """A plain visit lists no objects: the grid waits for a tree node or a filter."""
    await products_user.open("/products")

    await products_user.should_see(t("grid.select_tree_node", locale=EN))
    assert [name for name, _ in FakeCore.calls if name == "list_objects"] == []
    called = {name for name, _ in FakeCore.calls}
    assert {"list_channels", "list_object_types", "list_relation_definitions"} <= called


async def test_products_page_url_filters_reach_the_core(products_user: User) -> None:
    """`?type=…&incomplete=1` is the bookmarkable shortcut the dashboard links to."""
    await products_user.open("/products?type=product&incomplete=1")

    payloads = [kwargs for name, kwargs in FakeCore.calls if name == "list_objects"]
    assert payloads, "a filtered visit must load objects"
    assert payloads[0]["object_type"] == "product"
    assert payloads[0]["missing_required"] is True
    assert payloads[0]["limit"] == 1000


async def test_products_page_reports_an_empty_filter_result(products_user: User) -> None:
    """A filter that matches nothing says so instead of showing the empty hint."""
    await products_user.open("/products?type=product")

    await products_user.should_see(t("grid.no_objects", locale=EN))
    await products_user.should_not_see(t("grid.select_tree_node", locale=EN))


#: Objects the stubbed core answers a grid load with (core shape: `id`/`type`/`attributes`).
OBJECTS = [
    {
        "id": "product:1",
        "type": "product",
        "status": "draft",
        "attributes": {"name": "Brake disc"},
    },
    {
        "id": "product:2",
        "type": "product",
        "status": "draft",
        "attributes": {"name": "Brake pad"},
    },
]


def _stub_objects(*objects: dict) -> None:
    """Answers the next grid load with these objects."""
    FakeCore.data["list_objects"] = [dict(object_) for object_ in objects]


def _pick(user: User, marker: str, value: str) -> None:
    """Chooses an option the way a user does, so the change handlers run.

    NiceGUI's interaction helper types into inputs but has no equivalent for selects,
    so this sets the value inside the client context — exactly what `type()` does.
    """
    assert user.client is not None
    element = next(iter(user.find(marker=marker).elements))
    assert isinstance(element, (ui.input, ui.select))
    with user.client:
        element.value = value


async def _wait_until(condition: Callable[[], bool]) -> None:
    """Waits for an async handler of the page (a click only *schedules* it)."""
    for _ in range(50):
        if condition():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("the expected effect did not happen")


async def test_bulk_wizard_posts_the_chosen_operation(products_user: User) -> None:
    """The wizard's contract: operation, scope ids and parameters reach the core."""
    _stub_objects(*OBJECTS)
    await products_user.open("/products?type=product")

    products_user.find(marker="bulk-open").click()
    await products_user.should_see(t("grid.bulk_title", locale=EN))
    _pick(products_user, "bulk-op", "change_status")
    await products_user.should_see(marker="bulk-field-status")
    _pick(products_user, "bulk-field-status", "published")
    products_user.find(marker="bulk-start").click()
    await _wait_until(lambda: any(name == "post_bulk" for name, _ in FakeCore.calls))

    assert [kwargs for name, kwargs in FakeCore.calls if name == "post_bulk"] == [
        {
            "op": "change_status",
            "object_ids": ["product:1", "product:2"],
            "parameters": {"status": "published"},
        }
    ]


async def test_bulk_wizard_refuses_an_incomplete_configuration(products_user: User) -> None:
    """An operation without a target must not be posted at all."""
    _stub_objects(*OBJECTS)
    await products_user.open("/products?type=product")

    products_user.find(marker="bulk-open").click()
    _pick(products_user, "bulk-op", "set_attribute")
    await products_user.should_see(marker="bulk-field-attribute")
    products_user.find(marker="bulk-start").click()
    await _wait_until(
        lambda: products_user.notify.contains(t("grid.bulk_config_incomplete", locale=EN))
    )

    assert [name for name, _ in FakeCore.calls if name == "post_bulk"] == []
    assert products_user.notify.contains(t("grid.bulk_config_incomplete", locale=EN)), (
        products_user.notify.messages
    )
