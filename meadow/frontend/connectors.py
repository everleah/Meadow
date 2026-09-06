"""Admin page for connector configurations (admin/superadmin only).

Lists persisted inbound/outbound connectors (``GET /connectors``) and provides
create/edit (``POST``/``PATCH /connectors``), enable/disable (``PATCH`` with
``enabled``) and delete (``DELETE /connectors/{id}``). The ``parameters`` field
is free-form JSON (URL, method, headers, filter, retry_policy, coalesce,
match_by_key, …) and is edited as a JSON textarea.
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

#: Known connector types (matches the core's VALID_CONNECTOR_TYPES).
CONNECTOR_TYPES = ("webhook", "json_import", "json_export")


def _type_label(connector_type: str) -> str:
    """A localized label for a connector type."""
    return {
        "webhook": t("connectors.type_webhook"),
        "json_import": t("connectors.type_json_import"),
        "json_export": t("connectors.type_json_export"),
    }.get(connector_type, connector_type)


def _enabled_badge(enabled: object) -> str:
    """A coloured badge for the enabled state."""
    if enabled:
        cls = "bg-positive text-white"
        label = t("connectors.state_enabled")
    else:
        cls = "bg-neutral text-gray-600"
        label = t("connectors.state_disabled")
    return f'<span class="px-2 py-0.5 rounded text-xs {cls}">{label}</span>'


def _fmt_dt(value: str | None) -> str:
    """Formats a SurrealDB ISO timestamp for display (local time, no tz)."""
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:  # noqa: BLE001 - leave the raw value on parse failure
        return str(value)


def _json_pretty(value: object) -> str:
    """Pretty-prints a JSON value for the parameters textarea."""
    try:
        return _json.dumps(value, indent=2, ensure_ascii=False)
    except Exception:  # noqa: BLE001 - fall back to the raw string
        return str(value)


def _parse_parameters(text: str | None) -> dict | None:
    """Parses the parameters textarea JSON into a dict (or None on error)."""
    value = (text or "").strip()
    if not value:
        return {}
    try:
        parsed = _json.loads(value)
    except Exception as exc:  # noqa: BLE001 - show a friendly warning
        ui.notify(f"{t('connectors.invalid_json')}: {exc}", color="warning")
        return None  # type: ignore[return-value]
    return parsed if isinstance(parsed, dict) else {}


@ui.page("/connectors", response_timeout=15)
async def connectors_page() -> None:
    await build_shell(active="/connectors")
    if not auth.is_admin():
        ui.navigate.to("/")
        return
    client = CoreClient(CORE_URL)
    token = app.storage.user.get("token", "")

    try:
        connectors = await client.list_connectors(token, limit=1000)
    except Exception:  # noqa: BLE001 - show a friendly message on failure
        ui.label(t("connectors.load_failed")).classes("text-subtitle1 text-negative")
        return

    with ui.column().classes("w-full p-6"):
        ui.label(t("connectors.title")).classes("text-h4")
        ui.label(t("connectors.subtitle")).classes("text-subtitle1")

        def _rows() -> list[dict]:
            return [
                {
                    "id": str(c.get("id")),
                    "name": str(c.get("name") or ""),
                    "type": _type_label(str(c.get("type") or "")),
                    "enabled": _enabled_badge(c.get("enabled")),
                    "updated": _fmt_dt(c.get("updated_at")),
                    "updated_by": str(c.get("updated_by") or "").split(":")[-1],
                }
                for c in connectors
            ]

        grid = ui.aggrid(
            {
                "columnDefs": [
                    {"headerName": t("connectors.col_name"), "field": "name", "width": 220},
                    {"headerName": t("connectors.col_type"), "field": "type", "width": 150},
                    {"headerName": t("connectors.col_enabled"), "field": "enabled", "width": 120},
                    {"headerName": t("connectors.col_updated"), "field": "updated", "width": 190},
                    {
                        "headerName": t("connectors.col_updated_by"),
                        "field": "updated_by",
                        "width": 120,
                    },
                ],
                "rowData": _rows(),
                "rowSelection": {"mode": "singleRow"},
            }
        ).classes("w-full")

        with ui.row().classes("w-full justify-between mt-2 gap-2"):
            with ui.row().classes("gap-2"):
                ui.button(
                    t("connectors.new"),
                    icon="add",
                    on_click=lambda: open_editor(None),
                ).props("flat")
                ui.button(
                    t("connectors.field_enabled"),
                    icon="power_settings_new",
                    on_click=lambda: toggle_enabled(),
                ).props("flat")
                ui.button(
                    t("connectors.delete"), icon="delete", on_click=lambda: do_delete()
                ).props("flat")
            ui.button(t("connectors.reload"), icon="refresh", on_click=lambda: reload_rows()).props(
                "flat"
            )

        editor_dialog = ui.dialog()
        with editor_dialog, ui.card().classes("w-[42rem] max-w-[94vw]"):
            editor_title = ui.label("").classes("text-h6")
            with ui.column().classes("w-full mt-2 gap-2"):
                with ui.row().classes("w-full gap-2"):
                    name_input = (
                        ui.input(t("connectors.field_name")).props("filled dense").classes("grow")
                    )
                    type_select = (
                        ui.select(
                            {typ: _type_label(typ) for typ in CONNECTOR_TYPES},
                            label=t("connectors.field_type"),
                            value="webhook",
                        )
                        .props("filled dense")
                        .classes("w-56")
                    )
                enabled_switch = ui.switch(t("connectors.field_enabled"), value=True)
                ui.label(t("connectors.field_parameters")).classes("text-subtitle2")
                parameters_area = (
                    ui.textarea(t("connectors.field_parameters"))
                    .props("filled dense")
                    .classes("w-full font-mono text-sm")
                )
                ui.label(t("connectors.parameters_hint")).classes("text-caption text-gray-500")
            with ui.row().classes("w-full justify-end mt-2 gap-2"):
                ui.button(t("grid.close"), on_click=editor_dialog.close).props("flat")
                ui.button(
                    t("connectors.save"),
                    icon="save",
                    on_click=lambda: save_connector(),
                ).props("flat")

        def _fill_editor(connector: dict | None) -> None:
            editing = connector is not None
            editor_title.set_text(t("connectors.edit") if editing else t("connectors.new"))
            save_connector.editing_id = connector.get("id") if connector else None  # type: ignore[attr-defined]
            name_input.set_value(str(connector.get("name") or "") if editing else "")
            current_type = str(connector.get("type") or "webhook") if editing else "webhook"
            type_select.set_value(current_type)
            enabled_switch.set_value(bool(connector.get("enabled")) if editing else True)
            params = connector.get("parameters") if editing else None
            parameters_area.set_value(_json_pretty(params) if params is not None else "{}")

        def open_editor(connector: dict | None) -> None:
            _fill_editor(connector)
            editor_dialog.open()

        async def save_connector() -> None:
            editing_id = getattr(save_connector, "editing_id", None)
            name = (name_input.value or "").strip()
            if not name:
                ui.notify(t("connectors.name_required"), color="warning")
                return
            parameters = _parse_parameters(parameters_area.value)
            if parameters is None:
                return
            connector_type = type_select.value or "webhook"
            enabled = bool(enabled_switch.value)
            try:
                if editing_id:
                    await client.update_connector(
                        token,
                        editing_id,
                        name=name,
                        connector_type=connector_type,
                        parameters=parameters,
                        enabled=enabled,
                    )
                else:
                    await client.create_connector(
                        token,
                        name=name,
                        connector_type=connector_type,
                        parameters=parameters,
                        enabled=enabled,
                    )
            except CoreError as error:
                ui.notify(f"{t('connectors.save_failed')}: {error}", color="negative")
                return
            editor_dialog.close()
            await reload_rows()
            ui.notify(t("connectors.saved"), color="positive")

        async def reload_rows() -> None:
            nonlocal connectors
            try:
                connectors = await client.list_connectors(token, limit=1000)
            except Exception:  # noqa: BLE001 - keep the cached list on failure
                return
            grid.run_grid_method("setGridOption", "rowData", _rows())

        async def get_selected() -> dict | None:
            try:
                rows = await grid.get_selected_rows()
            except Exception:  # noqa: BLE001 - degrade to no selection
                rows = []
            if not rows:
                return None
            row = rows[0] if isinstance(rows, list) else rows
            if isinstance(row, dict) and row.get("id"):
                return next((c for c in connectors if str(c.get("id")) == str(row.get("id"))), row)
            return row if isinstance(row, dict) else None

        async def toggle_enabled() -> None:
            connector = await get_selected()
            if not connector:
                ui.notify(t("connectors.no_selection"), color="warning")
                return
            connector_id = str(connector.get("id"))
            new_enabled = not bool(connector.get("enabled"))
            try:
                await client.update_connector(token, connector_id, enabled=new_enabled)
            except CoreError as error:
                ui.notify(f"{t('connectors.save_failed')}: {error}", color="negative")
                return
            await reload_rows()
            ui.notify(t("connectors.saved"), color="positive")

        async def do_delete() -> None:
            connector = await get_selected()
            if not connector:
                ui.notify(t("connectors.no_selection"), color="warning")
                return
            connector_id = str(connector.get("id"))
            name = str(connector.get("name") or "")
            message = t("connectors.delete_confirm").format(name=name)
            with ui.dialog() as confirm, ui.card().classes("w-[30rem] max-w-[90vw]"):
                ui.label(message).classes("text-subtitle1")
                with ui.row().classes("w-full justify-end mt-2 gap-2"):
                    ui.button(t("grid.close"), on_click=confirm.close).props("flat")
                    ui.button(
                        t("connectors.delete"),
                        on_click=lambda: do_delete_confirm(connector_id, confirm),
                    ).props("color=negative")
            confirm.open()

        async def do_delete_confirm(connector_id: str, confirm: ui.dialog) -> None:
            confirm.close()
            try:
                await client.delete_connector(token, connector_id)
            except CoreError as error:
                ui.notify(f"{t('connectors.delete_failed')}: {error}", color="negative")
                return
            await reload_rows()
            ui.notify(t("connectors.deleted"), color="positive")
