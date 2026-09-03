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
from meadow.frontend.layout import build_shell


def _parent_key(parent: object | None) -> str | None:
    """Returns the parent's code (e.g. ``en``) from a record key or ``None``."""
    if isinstance(parent, str) and parent.startswith("lang_"):
        return parent[5:]
    return None


@ui.page("/languages", response_timeout=15)
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
            render_detail()

        form_dialog = ui.dialog()
        with form_dialog, ui.card().classes("w-[36rem] max-w-[92vw]"):
            title_label = ui.label("").classes("text-h6")
            with ui.column().classes("w-full mt-2 gap-2"):
                code_input = ui.input(t("lang_admin.code")).props("filled dense").classes("w-full")
                parent_select = (
                    ui.select({}, label=t("lang_admin.parent"), value=None)
                    .props("filled dense")
                    .classes("w-full")
                )
                with ui.row().classes("w-full gap-2"):
                    label_en = ui.input("Label (EN)").props("filled dense").classes("grow")
                    label_de = ui.input("Label (DE)").props("filled dense").classes("grow")
                with ui.row().classes("w-full gap-6"):
                    active_check = ui.checkbox(t("lang_admin.active"))
                    is_ui_check = ui.checkbox(t("lang_admin.is_ui"))
            with ui.row().classes("w-full justify-end mt-2 gap-2"):
                ui.button(t("grid.close"), on_click=form_dialog.close).props("flat")
                ui.button(t("lang_admin.save"), icon="save", on_click=lambda: save_lang()).props(
                    "flat"
                )

        def fill_parent_options(include_self: str | None = None) -> None:
            options = {"": t("lang_admin.root")}
            for lang in langs:
                code = str(lang.get("code"))
                if include_self and code == include_self:
                    continue
                options[f"lang_{code}"] = resolve_value(lang.get("label"), locale) or code
            parent_select.options = options
            parent_select.update()

        editing_code: dict = {"value": None}

        def open_editor(lang: dict | None, *, parent: str | None = None) -> None:
            editing_code["value"] = lang.get("code") if lang else None
            title_label.set_text(t("lang_admin.edit") if lang else t("lang_admin.new"))
            code_input.set_value(str(lang.get("code")) if lang else "")
            code_input.enable() if not lang else code_input.disable()
            fill_parent_options(editing_code["value"])
            if lang:
                lbl = lang.get("label") or {}
                label_en.set_value(str(lbl.get("en") or ""))
                label_de.set_value(str(lbl.get("de") or ""))
                parent_select.set_value(str(lang.get("parent") or ""))
                active_check.set_value(bool(lang.get("active", True)))
                is_ui_check.set_value(bool(lang.get("is_ui", False)))
            else:
                label_en.set_value("")
                label_de.set_value("")
                parent_select.set_value(parent or "")
                active_check.set_value(True)
                is_ui_check.set_value(False)
            form_dialog.open()

        async def save_lang() -> None:
            code = (code_input.value or "").strip()
            label: dict[str, str] = {}
            if label_en.value:
                label["en"] = label_en.value
            if label_de.value:
                label["de"] = label_de.value
            active = bool(active_check.value)
            is_ui = bool(is_ui_check.value)
            parent = parent_select.value or ""
            editing = editing_code["value"]
            try:
                if editing:
                    await client.update_language(
                        token,
                        editing,
                        label=label or None,
                        parent=parent,
                        active=active,
                        is_ui=is_ui,
                    )
                else:
                    if not code:
                        ui.notify(t("lang_admin.code_required"), color="warning")
                        return
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
            form_dialog.close()
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

        def render_detail() -> None:
            detail_host.clear()
            lang = selected["lang"]
            if not lang:
                with detail_host, ui.card().classes("w-full h-[60vh]"):
                    ui.label(t("lang_admin.select_hint")).classes("text-subtitle1 text-gray-500")
                return
            with detail_host, ui.card().classes("w-full"):
                with ui.row().classes("items-center gap-2 w-full"):
                    ui.label(resolve_value(lang.get("label"), locale) or lang.get("code")).classes(
                        "text-h6 grow"
                    )
                    ui.label(str(lang.get("code"))).classes("text-caption text-gray-500")
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
                        t("lang_admin.edit"), icon="edit", on_click=lambda: open_editor(lang)
                    ).props("flat")
                    ui.button(
                        t("lang_admin.new_child"),
                        icon="add",
                        on_click=lambda: open_editor(None, parent=f"lang_{lang.get('code')}"),
                    ).props("flat")
                    if str(lang.get("code")) != "root":
                        ui.button(
                            t("lang_admin.delete"),
                            icon="delete",
                            on_click=lambda: delete_dialog.open(),
                        ).props("flat color=negative")

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
            render_tree()
            render_detail()

        with ui.splitter(value=28, limits=(0, 60)).classes(
            "w-full h-[calc(100vh-210px)]"
        ) as splitter:
            with splitter.before, ui.card().classes("w-full h-full p-0"):
                with ui.row().classes("items-center gap-2 w-full p-2"):
                    ui.button(
                        icon="add",
                        on_click=lambda: open_editor(None),
                    ).props("flat dense").tooltip(t("lang_admin.new"))
                    ui.label(t("lang_admin.tree_title")).classes("text-caption text-gray-500")
                host = ui.column().classes("w-full grow").style("min-height: 0; overflow-y: auto")
                render_tree()
            with splitter.after, ui.column().classes("w-full h-full"):
                detail_host = ui.column().classes("w-full h-full")
                render_detail()
