"""Admin page for saved searches & materialized collections (admin/superadmin only).

Two tabs mirror the feature:

- **Saved Searches** (``type: saved_search``): list (``GET /saved-searches``),
  create/edit in a dialog whose query is edited as JSON (the Query DSL:
  filters/fulltext/references/sort/limit), an **Execute** action (``POST
  /saved-searches/{id}/execute``) that shows the matching object count/ids, and
  a **Delete** action (``DELETE /saved-searches/{id}``).
- **Collections** (``type: collection``): list (``GET /collections``) with a
  member count, create/edit (``POST``/``PATCH /collections``) over a saved
  search + optional ``refresh_interval`` cron, a **Refresh** action (``POST
  /collections/{id}/refresh``), a **Members** dialog (``GET
  /collections/{id}/members``) and a **Delete** action (``DELETE
  /collections/{id}``).
"""

from __future__ import annotations

import json as _json
from datetime import datetime

from nicegui import app, ui

from meadow.frontend import auth
from meadow.frontend.api import CoreClient, CoreError
from meadow.frontend.config import CORE_URL
from meadow.frontend.i18n import t
from meadow.frontend.layout import build_shell

#: Default query shown in the saved-search editor.
DEFAULT_QUERY = {
    "filters": [
        {"field": "type", "operator": "eq", "value": "product"},
        {"field": "status", "operator": "in", "value": ["published", "approved"]},
    ],
    "fulltext": {"query": ""},
    "references": [],
    "sort": [],
    "limit": 1000,
}


def _fmt_dt(value: str | None) -> str:
    """Formats a SurrealDB ISO timestamp for display (local time, no tz)."""
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:  # noqa: BLE001 - leave the raw value on parse failure
        return str(value)


def _query_of(search: dict) -> dict:
    """Returns the JSON query of a saved search (or the default)."""
    query = search.get("attributes", {}).get("query")
    if isinstance(query, dict):
        return query
    return dict(DEFAULT_QUERY)


def _name_of(row: dict) -> str:
    """Returns the display name attribute (grid row or full object)."""
    name = row.get("name")
    if isinstance(name, str) and name:
        return name
    name = row.get("attributes", {}).get("name")
    if isinstance(name, str) and name:
        return name
    return str(row.get("id") or "")


def _interval_of(row: dict) -> str:
    """Returns the refresh-interval cron (or '')."""
    return str(row.get("attributes", {}).get("refresh_interval") or "")


@ui.page("/saved-searches", response_timeout=15)
async def saved_searches_page() -> None:
    await build_shell(active="/saved-searches")
    if not auth.is_admin():
        ui.navigate.to("/")
        return
    client = CoreClient(CORE_URL)
    token = app.storage.user.get("token", "")

    try:
        searches = await client.list_saved_searches(token, limit=1000)
    except Exception:  # noqa: BLE001 - show a friendly message on failure
        searches = []
    try:
        collections = await client.list_collections(token, limit=1000)
    except Exception:  # noqa: BLE001
        collections = []

    with ui.column().classes("w-full p-6"):
        ui.label(t("saved_searches.title")).classes("text-h4")
        ui.label(t("saved_searches.subtitle")).classes("text-subtitle1")

        with ui.tabs() as tabs:
            tab_searches = ui.tab(t("saved_searches.tab_search"))
            tab_collections = ui.tab(t("saved_searches.tab_collections"))
        with ui.tab_panels(tabs, value=tab_searches):
            with ui.tab_panel(tab_searches):
                await _build_search_panel(client, token, searches)
            with ui.tab_panel(tab_collections):
                await _build_collection_panel(client, token, collections)


async def _build_search_panel(client: CoreClient, token: str, searches: list[dict]) -> None:
    filters: dict[str, str] = {"name": ""}

    def _matches(s: dict) -> bool:
        if filters["name"]:
            return filters["name"].lower() in _name_of(s).lower()
        return True

    def _rows() -> list[dict]:
        return [
            {
                "id": str(s.get("id")),
                "name": _name_of(s),
                "interval": _interval_of(s) or "—",
                "updated": _fmt_dt(s.get("updated_at")),
            }
            for s in searches
            if _matches(s)
        ]

    with ui.row().classes("w-full items-center gap-2 mt-2"):
        (
            ui.input(t("saved_searches.filter_name"))
            .props("filled dense")
            .classes("w-56")
            .on_value_change(lambda e: _set_filter(str(e.value)))
        )

    grid = ui.aggrid(
        {
            "columnDefs": [
                {"headerName": t("saved_searches.col_name"), "field": "name", "width": 260},
                {"headerName": t("saved_searches.col_interval"), "field": "interval", "width": 180},
                {"headerName": t("saved_searches.col_updated"), "field": "updated", "width": 200},
            ],
            "rowData": _rows(),
            "rowSelection": {"mode": "singleRow"},
        }
    ).classes("w-full")

    with ui.row().classes("w-full justify-between mt-2 gap-2"):
        with ui.row().classes("gap-2"):
            ui.button(
                t("saved_searches.new"), icon="add", on_click=lambda: open_editor(None)
            ).props("flat")
            ui.button(
                t("saved_searches.edit"), icon="edit", on_click=lambda: edit_selected()
            ).props("flat")
            ui.button(
                t("saved_searches.execute"), icon="play_arrow", on_click=lambda: execute_selected()
            ).props("flat")
            ui.button(
                t("saved_searches.delete"), icon="delete", on_click=lambda: delete_selected()
            ).props("flat color=negative")
        ui.button(t("saved_searches.reload"), icon="refresh", on_click=lambda: reload_data()).props(
            "flat"
        )

    def _set_filter(value: str) -> None:
        filters["name"] = value
        grid.run_grid_method("setGridOption", "rowData", _rows())

    # --- Saved-search editor -------------------------------------------------
    editor_dialog = ui.dialog()
    with editor_dialog, ui.card().classes("w-[46rem] max-w-[94vw]"):
        editor_title = ui.label("").classes("text-h6")
        with ui.column().classes("w-full mt-2 gap-2"):
            name_input = (
                ui.input(t("saved_searches.field_name")).props("filled dense").classes("w-full")
            )
            interval_input = (
                ui.input(t("saved_searches.field_interval"))
                .props("filled dense")
                .classes("w-full")
                .tooltip(t("saved_searches.interval_hint"))
            )
            ui.label(t("saved_searches.field_query")).classes("text-subtitle2")
            query_editor = ui.codemirror(
                _json.dumps(DEFAULT_QUERY, indent=2, ensure_ascii=False), language="JSON"
            ).classes("w-full h-72 font-mono text-sm")
            ui.label(t("saved_searches.query_hint")).classes("text-caption text-gray-500")
        with ui.row().classes("w-full justify-end mt-2 gap-2"):
            ui.button(t("grid.close"), on_click=editor_dialog.close).props("flat")
            ui.button(t("saved_searches.save"), icon="save", on_click=lambda: save_search()).props(
                "flat"
            )

    _state: dict = {"editing_id": None}

    def _fill_editor(search: dict | None) -> None:
        editing = search is not None
        editor_title.set_text(t("saved_searches.edit") if editing else t("saved_searches.new"))
        _state["editing_id"] = search.get("id") if editing else None
        name_input.set_value(_name_of(search) if editing else "")
        interval_input.set_value(_interval_of(search) if editing else "")
        query_editor.set_value(
            _json.dumps(
                _query_of(search) if editing else DEFAULT_QUERY, indent=2, ensure_ascii=False
            )
        )

    def open_editor(search: dict | None) -> None:
        _fill_editor(search)
        editor_dialog.open()

    async def save_search() -> None:
        editing_id = _state.get("editing_id")
        name = (name_input.value or "").strip()
        if not name:
            ui.notify(t("saved_searches.name_required"), color="warning")
            return
        try:
            query = _json.loads(query_editor.value or "{}")
        except _json.JSONDecodeError:
            ui.notify(t("saved_searches.query_invalid"), color="warning")
            return
        if not isinstance(query, dict):
            query = {}
        interval = (interval_input.value or "").strip() or None
        try:
            if editing_id:
                await client.update_saved_search(
                    token,
                    editing_id,
                    name=name,
                    query=query,
                    refresh_interval="" if interval is None else interval,
                )
            else:
                await client.create_saved_search(
                    token, name=name, query=query, refresh_interval=interval
                )
        except CoreError as error:
            ui.notify(f"{t('saved_searches.save_failed')}: {error}", color="negative")
            return
        editor_dialog.close()
        await reload_data()
        ui.notify(t("saved_searches.saved"), color="positive")

    async def reload_data() -> None:
        nonlocal searches
        try:
            searches = await client.list_saved_searches(token, limit=1000)
        except Exception:  # noqa: BLE001 - keep the cached list on failure
            return
        grid.run_grid_method("setGridOption", "rowData", _rows())

    async def get_selected() -> dict | None:
        try:
            rows = await grid.get_selected_rows()
        except Exception:  # noqa: BLE001
            return None
        return rows[0] if rows else None

    async def edit_selected() -> None:
        row = await get_selected()
        if not row:
            ui.notify(t("saved_searches.select_first"), color="warning")
            return
        full = next((s for s in searches if str(s.get("id")) == str(row.get("id"))), None)
        open_editor(full)

    async def execute_selected() -> None:
        row = await get_selected()
        if not row:
            ui.notify(t("saved_searches.select_first"), color="warning")
            return
        search_id = str(row.get("id"))
        try:
            result = await client.execute_saved_search(token, search_id)
        except CoreError as error:
            ui.notify(f"{t('saved_searches.execute_failed')}: {error}", color="negative")
            return
        await _show_execute_result(result)

    async def delete_selected() -> None:
        row = await get_selected()
        if not row:
            ui.notify(t("saved_searches.select_first"), color="warning")
            return
        search_id = str(row.get("id"))
        name = _name_of(row)
        with ui.dialog() as confirm, ui.card().classes("w-[28rem] max-w-[90vw]"):
            ui.label(t("saved_searches.delete_confirm").format(name=name)).classes("text-subtitle1")
            with ui.row().classes("w-full justify-end mt-2 gap-2"):
                ui.button(t("grid.close"), on_click=confirm.close).props("flat")

                async def _do() -> None:
                    confirm.close()
                    try:
                        await client.delete_saved_search(token, search_id)
                    except CoreError as error:
                        ui.notify(f"{t('saved_searches.delete_failed')}: {error}", color="negative")
                        return
                    await reload_data()
                    ui.notify(t("saved_searches.deleted"), color="positive")

                ui.button(t("saved_searches.delete"), on_click=_do).props("color=negative")
        confirm.open()

    grid.on(
        "cellDoubleClicked",
        lambda e: open_editor(
            next((s for s in searches if str(s.get("id")) == _id_of(e.args.get("data"))), None)
        ),
        ["data"],
    )


async def _show_execute_result(result: dict) -> None:
    count = int(result.get("count") or 0)
    ids = result.get("ids") or []
    with ui.dialog() as dlg, ui.card().classes("w-[38rem] max-w-[92vw]"):
        ui.label(t("saved_searches.execute_title").format(count=count)).classes("text-h6")
        with ui.column().classes("w-full mt-2 gap-1 overflow-auto max-h-80"):
            if not ids:
                ui.label(t("saved_searches.no_hits")).classes("text-caption text-gray-500")
            for object_id in ids:
                ui.label(str(object_id)).classes("text-caption font-mono")
        with ui.row().classes("w-full justify-end mt-2 gap-2"):
            ui.button(t("grid.close"), on_click=dlg.close).props("flat")
    dlg.open()


def _id_of(data: object) -> str:
    if isinstance(data, dict):
        return str(data.get("id") or "")
    return str(data)


async def _build_collection_panel(client: CoreClient, token: str, collections: list[dict]) -> None:
    # Keep an up-to-date saved-search list for the create dialog.
    try:
        searches = await client.list_saved_searches(token, limit=1000)
    except Exception:  # noqa: BLE001
        searches = []

    filters: dict[str, str] = {"name": ""}

    def _matches(c: dict) -> bool:
        if filters["name"]:
            return filters["name"].lower() in _name_of(c).lower()
        return True

    def _search_label(c: dict) -> str:
        search_id = str(c.get("attributes", {}).get("saved_search_id") or "")
        for s in searches:
            if str(s.get("id")) == search_id:
                return _name_of(s)
        return search_id.split(":")[-1] if search_id else "—"

    def _rows() -> list[dict]:
        return [
            {
                "id": str(c.get("id")),
                "name": _name_of(c),
                "search": _search_label(c),
                "members": str(c.get("member_count") or 0),
                "interval": _interval_of(c) or "—",
                "last_refreshed": _fmt_dt(c.get("attributes", {}).get("last_refreshed_at")),
            }
            for c in collections
            if _matches(c)
        ]

    with ui.row().classes("w-full items-center gap-2 mt-2"):
        (
            ui.input(t("saved_searches.filter_name"))
            .props("filled dense")
            .classes("w-56")
            .on_value_change(lambda e: _set_filter(str(e.value)))
        )

    grid = ui.aggrid(
        {
            "columnDefs": [
                {"headerName": t("saved_searches.col_name"), "field": "name", "width": 220},
                {"headerName": t("saved_searches.col_search"), "field": "search", "width": 200},
                {"headerName": t("saved_searches.col_members"), "field": "members", "width": 110},
                {"headerName": t("saved_searches.col_interval"), "field": "interval", "width": 180},
                {
                    "headerName": t("saved_searches.col_refreshed"),
                    "field": "last_refreshed",
                    "width": 200,
                },
            ],
            "rowData": _rows(),
            "rowSelection": {"mode": "singleRow"},
        }
    ).classes("w-full")

    with ui.row().classes("w-full justify-between mt-2 gap-2"):
        with ui.row().classes("gap-2"):
            ui.button(
                t("saved_searches.new_collection"), icon="add", on_click=lambda: open_editor(None)
            ).props("flat")
            ui.button(
                t("saved_searches.edit"), icon="edit", on_click=lambda: edit_selected()
            ).props("flat")
            ui.button(
                t("saved_searches.refresh"), icon="refresh", on_click=lambda: refresh_selected()
            ).props("flat")
            ui.button(
                t("saved_searches.members"), icon="list", on_click=lambda: members_selected()
            ).props("flat")
            ui.button(
                t("saved_searches.delete"), icon="delete", on_click=lambda: delete_selected()
            ).props("flat color=negative")
        ui.button(t("saved_searches.reload"), icon="refresh", on_click=lambda: reload_data()).props(
            "flat"
        )

    def _set_filter(value: str) -> None:
        filters["name"] = value
        grid.run_grid_method("setGridOption", "rowData", _rows())

    # --- Collection editor --------------------------------------------------
    editor_dialog = ui.dialog()
    with editor_dialog, ui.card().classes("w-[34rem] max-w-[92vw]"):
        editor_title = ui.label("").classes("text-h6")
        with ui.column().classes("w-full mt-2 gap-2"):
            name_input = (
                ui.input(t("saved_searches.field_name")).props("filled dense").classes("w-full")
            )
            search_select = (
                ui.select(
                    {str(s.get("id")): _name_of(s) for s in searches},
                    label=t("saved_searches.field_search"),
                    value=None,
                )
                .props("filled dense")
                .classes("w-full")
            )
            interval_input = (
                ui.input(t("saved_searches.field_interval"))
                .props("filled dense")
                .classes("w-full")
                .tooltip(t("saved_searches.interval_hint"))
            )
        with ui.row().classes("w-full justify-end mt-2 gap-2"):
            ui.button(t("grid.close"), on_click=editor_dialog.close).props("flat")
            ui.button(
                t("saved_searches.save"), icon="save", on_click=lambda: save_collection()
            ).props("flat")

    _state: dict = {"editing_id": None}

    def _fill_editor(collection: dict | None) -> None:
        editing = collection is not None
        editor_title.set_text(
            t("saved_searches.edit_collection") if editing else t("saved_searches.new_collection")
        )
        _state["editing_id"] = collection.get("id") if editing else None
        name_input.set_value(_name_of(collection) if editing else "")
        search_select.set_value(
            str(collection.get("attributes", {}).get("saved_search_id")) if editing else None
        )
        interval_input.set_value(_interval_of(collection) if editing else "")

    def open_editor(collection: dict | None) -> None:
        _fill_editor(collection)
        editor_dialog.open()

    async def save_collection() -> None:
        editing_id = _state.get("editing_id")
        name = (name_input.value or "").strip()
        if not name:
            ui.notify(t("saved_searches.name_required"), color="warning")
            return
        search_id = search_select.value
        if not search_id:
            ui.notify(t("saved_searches.search_required"), color="warning")
            return
        interval = (interval_input.value or "").strip() or None
        try:
            if editing_id:
                await client.update_collection(
                    token,
                    editing_id,
                    name=name,
                    saved_search_id=None if search_id is None else search_id,
                    refresh_interval="" if interval is None else interval,
                )
            else:
                await client.create_collection(
                    token,
                    name=name,
                    saved_search_id=str(search_id),
                    refresh_interval=interval,
                )
        except CoreError as error:
            ui.notify(f"{t('saved_searches.save_failed')}: {error}", color="negative")
            return
        editor_dialog.close()
        await reload_data()
        ui.notify(t("saved_searches.saved"), color="positive")

    async def reload_data() -> None:
        nonlocal collections
        try:
            collections = await client.list_collections(token, limit=1000)
        except Exception:  # noqa: BLE001 - keep the cached list on failure
            return
        grid.run_grid_method("setGridOption", "rowData", _rows())

    async def get_selected() -> dict | None:
        try:
            rows = await grid.get_selected_rows()
        except Exception:  # noqa: BLE001
            return None
        return rows[0] if rows else None

    async def edit_selected() -> None:
        row = await get_selected()
        if not row:
            ui.notify(t("saved_searches.select_first"), color="warning")
            return
        full = next((c for c in collections if str(c.get("id")) == str(row.get("id"))), None)
        open_editor(full)

    async def refresh_selected() -> None:
        row = await get_selected()
        if not row:
            ui.notify(t("saved_searches.select_first"), color="warning")
            return
        collection_id = str(row.get("id"))
        with ui.dialog() as confirm, ui.card().classes("w-[30rem] max-w-[92vw]"):
            ui.label(t("saved_searches.refresh_confirm")).classes("text-subtitle1")
            with ui.row().classes("w-full justify-end mt-2 gap-2"):
                ui.button(t("grid.close"), on_click=confirm.close).props("flat")

                async def _do() -> None:
                    confirm.close()
                    try:
                        result = await client.refresh_collection(token, collection_id)
                    except CoreError as error:
                        ui.notify(
                            f"{t('saved_searches.refresh_failed')}: {error}", color="negative"
                        )
                        return
                    ui.notify(
                        t("saved_searches.refreshed").format(count=result.get("count", 0)),
                        color="positive",
                    )
                    await reload_data()

                ui.button(t("saved_searches.refresh"), on_click=_do).props("color=primary")
        confirm.open()

    async def members_selected() -> None:
        row = await get_selected()
        if not row:
            ui.notify(t("saved_searches.select_first"), color="warning")
            return
        collection_id = str(row.get("id"))
        try:
            members = await client.list_collection_members(token, collection_id, limit=1000)
        except CoreError as error:
            ui.notify(f"{t('saved_searches.members_failed')}: {error}", color="negative")
            return
        await _show_members(_name_of(row), members)

    async def delete_selected() -> None:
        row = await get_selected()
        if not row:
            ui.notify(t("saved_searches.select_first"), color="warning")
            return
        collection_id = str(row.get("id"))
        name = _name_of(row)
        with ui.dialog() as confirm, ui.card().classes("w-[28rem] max-w-[90vw]"):
            ui.label(t("saved_searches.delete_confirm").format(name=name)).classes("text-subtitle1")
            with ui.row().classes("w-full justify-end mt-2 gap-2"):
                ui.button(t("grid.close"), on_click=confirm.close).props("flat")

                async def _do() -> None:
                    confirm.close()
                    try:
                        await client.delete_collection(token, collection_id)
                    except CoreError as error:
                        ui.notify(f"{t('saved_searches.delete_failed')}: {error}", color="negative")
                        return
                    await reload_data()
                    ui.notify(t("saved_searches.deleted"), color="positive")

                ui.button(t("saved_searches.delete"), on_click=_do).props("color=negative")
        confirm.open()

    grid.on(
        "cellDoubleClicked",
        lambda e: open_editor(
            next((c for c in collections if str(c.get("id")) == _id_of(e.args.get("data"))), None)
        ),
        ["data"],
    )


async def _show_members(name: str, members: list[dict]) -> None:
    with ui.dialog() as dlg, ui.card().classes("w-[42rem] max-w-[92vw]"):
        ui.label(t("saved_searches.members_title").format(name=name)).classes("text-h6")
        if not members:
            with ui.column().classes("w-full mt-2"):
                ui.label(t("saved_searches.no_members")).classes("text-caption text-gray-500")
        else:
            ui.table(
                columns=[
                    {
                        "name": "rank",
                        "label": t("saved_searches.col_rank"),
                        "field": "rank",
                        "align": "left",
                    },
                    {
                        "name": "label",
                        "label": t("saved_searches.col_member"),
                        "field": "label",
                        "align": "left",
                    },
                    {
                        "name": "type",
                        "label": t("saved_searches.col_type"),
                        "field": "type",
                        "align": "left",
                    },
                    {
                        "name": "status",
                        "label": t("saved_searches.col_status"),
                        "field": "status",
                        "align": "left",
                    },
                ],
                rows=[
                    {
                        "rank": m.get("rank"),
                        "label": m.get("label"),
                        "type": m.get("type"),
                        "status": m.get("status"),
                    }
                    for m in members
                ],
            ).classes("w-full")
        with ui.row().classes("w-full justify-end mt-2 gap-2"):
            ui.button(t("grid.close"), on_click=dlg.close).props("flat")
    dlg.open()
