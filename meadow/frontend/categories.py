"""Admin page for categories & relations (admin/superadmin only).

Categories are ordinary objects of type ``category``. This page manages the
category trees: create, hierarchise (re-parent), edit and delete categories, and
assign/remove the objects that belong to them (``category_member`` aggregation).
"""

from __future__ import annotations

from nicegui import app, ui

from meadow.frontend import config
from meadow.frontend.api import CoreClient, CoreError
from meadow.frontend.grid import (
    filter_tree_nodes,
    resolve_value,
    status_badge,
    tree_node_from_object,
)
from meadow.frontend.i18n import current_locale, t
from meadow.frontend.layout import build_shell
from meadow.frontend.lazy_tree import LazyTree
from meadow.frontend.object_picker import OBJECT_ROOT_ID, open_object_picker

ROOT_ID = OBJECT_ROOT_ID


async def _category_objects(client: CoreClient, token: str, locale: str) -> dict[str, dict]:
    """Returns every category object keyed by id (for the editor/move pickers)."""
    try:
        objects = await client.list_objects(token, object_type="category", limit=1000)
    except Exception:  # noqa: BLE001 - degrade to an empty map
        return {}
    return {str(o.get("id")): o for o in objects if isinstance(o, dict) and o.get("id")}


async def _category_roots(client: CoreClient, token: str, locale: str) -> list[dict]:
    """Returns the top-level category nodes (children of the universal root)."""
    top = await client.list_object_children(token, ROOT_ID, limit=100, count_members=True)
    return [
        tree_node_from_object(child, locale) for child in top if child.get("type") == "category"
    ]


@ui.page("/categories", response_timeout=15)
async def categories_page() -> None:
    await build_shell(active="/categories")
    locale = current_locale()
    client = CoreClient(config.CORE_URL)
    token = app.storage.user.get("token", "")

    with ui.column().classes("w-full p-6"):
        ui.label(t("categories.title")).classes("text-h4")
        ui.label(t("categories.subtitle")).classes("text-subtitle1")

        try:
            category_roots = await _category_roots(client, token, locale)
        except Exception:  # noqa: BLE001 - show a friendly message on failure
            ui.label(t("categories.load_failed")).classes("text-subtitle1 text-negative")
            return
        category_objects = await _category_objects(client, token, locale)

        # --- state -----------------------------------------------------------
        selected_category: dict = {"id": None, "label": "", "type": "", "status": ""}
        selected_tree: dict = {"id": None}
        current_tree: dict = {"tree": None, "nodes": []}

        # --- multilingual name editing (product-form style) ------------------
        # The category `name` is a ``{lang: value}`` map. The inline field edits
        # the value for the current UI language (the "main edit language"), and a
        # language icon opens a dialog to edit it in every active data language.
        try:
            languages = await client.list_languages(token)
        except Exception:  # noqa: BLE001 - fall back to the UI languages only
            languages = []
        name_langs = [
            lang
            for lang in languages
            if str(lang.get("code")) != "root" and lang.get("active", True)
        ]
        if not any(str(lang.get("code")) == locale for lang in name_langs):
            name_langs = [{"code": locale, "label": {locale: locale}}] + name_langs

        edit_state: dict[str, object] = {"id": None, "name": {}, "status": "draft", "sub": False}
        name_input_ref: dict[str, ui.element | None] = {"el": None}

        name_dialog = ui.dialog()
        with name_dialog, ui.card().classes("w-[34rem] max-w-[90vw]"):
            ui.label(t("categories.name_edit")).classes("text-h6")
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
            name = dict(edit_state["name"] or {})
            for code, inp in name_inputs.items():
                inp.set_value(str(name.get(code) or ""))
            name_dialog.open()

        def save_name() -> None:
            name: dict[str, str] = {}
            for code, inp in name_inputs.items():
                if inp.value:
                    name[code] = inp.value
            edit_state["name"] = name
            el = name_input_ref.get("el")
            if el is not None:
                el.set_value(str(name.get(locale) or ""))
            name_dialog.close()

        def _set_name_locale(value: str) -> None:
            name = dict(edit_state["name"] or {})
            if value:
                name[locale] = value
            else:
                name.pop(locale, None)
            edit_state["name"] = name

        # --- tree helpers ----------------------------------------------------
        async def load_category_children(node_id: str) -> list[dict]:
            children = await client.list_object_children(
                token, node_id, limit=100, count_members=True
            )
            return [
                tree_node_from_object(child, locale)
                for child in children
                if child.get("type") == "category"
            ]

        async def handle_category_select(e) -> None:
            node_id = e.value if hasattr(e, "value") else None
            if not node_id:
                selected_category.update({"id": None, "label": "", "type": "", "status": ""})
                await render_detail()
                return
            try:
                obj = await client.get_object(token, node_id)
            except Exception:  # noqa: BLE001 - degrade to a bare selection
                obj = {"id": node_id}
            selected_category.update(
                {
                    "id": str(obj.get("id")),
                    "label": resolve_value((obj.get("attributes") or {}).get("name"), locale)
                    or str(obj.get("type"))
                    or str(obj.get("id")),
                    "type": str(obj.get("type") or ""),
                    "status": str(obj.get("status") or ""),
                }
            )
            await render_detail()

        def render_tree() -> None:
            if host is None:
                return
            host.clear()
            nodes = category_roots
            if selected_tree["id"]:
                filtered = [r for r in category_roots if r["id"] == selected_tree["id"]]
                if filtered:
                    nodes = filtered
            with host:
                tree = LazyTree(
                    nodes or [],
                    on_select=handle_category_select,
                    load_children=load_category_children,
                    virtual_scroll=True,
                ).classes("w-full")
                tree.add_slot(
                    "default-header",
                    '<span :props="props">{{ props.node.label }}'
                    '<small v-if="props.node.type" class="text-caption text-gray-500">'
                    " &middot; {{ props.node.type }}</small>"
                    '<small v-if="props.node.count !== undefined" '
                    'class="text-caption text-gray-500">'
                    " &middot; {{ props.node.count }}</small></span>",
                )
                if not nodes:
                    ui.label(t("categories.no_categories")).classes("text-caption text-gray-500")
            current_tree["tree"] = tree
            current_tree["nodes"] = nodes

        def collapse_all() -> None:
            if current_tree["tree"] is not None:
                current_tree["tree"].collapse()

        def apply_tree_filter(value: str) -> None:
            tree = current_tree["tree"]
            if tree is not None:
                tree.set_nodes(filter_tree_nodes(current_tree["nodes"], value or ""))

        async def reload_tree() -> None:
            nonlocal category_roots
            try:
                category_roots = await _category_roots(client, token, locale)
            except Exception:  # noqa: BLE001 - keep the cached roots on failure
                category_roots = category_roots
            tree_select.options = {
                "all": t("grid.all_trees"),
                **{r["id"]: r["label"] for r in category_roots},
            }
            tree_select.update()
            render_tree()

        # --- detail panel -----------------------------------------------------
        async def render_detail() -> None:
            detail_host.clear()
            category_id = selected_category["id"]
            if not category_id:
                with detail_host, ui.card().classes("w-full h-[60vh]"):
                    ui.label(t("categories.select_hint")).classes("text-subtitle1 text-gray-500")
                return
            category = category_objects.get(str(category_id)) or {}
            raw_name = (category.get("attributes") or {}).get("name") or {}
            edit_state["id"] = category_id
            edit_state["name"] = dict(raw_name) if isinstance(raw_name, dict) else {}
            edit_state["status"] = str(category.get("status") or "draft")
            with (
                detail_host,
                ui.column().classes("w-full gap-3"),
            ):
                with ui.row().classes("items-center gap-2 w-full"):
                    ui.label(selected_category["label"]).classes("text-h6 grow")
                    if selected_category.get("status"):
                        ui.html(status_badge(selected_category["status"]))
                ui.label(selected_category["id"]).classes("text-caption text-gray-500")
                with ui.row().classes("w-full mt-2 gap-2"):
                    ui.button(
                        t("categories.new_sub"),
                        icon="add",
                        on_click=lambda: open_create(selected_category["id"]),
                    ).props("flat")
                    ui.button(
                        t("categories.move"),
                        icon="drive_file_move",
                        on_click=lambda: open_move(),
                    ).props("flat")
                    ui.button(
                        t("categories.delete"),
                        icon="delete",
                        on_click=lambda: open_delete(),
                    ).props("flat color=negative")
                with ui.row().classes("items-center gap-2 w-full mt-2"):
                    ui.button(
                        t("categories.view_in_grid"),
                        icon="open_in_new",
                        on_click=lambda: ui.navigate.to(
                            f"/products?category={selected_category['id']}"
                        ),
                    ).props("outline")
                    ui.label(t("categories.view_in_grid_hint")).classes(
                        "text-caption text-gray-500"
                    )
                # Inline edit form (name in the current UI language + multilingual).
                with ui.column().classes("w-full mt-2 gap-2"):
                    ui.label(t("categories.edit")).classes("text-subtitle2")
                    with ui.row().classes("w-full items-center gap-2 mt-1"):
                        name_input = (
                            ui.input(t("categories.name") + f" ({locale.upper()})")
                            .props("filled dense")
                            .classes("grow")
                        )
                        name_input.set_value(str((edit_state["name"] or {}).get(locale) or ""))
                        name_input.on_value_change(lambda e: _set_name_locale(str(e.value)))
                        name_input_ref["el"] = name_input
                        ui.button(icon="translate").props("flat round dense").on_click(
                            open_name
                        ).tooltip(t("form.languages_edit"))
                    status_select = (
                        ui.select(
                            {
                                "draft": "draft",
                                "in_review": "in_review",
                                "published": "published",
                                "archived": "archived",
                            },
                            label=t("categories.status"),
                            value=str(edit_state["status"] or "draft"),
                        )
                        .props("filled dense")
                        .classes("w-full")
                    )
                    status_select.on_value_change(
                        lambda e: edit_state.__setitem__("status", str(e.value))
                    )
                    with ui.row().classes("w-full justify-end mt-2 gap-2"):
                        ui.button(
                            t("categories.save"), icon="save", on_click=lambda: save_inline()
                        ).props("flat")

        async def save_inline() -> None:
            category_id = str(edit_state.get("id") or "")
            name = {k: v for k, v in (edit_state.get("name") or {}).items() if v}
            if not name:
                ui.notify(t("categories.name_required"), color="warning")
                return
            try:
                current = await client.get_object(token, category_id)
                attributes = dict(current.get("attributes") or {})
                attributes["name"] = name
                await client.update_object(
                    token,
                    category_id,
                    attributes=attributes,
                    status=str(edit_state.get("status") or "draft"),
                )
            except CoreError as exc:
                ui.notify(f"{t('categories.save_failed')}: {exc}", color="negative")
                return
            category_objects.clear()
            category_objects.update(await _category_objects(client, token, locale))
            selected_category["label"] = resolve_value(name, locale) or selected_category["label"]
            await reload_tree()
            await render_detail()
            ui.notify(t("categories.saved"), color="positive")

        # --- create dialog (multilingual name, product-form style) ------------
        create_dialog = ui.dialog()
        with create_dialog, ui.card().classes("w-[36rem] max-w-[92vw]"):
            title_label = ui.label("").classes("text-h6")
            with ui.column().classes("w-full mt-2 gap-2"):
                with ui.row().classes("w-full items-center gap-2"):
                    create_name_input = (
                        ui.input(t("categories.name") + f" ({locale.upper()})")
                        .props("filled dense")
                        .classes("grow")
                    )
                    ui.button(icon="translate").props("flat round dense").on_click(
                        open_name
                    ).tooltip(t("form.languages_edit"))
                create_status_select = (
                    ui.select(
                        {
                            "draft": "draft",
                            "in_review": "in_review",
                            "published": "published",
                            "archived": "archived",
                        },
                        label=t("categories.status"),
                        value="draft",
                    )
                    .props("filled dense")
                    .classes("w-full")
                )
                create_status_select.on_value_change(
                    lambda e: edit_state.__setitem__("status", str(e.value))
                )
            with ui.row().classes("w-full justify-end mt-2 gap-2"):
                ui.button(t("grid.close"), on_click=create_dialog.close).props("flat")
                ui.button(
                    t("categories.save"), icon="save", on_click=lambda: save_category()
                ).props("flat")

        def open_create(parent_id: str | None) -> None:
            edit_state["id"] = None
            edit_state["name"] = {}
            edit_state["status"] = "draft"
            edit_state["sub"] = bool(parent_id)
            title_label.set_text(
                t("categories.create_sub") if parent_id else t("categories.create_root")
            )
            create_name_input.set_value("")
            create_name_input.on_value_change(lambda e: _set_name_locale(str(e.value)))
            create_status_select.set_value("draft")
            name_input_ref["el"] = create_name_input
            create_dialog.open()

        async def save_category() -> None:
            name: dict[str, str] = {k: v for k, v in (edit_state.get("name") or {}).items() if v}
            if not name:
                ui.notify(t("categories.name_required"), color="warning")
                return
            try:
                created = await client.create_object(
                    token,
                    object_type="category",
                    attributes={"name": name},
                    status=str(edit_state.get("status") or "draft"),
                )
                # A sub-category is re-parented from the universal root to the
                # selected category.
                if edit_state.get("sub") and selected_category["id"]:
                    await client.reparent_object(token, created["id"], selected_category["id"])
            except CoreError as exc:
                ui.notify(f"{t('categories.save_failed')}: {exc}", color="negative")
                return
            create_dialog.close()
            category_objects.clear()
            category_objects.update(await _category_objects(client, token, locale))
            await reload_tree()
            ui.notify(t("categories.saved"), color="positive")

        # --- move dialog ------------------------------------------------------
        # The new parent is picked with the shared object picker (search +
        # structure tree, restricted to `category` and excluding the moved node).
        move_dialog = ui.dialog()
        with move_dialog, ui.card().classes("w-[32rem] max-w-[92vw]"):
            ui.label(t("categories.move_title")).classes("text-h6")
            ui.label(t("categories.move_hint")).classes("text-caption text-gray-600")
            move_target = ui.label(t("grid.bulk_nothing_picked")).classes(
                "text-caption text-gray-500"
            )
            picked: dict = {}

            async def pick_parent() -> None:
                category_id = selected_category["id"]

                async def on_pick(hit: dict) -> None:
                    picked["id"] = hit["id"]
                    move_target.set_text(f"{hit.get('label')} · {hit.get('type')}")

                await open_object_picker(
                    client=client,
                    token=token,
                    locale=locale,
                    title=t("categories.parent"),
                    on_pick=on_pick,
                    target_types=["category"],
                    exclude_ids=[category_id or ""],
                )

            with ui.row().classes("w-full justify-end mt-2 gap-2"):
                ui.button(t("grid.close"), on_click=move_dialog.close).props("flat")
                ui.button(
                    t("form.pick_in_structure"), icon="account_tree", on_click=pick_parent
                ).props("flat")
                ui.button(
                    t("categories.move"), icon="drive_file_move", on_click=lambda: do_move()
                ).props("flat")

        async def open_move() -> None:
            if not selected_category["id"]:
                return
            picked.clear()
            move_target.set_text(t("grid.bulk_nothing_picked"))
            move_dialog.open()

        async def do_move() -> None:
            category_id = selected_category["id"]
            parent = picked.get("id")
            if not category_id or not parent:
                ui.notify(t("categories.parent_required"), color="warning")
                return
            try:
                await client.reparent_object(token, category_id, parent)
            except CoreError as exc:
                ui.notify(f"{t('categories.move_failed')}: {exc}", color="negative")
                return
            move_dialog.close()
            await reload_tree()
            ui.notify(t("categories.moved"), color="positive")

        # --- delete confirm ----------------------------------------------------
        delete_dialog = ui.dialog()
        with delete_dialog, ui.card().classes("w-[32rem] max-w-[92vw]"):
            ui.label(t("categories.delete_confirm")).classes("text-subtitle1 text-negative")
            ui.label(t("categories.delete_warn")).classes("text-caption text-gray-600")
            with ui.row().classes("w-full justify-end mt-2 gap-2"):
                ui.button(t("grid.close"), on_click=delete_dialog.close).props("flat")
                ui.button(
                    t("categories.delete"), icon="delete", on_click=lambda: do_delete()
                ).props("flat color=negative")

        async def open_delete() -> None:
            if not selected_category["id"]:
                return
            delete_dialog.open()

        async def do_delete() -> None:
            category_id = selected_category["id"]
            if not category_id:
                return
            try:
                await client.delete_object(token, category_id)
            except CoreError as exc:
                ui.notify(f"{t('categories.delete_failed')}: {exc}", color="negative")
                return
            delete_dialog.close()
            selected_category.update({"id": None, "label": "", "type": "", "status": ""})
            category_objects.clear()
            category_objects.update(await _category_objects(client, token, locale))
            await reload_tree()
            await render_detail()
            ui.notify(t("categories.deleted"), color="positive")

        # --- layout: splitter with tree + detail -----------------------------
        with ui.splitter(value=28, limits=(0, 60)).classes(
            "w-full h-[calc(100vh-210px)]"
        ) as splitter:
            with splitter.before, ui.card().classes("w-full h-full p-0"):
                with ui.row().classes("items-center gap-2 w-full p-2").style("flex-wrap: nowrap"):
                    ui.button(icon="add", on_click=lambda: open_create(None)).props(
                        "flat dense"
                    ).tooltip(t("categories.new_root"))
                    tree_select = (
                        ui.select(
                            {
                                "all": t("grid.all_trees"),
                                **{r["id"]: r["label"] for r in category_roots},
                            },
                            value="all",
                        )
                        .props("outline dense")
                        .classes("grow")
                    )

                    def on_tree_select(value: str) -> None:
                        selected_tree["id"] = None if value in (None, "all") else value
                        render_tree()

                    tree_select.on_value_change(lambda e: on_tree_select(e.value))  # type: ignore
                with ui.row().classes("items-center gap-2 w-full px-2").style("flex-wrap: nowrap"):
                    filter_input = ui.input(t("grid.filter")).props("outline dense").classes("grow")
                    ui.button(icon="unfold_less", on_click=collapse_all).props(
                        "flat dense shrink-0"
                    ).tooltip(t("grid.collapse_all"))
                host = ui.column().classes("w-full grow").style("min-height: 0; overflow-y: auto")
                filter_input.on_value_change(lambda e: apply_tree_filter(e.value or ""))
                render_tree()
            with splitter.after, ui.column().classes("w-full h-full pl-3"):
                detail_host = ui.column().classes("w-full h-full")
                await render_detail()
