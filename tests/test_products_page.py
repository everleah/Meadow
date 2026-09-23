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
from urllib.parse import quote

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

    #: Calls the page made, as `(method name, positional arguments, keyword arguments)`.
    calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
    #: Rows to answer with, per method name (missing names answer with `[]`/`{}`).
    #: An `Exception` **value** makes the call fail, like a core that answers an error.
    data: dict[str, Any] = {}

    def __init__(self, base_url: str, **kwargs: Any) -> None:
        self.base_url = base_url
        self.timeout = kwargs.get("timeout", 10.0)

    def __getattr__(self, name: str) -> Callable[..., Awaitable[Any]]:
        """Answers every client method: record the call, return the fixed data."""

        async def call(*args: Any, **kwargs: Any) -> Any:
            FakeCore.calls.append((name, args, kwargs))
            if name in FakeCore.data:
                answer = FakeCore.data[name]
                if isinstance(answer, Exception):
                    raise answer
                return answer
            # Like the real client: listers answer lists, everything else a record.
            return [] if name.startswith("list_") else {}

        return call

    @classmethod
    def reset(cls, data: dict[str, Any] | None = None) -> None:
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
        from meadow.frontend import auth, form, grid, layout

        FakeCore.reset()
        monkeypatch.setattr(grid, "CoreClient", FakeCore)
        monkeypatch.setattr(layout, "CoreClient", FakeCore)
        # The single-object form (`/products/{id}`) needs the same stub.
        monkeypatch.setattr(form, "CoreClient", FakeCore)

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
    assert [name for name, _, _ in FakeCore.calls if name == "list_objects"] == []
    called = {name for name, _, _ in FakeCore.calls}
    assert {"list_channels", "list_object_types", "list_relation_definitions"} <= called


async def test_shell_drawer_entries_are_plain_links(products_user: User) -> None:
    """The drawer navigates with real anchors, not through the websocket.

    `ui.navigate.to` only sends `window.open(…)` **after** the click reached the server,
    so in a tab whose socket is gone (suspended Zen/Firefox tab, restarted dev server)
    a click silently does nothing and the app looks broken. An `<a href>` navigates in
    the browser itself; verified in Firefox with `window.socket.disconnect()` (live and
    dead socket both land on the target page).
    """
    await products_user.open("/products")

    links = products_user.find(marker="nav-link").elements
    hrefs = {element.props.get("href") for element in links}
    assert {"/", "/products", "/export", "/import", "/help"} <= hrefs


async def test_a_failing_catalog_call_degrades_instead_of_killing_the_page(
    products_user: User,
) -> None:
    """One rejected catalog request must not take the whole grid down.

    Found live 2026-09-23: after a core restart the stored token was refused, the
    page's `GET /languages` answered `401`, and the grid replied `500 UnboundLocalError`
    — an error *page* over a name that was only bound inside the `try`. Every catalog
    call of the page degrades instead (the session handler navigates to the login
    page), so the page must still build.
    """
    from meadow.frontend.api import CoreError

    FakeCore.reset({"list_languages": CoreError("invalid or expired token", status=401)})
    await products_user.open("/products")

    # The page is there (with its empty-grid hint) instead of a server error.
    await products_user.should_see(t("grid.select_tree_node", locale=EN))
    assert "list_languages" in {name for name, _, _ in FakeCore.calls}


async def test_products_page_url_filters_reach_the_core(products_user: User) -> None:
    """`?type=…&incomplete=1` is the bookmarkable shortcut the dashboard links to."""
    from meadow.frontend import grid_query

    await products_user.open("/products?type=product&incomplete=1")

    payloads = [kwargs for name, _, kwargs in FakeCore.calls if name == "list_objects"]
    assert payloads, "a filtered visit must load objects"
    assert payloads[0]["object_type"] == "product"
    assert payloads[0]["missing_required"] is True
    # One **chunk** per request, from the offset the page has already loaded: every row
    # carries all own attributes plus the language/inheritance cells, so 1000 rows in
    # one request (the old `FILTERED_LIMIT`) made the page megabytes large.
    assert payloads[0]["limit"] == grid_query.FILTERED_LIMIT
    assert payloads[0]["start"] == 0


async def test_a_short_chunk_offers_no_load_more(products_user: User) -> None:
    """A chunk smaller than the page size is the end of the list."""
    _stub_objects(*OBJECTS)
    await products_user.open("/products?type=product")

    await products_user.should_see(t("grid.loaded_count", count=len(OBJECTS), locale=EN))
    await products_user.should_not_see(marker="grid-load-more")


async def test_the_grid_loads_the_next_chunk_on_demand(products_user: User) -> None:
    """A full chunk shows the control, and the next chunk is appended from `start`."""
    from meadow.frontend import grid_query

    _stub_objects(*_many_objects(grid_query.FILTERED_LIMIT))
    await products_user.open("/products?type=product")

    await products_user.should_see(marker="grid-load-more")
    await products_user.should_see(
        t("grid.loaded_count", count=grid_query.FILTERED_LIMIT, locale=EN)
    )

    # The next request continues where the loaded chunk ended; a short answer closes
    # the list (and the counter counts both chunks).
    _stub_objects(*_many_objects(5, start=grid_query.FILTERED_LIMIT))
    products_user.find(marker="grid-load-more").click()
    await _wait_until(lambda: len(_list_objects_calls()) > 1)

    payloads = _list_objects_calls()
    assert payloads[1]["start"] == grid_query.FILTERED_LIMIT
    assert payloads[1]["limit"] == grid_query.FILTERED_LIMIT
    await products_user.should_see(
        t("grid.loaded_count", count=grid_query.FILTERED_LIMIT + 5, locale=EN)
    )
    await products_user.should_not_see(marker="grid-load-more")


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


def _many_objects(count: int, *, start: int = 0) -> list[dict]:
    """`count` distinct objects, numbered from `start` (a stubbed core page)."""
    return [
        {
            "id": f"product:{start + index}",
            "type": "product",
            "status": "draft",
            "attributes": {"name": f"Object {start + index}"},
        }
        for index in range(count)
    ]


def _stub_tree(*objects: dict) -> None:
    """Answers the structure tree's root load (children of the universal root)."""
    FakeCore.data["list_object_children"] = [dict(object_) for object_ in objects]


def _list_objects_calls() -> list[dict[str, Any]]:
    """The keyword arguments of every `list_objects` call the page made."""
    return [kwargs for name, _, kwargs in FakeCore.calls if name == "list_objects"]


def _search_terms() -> list[Any]:
    """The query every `picker_search` call carried (it is the second argument)."""
    return [
        args[1] for name, args, _ in FakeCore.calls if name == "picker_search" and len(args) > 1
    ]


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


async def _wait_until(condition: Callable[[], bool], note: str = "") -> None:
    """Waits for an async handler of the page (a click only *schedules* it)."""
    for _ in range(50):
        if condition():
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"the expected effect did not happen: {note}")


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
    await _wait_until(lambda: any(name == "post_bulk" for name, _, _ in FakeCore.calls))

    assert [kwargs for name, _, kwargs in FakeCore.calls if name == "post_bulk"] == [
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

    assert [name for name, _, _ in FakeCore.calls if name == "post_bulk"] == []
    assert products_user.notify.contains(t("grid.bulk_config_incomplete", locale=EN)), (
        products_user.notify.messages
    )


async def test_the_object_form_renders_for_a_stored_status_the_state_machine_lacks(
    products_user: User,
) -> None:
    """A stored status is always selectable — this used to be a **500**.

    The core validates no status vocabulary (`plan/detail/workflow-freigaben.md`), so
    the value can come from an import, the API, an MCF document or a rule. A
    `ui.select` whose value is not among its options raises
    `ValueError: Invalid value: …` while the page builds, which NiceGUI answers with a
    500 — exactly what happened on 2026-09-21 for the fifth state of the machine: the
    selector knew four of five and an `approved` object took the whole page down.
    """
    FakeCore.data["get_object"] = {
        "id": "object:1",
        "type": "product",
        "status": "approved",
        "attributes": {"name": "Brake disc"},
    }
    # The real core answers `{classes, attributes}` here (the fake's default `[]`
    # is not a shape the page can render).
    FakeCore.data["list_object_category_attributes"] = {"classes": [], "attributes": []}
    await products_user.open(f"/products/{quote('object:1', safe='')}")

    element = next(iter(products_user.find(marker="form-status").elements))
    assert isinstance(element, ui.select)
    assert element.value == "approved"
    assert "approved" in element.options

    # …and a value no state machine knows stays selectable too.
    FakeCore.data["get_object"] = {
        "id": "object:1",
        "type": "product",
        "status": "legacy_state",
        "attributes": {"name": "Brake disc"},
    }
    await products_user.open(f"/products/{quote('object:1', safe='')}")
    element = next(iter(products_user.find(marker="form-status").elements))
    assert isinstance(element, ui.select)
    assert element.value == "legacy_state"
    assert "legacy_state" in element.options


async def test_filter_search_reaches_the_core(products_user: User) -> None:
    """Typing into the filter bar starts a server-side search (not a row scan)."""
    _stub_objects(*OBJECTS)
    _stub_tree()
    await products_user.open("/products?type=product")

    products_user.find(marker="filter-search").type("Brake")
    await _wait_until(lambda: "Brake" in _search_terms())

    assert "Brake" in _search_terms()


async def test_structure_tree_selection_filters_by_ancestor(products_user: User) -> None:
    """Picking a structure node loads its descendants (`?ancestor=`)."""
    _stub_objects(*OBJECTS)
    _stub_tree(*OBJECTS)
    await products_user.open("/products")

    # The tree reports the Quasar `update:selected` prop event with the node id.
    products_user.find(marker="structure-tree").trigger("update:selected", "product:1")
    await _wait_until(
        lambda: any(call.get("ancestor") == "product:1" for call in _list_objects_calls())
    )

    assert any(call.get("ancestor") == "product:1" for call in _list_objects_calls())


async def test_advanced_dialog_loads_the_catalog_and_validates_on_apply(
    products_user: User,
) -> None:
    """Opening loads the field catalog; applying an untouched form is refused.

    The refusal is the interesting part: the dialog validates the condition tree
    *before* it touches the grid, so an empty group never becomes a filter — and the
    click through the real button is what proves that wiring.
    """
    _stub_objects(*OBJECTS)
    _stub_tree()
    FakeCore.data["list_search_fields"] = {}
    await products_user.open("/products?type=product")
    before = len(_list_objects_calls())

    products_user.find(marker="advanced-open").click()
    await _wait_until(
        lambda: any(name == "list_search_fields" for name, _, _ in FakeCore.calls),
        str([name for name, _, _ in FakeCore.calls]),
    )
    assert any(name == "list_saved_searches" for name, _, _ in FakeCore.calls)

    products_user.find(marker="advanced-apply").click()
    await _wait_until(lambda: bool(products_user.notify.messages))

    assert len(_list_objects_calls()) == before
    assert any("group" in message for message in products_user.notify.messages), (
        products_user.notify.messages
    )


#: The type registry the create dialog reads (a plain label attribute plus a key).
CREATE_TYPE = {
    "name": "product",
    "label_attribute": "title",
    "key_attributes": ["sku"],
}


def _stub_create() -> None:
    """Answers the registry, the attribute definition and the write of a new object."""
    FakeCore.data["list_object_types"] = [dict(CREATE_TYPE)]
    FakeCore.data["list_attribute_vocabulary"] = [
        {"name": "title", "label": {"en": "Title"}},
        {"name": "sku", "label": {"en": "SKU"}},
    ]
    FakeCore.data["create_object"] = {"id": "product:new", "type": "product"}


async def _fill_create_dialog(user: User, name: str, key: str) -> None:
    """Opens the dialog on the current tree selection and fills attribute and key."""
    user.find(marker="create-open").click()
    await user.should_see(marker="create-name")
    user.find(marker="create-name").type(name)
    user.find(marker="create-key-sku").type(key)
    user.find(marker="create-save").click()
    await _wait_until(lambda: any(name == "create_object" for name, _, _ in FakeCore.calls))


async def test_create_dialog_writes_the_object_with_its_parent(products_user: User) -> None:
    """The dialog posts type, label attribute and business keys to the core."""
    _stub_objects(*OBJECTS)
    _stub_tree()
    _stub_create()
    await products_user.open("/products")

    await _fill_create_dialog(products_user, "Brake drum", "BD-1")

    assert [kwargs for name, _, kwargs in FakeCore.calls if name == "create_object"] == [
        {
            "object_type": "product",
            "attributes": {"title": "Brake drum", "sku": "BD-1"},
            "parent": None,
        }
    ]


async def test_create_dialog_passes_the_selected_tree_node_as_parent(
    products_user: User,
) -> None:
    """The composition parent is part of the write: the tree selection travels."""
    _stub_objects(*OBJECTS)
    _stub_tree(*OBJECTS)
    _stub_create()
    await products_user.open("/products")
    products_user.find(marker="structure-tree").trigger("update:selected", "product:1")
    await _wait_until(
        lambda: any(call.get("ancestor") == "product:1" for call in _list_objects_calls())
    )

    await _fill_create_dialog(products_user, "Brake shoe", "BS-2")

    assert [kwargs for name, _, kwargs in FakeCore.calls if name == "create_object"] == [
        {
            "object_type": "product",
            "attributes": {"title": "Brake shoe", "sku": "BS-2"},
            "parent": "product:1",
        }
    ]


async def test_column_view_saves_and_lists_the_visible_fields(products_user: User) -> None:
    """Saving a named column set stores it and lists it in the dialog."""
    _stub_objects(*OBJECTS)
    _stub_tree()
    await products_user.open("/products?type=product")

    products_user.find(marker="views-open").click()
    products_user.find(marker="views-name").type("Brakes")
    products_user.find(marker="views-save").click()
    await _wait_until(lambda: bool(products_user.notify.messages))

    assert products_user.notify.contains("Brakes"), products_user.notify.messages
    await products_user.should_see("Brakes")


async def test_filter_bar_reset_returns_to_the_empty_grid(products_user: User) -> None:
    """The reset button empties the bar; without a filter the grid waits again.

    `q` and "only incomplete" are cleared, the type/tree/category stay (they are not
    part of the bar) — and the grid loads nothing by itself again, which the hint
    proves.
    """
    _stub_objects(*OBJECTS)
    _stub_tree()
    await products_user.open("/products?q=Brake&incomplete=1")
    await products_user.should_not_see(t("grid.select_tree_node", locale=EN))
    assert "Brake" in _search_terms()

    products_user.find(marker="filter-reset").click()
    await products_user.should_see(t("grid.select_tree_node", locale=EN))

    search = next(iter(products_user.find(marker="filter-search").elements))
    assert isinstance(search, ui.input)
    assert search.value == ""


async def test_the_category_controls_belong_to_the_category_view(products_user: User) -> None:
    """Taxonomy/tree/dimension switchers appear with that view — and leave with it.

    They sit in a row of their own (three selectors plus help buttons in one line was
    unreadable); the row hides as a whole, so the structure view leaves no empty gap.
    """
    _stub_objects(*OBJECTS)
    _stub_tree()
    await products_user.open("/products")

    await products_user.should_not_see(marker="taxonomy-select")

    products_user.find(marker="view-category").click()
    await products_user.should_see(marker="taxonomy-select")

    products_user.find(marker="view-structure").click()
    await products_user.should_not_see(marker="taxonomy-select")


async def test_reference_dialog_opens_from_the_refs_cell(products_user: User) -> None:
    """The `_refs` cell is the dialog's only entry; the click travels through the grid.

    The AG Grid is client-rendered, so a test cannot click a cell — but the click arrives
    in Python as an event, and that event is what this test fires.
    """
    _stub_objects(*OBJECTS)
    _stub_tree()
    await products_user.open("/products?type=product")

    products_user.find(marker="objects-grid").trigger(
        "cellClicked",
        {"data": {"_id": "product:1", "_type": "product"}, "colId": "_refs"},
    )
    await _wait_until(
        lambda: any(name == "list_object_references" for name, _, _ in FakeCore.calls),
        str([name for name, _, _ in FakeCore.calls]),
    )

    references = [args for name, args, _ in FakeCore.calls if name == "list_object_references"]
    assert [args[1] for args in references] == ["product:1"]
    await products_user.should_see(t("grid.references", locale=EN))
    await products_user.should_see(t("grid.ref_empty", locale=EN))


async def test_structure_view_renders_the_tree(products_user: User) -> None:
    """The structure view draws the lazy tree of the loaded root nodes."""
    _stub_objects(*OBJECTS)
    _stub_tree(*OBJECTS)
    await products_user.open("/products")

    await products_user.should_see(marker="structure-tree")
    await products_user.should_not_see(t("grid.tree_load_failed", locale=EN))


async def test_collection_view_lists_the_saved_collections(products_user: User) -> None:
    """Switching to the collection view renders a tree of the saved collections."""
    _stub_objects(*OBJECTS)
    _stub_tree()
    FakeCore.data["list_collections"] = [
        {"id": "collection:1", "name": "Brakes", "member_count": 2},
    ]
    await products_user.open("/products")

    products_user.find(marker="view-collection").click()
    await products_user.should_see(marker="structure-tree")

    assert any(name == "list_collections" for name, _, _ in FakeCore.calls)
    await products_user.should_not_see(t("grid.tree_load_failed", locale=EN))
