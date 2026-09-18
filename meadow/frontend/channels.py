"""Channel catalog admin page (`/channels`).

A **channel** is an output channel (webshop, print catalog, marketplace) and a
managed configuration catalog like units or enum sets: ``GET /channels`` lists
it, ``POST``/``PATCH``/``DELETE /channels`` (admin) maintain it. Channels are the
reference for the **channel-specific releases** of an object
(`PUT`/`DELETE /objects/{id}/releases`, see `meadow/frontend/form.py`), for the
grid filter ``?released_for=`` and for exports with the source kind ``channel``.

The shared vocabulary (`RELEASE_STATUSES`), the label helpers and the badge
renderer live in `meadow/frontend/display.py`, so the admin page, the product
form and the grid render channels the same way.
See `plan/detail/workflow-freigaben.md`.
"""

from __future__ import annotations

from typing import Any

from nicegui import app, ui

from meadow.frontend import auth
from meadow.frontend.api import CoreClient, CoreError
from meadow.frontend.config import CORE_URL
from meadow.frontend.display import channel_label
from meadow.frontend.i18n import current_locale, t
from meadow.frontend.layout import PAGE_TIMEOUT, build_shell
from meadow.frontend.multilang import base_lang, multilingual_value_editor


@ui.page("/channels", response_timeout=PAGE_TIMEOUT)
async def channels_page() -> None:
    await build_shell(active="/channels")
    if not auth.is_admin():
        ui.navigate.to("/")
        return
    client = CoreClient(CORE_URL)
    token = app.storage.user.get("token", "")
    locale = current_locale()

    try:
        channels = await client.list_channels(token, limit=1000)
    except Exception:  # noqa: BLE001 - show a friendly message on failure
        ui.label(t("channels.load_failed")).classes("text-subtitle1 text-negative")
        return

    try:
        languages = await client.list_languages(token)
    except Exception:  # noqa: BLE001 - degrade to the UI language only
        languages = []

    def _active_badge(active: bool) -> str:
        if active:
            return (
                '<span class="px-2 py-0.5 rounded text-xs bg-positive text-white">'
                f"{t('channels.active_yes')}</span>"
            )
        return (
            '<span class="px-2 py-0.5 rounded text-xs bg-neutral text-gray-600">'
            f"{t('channels.active_no')}</span>"
        )

    def _rows() -> list[dict]:
        return [
            {
                "id": str(channel.get("id") or ""),
                "code": str(channel.get("code") or ""),
                "label": channel_label(channel, locale),
                "active": _active_badge(bool(channel.get("active", True))),
                "position": int(channel.get("position") or 0),
                "description": str(channel.get("description") or ""),
            }
            for channel in channels
        ]

    with ui.column().classes("w-full p-6"):
        ui.label(t("channels.title")).classes("text-h4")
        ui.label(t("channels.subtitle")).classes("text-subtitle1")

        column_defs = [
            {"headerName": t("channels.col_code"), "field": "code", "width": 180},
            {"headerName": t("channels.col_label"), "field": "label", "width": 240},
            {"headerName": t("channels.col_active"), "field": "active", "width": 130},
            {"headerName": t("channels.col_position"), "field": "position", "width": 110},
            {"headerName": t("channels.col_description"), "field": "description", "flex": 1},
        ]
        grid = ui.aggrid(
            {
                "columnDefs": column_defs,
                "rowData": _rows(),
                "rowSelection": {"mode": "singleRow"},
            },
            html_columns=[i for i, col in enumerate(column_defs) if col["field"] == "active"],
        ).classes("w-full")
        with ui.row().classes("w-full justify-between mt-2 gap-2"):
            ui.button(t("channels.new"), icon="add", on_click=lambda: open_editor(None)).props(
                "flat"
            )
            ui.button(
                t("channels.reload"), icon="refresh", on_click=lambda: reload_channels()
            ).props("flat")

        editor_dialog = ui.dialog()
        with editor_dialog, ui.card().classes("w-[36rem] max-w-[94vw]"):
            editor_title = ui.label("").classes("text-h6")
            state: dict = {"editing_id": None}
            with ui.column().classes("w-full mt-2 gap-2"):
                code_input = (
                    ui.input(t("channels.field_code")).props("filled dense").classes("w-full")
                )
                channel_label_store: dict[str, Any] = {"label": {}}
                channel_label_field = multilingual_value_editor(
                    field_label=t("channels.col_label"),
                    locale=locale,
                    languages=languages,
                    value=None,
                    store=channel_label_store,
                    key="label",
                )
                with ui.row().classes("w-full gap-2"):
                    active_switch = ui.switch(t("channels.field_active"), value=True)
                    position_input = (
                        ui.number(t("channels.field_position"), value=0)
                        .props("filled dense")
                        .classes("w-32")
                    )
                description_input = (
                    ui.input(t("channels.field_description"))
                    .props("filled dense")
                    .classes("w-full")
                )
            with ui.row().classes("w-full justify-between mt-2 gap-2"):
                delete_btn = ui.button(
                    t("channels.delete"), icon="delete", on_click=lambda: do_delete()
                ).props("flat color=negative")
                with ui.row().classes("gap-2"):
                    ui.button(t("grid.close"), on_click=editor_dialog.close).props("flat")
                    ui.button(t("channels.save"), icon="save", on_click=lambda: do_save()).props(
                        "flat"
                    )

        def _fill_editor(channel: dict | None) -> None:
            editing = channel is not None
            channel = channel or {}  # the reads below only run while editing
            editor_title.set_text(t("channels.edit") if editing else t("channels.new"))
            state["editing_id"] = channel.get("id") if editing else None
            delete_btn.visible = editing
            code_input.set_value(str(channel.get("code") or "") if editing else "")
            code_input.disable() if editing else code_input.enable()
            label = (channel.get("label") if editing else {}) or {}
            if not isinstance(label, dict):
                label = {}
            channel_label_store["label"] = dict(label)
            channel_label_field.set_value(
                str(channel_label_store["label"].get(base_lang(locale)) or "")
            )
            active_switch.set_value(bool(channel.get("active", True)) if editing else True)
            position_input.set_value(int(channel.get("position") or 0) if editing else 0)
            description_input.set_value(str(channel.get("description") or "") if editing else "")

        def open_editor(channel: dict | None) -> None:
            _fill_editor(channel)
            editor_dialog.open()

        async def reload_channels() -> None:
            nonlocal channels
            try:
                channels = await client.list_channels(token, limit=1000)
            except Exception:  # noqa: BLE001 - keep the cached list on failure
                return
            grid.run_grid_method("setGridOption", "rowData", _rows())

        async def do_save() -> None:
            editing_id = state.get("editing_id")
            code = (code_input.value or "").strip()
            if not editing_id and not code:
                ui.notify(t("channels.code_required"), color="warning")
                return
            label = dict(channel_label_store["label"] or {})
            try:
                if editing_id:
                    await client.update_channel(
                        token,
                        editing_id,
                        label=label,
                        description=str(description_input.value or ""),
                        active=bool(active_switch.value),
                        position=int(position_input.value or 0),
                    )
                else:
                    await client.create_channel(
                        token,
                        code=code,
                        label=label,
                        description=str(description_input.value or ""),
                        active=bool(active_switch.value),
                        position=int(position_input.value or 0),
                    )
            except CoreError as error:
                ui.notify(f"{t('channels.save_failed')}: {error}", color="negative")
                return
            editor_dialog.close()
            await reload_channels()
            ui.notify(t("channels.saved"), color="positive")

        async def do_delete() -> None:
            editing_id = state.get("editing_id")
            if not editing_id:
                return
            code = code_input.value or ""
            with ui.dialog() as confirm, ui.card().classes("w-[28rem] max-w-[90vw]"):
                ui.label(t("channels.delete_confirm").format(code=code)).classes("text-subtitle1")
                with ui.row().classes("w-full justify-end mt-2 gap-2"):
                    ui.button(t("grid.close"), on_click=confirm.close).props("flat")

                    async def _do() -> None:
                        confirm.close()
                        try:
                            await client.delete_channel(token, editing_id)
                        except CoreError as error:
                            ui.notify(f"{t('channels.delete_failed')}: {error}", color="negative")
                            return
                        editor_dialog.close()
                        await reload_channels()
                        ui.notify(t("channels.deleted"), color="positive")

                    ui.button(t("channels.delete"), on_click=_do).props("color=negative")
            confirm.open()

        def _channel_for(data: object) -> dict | None:
            """Resolves a double-click payload to the full API channel record."""
            cid = str(data.get("id") or "") if isinstance(data, dict) else str(data or "")
            if not cid:
                return None
            return next((c for c in channels if str(c.get("id")) == cid), None)

        grid.on(
            "cellDoubleClicked",
            lambda e: open_editor(_channel_for(e.args.get("data"))),
            ["data"],
        )
