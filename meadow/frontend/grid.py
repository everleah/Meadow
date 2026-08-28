"""Object list grid (AG Grid): the main work area (Phase 7, steps 3-5).

The grid lists objects with human-friendly display (resolved name, status
badge), supports inline editing / batch save, offers a column picker and
personal views, and shows a composition (structure) tree that filters the
grid to the selected subtree. Category & structure navigation with the
Kategorien toggle and the completeness column follow in later steps.
"""

from __future__ import annotations

from nicegui import app, ui

from meadow.frontend import config
from meadow.frontend.api import CoreClient, is_session_expired
from meadow.frontend.i18n import current_locale, t
from meadow.frontend.layout import build_shell


def resolve_value(value: object, locale: str) -> str:
    """Resolves a (possibly multilingual) attribute value to a display string."""
    if isinstance(value, dict):
        if locale in value:
            return str(value[locale])
        if value:
            return str(next(iter(value.values())))
        return ""
    return "" if value is None else str(value)


def status_badge(status: str) -> str:
    """Returns a coloured badge for a workflow status."""
    classes = {
        "published": "bg-primary text-white",
        "draft": "bg-neutral text-gray-600",
        "archived": "bg-info text-white",
    }.get(status, "bg-secondary text-white")
    return f'<span class="px-2 py-0.5 rounded text-xs {classes}">{status}</span>'


def row_data(object_: dict, locale: str, attribute_names: list[str] | None = None) -> dict:
    """Maps a core object to an AG Grid row.

    With ``attribute_names``, one ``attr_<name>`` field per attribute is added
    (resolved to a display string for the working locale).
    """
    attributes = object_.get("attributes") or {}
    row = {
        "_id": str(object_.get("id", "")),
        "_name": resolve_value(attributes.get("name"), locale),
        "_type": str(object_.get("type", "")),
        "_status": status_badge(str(object_.get("status", ""))),
    }
    for name in attribute_names or []:
        row[f"attr_{name}"] = resolve_value(attributes.get(name), locale)
    return row


def attribute_columns(definitions: list[dict], locale: str) -> list[dict]:
    """Builds hidden AG Grid column defs for attribute definitions.

    The header uses the definition's (multilingual) label, falling back to the
    attribute name. Attribute columns are hidden by default so the grid stays
    clean until the user enables them via the column picker.
    """
    columns = []
    for definition in definitions:
        name = str(definition.get("name", ""))
        if not name:
            continue
        label = resolve_value(definition.get("label"), locale) or name
        columns.append({"headerName": label, "field": f"attr_{name}", "width": 140, "hide": True})
    return columns


def collect_subtree(node_id: str, children_by_id: dict[str, list[str]]) -> list[str]:
    """Returns the node id and all its descendants (breadth-first)."""
    result: list[str] = []
    queue = [node_id]
    seen: set[str] = set()
    while queue:
        current = queue.pop(0)
        if current in seen:
            continue
        seen.add(current)
        result.append(current)
        queue.extend(children_by_id.get(current, []))
    return result


def enrich_tree_nodes(
    nodes: list[dict],
    objects_by_id: dict[str, dict],
    locale: str,
    root_id: str = "object:object_root",
    root_label: str = "",
) -> tuple[list[dict], dict[str, list[str]]]:
    """Adds labels to composition-tree nodes and returns the children map.

    Returns ``(nodes_with_labels, children_by_id)``. Node labels come from the
    object's resolved (multilingual) name; the root falls back to
    ``root_label`` when it has no name.
    """
    children_by_id: dict[str, list[str]] = {}

    def enrich(node: dict, is_root: bool) -> dict:
        node_id = str(node.get("id", ""))
        obj = objects_by_id.get(node_id, {})
        obj_type = str(obj.get("type", ""))
        label = resolve_value((obj.get("attributes") or {}).get("name"), locale)
        if not label:
            label = root_label if is_root else (obj_type or node_id)
        raw_children = node.get("children", [])
        children_by_id[node_id] = [str(c.get("id")) for c in raw_children if c.get("id")]
        return {
            "id": node_id,
            "label": label,
            "type": obj_type,
            "children": [enrich(c, False) for c in raw_children],
        }

    enriched = [enrich(n, n.get("id") == root_id) for n in nodes]
    return enriched, children_by_id


def filter_tree_nodes(nodes: list[dict], query: str) -> list[dict]:
    """Filters QTree nodes by label, keeping ancestors of matches.

    An empty query returns the nodes unchanged. A node is kept when its label
    contains the query (case-insensitive) or when a descendant matches; kept
    nodes carry only their matching descendants.
    """
    q = query.strip().lower()
    if not q:
        return nodes

    def keep(node: dict) -> dict | None:
        children = [keep(c) for c in node.get("children", [])]
        children = [c for c in children if c is not None]
        if q in str(node.get("label", "")).lower() or children:
            return dict(node, children=children)
        return None

    return [n for n in (keep(x) for x in nodes) if n is not None]


def build_patch(object_id: str, client_name: object, original: dict, locale: str) -> dict:
    """Builds a batch-update item for an edited name value.

    Preserves other locales when the attribute is multilingual; otherwise the
    scalar name is replaced.
    """
    original_name = (original.get("attributes") or {}).get("name")
    if isinstance(original_name, dict):
        new_name = dict(original_name)
        new_name[locale] = client_name
    else:
        new_name = client_name
    return {"id": object_id, "attributes": {"name": new_name}}


@ui.page("/products", response_timeout=10)
async def products_page() -> None:
    """Lists objects in the main grid with inline editing + batch save."""
    build_shell(active="/products")
    locale = current_locale()
    with ui.column().classes("w-full p-6"):
        ui.label(t("nav.products")).classes("text-h4")
        client = CoreClient(config.CORE_URL)
        token = app.storage.user.get("token", "")
        try:
            objects = await client.list_objects(token, limit=100)
        except Exception:  # noqa: BLE001 - show a friendly message on failure
            ui.label(t("grid.load_failed")).classes("text-subtitle1 text-negative")
            return
        if not objects:
            ui.label(t("grid.no_objects")).classes("text-subtitle1")
            return

        try:
            definitions = await client.list_attribute_definitions(token)
        except Exception:  # noqa: BLE001 - degrade to base columns on failure
            definitions = []
        attribute_names = [
            str(definition.get("name")) for definition in definitions if definition.get("name")
        ]
        column_defs = [
            {"headerName": t("grid.id"), "field": "_id", "width": 100, "pinned": "left"},
            {"headerName": t("grid.name"), "field": "_name", "editable": True},
            {"headerName": t("grid.type"), "field": "_type", "width": 120},
            {"headerName": t("grid.status"), "field": "_status", "width": 140},
        ] + attribute_columns(definitions, locale)

        original_by_id = {str(object_.get("id")): object_ for object_ in objects}
        children_by_id: dict[str, list[str]] = {}
        root_id = "object:object_root"

        try:
            tree_data = await client.get_object_tree(token)
            tree_nodes, children_by_id = enrich_tree_nodes(
                tree_data, original_by_id, locale, root_id, t("grid.structure")
            )
        except Exception:  # noqa: BLE001 - degrade to a grid without the tree
            tree_nodes = []
            children_by_id = {}

        all_rows = [row_data(object_, locale, attribute_names) for object_ in objects]
        rows_by_id = {row["_id"]: row for row in all_rows}

        def handle_tree_select(e) -> None:
            selected = e.value if hasattr(e, "value") else None
            if not selected:
                grid.run_grid_method("setGridOption", "rowData", all_rows)
            else:
                ids = collect_subtree(selected, children_by_id)
                filtered = [rows_by_id[i] for i in ids if i in rows_by_id]
                grid.run_grid_method("setGridOption", "rowData", filtered)

        staged_ids: set[str] = set()

        def mark_changed(event) -> None:
            data = event.args.get("data") if isinstance(event.args, dict) else None
            oid = data.get("_id") if isinstance(data, dict) else None
            if oid:
                staged_ids.add(oid)
                staged_label.set_text(t("grid.staged_changes").format(count=len(staged_ids)))

        async def save() -> None:
            client_rows = await grid.get_client_data()
            patches = []
            for row in client_rows:
                oid = row.get("_id")
                original = original_by_id.get(oid)
                if original is None:
                    continue
                original_name = resolve_value(
                    (original.get("attributes") or {}).get("name"), locale
                )
                if row.get("_name") != original_name:
                    patches.append(build_patch(oid, row.get("_name"), original, locale))
            if not patches:
                ui.notify(t("grid.no_changes"))
                return
            try:
                results = await client.batch_update(token, patches)
            except Exception:  # noqa: BLE001 - show a friendly message on failure
                ui.notify(t("grid.save_failed"), color="negative")
                return
            if is_session_expired(results):
                ui.notify(t("grid.session_expired"), color="warning")
                app.storage.user.clear()
                ui.navigate.to("/login")
                return
            ok = sum(1 for result in results if result.get("success"))
            ui.notify(t("grid.saved_count").format(ok=ok, total=len(results)))
            ui.navigate.reload()

        def discard() -> None:
            ui.navigate.reload()

        # Personal views (step 4b): save/restore the visible column set per user.
        views = app.storage.user.get("views", {})
        visible_fields: set[str] = {"_id", "_name", "_type", "_status"}
        checkbox_refs: dict[str, ui.checkbox] = {}

        def apply_visibility(fields: set[str]) -> None:
            for column in column_defs:
                grid.run_grid_method(
                    "setColumnsVisible", [column["field"]], column["field"] in fields
                )

        def save_view() -> None:
            name = view_input.value.strip()
            if not name:
                ui.notify(t("grid.view_name_required"), color="warning")
                return
            views[name] = sorted(visible_fields)
            app.storage.user["views"] = views
            view_input.value = ""
            refresh_views()
            ui.notify(t("grid.view_saved").format(name=name))

        def load_view(name: str) -> None:
            fields = set(views.get(name, []))
            visible_fields.clear()
            visible_fields.update(fields)
            apply_visibility(visible_fields)
            for column in column_defs:
                checkbox_refs[column["field"]].value = column["field"] in visible_fields
            ui.notify(t("grid.view_loaded").format(name=name))

        def delete_view(name: str) -> None:
            views.pop(name, None)
            app.storage.user["views"] = views
            refresh_views()

        def refresh_views() -> None:
            views_container.clear()
            for name in views:
                with views_container, ui.row().classes("items-center gap-1 w-full"):
                    ui.button(name, on_click=lambda n=name: load_view(n)).props("flat dense")
                    ui.button(icon="delete", on_click=lambda n=name: delete_view(n)).props(
                        "flat dense color=negative"
                    )

        with ui.dialog() as views_dialog, ui.card().classes("w-96"):
            ui.label(t("grid.views")).classes("text-h6")
            with ui.row().classes("items-center gap-2 w-full"):
                view_input = ui.input(t("grid.view_name")).props("outline dense").classes("grow")
                ui.button(t("grid.save_view"), icon="save", on_click=save_view).props(
                    "outline dense"
                )
            views_container = ui.column()
            refresh_views()
            with ui.row().classes("w-full justify-end"):
                ui.button(t("grid.close"), on_click=views_dialog.close).props("flat dense")

        def toggle_tree() -> None:
            tree_card.set_visibility(not tree_card.visible)

        with ui.row().classes("w-full no-wrap items-start gap-4"):
            # Left: collapsible structure tree card.
            with ui.card().classes("w-72 shrink-0") as tree_card:
                with ui.row().classes("items-center gap-2 w-full"):
                    ui.label(t("grid.structure")).classes("text-subtitle2 text-bold grow")
                    if tree_nodes:
                        ui.button(icon="chevron_left", on_click=toggle_tree).props(
                            "flat dense"
                        ).tooltip(t("grid.collapse_tree"))
                with ui.row().classes("items-center gap-2 w-full"):
                    filter_input = ui.input(t("grid.filter")).props("outline dense").classes("grow")
                    if tree_nodes:
                        ui.button(icon="expand_all", on_click=lambda: tree.expand()).props(
                            "flat dense"
                        ).tooltip(t("grid.expand_all"))
                        ui.button(icon="collapse_all", on_click=lambda: tree.collapse()).props(
                            "flat dense"
                        ).tooltip(t("grid.collapse_all"))
                if tree_nodes:
                    tree = ui.tree(tree_nodes, on_select=handle_tree_select)
                    tree.expand()
                    tree.add_slot(
                        "default-header",
                        '<span :props="props">{{ props.node.label }}'
                        '<small v-if="props.node.type" class="text-caption text-gray-500">'
                        " &middot; {{ props.node.type }}</small></span>",
                    )

                    def apply_tree_filter(value: str) -> None:
                        tree.props["nodes"] = filter_tree_nodes(tree_nodes, value)
                        tree.update()

                    filter_input.on_value_change(lambda e: apply_tree_filter(e.value))
                else:
                    ui.label(t("grid.tree_load_failed")).classes("text-caption text-negative")
            # Right: grid area (fills the remaining width).
            with ui.column().classes("flex-1"):
                with ui.row().classes("items-center gap-3"):
                    ui.button(t("grid.save"), icon="save", on_click=save).classes(
                        "bg-primary text-white"
                    )
                    ui.button(t("grid.discard"), icon="undo", on_click=discard).props("outline")
                    staged_label = ui.label(t("grid.staged_changes").format(count=0)).classes(
                        "text-caption"
                    )
                    ui.button(icon="view_sidebar", on_click=toggle_tree).props(
                        "flat dense"
                    ).tooltip(t("grid.toggle_tree")).classes("ml-auto")
                    ui.button(t("grid.views"), icon="bookmarks", on_click=views_dialog.open).props(
                        "outline"
                    )

                    def make_toggler(field: str):
                        def toggle(event) -> None:
                            grid.run_grid_method("setColumnsVisible", [field], event.value)
                            if event.value:
                                visible_fields.add(field)
                            else:
                                visible_fields.discard(field)

                        return toggle

                    with (
                        ui.button(t("grid.columns"), icon="view_column")
                        .props("outline")
                        .classes("ml-2"),
                        ui.menu(),
                    ):
                        for column in column_defs:
                            checkbox = ui.checkbox(
                                column["headerName"], value=not column.get("hide")
                            ).on_value_change(make_toggler(column["field"]))
                            checkbox_refs[column["field"]] = checkbox

                grid = ui.aggrid(
                    {
                        "columnDefs": column_defs,
                        "rowData": all_rows,
                        "rowSelection": {"mode": "multiRow"},
                        "pagination": True,
                        "paginationPageSize": 50,
                    },
                    html_columns=[3],
                ).classes("w-full h-96")
                grid.on("cellValueChanged", mark_changed, ["data"])
