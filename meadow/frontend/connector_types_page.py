"""Connector type registry (`/connector-types`, admin only).

The three built-in types live in the core (Rust literals). This page registers a
**fourth** type from data: name, label key, direction and the same parameter schema
the built-in types are served in (`GET /connector-types`), so the connector form
renders it without a UI change.

A registered type is shown as **`wired: false`** — no consumer reads its parameters
yet (`plan/detail/konnektor-konfiguration.md` §14.1). The page says that plainly
instead of letting someone believe a connector of that type delivers anything.
"""

from __future__ import annotations

import json

import httpx
from nicegui import app, ui

from meadow.frontend import auth
from meadow.frontend.api import CoreClient, CoreError
from meadow.frontend.config import CORE_URL
from meadow.frontend.i18n import t
from meadow.frontend.layout import PAGE_TIMEOUT, build_shell

#: A starter schema with one text field — the smallest thing that validates.
TEMPLATE: dict = {
    "groups": [
        {
            "key": "main",
            "label_key": "connectors.group_target",
            "fields": [
                {
                    "key": "endpoint",
                    "type": "string",
                    "widget": "text",
                    "required": True,
                    "label_key": "connectors.summary_url",
                }
            ],
        }
    ]
}


def field_count(schema: object) -> int:
    """How many fields a stored schema declares (the list's size column)."""
    if not isinstance(schema, dict):
        return 0
    groups = schema.get("groups")
    if not isinstance(groups, list):
        return 0
    total = 0
    for group in groups:
        fields = group.get("fields") if isinstance(group, dict) else None
        if isinstance(fields, list):
            total += len(fields)
    return total


@ui.page("/connector-types", response_timeout=PAGE_TIMEOUT)
async def connector_types_page() -> None:
    await build_shell(active="/connector-types")
    if not auth.is_admin():
        ui.navigate.to("/")
        return
    client = CoreClient(CORE_URL)
    token = app.storage.user.get("token", "")

    with ui.column().classes("w-full p-6"):
        ui.label(t("connector_types.title")).classes("text-h4")
        ui.label(t("connector_types.subtitle")).classes("text-subtitle1")
        ui.label(t("connector_types.unwired_note")).classes("text-caption text-gray-500")

        state: dict[str, object] = {"name": None}
        rows: list[dict] = []

        table = ui.table(
            columns=[
                {"name": "name", "label": t("connector_types.col_name"), "field": "name"},
                {
                    "name": "direction",
                    "label": t("connector_types.col_direction"),
                    "field": "direction",
                },
                {"name": "fields", "label": t("connector_types.col_fields"), "field": "fields"},
            ],
            rows=rows,
            row_key="name",
            selection="single",
        ).classes("w-full")

        with ui.dialog() as editor, ui.card().classes("w-[52rem] max-w-[95vw]"):
            title = ui.label(t("connector_types.new")).classes("text-h6")
            name_input = ui.input(t("connector_types.name")).props("filled dense").classes("w-64")
            label_input = (
                ui.input(t("connector_types.label_key")).props("filled dense").classes("w-64")
            )
            direction_select = (
                ui.select(
                    {
                        "inbound": t("connector_types.direction_inbound"),
                        "outbound": t("connector_types.direction_outbound"),
                    },
                    label=t("connector_types.direction"),
                    value="inbound",
                )
                .props("filled dense")
                .classes("w-48")
            )
            ui.label(t("connector_types.schema_hint")).classes("text-caption text-gray-500")
            schema_area = (
                ui.textarea(t("connector_types.schema"))
                .props("filled")
                .classes("w-full h-72 font-mono text-sm")
                .mark("type-schema")
            )
            with ui.row().classes("w-full justify-between mt-2"):
                ui.button(
                    t("connector_types.template"),
                    icon="auto_fix_high",
                    on_click=lambda: schema_area.set_value(
                        json.dumps(TEMPLATE, indent=2, ensure_ascii=False)
                    ),
                ).props("flat")
                with ui.row().classes("gap-2"):
                    ui.button(t("grid.close"), on_click=editor.close).props("flat")
                    ui.button(
                        t("connector_types.save"), icon="save", on_click=lambda: save()
                    ).props("flat")

        def fill(spec: dict | None) -> None:
            """Fills the dialog from a served type spec (or empties it for a new one)."""
            editing = spec is not None
            spec = spec or {}
            title.set_text(t("connector_types.edit") if editing else t("connector_types.new"))
            state["name"] = str(spec.get("type") or "") if editing else None
            name_input.set_value(str(spec.get("type") or ""))
            # The name is the identity of the type: renaming would orphan connectors.
            name_input.set_enabled(not editing)
            label_input.set_value(str(spec.get("label_key") or ""))
            direction_select.set_value(str(spec.get("direction") or "inbound"))
            # The served entry carries the groups directly; the registry stores them
            # under `schema` — the same document, one nesting level apart.
            schema = {"groups": spec.get("groups")} if spec.get("groups") else TEMPLATE
            schema_area.set_value(json.dumps(schema, indent=2, ensure_ascii=False))

        async def save() -> None:
            name = (name_input.value or "").strip()
            if not name:
                ui.notify(t("connector_types.name_required"), color="warning")
                return
            try:
                schema = json.loads(schema_area.value or "{}")
            except ValueError as error:
                ui.notify(f"{t('connector_types.schema_invalid')}: {error}", color="negative")
                return
            if not isinstance(schema, dict):
                ui.notify(t("connector_types.schema_invalid"), color="negative")
                return
            label_key = (label_input.value or "").strip()
            direction = str(direction_select.value or "inbound")
            try:
                if state["name"]:
                    await client.update_connector_type(
                        token,
                        str(state["name"]),
                        label_key=label_key,
                        direction=direction,
                        schema=schema,
                    )
                else:
                    await client.create_connector_type(
                        token, name=name, label_key=label_key, direction=direction, schema=schema
                    )
            except CoreError as error:
                ui.notify(f"{t('connector_types.save_failed')}: {error}", color="negative")
                return
            editor.close()
            await reload_rows()
            ui.notify(t("connector_types.saved"), color="positive")

        async def reload_types() -> None:
            nonlocal rows
            try:
                document = await client.list_connector_types(token)
            except CoreError, httpx.HTTPError:
                rows = []
                table.update_rows([])
                return
            # Only the registered ones are editable here; the built-ins come from the
            # core's Rust literals and would be a lie in this list.
            rows = [
                {
                    "name": entry.get("type"),
                    "direction": str(entry.get("direction") or ""),
                    "fields": field_count(entry),
                    "_raw": entry,
                }
                for entry in document
                if isinstance(entry, dict)
                and str(entry.get("type") or "") not in ("webhook", "json_import", "json_export")
            ]
            table.update_rows(rows)

        async def reload_rows() -> None:
            await reload_types()

        def new_type() -> None:
            fill(None)
            editor.open()

        async def edit_selected() -> None:
            selected = table.selected
            if not selected:
                ui.notify(t("connector_types.no_selection"), color="warning")
                return
            raw = selected[0].get("_raw")
            fill(raw if isinstance(raw, dict) else None)
            editor.open()

        async def do_delete() -> None:
            selected = table.selected
            if not selected:
                ui.notify(t("connector_types.no_selection"), color="warning")
                return
            name = str(selected[0].get("name") or "")
            with ui.dialog() as confirm, ui.card():
                ui.label(t("connector_types.confirm_delete").format(name=name))
                with ui.row().classes("w-full justify-end gap-2"):
                    ui.button(t("grid.close"), on_click=confirm.close).props("flat")
                    ui.button(
                        t("connector_types.delete"),
                        on_click=lambda: delete_confirmed(name, confirm),
                    ).props("flat color=negative")
            confirm.open()

        async def delete_confirmed(name: str, confirm: ui.dialog) -> None:
            confirm.close()
            try:
                await client.delete_connector_type(token, name)
            except CoreError as error:
                # `409`: a connector still uses it — the message says which rule.
                ui.notify(f"{t('connector_types.delete_failed')}: {error}", color="negative")
                return
            await reload_rows()
            ui.notify(t("connector_types.deleted"), color="positive")

        with ui.row().classes("w-full gap-2 mt-2"):
            ui.button(t("connector_types.new"), icon="add", on_click=lambda: new_type()).props(
                "flat"
            )
            ui.button(t("connector_types.edit"), icon="edit", on_click=edit_selected).props("flat")
            ui.button(
                t("connector_types.delete"), icon="delete", on_click=lambda: do_delete()
            ).props("flat")

        table.on("rowDblclick", lambda event: edit_selected())
        await reload_types()
