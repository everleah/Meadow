"""Admin page for groups (admin/superadmin only).

Lists groups (``GET /groups``) and provides a create/edit/delete dialog
(``POST``/``PATCH``/``DELETE /groups``; fields `name`, `grant_permissions`,
`deny_permissions`, `scope`, `branding`).
"""

from __future__ import annotations

import json as _json

from nicegui import app, ui

from meadow.frontend import auth
from meadow.frontend.api import CoreClient, CoreError
from meadow.frontend.config import CORE_URL
from meadow.frontend.i18n import t
from meadow.frontend.layout import build_shell


def _join(value: object) -> str:
    """Joins a string list for display."""
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return ""


@ui.page("/groups", response_timeout=15)
async def groups_page() -> None:
    build_shell(active="/groups")
    if not auth.is_admin():
        ui.navigate.to("/")
        return
    client = CoreClient(CORE_URL)
    token = app.storage.user.get("token", "")

    try:
        groups = await client.list_groups(token, limit=500)
    except Exception:  # noqa: BLE001 - show a friendly message on failure
        ui.label(t("groups.load_failed")).classes("text-subtitle1 text-negative")
        return

    with ui.column().classes("w-full p-6"):
        ui.label(t("groups.title")).classes("text-h4")
        ui.label(t("groups.subtitle")).classes("text-subtitle1")

        def _rows() -> list[dict]:
            return [
                {
                    "id": str(g.get("id")),
                    "name": str(g.get("name") or ""),
                    "grant": _join(g.get("grant_permissions")),
                    "deny": _join(g.get("deny_permissions")),
                    "scope": _json.dumps(g.get("scope") or {}, ensure_ascii=False),
                    "branding": _json.dumps(g.get("branding") or {}, ensure_ascii=False),
                }
                for g in groups
            ]

        grid = ui.aggrid(
            {
                "columnDefs": [
                    {"headerName": t("groups.col_name"), "field": "name", "width": 200},
                    {"headerName": t("groups.col_grant"), "field": "grant", "width": 220},
                    {"headerName": t("groups.col_deny"), "field": "deny", "width": 180},
                    {"headerName": t("groups.col_scope"), "field": "scope", "width": 200},
                    {"headerName": t("groups.col_branding"), "field": "branding", "width": 200},
                ],
                "rowData": _rows(),
                "rowSelection": {"mode": "singleRow"},
            }
        ).classes("w-full")
        with ui.row().classes("w-full justify-end mt-2 gap-2"):
            ui.button(t("groups.new"), icon="add", on_click=lambda: open_editor(None)).props("flat")
            ui.button(t("jobs.reload"), icon="refresh", on_click=lambda: reload()).props("flat")

        form_dialog = ui.dialog()
        with form_dialog, ui.card().classes("w-[40rem] max-w-[94vw] max-h-[85vh] overflow-y-auto"):
            title_label = ui.label("").classes("text-h6")
            with ui.column().classes("w-full mt-2 gap-2"):
                name_input = (
                    ui.input(t("groups.field_name")).props("filled dense").classes("w-full")
                )
                grant_input = (
                    ui.input(t("groups.field_grant")).props("filled dense").classes("w-full")
                )
                deny_input = (
                    ui.input(t("groups.field_deny")).props("filled dense").classes("w-full")
                )
                scope_area = (
                    ui.textarea(t("groups.field_scope")).props("filled dense").classes("w-full")
                )
                branding_area = (
                    ui.textarea(t("groups.field_branding")).props("filled dense").classes("w-full")
                )
            with ui.row().classes("w-full justify-between mt-2 gap-2"):
                delete_btn = ui.button(
                    t("attr_def.delete"), icon="delete", on_click=lambda: delete_selected()
                ).props("flat color=negative")
                with ui.row().classes("gap-2"):
                    ui.button(t("grid.close"), on_click=form_dialog.close).props("flat")
                    ui.button(t("groups.save"), icon="save", on_click=lambda: save()).props("flat")

        def _split(value: str) -> list[str]:
            return [item.strip() for item in value.replace(";", ",").split(",") if item.strip()]

        def _fill_form(group: dict | None) -> None:
            editing = group is not None
            title_label.set_text(t("groups.edit") if editing else t("groups.new"))
            delete_btn.visible = editing
            name_input.set_value(str(group.get("name") or "") if editing else "")
            grant_input.set_value(_join(group.get("grant_permissions")) if editing else "")
            deny_input.set_value(_join(group.get("deny_permissions")) if editing else "")
            scope_area.set_value(
                _json.dumps(group.get("scope") or {}, indent=2, ensure_ascii=False)
                if editing
                else ""
            )
            branding_area.set_value(
                _json.dumps(group.get("branding") or {}, indent=2, ensure_ascii=False)
                if editing
                else ""
            )

        def open_editor(group: dict | None) -> None:
            _fill_form(group)
            save.editing_id = group.get("id") if group else None  # type: ignore[attr-defined]
            form_dialog.open()

        def _parse_json(text: str, label: str) -> dict | None:
            if not text.strip():
                return {}
            try:
                value = _json.loads(text)
            except Exception as exc:  # noqa: BLE001 - show a friendly warning
                ui.notify(f"{label}: {exc}", color="warning")
                return None
            return value if isinstance(value, dict) else {}

        async def reload() -> None:
            nonlocal groups
            try:
                groups = await client.list_groups(token, limit=500)
            except Exception:  # noqa: BLE001 - keep the cached list on failure
                return
            grid.run_grid_method("setGridOption", "rowData", _rows())

        async def save() -> None:
            editing_id = getattr(save, "editing_id", None)
            name = (name_input.value or "").strip()
            if not name:
                ui.notify(t("groups.name_required"), color="warning")
                return
            grant = _split(grant_input.value or "")
            deny = _split(deny_input.value or "")
            scope = _parse_json(scope_area.value or "", t("groups.field_scope"))
            branding = _parse_json(branding_area.value or "", t("groups.field_branding"))
            if scope is None or branding is None:
                return
            try:
                if editing_id:
                    await client.update_group(
                        token,
                        editing_id,
                        name=name,
                        grant_permissions=grant,
                        deny_permissions=deny,
                        scope=scope,
                        branding=branding,
                    )
                else:
                    await client.create_group(
                        token,
                        name=name,
                        grant_permissions=grant,
                        deny_permissions=deny,
                        scope=scope,
                        branding=branding,
                    )
            except CoreError as error:
                ui.notify(f"{t('groups.save_failed')}: {error}", color="negative")
                return
            form_dialog.close()
            await reload()
            ui.notify(t("groups.saved"), color="positive")

        async def delete_selected() -> None:
            editing_id = getattr(save, "editing_id", None)
            if not editing_id:
                return
            name = name_input.value or ""
            with ui.dialog() as confirm, ui.card().classes("w-[28rem] max-w-[90vw]"):
                ui.label(t("groups.delete_confirm").format(name=name)).classes("text-subtitle1")
                with ui.row().classes("w-full justify-end mt-2 gap-2"):
                    ui.button(t("grid.close"), on_click=confirm.close).props("flat")

                    async def _do() -> None:
                        confirm.close()
                        try:
                            await client.delete_group(token, editing_id)
                        except CoreError as error:
                            ui.notify(f"{t('groups.delete_failed')}: {error}", color="negative")
                            return
                        form_dialog.close()
                        await reload()
                        ui.notify(t("groups.deleted"), color="positive")

                    ui.button(t("attr_def.delete"), on_click=_do).props("color=negative")
            confirm.open()

        def _group_for(data: object) -> dict | None:
            """Resolves a double-click payload to a group row."""
            if isinstance(data, dict):
                return data
            return next((g for g in groups if str(g.get("id")) == str(data)), None)

        grid.on(
            "cellDoubleClicked", lambda e: open_editor(_group_for(e.args.get("data"))), ["data"]
        )
