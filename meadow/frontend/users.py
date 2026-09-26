"""Admin page for users and groups (admin/superadmin only).

The page combines both onto a single screen (mirroring `/attribute-definitions`,
which hosts attribute definitions + attribute groups):
- **Users**: an AG-Grid (``GET /users``) with a filter bar (name / role /
  status, analogous to the attribute-definitions filter) and a create/edit/delete
  dialog (``POST``/``PATCH``/``DELETE /users``). For `superadmin` a **"Login als"**
  action impersonates the selected user (``POST /auth/impersonate/{user_id}``).
- **Groups**: an expandable section (``GET /groups``) with a create/edit/delete
  dialog (``POST``/``PATCH``/``DELETE /groups``; fields `name`,
  `grant_permissions`, `deny_permissions`, `scope`, `branding`).
"""

from __future__ import annotations

import json as _json

import httpx
from nicegui import app, ui

from meadow.frontend import auth
from meadow.frontend.api import CoreClient, CoreError
from meadow.frontend.config import CORE_URL
from meadow.frontend.filters import filter_select
from meadow.frontend.i18n import t
from meadow.frontend.layout import PAGE_TIMEOUT, build_shell

ROLES = ("superadmin", "admin", "editor", "viewer")
STATUSES = ("active", "suspended")
LOCALES = ("en", "de", "fr")


def _split_join(value: object) -> str:
    """Joins a string list for display."""
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return ""


def _password_badge(user: dict) -> str:
    """A coloured badge for the password requirement of a user record.

    Absent `must_change_password` counts as `true` (the user still has to set
    their own password), mirroring the core and the login hint.
    """
    required = bool(user.get("must_change_password", True))
    if required:
        return '<span class="px-2 py-0.5 rounded text-xs bg-warning text-white">new</span>'
    return '<span class="px-2 py-0.5 rounded text-xs bg-positive text-white">ok</span>'


@ui.page("/users", response_timeout=PAGE_TIMEOUT)
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
    except CoreError, httpx.HTTPError:  # show a friendly message on failure
        ui.label(t("users.load_failed")).classes("text-subtitle1 text-negative")
        return
    try:
        groups = await client.list_groups(token, limit=500)
    except CoreError, httpx.HTTPError:  # degrade to no groups
        groups = []

    with ui.column().classes("w-full p-6"):
        ui.label(t("users.title")).classes("text-h4")
        ui.label(t("users.subtitle")).classes("text-subtitle1")

        # --------------------------------------------------------------- #
        # Users                                                           #
        # --------------------------------------------------------------- #
        user_filters: dict[str, str] = {"name": "", "role": "", "status": ""}

        def _user_matches(u: dict) -> bool:
            if user_filters["name"]:
                q = user_filters["name"].lower()
                hay = f"{u.get('username', '')} {u.get('email', '')}".lower()
                if q not in hay:
                    return False
            roles = u.get("roles") or []
            if user_filters["role"] and user_filters["role"] not in roles:
                return False
            return not (user_filters["status"] and str(u.get("status")) != user_filters["status"])

        def _user_rows() -> list[dict]:
            return [
                {
                    "id": str(u.get("id")),
                    "username": str(u.get("username") or ""),
                    "email": str(u.get("email") or ""),
                    "roles": ", ".join(u.get("roles") or []),
                    "status": str(u.get("status") or ""),
                    "locale": str(u.get("locale") or ""),
                    "password": _password_badge(u),
                    "updated": str(u.get("updated_at") or ""),
                }
                for u in users
                if _user_matches(u)
            ]

        # Filter bar (free-text name / role / status).
        with ui.row().classes("w-full items-center gap-2 mt-2"):
            (
                ui.input(t("users.filter_name"))
                .props("filled dense")
                .classes("w-48")
                .on_value_change(lambda e: _set_user_filter("name", str(e.value)))
            )
            filter_select(
                {r: r for r in ROLES},
                label=t("users.filter_role"),
                classes="w-44",
                on_change=lambda value: _set_user_filter("role", str(value)),
            )
            filter_select(
                {s: s for s in STATUSES},
                label=t("users.filter_status"),
                classes="w-40",
                on_change=lambda value: _set_user_filter("status", str(value)),
            )

        def _apply_user_filters() -> None:
            users_grid.run_grid_method("setGridOption", "rowData", _user_rows())

        def _set_user_filter(key: str, value: str) -> None:
            user_filters[key] = value
            _apply_user_filters()

        users_column_defs = [
            {"headerName": t("users.col_username"), "field": "username", "width": 180},
            {"headerName": t("users.col_email"), "field": "email", "width": 220},
            {"headerName": t("users.col_roles"), "field": "roles", "width": 180},
            {"headerName": t("users.col_status"), "field": "status", "width": 120},
            {"headerName": t("users.col_password"), "field": "password", "width": 130},
            {"headerName": t("users.col_locale"), "field": "locale", "width": 90},
            {"headerName": t("jobs.col_updated"), "field": "updated", "width": 190},
        ]
        users_grid = ui.aggrid(
            {
                "columnDefs": users_column_defs,
                "rowData": _user_rows(),
                "rowSelection": {"mode": "singleRow"},
            },
            html_columns=[
                i for i, column in enumerate(users_column_defs) if column["field"] == "password"
            ],
        ).classes("w-full")
        with ui.row().classes("w-full justify-between mt-2 gap-2"):
            with ui.row().classes("gap-2"):
                ui.button(
                    t("users.new"), icon="add", on_click=lambda: open_user_editor(None)
                ).props("flat")
                if can_impersonate:
                    ui.button(
                        t("users.impersonate"),
                        icon="swap_horiz",
                        on_click=lambda: impersonate_selected(),
                    ).props("flat")
            ui.button(t("jobs.reload"), icon="refresh", on_click=lambda: reload_users()).props(
                "flat"
            )

        user_dialog = ui.dialog()
        with user_dialog, ui.card().classes("w-[36rem] max-w-[94vw]"):
            user_title = ui.label("").classes("text-h6")
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
                    ui.select(
                        {r: r for r in ROLES},
                        label=t("users.field_roles"),
                        value=[],
                        multiple=True,
                    )
                    .props("filled dense")
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
                must_change_switch = ui.switch(t("users.must_change_password"), value=True)
                ui.label(t("users.must_change_password_hint")).classes("text-caption text-gray-500")
            with ui.row().classes("w-full justify-between mt-2 gap-2"):
                user_delete_btn = ui.button(
                    t("attr_def.delete"), icon="delete", on_click=lambda: delete_user_selected()
                ).props("flat color=negative")
                with ui.row().classes("gap-2"):
                    ui.button(t("grid.close"), on_click=user_dialog.close).props("flat")
                    ui.button(t("users.save"), icon="save", on_click=lambda: save_user()).props(
                        "flat"
                    )

        def _fill_user_form(user: dict | None) -> None:
            editing = user is not None
            # `None` means "create": fall back to an empty record so every field
            # is read/written uniformly (and never from `None`).
            record = user or {}
            user_title.set_text(t("users.edit") if editing else t("users.new"))
            user_delete_btn.visible = editing
            username_input.set_value(str(record.get("username") or ""))
            if editing:
                username_input.disable()
            else:
                username_input.enable()
            email_input.set_value(str(record.get("email") or ""))
            password_input.set_value("")
            # Only accept a real list: a display row carries a joined string.
            roles = record.get("roles")
            roles_select.set_value([str(role) for role in roles] if isinstance(roles, list) else [])
            status_select.set_value(str(record.get("status") or "active"))
            locale_select.set_value(str(record.get("locale") or "en"))
            # Absent counts as "must change" (mirrors the core).
            must_change_switch.set_value(bool(record.get("must_change_password", True)))

        def open_user_editor(user: dict | None) -> None:
            _fill_user_form(user)
            save_user.editing_id = user.get("id") if user else None  # type: ignore[attr-defined]
            user_dialog.open()

        async def reload_users() -> None:
            nonlocal users
            try:
                users = await client.list_users(token, limit=500)
            except CoreError, httpx.HTTPError:  # keep the cached list on failure
                return
            users_grid.run_grid_method("setGridOption", "rowData", _user_rows())

        async def save_user() -> None:
            editing_id = getattr(save_user, "editing_id", None)
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
                        "must_change_password": bool(must_change_switch.value),
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
                        must_change_password=bool(must_change_switch.value),
                    )
            except CoreError as error:
                ui.notify(f"{t('users.save_failed')}: {error}", color="negative")
                return
            user_dialog.close()
            await reload_users()
            ui.notify(t("users.saved"), color="positive")

        async def delete_user_selected() -> None:
            editing_id = getattr(save_user, "editing_id", None)
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
                        user_dialog.close()
                        await reload_users()
                        ui.notify(t("users.deleted"), color="positive")

                    ui.button(t("attr_def.delete"), on_click=_do).props("color=negative")
            confirm.open()

        async def impersonate_selected() -> None:
            try:
                selected = await users_grid.get_selected_rows()
            except Exception:  # noqa: BLE001 - the AG Grid bridge has no narrow contract
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
            """Resolves a grid row/double-click payload to the **full** user record.

            The grid rows carry display strings (e.g. `roles` joined with
            commas), so the raw record must be resolved by id before editing —
            otherwise saving would send the display string as the role list.
            """
            user_id = data.get("id") if isinstance(data, dict) else data
            return next((u for u in users if str(u.get("id")) == str(user_id)), None)

        users_grid.on(
            "cellDoubleClicked",
            lambda e: open_user_editor(_user_for(e.args.get("data"))),
            ["data"],
        )

        # --------------------------------------------------------------- #
        # Groups (expandable section, like the attribute groups on the    #
        # attribute-definitions page)                                    #
        # --------------------------------------------------------------- #
        with (
            ui.expansion(t("groups.title"), icon="group")
            .classes("w-full mt-4")
            .props("header-class=bg-green-2 text-green-9")
        ):
            # The catalog drives the permission matrix: its areas, their order and the role
            # defaults come from the core, so the editor cannot offer a key the write path
            # would reject. Without it (a failed load) the matrix stays empty and a save
            # **omits** the permission lists instead of sending empty ones — the danger is not
            # a missing label, it is a group silently losing its rights.
            try:
                catalog = await client.list_permissions(token)
            except CoreError, httpx.HTTPError:
                catalog = {}
                ui.notify(t("groups.catalog_failed"), color="warning")

            def _roles_label(permission: dict) -> str:
                """The lowest role that holds the permission by default (`Viewer`).

                The matrix shows the role default next to the switch: the empty state means
                "as the roles say", and this is what they say. The catalog carries the
                **expanded** roles, so the first one is the lowest.
                """
                roles = permission.get("roles") or []
                if not isinstance(roles, list) or not roles:
                    return ""
                return t(f"role.{roles[0]}")

            def _group_rows() -> list[dict]:
                return [
                    {
                        "id": str(g.get("id")),
                        "name": str(g.get("name") or ""),
                        "grant": _split_join(g.get("grant_permissions")),
                        "deny": _split_join(g.get("deny_permissions")),
                        "scope": _json.dumps(g.get("scope") or {}, ensure_ascii=False),
                        "branding": _json.dumps(g.get("branding") or {}, ensure_ascii=False),
                    }
                    for g in groups
                ]

            groups_grid = ui.aggrid(
                {
                    "columnDefs": [
                        {"headerName": t("groups.col_name"), "field": "name", "width": 200},
                        {"headerName": t("groups.col_grant"), "field": "grant", "width": 220},
                        {"headerName": t("groups.col_deny"), "field": "deny", "width": 180},
                        {"headerName": t("groups.col_scope"), "field": "scope", "width": 200},
                        {"headerName": t("groups.col_branding"), "field": "branding", "width": 200},
                    ],
                    "rowData": _group_rows(),
                    "rowSelection": {"mode": "singleRow"},
                }
            ).classes("w-full")
            with ui.row().classes("w-full justify-between mt-2 gap-2"):
                ui.button(
                    t("groups.new"), icon="add", on_click=lambda: open_group_editor(None)
                ).props("flat")
                ui.button(t("jobs.reload"), icon="refresh", on_click=lambda: reload_groups()).props(
                    "flat"
                )

            group_dialog = ui.dialog()
            with (
                group_dialog,
                ui.card().classes("w-[56rem] max-w-[94vw] max-h-[85vh] overflow-y-auto"),
            ):
                group_title = ui.label("").classes("text-h6")
                with ui.column().classes("w-full mt-2 gap-2"):
                    name_input = (
                        ui.input(t("groups.field_name")).props("filled dense").classes("w-full")
                    )
                    permission_toggles: dict[str, ui.toggle] = {}
                    with ui.column().classes("w-full gap-1"):
                        ui.label(t("groups.perm_title")).classes("text-subtitle2")
                        ui.label(t("groups.perm_hint")).classes("text-caption text-grey-7")
                        for area in catalog.get("areas") or []:
                            area_key = str(area.get("key") or "")
                            area_permissions = [
                                permission
                                for permission in catalog.get("permissions") or []
                                if permission.get("area") == area_key
                            ]
                            if not area_permissions:
                                continue
                            ui.label(t(str(area.get("label_key") or area_key))).classes(
                                "text-caption text-grey-7 mt-2"
                            )
                            for permission in area_permissions:
                                key = str(permission.get("key") or "")
                                action = str(permission.get("action") or key)
                                label_key = str(permission.get("label_key") or key)
                                label = t(label_key)
                                if label == label_key:
                                    # No translation for the full key yet: the action under the
                                    # area header reads the same ("Object · read").
                                    label = t(f"perm.action.{action}")
                                with ui.row().classes("w-full items-center gap-2 no-wrap"):
                                    ui.label(label).classes("flex-1").tooltip(
                                        str(permission.get("description") or "")
                                    )
                                    ui.label(_roles_label(permission)).classes(
                                        "text-caption text-grey-6"
                                    )
                                    permission_toggles[key] = ui.toggle(
                                        {
                                            "inherit": t("groups.perm_inherit"),
                                            "grant": t("groups.perm_grant"),
                                            "deny": t("groups.perm_deny"),
                                        },
                                        value="inherit",
                                    ).props("dense unelevated")
                    scope_area = (
                        ui.textarea(t("groups.field_scope")).props("filled dense").classes("w-full")
                    )
                    branding_area = (
                        ui.textarea(t("groups.field_branding"))
                        .props("filled dense")
                        .classes("w-full")
                    )
                with ui.row().classes("w-full justify-between mt-2 gap-2"):
                    group_delete_btn = ui.button(
                        t("attr_def.delete"),
                        icon="delete",
                        on_click=lambda: delete_group_selected(),
                    ).props("flat color=negative")
                    with ui.row().classes("gap-2"):
                        ui.button(t("grid.close"), on_click=group_dialog.close).props("flat")
                        ui.button(
                            t("groups.save"), icon="save", on_click=lambda: save_group()
                        ).props("flat")

            def _fill_group_form(group: dict | None) -> None:
                editing = group is not None
                group = group or {}  # the reads below only run while editing
                group_title.set_text(t("groups.edit") if editing else t("groups.new"))
                group_delete_btn.visible = editing
                name_input.set_value(str(group.get("name") or "") if editing else "")
                granted = set(group.get("grant_permissions") or []) if editing else set()
                denied = set(group.get("deny_permissions") or []) if editing else set()
                for key, toggle in permission_toggles.items():
                    if key in granted:
                        toggle.value = "grant"
                    elif key in denied:
                        toggle.value = "deny"
                    else:
                        toggle.value = "inherit"
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

            def open_group_editor(group: dict | None) -> None:
                _fill_group_form(group)
                save_group.editing_id = group.get("id") if group else None  # type: ignore[attr-defined]
                group_dialog.open()

            def _parse_json(text: str, label: str) -> dict | None:
                if not text.strip():
                    return {}
                try:
                    value = _json.loads(text)
                except ValueError as exc:  # a body that is not JSON shows a warning
                    ui.notify(f"{label}: {exc}", color="warning")
                    return None
                return value if isinstance(value, dict) else {}

            async def reload_groups() -> None:
                nonlocal groups
                try:
                    groups = await client.list_groups(token, limit=500)
                except CoreError, httpx.HTTPError:  # keep the cached list on failure
                    return
                groups_grid.run_grid_method("setGridOption", "rowData", _group_rows())

            async def save_group() -> None:
                editing_id = getattr(save_group, "editing_id", None)
                name = (name_input.value or "").strip()
                if not name:
                    ui.notify(t("groups.name_required"), color="warning")
                    return
                # An empty matrix means the catalog never arrived: omit the lists (an update
                # keeps what is stored) rather than sending empty ones.
                grant = (
                    [key for key, toggle in permission_toggles.items() if toggle.value == "grant"]
                    if permission_toggles
                    else None
                )
                deny = (
                    [key for key, toggle in permission_toggles.items() if toggle.value == "deny"]
                    if permission_toggles
                    else None
                )
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
                group_dialog.close()
                await reload_groups()
                ui.notify(t("groups.saved"), color="positive")

            async def delete_group_selected() -> None:
                editing_id = getattr(save_group, "editing_id", None)
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
                            group_dialog.close()
                            await reload_groups()
                            ui.notify(t("groups.deleted"), color="positive")

                        ui.button(t("attr_def.delete"), on_click=_do).props("color=negative")
                confirm.open()

            def _group_for(data: object) -> dict | None:
                """Resolves a double-click payload to the **raw** group.

                The grid hands over the row it built — permissions joined into strings — so a
                dict is resolved back by id: the form needs the lists themselves, not their
                rendering. (Reading the row as if it were the group made the matrix start empty
                and a save send empty lists, which wiped the group's rights; found 2026-09-26 by
                saving the seeded `webshop` group and reading it back.)
                """
                group_id = data.get("id") if isinstance(data, dict) else data
                return next((g for g in groups if str(g.get("id")) == str(group_id)), None)

            groups_grid.on(
                "cellDoubleClicked",
                lambda e: open_group_editor(_group_for(e.args.get("data"))),
                ["data"],
            )
