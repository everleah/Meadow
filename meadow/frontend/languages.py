"""Admin page for the language tree (admin/superadmin only).

Displays the `language_tree` nodes as a tree (root -> base languages ->
regional variants) and allows adding, editing, re-parenting and deleting
nodes, plus toggling `active` (data maintenance) and `is_ui` (UI language).
"""

from __future__ import annotations

from nicegui import app, ui

from meadow.frontend import config
from meadow.frontend.api import CoreClient, CoreError
from meadow.frontend.grid import resolve_value
from meadow.frontend.i18n import current_locale, t
from meadow.frontend.layout import PAGE_TIMEOUT, build_shell
from meadow.frontend.object_picker import open_structure_picker


def _parent_key(parent: object | None) -> str | None:
    """Returns the parent's code (e.g. ``en``) from a record key or ``None``."""
    if isinstance(parent, str) and parent.startswith("lang_"):
        return parent[5:]
    return None


@ui.page("/languages", response_timeout=PAGE_TIMEOUT)
async def languages_page() -> None:
    await build_shell(active="/languages")
    locale = current_locale()
    client = CoreClient(config.CORE_URL)
    token = app.storage.user.get("token", "")

    with ui.column().classes("w-full p-6"):
        ui.label(t("lang_admin.title")).classes("text-h4")
        ui.label(t("lang_admin.subtitle")).classes("text-subtitle1")

        try:
            langs = await client.list_languages(token)
        except Exception:  # noqa: BLE001 - show a friendly message on failure
            ui.label(t("lang_admin.load_failed")).classes("text-subtitle1 text-negative")
            return

        selected: dict = {"code": None, "lang": None}
        edit_state: dict[str, object] = {
            "code": None,
            "label": {},
            "active": True,
            "is_ui": False,
            "parent": "",
        }
        name_input_ref: dict[str, ui.element | None] = {"el": None}
        # Active data languages (not root) for the multilingual label editor.
        name_langs = [
            lang for lang in langs if str(lang.get("code")) != "root" and lang.get("active", True)
        ]
        if not any(str(lang.get("code")) == locale for lang in name_langs):
            name_langs = [{"code": locale, "label": {locale: locale}}] + name_langs

        def build_tree_nodes() -> list[dict]:
            """Turns the flat language list into nested `ui.tree` nodes."""
            by_code = {str(lang.get("code")): lang for lang in langs if lang.get("code")}
            children: dict[str, list[str]] = {}
            roots: list[str] = []
            for lang in langs:
                code = str(lang.get("code"))
                parent_code = _parent_key(lang.get("parent"))
                if parent_code is None:
                    roots.append(code)
                else:
                    children.setdefault(parent_code, []).append(code)

            def attach(code: str) -> dict:
                node: dict = {
                    "id": code,
                    "label": resolve_value(by_code[code].get("label"), locale) or code,
                }
                kids = [attach(c) for c in sorted(children.get(code, []))]
                if kids:
                    node["children"] = kids
                return node

            return [attach(code) for code in sorted(roots)]

        async def handle_select(e) -> None:
            code = e.value if hasattr(e, "value") else None
            if not code:
                selected.update({"code": None, "lang": None})
            else:
                lang = next((lang for lang in langs if str(lang.get("code")) == code), None)
                selected.update({"code": code, "lang": lang})
            detail_host.clear()
            await render_detail()

        # --- multilingual label editing (product-form style) -----------------
        name_dialog = ui.dialog()
        with name_dialog, ui.card().classes("w-[34rem] max-w-[90vw]"):
            ui.label(t("lang_admin.label_edit")).classes("text-h6")
            name_inputs: dict[str, ui.input] = {}
            with ui.column().classes("w-full gap-2 mt-2"):
                for lang in name_langs:
                    code = str(lang.get("code"))
                    name_inputs[code] = (
                        ui.input(resolve_value(lang.get("label"), locale) or code)
                        .props("filled dense")
                        .classes("w-full")
                    )
            with ui.row().classes("w-full justify-end mt-2 gap-2"):
                ui.button(t("grid.close"), on_click=name_dialog.close).props("flat")
                ui.button(t("grid.save"), icon="save", on_click=lambda: save_name()).props("flat")

        def open_name() -> None:
            label = dict(edit_state["label"] or {})
            for code, inp in name_inputs.items():
                inp.set_value(str(label.get(code) or ""))
            name_dialog.open()

        def save_name() -> None:
            label: dict[str, str] = {}
            for code, inp in name_inputs.items():
                if inp.value:
                    label[code] = inp.value
            edit_state["label"] = label
            el = name_input_ref.get("el")
            if el is not None:
                el.set_value(str(label.get(locale) or ""))
            name_dialog.close()

        def _set_label_locale(value: str) -> None:
            label = dict(edit_state["label"] or {})
            if value:
                label[locale] = value
            else:
                label.pop(locale, None)
            edit_state["label"] = label

        # Node id of the synthetic "top level" entry in the parent picker.
        TOP_ID = "__top__"

        def parent_children() -> dict[str, list[str]]:
            """Maps a parent code (``""`` = top level) to its child codes."""
            children: dict[str, list[str]] = {}
            for lang in langs:
                code = str(lang.get("code"))
                children.setdefault(_parent_key(lang.get("parent")) or "", []).append(code)
            return children

        def descendant_codes(code: str) -> set[str]:
            """All codes below ``code`` (guards against parent cycles)."""
            children = parent_children()
            out: set[str] = set()
            stack = list(children.get(code, []))
            while stack:
                current = stack.pop()
                if current in out:
                    continue
                out.add(current)
                stack.extend(children.get(current, []))
            return out

        def parent_display() -> str:
            """Renders the currently chosen parent (empty value = top level)."""
            parent = str(edit_state.get("parent") or "")
            if not parent:
                return t("lang_admin.root")
            code = _parent_key(parent) or parent
            lang = next((item for item in langs if str(item.get("code")) == code), None)
            return resolve_value((lang or {}).get("label"), locale) or code

        async def pick_parent(exclude_code: str | None, label_el: ui.label) -> None:
            """Picks the parent language with the shared structure-tree dialog."""
            blocked = descendant_codes(exclude_code) if exclude_code else set()
            if exclude_code:
                blocked.add(exclude_code)
            by_code = {str(item.get("code")): item for item in langs}
            children = parent_children()

            def lang_node(code: str) -> dict:
                allowed = code != "root" and code not in blocked
                return {
                    "id": f"lang_{code}",
                    "label": resolve_value(by_code[code].get("label"), locale) or code,
                    "type": code,
                    "allowed": allowed,
                    "selectable": allowed,
                    "lazy": bool(children.get(code)),
                }

            async def load_children(node_id: str) -> list[dict]:
                if node_id == TOP_ID:
                    top = {
                        "id": TOP_ID,
                        "label": t("lang_admin.root"),
                        "type": "",
                        "allowed": True,
                        "selectable": True,
                        "lazy": False,
                    }
                    return [top, *[lang_node(code) for code in sorted(children.get("root", []))]]
                code = node_id[5:] if node_id.startswith("lang_") else node_id
                return [lang_node(child) for child in sorted(children.get(code, []))]

            async def on_pick(hit: dict) -> None:
                node_id = str(hit.get("id") or "")
                edit_state["parent"] = "" if node_id == TOP_ID else node_id
                label_el.set_text(parent_display())

            await open_structure_picker(
                title=t("lang_admin.pick_parent"),
                on_pick=on_pick,
                root_id=TOP_ID,
                load_children=load_children,
                hint=t("lang_admin.parent_hint"),
            )

        def parent_row(exclude_code: str | None) -> ui.label:
            """Parent-language row: current value plus the structure-picker button."""
            with ui.row().classes("w-full items-center gap-2").style("flex-wrap: nowrap"):
                with ui.column().classes("grow gap-0"):
                    ui.label(t("lang_admin.parent")).classes("text-caption text-gray-500")
                    label = ui.label("").classes("text-body2")

                async def open_pick() -> None:
                    await pick_parent(exclude_code, label)

                ui.button(icon="account_tree", on_click=open_pick).props("flat dense").tooltip(
                    t("form.pick_in_structure")
                )
            return label

        # --- create dialog (multilingual label, product-form style) ----------
        create_dialog = ui.dialog()
        with create_dialog, ui.card().classes("w-[36rem] max-w-[92vw]"):
            title_label = ui.label("").classes("text-h6")
            with ui.column().classes("w-full mt-2 gap-2"):
                create_code_input = (
                    ui.input(t("lang_admin.code")).props("filled dense").classes("w-full")
                )
                with ui.row().classes("w-full items-center gap-2"):
                    create_label_input = (
                        ui.input(t("lang_admin.label") + f" ({locale.upper()})")
                        .props("filled dense")
                        .classes("grow")
                    )
                    ui.button(icon="translate").props("flat round dense").on_click(
                        open_name
                    ).tooltip(t("form.languages_edit"))
                create_parent_label = parent_row(None)
                with ui.row().classes("w-full gap-6"):
                    create_active_check = ui.checkbox(t("lang_admin.active"))
                    create_is_ui_check = ui.checkbox(t("lang_admin.is_ui"))
            with ui.row().classes("w-full justify-end mt-2 gap-2"):
                ui.button(t("grid.close"), on_click=create_dialog.close).props("flat")
                ui.button(t("lang_admin.save"), icon="save", on_click=lambda: save_create()).props(
                    "flat"
                )

        def open_create(parent: str | None = None) -> None:
            edit_state["code"] = None
            edit_state["label"] = {}
            edit_state["active"] = True
            edit_state["is_ui"] = False
            edit_state["parent"] = parent or ""
            title_label.set_text(t("lang_admin.new_child") if parent else t("lang_admin.new"))
            create_code_input.set_value("")
            create_label_input.set_value("")
            create_label_input.on_value_change(lambda e: _set_label_locale(str(e.value)))
            name_input_ref["el"] = create_label_input
            create_parent_label.set_text(parent_display())
            create_active_check.set_value(True)
            create_is_ui_check.set_value(False)
            create_dialog.open()

        async def save_create() -> None:
            code = (create_code_input.value or "").strip()
            label = {k: v for k, v in (edit_state.get("label") or {}).items() if v}
            if not code:
                ui.notify(t("lang_admin.code_required"), color="warning")
                return
            if not label:
                ui.notify(t("lang_admin.label_required"), color="warning")
                return
            parent = str(edit_state.get("parent") or "")
            active = bool(edit_state.get("active", True))
            is_ui = bool(edit_state.get("is_ui", False))
            try:
                await client.create_language(
                    token,
                    code=code,
                    parent=parent or None,
                    label=label,
                    active=active,
                    is_ui=is_ui,
                )
            except CoreError as exc:
                ui.notify(f"{t('lang_admin.save_failed')}: {exc}", color="negative")
                return
            create_dialog.close()
            await reload()
            ui.notify(t("lang_admin.saved"), color="positive")

        delete_dialog = ui.dialog()
        with delete_dialog, ui.card().classes("w-[32rem] max-w-[92vw]"):
            ui.label(t("lang_admin.delete_confirm")).classes("text-subtitle1 text-negative")
            ui.label(t("lang_admin.delete_warn")).classes("text-caption text-gray-600")
            with ui.row().classes("w-full justify-end mt-2 gap-2"):
                ui.button(t("grid.close"), on_click=delete_dialog.close).props("flat")
                ui.button(
                    t("lang_admin.delete"), icon="delete", on_click=lambda: do_delete()
                ).props("flat color=negative")

        async def do_delete() -> None:
            code = selected["code"]
            if not code:
                return
            try:
                await client.delete_language(token, code)
            except CoreError as exc:
                ui.notify(f"{t('lang_admin.delete_failed')}: {exc}", color="negative")
                return
            delete_dialog.close()
            selected.update({"code": None, "lang": None})
            await reload()
            ui.notify(t("lang_admin.deleted"), color="positive")

        detail_host = ui.column()

        async def render_detail() -> None:
            detail_host.clear()
            lang = selected["lang"]
            if not lang:
                with detail_host, ui.card().classes("w-full h-[60vh]"):
                    ui.label(t("lang_admin.select_hint")).classes("text-subtitle1 text-gray-500")
                return
            code = str(lang.get("code"))
            raw_label = lang.get("label") or {}
            edit_state["code"] = code
            edit_state["label"] = dict(raw_label) if isinstance(raw_label, dict) else {}
            edit_state["active"] = bool(lang.get("active", True))
            edit_state["is_ui"] = bool(lang.get("is_ui", False))
            edit_state["parent"] = str(lang.get("parent") or "")
            with detail_host, ui.column().classes("w-full gap-3"):
                with ui.row().classes("items-center gap-2 w-full"):
                    ui.label(resolve_value(raw_label, locale) or code).classes("text-h6 grow")
                    ui.label(code).classes("text-caption text-gray-500")
                ui.label(str(lang.get("fallback_order"))).classes("text-caption text-gray-500")
                active_cls = (
                    "bg-positive text-white" if lang.get("active", True) else "bg-grey text-white"
                )
                ui_cls = (
                    "bg-primary text-white" if lang.get("is_ui") else "bg-neutral text-gray-600"
                )
                with ui.row().classes("items-center gap-2 w-full mt-2"):
                    ui.html(
                        f'<span class="px-2 py-0.5 rounded text-xs {active_cls}">'
                        + t("lang_admin.active")
                        + "</span>"
                    ).classes("shrink-0")
                    ui.html(
                        f'<span class="px-2 py-0.5 rounded text-xs {ui_cls}">UI</span>'
                    ).classes("shrink-0")
                with ui.row().classes("w-full mt-2 gap-2"):
                    ui.button(
                        t("lang_admin.new_child"),
                        icon="add",
                        on_click=lambda: open_create(f"lang_{code}"),
                    ).props("flat")
                    if code != "root":
                        ui.button(
                            t("lang_admin.delete"),
                            icon="delete",
                            on_click=lambda: delete_dialog.open(),
                        ).props("flat color=negative")
                # Inline edit form (label in the current UI language + multilingual).
                with ui.column().classes("w-full mt-2 gap-2"):
                    ui.label(t("lang_admin.edit")).classes("text-subtitle2")
                    # The code is the language id and cannot be changed after
                    # creation; show it read-only so it is always visible.
                    ui.input(t("lang_admin.code"), value=code).props(
                        "filled dense readonly"
                    ).classes("w-full")
                    with ui.row().classes("w-full items-center gap-2 mt-1"):
                        label_input = (
                            ui.input(t("lang_admin.label") + f" ({locale.upper()})")
                            .props("filled dense")
                            .classes("grow")
                        )
                        label_input.set_value(str((edit_state["label"] or {}).get(locale) or ""))
                        label_input.on_value_change(lambda e: _set_label_locale(str(e.value)))
                        name_input_ref["el"] = label_input
                        ui.button(icon="translate").props("flat round dense").on_click(
                            open_name
                        ).tooltip(t("form.languages_edit"))
                    parent_row(code)
                    active_check = ui.checkbox(
                        t("lang_admin.active"), value=bool(edit_state["active"])
                    )
                    active_check.on_value_change(
                        lambda e: edit_state.__setitem__("active", bool(e.value))
                    )
                    is_ui_check = ui.checkbox(
                        t("lang_admin.is_ui"), value=bool(edit_state["is_ui"])
                    )
                    is_ui_check.on_value_change(
                        lambda e: edit_state.__setitem__("is_ui", bool(e.value))
                    )
                    with ui.row().classes("w-full justify-end mt-2 gap-2"):
                        ui.button(
                            t("lang_admin.save"), icon="save", on_click=lambda: save_inline()
                        ).props("flat")

        async def save_inline() -> None:
            code = str(edit_state.get("code") or "")
            label = {k: v for k, v in (edit_state.get("label") or {}).items() if v}
            if not label:
                ui.notify(t("lang_admin.label_required"), color="warning")
                return
            parent = str(edit_state.get("parent") or "")
            active = bool(edit_state.get("active", True))
            is_ui = bool(edit_state.get("is_ui", False))
            try:
                await client.update_language(
                    token,
                    code,
                    label=label or None,
                    parent=parent,
                    active=active,
                    is_ui=is_ui,
                )
            except CoreError as exc:
                ui.notify(f"{t('lang_admin.save_failed')}: {exc}", color="negative")
                return
            await reload()
            ui.notify(t("lang_admin.saved"), color="positive")

        current_tree: dict = {"tree": None, "nodes": []}

        def render_tree() -> None:
            if host is None:
                return
            host.clear()
            nodes = build_tree_nodes()
            with host:
                if not nodes:
                    ui.label(t("lang_admin.no_languages")).classes("text-caption text-gray-500")
                else:
                    tree = ui.tree(nodes, on_select=handle_select).classes("w-full")
                    current_tree["tree"] = tree
                    current_tree["nodes"] = nodes

        async def reload() -> None:
            nonlocal langs
            try:
                langs = await client.list_languages(token)
            except Exception:  # noqa: BLE001 - keep the cached list on failure
                langs = langs
            if selected["code"]:
                selected["lang"] = next(
                    (lang for lang in langs if str(lang.get("code")) == selected["code"]),
                    selected["lang"],
                )
            render_tree()
            await render_detail()

        with ui.splitter(value=28, limits=(0, 60)).classes(
            "w-full h-[calc(100vh-210px)]"
        ) as splitter:
            with splitter.before, ui.card().classes("w-full h-full p-0"):
                with ui.row().classes("items-center gap-2 w-full p-2"):
                    ui.button(
                        icon="add",
                        on_click=lambda: open_create(None),
                    ).props("flat dense").tooltip(t("lang_admin.new"))
                    ui.label(t("lang_admin.tree_title")).classes("text-caption text-gray-500")
                host = ui.column().classes("w-full grow").style("min-height: 0; overflow-y: auto")
                render_tree()
            with splitter.after, ui.column().classes("w-full h-full pl-3"):
                detail_host = ui.column().classes("w-full h-full")
                await render_detail()
