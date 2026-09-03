"""Admin page for users (admin/superadmin only).

Lists users (``GET /users``) and provides a create/edit/delete dialog
(``POST``/``PATCH``/``DELETE /users``). For `superadmin` a **"Login als"**
action impersonates the selected user (``POST /auth/impersonate/{user_id}``),
switching the session to that user's roles (with an impersonation banner).
"""

from __future__ import annotations

from nicegui import app, ui

from meadow.frontend import auth
from meadow.frontend.api import CoreClient, CoreError
from meadow.frontend.config import CORE_URL
from meadow.frontend.i18n import t
from meadow.frontend.layout import build_shell

ROLES = ("superadmin", "admin", "editor", "viewer")
STATUSES = ("active", "suspended")
LOCALES = ("en", "de", "fr")


@ui.page("/users", response_timeout=15)
async def users_page() -> None:
    await build_shell(active="/users")
    if not auth.is_admin():
        ui.navigate.to("/")
        return
    can_impersonate = auth.is_superadmin()
    client = CoreClient(CORE_URL)
    token = app.storage.user.get("token", "")

    try:
        users = await client.list_users(token, limit=500)
    except Exception:  # noqa: BLE001 - show a friendly message on failure
        ui.label(t("users.load_failed")).classes("text-subtitle1 text-negative")
        return

    with ui.column().classes("w-full p-6"):
        ui.label(t("users.title")).classes("text-h4")
        ui.label(t("users.subtitle")).classes("text-subtitle1")

        def _rows() -> list[dict]:
            return [
                {
                    "id": str(u.get("id")),
                    "username": str(u.get("username") or ""),
                    "email": str(u.get("email") or ""),
                    "roles": ", ".join(u.get("roles") or []),
                    "status": str(u.get("status") or ""),
                    "locale": str(u.get("locale") or ""),
                    "updated": str(u.get("updated_at") or ""),
                }
                for u in users
            ]

        grid = ui.aggrid(
            {
                "columnDefs": [
                    {"headerName": t("users.col_username"), "field": "username", "width": 180},
                    {"headerName": t("users.col_email"), "field": "email", "width": 220},
                    {"headerName": t("users.col_roles"), "field": "roles", "width": 180},
                    {"headerName": t("users.col_status"), "field": "status", "width": 120},
                    {"headerName": t("users.col_locale"), "field": "locale", "width": 90},
                    {"headerName": t("jobs.col_updated"), "field": "updated", "width": 190},
                ],
                "rowData": _rows(),
                "rowSelection": {"mode": "singleRow"},
            }
        ).classes("w-full")
        with ui.row().classes("w-full justify-between mt-2 gap-2"):
            with ui.row().classes("gap-2"):
                ui.button(t("users.new"), icon="add", on_click=lambda: open_editor(None)).props(
                    "flat"
                )
                if can_impersonate:
                    ui.button(
                        t("users.impersonate"),
                        icon="swap_horiz",
                        on_click=lambda: impersonate_selected(),
                    ).props("flat")
            ui.button(t("jobs.reload"), icon="refresh", on_click=lambda: reload()).props("flat")

        form_dialog = ui.dialog()
        with form_dialog, ui.card().classes("w-[36rem] max-w-[94vw]"):
            title_label = ui.label("").classes("text-h6")
            with ui.column().classes("w-full mt-2 gap-2"):
                username_input = (
                    ui.input(t("users.field_username")).props("filled dense").classes("w-full")
                )
                email_input = (
                    ui.input(t("users.field_email")).props("filled dense").classes("w-full")
                )
                password_input = (
                    ui.input(t("users.field_password"), password=True, password_toggle_button=True)
                    .props("filled dense")
                    .classes("w-full")
                )
                roles_select = (
                    ui.select({r: r for r in ROLES}, label=t("users.field_roles"), value=[])
                    .props("filled dense multiple")
                    .classes("w-full")
                )
                with ui.row().classes("w-full gap-2"):
                    status_select = (
                        ui.select(
                            {s: s for s in STATUSES},
                            label=t("users.field_status"),
                            value="active",
                        )
                        .props("filled dense")
                        .classes("grow")
                    )
                    locale_select = (
                        ui.select(
                            {code: code for code in LOCALES},
                            label=t("users.field_locale"),
                            value="en",
                        )
                        .props("filled dense")
                        .classes("w-40")
                    )
            with ui.row().classes("w-full justify-between mt-2 gap-2"):
                delete_btn = ui.button(
                    t("attr_def.delete"), icon="delete", on_click=lambda: delete_selected()
                ).props("flat color=negative")
                with ui.row().classes("gap-2"):
                    ui.button(t("grid.close"), on_click=form_dialog.close).props("flat")
                    ui.button(t("users.save"), icon="save", on_click=lambda: save()).props("flat")

        def _fill_form(user: dict | None) -> None:
            editing = user is not None
            title_label.set_text(t("users.edit") if editing else t("users.new"))
            delete_btn.visible = editing
            username_input.set_value(str(user.get("username") or "") if editing else "")
            if editing:
                username_input.disable()
            else:
                username_input.enable()
            email_input.set_value(str(user.get("email") or "") if editing else "")
            password_input.set_value("")
            roles_select.set_value(list(user.get("roles") or []) if editing else [])
            status_select.set_value(str(user.get("status") or "active"))
            locale_select.set_value(str(user.get("locale") or "en"))

        def open_editor(user: dict | None) -> None:
            _fill_form(user)
            save.editing_id = user.get("id") if user else None  # type: ignore[attr-defined]
            form_dialog.open()

        async def reload() -> None:
            nonlocal users
            try:
                users = await client.list_users(token, limit=500)
            except Exception:  # noqa: BLE001 - keep the cached list on failure
                return
            grid.run_grid_method("setGridOption", "rowData", _rows())

        async def save() -> None:
            editing_id = getattr(save, "editing_id", None)
            username = (username_input.value or "").strip()
            email = (email_input.value or "").strip()
            roles = list(roles_select.value or [])
            if not username or not email:
                ui.notify(t("users.name_email_required"), color="warning")
                return
            if not editing_id and not password_input.value:
                ui.notify(t("users.password_required"), color="warning")
                return
            try:
                if editing_id:
                    payload: dict = {
                        "email": email,
                        "roles": roles,
                        "status": status_select.value or "active",
                        "locale": locale_select.value or "en",
                    }
                    if password_input.value:
                        payload["password"] = password_input.value
                    await client.update_user(token, editing_id, **payload)
                else:
                    await client.create_user(
                        token,
                        username=username,
                        email=email,
                        password=password_input.value or "",
                        roles=roles,
                        locale=locale_select.value or "en",
                        status=status_select.value or "active",
                    )
            except CoreError as error:
                ui.notify(f"{t('users.save_failed')}: {error}", color="negative")
                return
            form_dialog.close()
            await reload()
            ui.notify(t("users.saved"), color="positive")

        async def delete_selected() -> None:
            editing_id = getattr(save, "editing_id", None)
            if not editing_id:
                return
            username = username_input.value or ""
            with ui.dialog() as confirm, ui.card().classes("w-[28rem] max-w-[90vw]"):
                ui.label(t("users.delete_confirm").format(name=username)).classes("text-subtitle1")
                with ui.row().classes("w-full justify-end mt-2 gap-2"):
                    ui.button(t("grid.close"), on_click=confirm.close).props("flat")

                    async def _do() -> None:
                        confirm.close()
                        try:
                            await client.delete_user(token, editing_id)
                        except CoreError as error:
                            ui.notify(f"{t('users.delete_failed')}: {error}", color="negative")
                            return
                        form_dialog.close()
                        await reload()
                        ui.notify(t("users.deleted"), color="positive")

                    ui.button(t("attr_def.delete"), on_click=_do).props("color=negative")
            confirm.open()

        async def impersonate_selected() -> None:
            try:
                selected = await grid.get_selected_rows()
            except Exception:  # noqa: BLE001 - degrade to no selection
                selected = []
            if not selected:
                ui.notify(t("users.no_selection"), color="warning")
                return
            row = selected[0] if isinstance(selected, list) else selected
            oid = row.get("id") if isinstance(row, dict) else None
            if not oid:
                ui.notify(t("users.no_selection"), color="warning")
                return
            imp = await client.impersonate_user(token, oid)
            me = await client.me(imp["token"])
            app.storage.user.update(
                token=imp["token"],
                sub=me.sub,
                username=me.username,
                roles=me.roles,
                impersonator=me.impersonator,
                authenticated=True,
            )
            ui.navigate.to("/")

        def _user_for(data: object) -> dict | None:
            """Resolves a double-click payload to a user row."""
            if isinstance(data, dict):
                return data
            return next((u for u in users if str(u.get("id")) == str(data)), None)

        grid.on("cellDoubleClicked", lambda e: open_editor(_user_for(e.args.get("data"))), ["data"])
