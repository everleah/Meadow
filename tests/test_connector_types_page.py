"""Page-level tests for `/connector-types` — the registry of own connector types.

The page is the only place where a type is written, and it is easy to get wrong in
ways the API tests cannot see: the list must hide the built-in types (they come from
the core's Rust literals and are not editable), the template must produce a schema
the core accepts, and a save must carry name, direction and the parsed schema — a
type registered with the wrong shape would break the connector form for everyone.

Replaced, like in `test_users_page.py`: the core client (`FakeCore`) and the auth
middleware; `auth.is_admin()` answers `True`, so the page builds.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import AsyncGenerator, Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from nicegui import ui
from nicegui.testing import User, user_simulation

from meadow.frontend.i18n import t

EN = "en"
MAIN_FILE = Path(__file__).resolve().parents[1] / "meadow" / "frontend" / "__main__.py"

BUILT_IN_TYPES: list[dict[str, Any]] = [
    {"type": "webhook", "direction": "outbound", "wired": True, "groups": [{"key": "target"}]},
    {"type": "json_import", "direction": "inbound", "wired": True, "groups": []},
    {"type": "json_export", "direction": "outbound", "wired": True, "groups": []},
]
REGISTERED_TYPE: dict[str, Any] = {
    "type": "erp_bridge",
    "label_key": "connectors.type_erp_bridge",
    "direction": "inbound",
    "wired": False,
    "groups": [
        {
            "key": "main",
            "label_key": "connectors.group_target",
            "fields": [
                {"key": "endpoint", "type": "string", "widget": "text", "required": True},
                {"key": "token", "type": "string", "widget": "text", "secret": True},
            ],
        }
    ],
}


class FakeCore:
    """Stands in for `CoreClient`: records every call and answers with fixed data."""

    calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
    data: dict[str, Any] = {}

    def __init__(self, base_url: str, **kwargs: Any) -> None:
        self.base_url = base_url
        self.timeout = kwargs.get("timeout", 10.0)

    def __getattr__(self, name: str) -> Callable[..., Awaitable[Any]]:
        async def call(*args: Any, **kwargs: Any) -> Any:
            FakeCore.calls.append((name, args, kwargs))
            if name in FakeCore.data:
                answer = FakeCore.data[name]
                if isinstance(answer, Exception):
                    raise answer
                return answer
            return [] if name.startswith("list_") else {}

        return call

    @classmethod
    def reset(cls, data: dict[str, Any] | None = None) -> None:
        cls.calls = []
        cls.data = data or {}

    @classmethod
    def payload(cls, name: str) -> dict[str, Any]:
        """The keyword arguments of the last call of `name`."""
        found = [kwargs for method, _, kwargs in cls.calls if method == name]
        assert found, f"`{name}` was never called"
        return found[-1]


def _drop_frontend_modules() -> None:
    """Forgets the frontend modules so the simulation imports them fresh."""
    for name in [name for name in sys.modules if name.startswith("meadow.frontend")]:
        del sys.modules[name]


@pytest.fixture
async def types_user(monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[User]:
    """Boots the real app for one test, with a stub core and an admin session."""
    _drop_frontend_modules()
    async with user_simulation(main_file=MAIN_FILE) as user:
        from meadow.frontend import auth, connector_types_page, layout

        FakeCore.reset({"list_connector_types": [*BUILT_IN_TYPES, REGISTERED_TYPE]})
        monkeypatch.setattr(connector_types_page, "CoreClient", FakeCore)
        monkeypatch.setattr(layout, "CoreClient", FakeCore)
        monkeypatch.setattr(auth, "is_admin", lambda: True)

        async def allow(  # the middleware calls `self.dispatch(request, call_next)`
            self: Any, request: Any, call_next: Callable[[Any], Awaitable[Any]]
        ) -> Any:
            return await call_next(request)

        monkeypatch.setattr(auth.AuthMiddleware, "dispatch", allow)
        yield user


async def _wait_until(condition: Callable[[], bool], tries: int = 60) -> None:
    """Waits for recorded state (a click on an async handler only schedules it)."""
    for _ in range(tries):
        if condition():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("condition was not reached")


def _table(user: User) -> ui.table:
    found = user.find(marker="types-table").elements
    assert found, "the type table is missing"
    element = next(iter(found))
    assert isinstance(element, ui.table), "the marker is not on a table"
    return element


def _schema_area(user: User) -> ui.textarea:
    found = user.find(marker="type-schema").elements
    assert found, "the schema editor is missing"
    element = next(iter(found))
    assert isinstance(element, ui.textarea), "the marker is not on a text editor"
    return element


def _schema_text(user: User) -> str:
    return str(_schema_area(user).value or "")


async def test_the_list_shows_only_the_registered_types(types_user: User) -> None:
    """The built-ins live in the core: listing them here would offer a lie."""
    user = types_user
    await user.open("/connector-types")

    rows = _table(user).rows
    assert [row["name"] for row in rows] == ["erp_bridge"]
    # The size column counts the declared fields, not the groups.
    assert rows[0]["fields"] == 2
    assert rows[0]["direction"] == "inbound"


async def test_the_template_button_fills_a_valid_schema(types_user: User) -> None:
    """The starter schema has to be the smallest thing the core accepts."""
    user = types_user
    await user.open("/connector-types")
    user.find(marker="type-new").click()
    user.find(marker="type-template").click()

    schema = json.loads(_schema_text(user))
    groups = schema["groups"]
    assert [group["key"] for group in groups] == ["main"]
    field = groups[0]["fields"][0]
    assert field["key"] == "endpoint"
    assert field["required"] is True


async def test_saving_a_new_type_sends_name_direction_and_schema(types_user: User) -> None:
    """The dialog's fields reach `POST /connector-types` — nothing is filled in for the user."""
    user = types_user
    await user.open("/connector-types")
    user.find(marker="type-new").click()
    user.find(marker="type-name").type("supplier_portal")
    user.find(marker="type-label").type("connectors.type_supplier_portal")
    user.find(marker="type-save").click()
    await _wait_until(lambda: any(call[0] == "create_connector_type" for call in FakeCore.calls))

    payload = FakeCore.payload("create_connector_type")
    assert payload["name"] == "supplier_portal"
    assert payload["label_key"] == "connectors.type_supplier_portal"
    # The select keeps its default; the schema is the template, parsed.
    assert payload["direction"] == "inbound"
    assert payload["schema"]["groups"][0]["fields"][0]["key"] == "endpoint"
    assert user.notify.contains(t("connector_types.saved", locale=EN)), user.notify.messages
    # The list is reloaded, so the new type appears without a page reload.
    assert [call[0] for call in FakeCore.calls].count("list_connector_types") >= 2


async def test_a_missing_name_only_warns(types_user: User) -> None:
    """An empty name is refused in the dialog — the core would answer `400`."""
    user = types_user
    await user.open("/connector-types")
    user.find(marker="type-new").click()
    user.find(marker="type-save").click()
    await _wait_until(lambda: user.notify.contains(t("connector_types.name_required", locale=EN)))

    assert not [call for call in FakeCore.calls if call[0] == "create_connector_type"]


async def test_an_unparsable_schema_is_refused_before_the_core(types_user: User) -> None:
    """Broken JSON stays in the dialog with the parser's message."""
    user = types_user
    await user.open("/connector-types")
    user.find(marker="type-new").click()
    user.find(marker="type-name").type("broken_type")
    _schema_area(user).set_value("{not json")
    user.find(marker="type-save").click()
    await _wait_until(lambda: user.notify.contains(t("connector_types.schema_invalid", locale=EN)))

    assert not [call for call in FakeCore.calls if call[0] == "create_connector_type"]


async def test_deleting_without_a_selection_asks_to_select(types_user: User) -> None:
    """Nothing selected means nothing to delete — and no call."""
    user = types_user
    await user.open("/connector-types")
    user.find(marker="type-delete").click()
    await _wait_until(lambda: user.notify.contains(t("connector_types.no_selection", locale=EN)))

    assert not [call for call in FakeCore.calls if call[0] == "delete_connector_type"]


async def test_a_failing_catalog_leaves_an_empty_list(types_user: User) -> None:
    """A core that cannot deliver the types degrades to an empty list, not to a 500."""
    # The class has to come from the module the fixture imported: the frontend is
    # re-imported per test, so a module-level import would be a different class.
    from meadow.frontend.api import CoreError

    FakeCore.reset({"list_connector_types": CoreError("core unavailable")})
    user = types_user
    await user.open("/connector-types")

    assert _table(user).rows == []
