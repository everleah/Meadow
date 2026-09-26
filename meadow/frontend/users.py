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
from meadow.frontend.display import OBJECT_STATUSES, resolve_value
from meadow.frontend.filters import filter_select
from meadow.frontend.i18n import current_locale, t
from meadow.frontend.layout import PAGE_TIMEOUT, build_shell
from meadow.frontend.multilang import base_lang
from meadow.frontend.object_picker import open_object_picker
from meadow.frontend.relation_roles import (
    COLLECTION_MEMBER_DEF,
    COLLECTION_TYPE,
    PRIMARY_CATEGORY_DEF,
    PRIMARY_CATEGORY_TYPE,
)

ROLES = ("superadmin", "admin", "editor", "viewer")
STATUSES = ("active", "suspended")
LOCALES = ("en", "de", "fr")

#: The scope blocks the assistant offers: block key -> (label key, relation key).
#: A field rule carries its own field name, a reference rule the built-in relation it
#: walks - the two relations are the only ones the scope language knows a meaning for.
SCOPE_BLOCKS: dict[str, tuple[str, str | None]] = {
    "type": ("groups.scope_field_type", None),
    "status": ("groups.scope_field_status", None),
    "category": ("groups.scope_category", PRIMARY_CATEGORY_DEF),
    "collection": ("groups.scope_collection", COLLECTION_MEMBER_DEF),
}


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

            # Labels for the scope card: a rule stores the bare type name, the row and the
            # sentence show the catalogue's label.
            type_labels: dict[str, str] = {}
            try:
                object_types = await client.list_object_types(token, limit=1000)
            except CoreError, httpx.HTTPError:  # the rows fall back to the type name
                object_types = []
            for entry in object_types:
                if not isinstance(entry, dict) or not entry.get("name"):
                    continue
                name = str(entry["name"])
                type_labels[name] = (
                    resolve_value(entry.get("label"), base_lang(current_locale())) or name
                )

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

            groups_grid = (
                ui.aggrid(
                    {
                        "columnDefs": [
                            {"headerName": t("groups.col_name"), "field": "name", "width": 200},
                            {"headerName": t("groups.col_grant"), "field": "grant", "width": 220},
                            {"headerName": t("groups.col_deny"), "field": "deny", "width": 180},
                            {"headerName": t("groups.col_scope"), "field": "scope", "width": 200},
                            {
                                "headerName": t("groups.col_branding"),
                                "field": "branding",
                                "width": 200,
                            },
                        ],
                        "rowData": _group_rows(),
                        "rowSelection": {"mode": "singleRow"},
                    }
                )
                .classes("w-full")
                .mark("groups-grid")
            )
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
                                    permission_toggles[key] = (
                                        ui.toggle(
                                            {
                                                "inherit": t("groups.perm_inherit"),
                                                "grant": t("groups.perm_grant"),
                                                "deny": t("groups.perm_deny"),
                                            },
                                            value="inherit",
                                        )
                                        .props("dense unelevated")
                                        .props(f"data-key={key}")
                                        .mark("permission-toggle")
                                    )
                    # ----------------------------------------------------- #
                    # Data scope (ABAC)                                     #
                    # ----------------------------------------------------- #
                    # Building blocks instead of a JSON textarea: the scope decides
                    # *which data* the group sees, and nobody writes that document on
                    # the first try. The counter answers what the blocks mean before
                    # anything is saved - the editing admin is normally not a member
                    # of the group, so only the core can count it
                    # (`POST /groups/preview-scope`).
                    scope_state: dict[str, object] = {
                        "rules": [],
                        "original": {},
                        "unreadable": False,
                    }
                    #: Labels of the picked targets: a rule stores ids, a row shows names.
                    scope_labels: dict[str, str] = {}
                    with ui.column().classes("w-full gap-1"):
                        ui.label(t("groups.scope_title")).classes("text-subtitle2")
                        ui.label(t("groups.scope_hint")).classes("text-caption text-grey-7")
                        scope_sentence = ui.label("").classes("text-body2").mark("scope-sentence")
                        scope_host = ui.column().classes("w-full gap-1")
                        scope_buttons = ui.row().classes("w-full items-center gap-2")
                        scope_count = (
                            ui.label("").classes("text-caption text-grey-6").mark("scope-count")
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
                        ).props("flat").mark("group-save")

            def _scope_rules() -> list[dict]:
                """The rules of the document on screen, in the shape the core stores."""
                rules = scope_state["rules"]
                if not isinstance(rules, list):
                    return []
                return [rule for rule in rules if isinstance(rule, dict)]

            def _rule_block(rule: dict) -> str | None:
                """Which assistant block a rule is (`None`: not one of the four).

                An unknown rule is not a mistake to repair but a rule to keep: a scope
                written by an import or an earlier version carries one, and the card shows
                it verbatim rather than dropping what it cannot render.
                """
                field = rule.get("field")
                if isinstance(field, str):
                    return field if field in ("type", "status") else None
                reference = rule.get("reference")
                relation = reference.get("type") if isinstance(reference, dict) else None
                for block, (_label_key, relation_key) in SCOPE_BLOCKS.items():
                    if relation_key is not None and relation_key == relation:
                        return block
                return None

            def _rule_values(rule: dict) -> list[str]:
                """The values a rule names (a field list or a target list)."""
                value = rule.get("value")
                if isinstance(value, list):
                    return [str(entry) for entry in value]
                reference = rule.get("reference")
                if isinstance(reference, dict):
                    out = reference.get("out")
                    if isinstance(out, str):
                        return [out]
                    if isinstance(out, list):
                        return [str(entry) for entry in out]
                return []

            def _type_options(values: list[str]) -> dict[str, str]:
                """Select options of the object-type block.

                A stored type the catalogue does not list stays selectable: a `ui.select`
                whose value is not among its options raises and takes the whole page down
                with it (the trap `display.status_options` documents for statuses).
                """
                options = dict(type_labels)
                for value in values:
                    options.setdefault(value, value)
                return options

            def _target_labels(values: list[str]) -> str:
                return ", ".join(scope_labels.get(value, value) for value in values)

            def _type_values(values: list[str]) -> str:
                return ", ".join(type_labels.get(value, value) for value in values)

            def _apply_rule_values(index: int, values: list[str]) -> None:
                """Stores the values of one block (no rendering: see the callers)."""
                rules = _scope_rules()
                if index >= len(rules):
                    return
                rule = dict(rules[index])
                reference = rule.get("reference")
                if isinstance(reference, dict):
                    reference = dict(reference)
                    reference["out"] = values
                    rule["reference"] = reference
                else:
                    rule["value"] = values
                rules[index] = rule
                scope_state["rules"] = rules

            def _set_count(text: str, *, error: bool = False) -> None:
                scope_count.set_text(text)
                scope_count.classes(
                    replace="text-caption text-negative" if error else "text-caption text-grey-6"
                )

            def _scope_sentence() -> str:
                """The document in words: the blocks as one sentence, ANDed."""
                rules = _scope_rules()
                if scope_state["unreadable"]:
                    return ""
                if any(not _rule_values(rule) for rule in rules):
                    return t("groups.scope_incomplete")
                parts: list[str] = []
                for rule in rules:
                    block = _rule_block(rule)
                    values = _rule_values(rule)
                    if block is None or not values:
                        continue
                    shown = _type_values(values) if block == "type" else _target_labels(values)
                    parts.append(t(f"groups.scope_phrase_{block}").format(values=shown))
                if not parts:
                    return t("groups.scope_sentence_all")
                return t("groups.scope_sentence").format(
                    parts=f" {t('groups.scope_and')} ".join(parts)
                )

            async def refresh_scope_count() -> None:
                """Asks the core what the document on screen would show.

                A block without values is not a scope yet — and a document the core would
                refuse is not worth a round trip, so the counter says what is missing
                instead. The core's own message shows when it rejects the document anyway
                (an unknown relation, a target that no longer exists).
                """
                if scope_state["unreadable"] or any(
                    not _rule_values(rule) for rule in _scope_rules()
                ):
                    _set_count(t("groups.scope_count_incomplete"))
                    return
                rules = _scope_rules()
                try:
                    result = await client.preview_scope(token, {"rules": rules} if rules else {})
                except CoreError as error:
                    _set_count(t("groups.scope_count_failed").format(error=error), error=True)
                    return
                except httpx.HTTPError:
                    _set_count(
                        t("groups.scope_count_failed").format(
                            error=t("groups.scope_count_offline")
                        ),
                        error=True,
                    )
                    return
                _set_count(
                    t("groups.scope_count").format(
                        count=result.get("count"), total=result.get("total")
                    )
                )

            def _render_block_row(index: int, rule: dict) -> None:
                """One block: its own control plus the remove button.

                The row leads with its own add order, so a field block keeps its select —
                rebuilding it inside its own change event would close the dropdown
                mid-selection.
                """
                block = _rule_block(rule)
                values = _rule_values(rule)
                with ui.row().classes("w-full items-center gap-2 no-wrap"):
                    ui.label(
                        t(SCOPE_BLOCKS[block][0]) if block else t("groups.scope_other")
                    ).classes("w-32 text-caption text-grey-7")
                    if block in ("type", "status"):
                        options = (
                            _type_options(values)
                            if block == "type"
                            else {status: status for status in OBJECT_STATUSES}
                        )
                        select = (
                            ui.select(options, value=values, multiple=True)
                            .props("filled dense")
                            .classes("flex-1")
                        )
                        select.on_value_change(
                            lambda event, i=index: _set_field_values(i, event.value)
                        )
                    elif block:
                        ui.label(_target_labels(values) or t("groups.scope_no_values")).classes(
                            "flex-1 text-caption"
                        )
                        ui.button(
                            t("groups.scope_pick"),
                            icon="search",
                            on_click=lambda i=index: pick_targets(i),
                        ).props("flat dense size=sm")
                    else:
                        ui.label(_json.dumps(rule, ensure_ascii=False)).classes(
                            "flex-1 text-caption font-mono break-all"
                        ).tooltip(t("groups.scope_other_hint"))
                    ui.button(icon="close", on_click=lambda i=index: remove_block(i)).props(
                        "flat dense size=sm"
                    ).tooltip(t("groups.scope_remove"))

            def render_scope() -> None:
                """Renders the rows, the add buttons and the sentence (not the counter)."""
                rules = _scope_rules()
                scope_host.clear()
                with scope_host:
                    if scope_state["unreadable"]:
                        ui.label(t("groups.scope_unreadable")).classes("text-caption text-negative")
                    elif not rules:
                        ui.label(t("groups.scope_empty")).classes("text-caption text-grey-7")
                    for index, rule in enumerate(rules):
                        _render_block_row(index, rule)
                scope_buttons.clear()
                taken = {_rule_block(rule) for rule in rules}
                with scope_buttons:
                    for block, (label_key, _relation) in SCOPE_BLOCKS.items():
                        if block in taken:
                            continue
                        ui.button(
                            t("groups.scope_add").format(block=t(label_key)),
                            icon="add",
                            on_click=lambda b=block: add_block(b),
                        ).props("flat dense").mark(f"scope-add-{block}")
                scope_sentence.set_text(_scope_sentence())

            async def add_block(block: str) -> None:
                """Adds a block, empty: it carries values with the first pick."""
                _label_key, relation = SCOPE_BLOCKS[block]
                rule: dict = (
                    {"field": block, "operator": "in", "value": []}
                    if relation is None
                    else {"reference": {"type": relation, "out": []}}
                )
                scope_state["rules"] = [*_scope_rules(), rule]
                render_scope()
                await refresh_scope_count()

            async def remove_block(index: int) -> None:
                rules = _scope_rules()
                if index < len(rules):
                    del rules[index]
                scope_state["rules"] = rules
                render_scope()
                await refresh_scope_count()

            async def _set_field_values(index: int, values: object) -> None:
                """Stores a select's values and refreshes the preview.

                No rebuild on purpose: the select on screen already shows the choice.
                """
                chosen = [str(entry) for entry in values] if isinstance(values, list) else []
                _apply_rule_values(index, chosen)
                scope_sentence.set_text(_scope_sentence())
                await refresh_scope_count()

            async def pick_targets(index: int) -> None:
                """Opens the picker for a categorisation or a collection block."""
                rules = _scope_rules()
                if index >= len(rules):
                    return
                block = _rule_block(rules[index])
                if block not in ("category", "collection"):
                    return

                async def on_pick(hits: object) -> None:
                    """Merges the picked targets into the block.

                    The picker starts empty on every open, so a second pick adds to the
                    first: the chips are the selection, removal happens on the row.
                    """
                    picked = hits if isinstance(hits, list) else [hits]
                    chosen = _rule_values(_scope_rules()[index])
                    for hit in picked:
                        if not isinstance(hit, dict):
                            continue
                        target = str(hit.get("id") or "")
                        if not target:
                            continue
                        scope_labels.setdefault(target, str(hit.get("label") or target))
                        if target not in chosen:
                            chosen.append(target)
                    _apply_rule_values(index, chosen)
                    render_scope()
                    await refresh_scope_count()

                taxonomy = PRIMARY_CATEGORY_TYPE if block == "category" else None
                await open_object_picker(
                    client=client,
                    token=token,
                    locale=current_locale(),
                    title=t(SCOPE_BLOCKS[block][0]),
                    on_pick=on_pick,
                    target_types=[taxonomy or COLLECTION_TYPE],
                    multi=True,
                    taxonomy=taxonomy,
                )

            async def _resolve_target_labels(targets: list[str]) -> None:
                """Fills the label cache for targets a stored document already names.

                A rule stores record ids, the rows and the sentence show names; the picker
                fills the cache for what it hands over, this covers a document written
                earlier (one bulk read, ids the core cannot resolve stay ids).
                """
                missing = sorted({target for target in targets if target not in scope_labels})
                if not missing:
                    return
                try:
                    objects = await client.list_objects(
                        token, ids=missing, limit=len(missing), locale=current_locale()
                    )
                except CoreError, httpx.HTTPError:  # rows fall back to the ids
                    return
                for object_ in objects:
                    attributes = object_.get("attributes") or {}
                    key = str(object_.get("id") or "")
                    label = resolve_value(attributes.get("name"), base_lang(current_locale()))
                    scope_labels[key] = label or str(attributes.get("code") or key)

            async def _prepare_scope(group: dict | None) -> None:
                """Reads the stored scope into the card."""
                document = (group or {}).get("scope")
                rules = document.get("rules") if isinstance(document, dict) else None
                known = isinstance(rules, list) and all(isinstance(rule, dict) for rule in rules)
                if isinstance(document, dict) and (document == {} or known):
                    scope_state["rules"] = list(rules or [])
                    scope_state["unreadable"] = False
                else:
                    # Not a document the card can show: it is kept untouched (`save_group`
                    # writes the original back) instead of being reduced to nothing.
                    scope_state["rules"] = []
                    scope_state["unreadable"] = True
                scope_state["original"] = document if isinstance(document, dict) else {}
                targets = [
                    value for rule in _scope_rules() for value in _rule_values(rule) if ":" in value
                ]
                await _resolve_target_labels(targets)
                render_scope()

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
                branding_area.set_value(
                    _json.dumps(group.get("branding") or {}, indent=2, ensure_ascii=False)
                    if editing
                    else ""
                )

            async def open_group_editor(group: dict | None) -> None:
                """Fills the form and renders the stored scope into the card."""
                _fill_group_form(group)
                save_group.editing_id = group.get("id") if group else None  # type: ignore[attr-defined]
                group_dialog.open()
                await _prepare_scope(group)
                await refresh_scope_count()

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
                # The scope comes from the card: the blocks are the document. A document
                # the card could not show is written back **unchanged** - dropping rules
                # nobody rendered would silently widen the group to the whole catalogue.
                rules = _scope_rules()
                original = scope_state["original"]
                scope = (
                    dict(original)
                    if scope_state["unreadable"] and isinstance(original, dict)
                    else ({"rules": rules} if rules else {})
                )
                branding = _parse_json(branding_area.value or "", t("groups.field_branding"))
                if branding is None:
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
