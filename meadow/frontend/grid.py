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
from meadow.frontend.lazy_tree import LazyTree


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


def completeness_badge(percent: int) -> str:
    """Returns a coloured badge for a completeness percentage.

    Grey for 0 % (nothing set), orange for 1-99 %, green for 100 %.
    """
    if percent >= 100:
        classes = "bg-positive text-white"
    elif percent >= 1:
        classes = "bg-orange text-white"
    else:
        classes = "bg-grey text-white"
    return f'<span class="px-2 py-0.5 rounded text-xs {classes}">{percent}%</span>'


def row_data(object_: dict, locale: str, attribute_names: list[str] | None = None) -> dict:
    """Maps a core object to an AG Grid row.

    With ``attribute_names``, one ``attr_<name>`` field per attribute is added
    (resolved to a display string for the working locale). The ``_completeness``
    field mirrors the core's ``completeness`` value for the working locale.
    """
    attributes = object_.get("attributes") or {}
    row = {
        "_id": str(object_.get("id", "")),
        "_name": resolve_value(attributes.get("name"), locale),
        "_type": str(object_.get("type", "")),
        "_status": status_badge(str(object_.get("status", ""))),
    }
    completeness = object_.get("completeness") or {}
    if isinstance(completeness, dict):
        percent = completeness.get(locale)
        row["_completeness"] = completeness_badge(int(percent)) if percent is not None else ""
    else:
        row["_completeness"] = ""
    for name in attribute_names or []:
        row[f"attr_{name}"] = resolve_value(attributes.get(name), locale)
    # Action cell: open the reference picker for this row.
    row["_refs"] = "🔗"
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
    nodes carry only their matching descendants. A ``lazy`` node (children are
    loaded on demand) that has no matching children is kept **without** a
    ``children`` key so it stays expandable via ``@lazy-load``.
    """
    q = query.strip().lower()
    if not q:
        return nodes

    def keep(node: dict) -> dict | None:
        children = [keep(c) for c in node.get("children", [])]
        children = [c for c in children if c is not None]
        if q in str(node.get("label", "")).lower() or children:
            kept = dict(node)
            if children or not node.get("lazy"):
                kept["children"] = children
            return kept
        return None

    return [n for n in (keep(x) for x in nodes) if n is not None]


def tree_node_from_object(object_: dict, locale: str) -> dict:
    """Builds a QTree node dict from a child object, marking lazy nodes."""
    node_id = str(object_.get("id", ""))
    attributes = object_.get("attributes") or {}
    obj_type = str(object_.get("type", ""))
    label = resolve_value(attributes.get("name"), locale) or obj_type or node_id
    node = {"id": node_id, "label": label, "type": obj_type}
    if object_.get("hasChildren"):
        node["lazy"] = True
    if "memberCount" in object_:
        node["count"] = object_.get("memberCount")
    return node


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
            objects = await client.list_objects(token, limit=100, locale=locale)
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
            {"headerName": t("grid.completeness"), "field": "_completeness", "width": 140},
            {"headerName": "", "field": "_refs", "width": 60},
        ] + attribute_columns(definitions, locale)

        original_by_id = {str(object_.get("id")): object_ for object_ in objects}
        root_id = "object:object_root"
        tree_nodes: list[dict] = []

        async def load_children(node_id: str) -> list[dict]:
            children = await client.list_object_children(token, node_id, limit=100)
            return [tree_node_from_object(child, locale) for child in children]

        try:
            top_children = await client.list_object_children(token, root_id, limit=100)
            tree_nodes = [tree_node_from_object(child, locale) for child in top_children]
        except Exception:  # noqa: BLE001 - degrade to a grid without the tree
            tree_nodes = []

        all_rows = [row_data(object_, locale, attribute_names) for object_ in objects]

        async def handle_tree_select(e) -> None:
            nonlocal all_rows
            selected = e.value if hasattr(e, "value") else None
            if not selected:
                selected_objects = objects
            else:
                selected_objects = await client.list_objects(
                    token, ancestor=selected, limit=1000, locale=locale
                )
            original_by_id.clear()
            original_by_id.update({str(o.get("id")): o for o in selected_objects})
            all_rows = [row_data(o, locale, attribute_names) for o in selected_objects]
            grid.run_grid_method("setGridOption", "rowData", all_rows)

        # Category tree (left-panel view): aggregation-based navigation. Category
        # nodes are objects of type `category`; their hierarchy is `depends_on`
        # (so the generic LazyTree renders it), but selecting a node filters the
        # grid via `?category=<id>` (category_member aggregation), not `?ancestor=`.
        category_roots: list[dict] = []
        # Currently selected category tree root id (None = show all roots/trees).
        selected_tree: dict = {"id": None}
        # Special "Nicht kategorisiert" entry (lists objects with no category_member).
        uncategorized_id = "__uncategorized__"
        uncategorized_node = {"id": uncategorized_id, "label": t("grid.uncategorized")}

        async def load_category_children(node_id: str) -> list[dict]:
            children = await client.list_object_children(
                token, node_id, limit=100, count_members=True
            )
            return [
                tree_node_from_object(child, locale)
                for child in children
                if child.get("type") == "category"
            ]

        try:
            top_children = await client.list_object_children(
                token, root_id, limit=100, count_members=True
            )
            category_roots = [
                tree_node_from_object(child, locale)
                for child in top_children
                if child.get("type") == "category"
            ]
        except Exception:  # noqa: BLE001 - degrade to a structure-only panel
            category_roots = []

        async def handle_category_select(e) -> None:
            nonlocal all_rows
            selected = e.value if hasattr(e, "value") else None
            if selected == uncategorized_id:
                selected_objects = await client.list_objects(
                    token, uncategorized=True, limit=1000, locale=locale
                )
            elif not selected:
                selected_objects = objects
            else:
                selected_objects = await client.list_objects(
                    token, category=selected, limit=1000, locale=locale
                )
            original_by_id.clear()
            original_by_id.update({str(o.get("id")): o for o in selected_objects})
            all_rows = [row_data(o, locale, attribute_names) for o in selected_objects]
            grid.run_grid_method("setGridOption", "rowData", all_rows)

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
                if oid is None:
                    continue
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
        visible_fields: set[str] = {"_id", "_name", "_type", "_status", "_completeness", "_refs"}
        checkbox_refs: dict[str, ui.checkbox] = {}

        def apply_visibility(fields: set[str]) -> None:
            for column in column_defs:
                grid.run_grid_method(
                    "setColumnsVisible", [column["field"]], column["field"] in fields
                )

        def save_view() -> None:
            name = None if view_input.value is None else view_input.value.strip()
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

        # Reference picker (modal): display + add/remove an object's references.
        relation_options: dict[str, str] = {}
        try:
            for definition in await client.list_relation_definitions(token):
                if str(definition.get("kind")) != "aggregation":
                    continue
                # The core's `create_reference` expects the definition's short
                # record key (e.g. `rel_category_member`, from the record id
                # `relation_definition:rel_category_member`), not the full id or
                # the display `name` (which differs, e.g. `category_member`).
                def_key = str(definition["id"]).split(":", 1)[-1]
                relation_options[def_key] = (
                    resolve_value(definition.get("label"), locale) or def_key
                )
        except Exception:  # noqa: BLE001 - degrade to an empty picker
            relation_options = {}

        current_ref_object: dict = {"id": None}

        ref_dialog = ui.dialog()
        with ref_dialog, ui.card().classes("w-[640px]"):
            ui.label(t("grid.references")).classes("text-h6")
            ref_object_label = ui.label().classes("text-caption text-gray-500")
            ref_chips = ui.column()
            with ui.row().classes("items-center gap-2 w-full"):
                ref_definition = (
                    ui.select(relation_options, value=None, label=t("grid.relation_type"))
                    .props("outline dense")
                    .classes("grow")
                )
            ref_search = (
                ui.input(t("grid.reference_search")).props("outline dense").classes("w-full")
            )
            ref_hits = ui.column()
            with ui.row().classes("w-full justify-end"):
                ui.button(t("grid.close"), on_click=ref_dialog.close).props("flat dense")

        async def remove_reference(reference_id: str) -> None:
            try:
                await client.delete_reference(token, reference_id)
            except Exception:  # noqa: BLE001 - show a friendly message
                ui.notify(t("grid.ref_remove_failed"), color="negative")
            await refresh_references()

        async def refresh_references() -> None:
            ref_chips.clear()
            target = current_ref_object["id"]
            if not target:
                return
            try:
                references = await client.list_object_references(token, target, locale=locale)
            except Exception:  # noqa: BLE001 - degrade to no chips
                references = []
            # Resolve relation-type labels for the group headings (all kinds,
            # including the composition `rel_depends_on`).
            def_labels: dict[str, str] = {}
            try:
                for definition in await client.list_relation_definitions(token):
                    dk = str(definition["id"]).split(":", 1)[-1]
                    def_labels[dk] = resolve_value(definition.get("label"), locale) or dk
            except Exception:  # noqa: BLE001 - fall back to raw keys
                def_labels = {}
            # Group the object's references by relation type; the chip shows the
            # "other" object (the one *this* object points to / is pointed by).
            groups: dict[str, list[dict]] = {}
            for reference in references:
                dk = str(reference.get("definition_id") or "")
                other = (
                    reference.get("in_object")
                    if reference.get("out") == target
                    else reference.get("out_object")
                ) or {}
                groups.setdefault(dk, []).append(
                    {
                        "id": reference.get("id"),
                        "label": other.get("label") or "",
                        "type": other.get("type") or "",
                        "definition_id": dk,
                        "removable": dk != "rel_depends_on",
                    }
                )
            if not groups:
                with ref_chips, ui.column().classes("w-full"):
                    ui.label(t("grid.ref_empty")).classes("text-caption text-gray-500")
                return

            # Order groups: composition first, then aggregations by their label.
            def group_sort_key(dk: str) -> tuple:
                rank = 0 if dk == "rel_depends_on" else 1
                return (rank, def_labels.get(dk, dk).lower())

            for dk in sorted(groups, key=group_sort_key):
                label = def_labels.get(dk, dk)
                with ref_chips, ui.column().classes("w-full gap-1"):
                    ui.label(label).classes("text-caption font-medium text-gray-500")
                    for chip in sorted(groups[dk], key=lambda c: (c["label"].lower(), c["type"])):
                        with ui.row().classes("items-center gap-2 w-full"):
                            ui.label(f"{chip['label']} · {chip['type']}").classes(
                                "bg-neutral px-2 py-0.5 rounded text-xs"
                            )
                            if chip["removable"]:
                                ui.button(
                                    icon="close",
                                    on_click=lambda rid=chip["id"]: remove_reference(rid),
                                ).props("flat dense size=sm").tooltip(t("grid.ref_remove"))

        async def add_reference(hit: dict) -> None:
            definition_id = ref_definition.value
            if not definition_id:
                ui.notify(t("grid.ref_relation_required"), color="warning")
                return
            try:
                await client.create_reference(
                    token, current_ref_object["id"], hit["id"], definition_id
                )
            except Exception:  # noqa: BLE001 - show a friendly message
                ui.notify(t("grid.ref_add_failed"), color="negative")
                return
            ref_search.value = ""
            ref_hits.clear()
            await refresh_references()

        async def search_references(value: str) -> None:
            q = (value or "").strip()
            ref_hits.clear()
            if not q or not current_ref_object["id"]:
                return
            try:
                hits = await client.picker_search(token, q, locale=locale, limit=20)
            except Exception:  # noqa: BLE001 - degrade to no hits
                return
            for hit in hits:
                with ref_hits, ui.row().classes("items-center gap-2 w-full"):
                    ui.button(
                        f"{hit['label']} · {hit['type']}",
                        on_click=lambda h=hit: add_reference(h),
                    ).props("outline dense").classes("w-full justify-start")

        async def open_references(object_id: str) -> None:
            current_ref_object["id"] = object_id
            ref_object_label.set_text(object_id)
            ref_definition.value = next(iter(relation_options), None) if relation_options else None
            ref_search.value = ""
            ref_hits.clear()
            await refresh_references()
            ref_dialog.open()

        async def handle_ref_cell_clicked(e) -> None:
            args = e.args if isinstance(e.args, dict) else {}
            if args.get("colId") == "_refs":
                data = args.get("data")
                if data and data.get("_id"):
                    await open_references(data["_id"])

        ref_search.on_value_change(lambda e: search_references(e.value))

        current_tree: dict = {"tree": None, "nodes": []}

        def collapse_all() -> None:
            if current_tree["tree"] is not None:
                current_tree["tree"].collapse()

        def expand_all() -> None:
            if current_tree["tree"] is not None:
                current_tree["tree"].expand()

        def apply_tree_filter(value: str) -> None:
            tree = current_tree["tree"]
            if tree is not None:
                tree.set_nodes(filter_tree_nodes(current_tree["nodes"], value))

        def toggle_panel() -> None:
            if splitter.value is None:
                splitter.value = 25
            splitter.value = 0 if splitter.value > 0 else 25

        host: ui.column | None = None

        def render_tree(view: str) -> None:
            nonlocal current_tree
            if host is None:
                return
            host.clear()
            if view == "category" and category_roots:
                selected_id = selected_tree["id"]
                if selected_id:
                    nodes = [r for r in category_roots if r["id"] == selected_id]
                    if not nodes:
                        nodes = category_roots
                else:
                    nodes = category_roots
                nodes = [uncategorized_node, *nodes]
                loader = load_category_children
                on_select = handle_category_select
            elif tree_nodes:
                nodes = tree_nodes
                loader = load_children
                on_select = handle_tree_select
            else:
                with host:
                    ui.label(t("grid.tree_load_failed")).classes("text-caption text-negative")
                current_tree = {"tree": None, "nodes": []}
                return
            with host:
                tree = LazyTree(
                    nodes,
                    on_select=on_select,
                    load_children=loader,
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
            current_tree = {"tree": tree, "nodes": nodes}

        with ui.splitter(value=25, limits=(0, 60)).classes(
            "w-full h-[calc(100vh-210px)]"
        ) as splitter:
            # Left: resizable tree card (structure or categories via toggle).
            with splitter.before, ui.card().classes("w-full h-full p-0"):  # noqa: SIM117 - layout nesting
                # Line 1: view selector as icon buttons (active highlighted).
                def style_view_button(btn: ui.button, active: bool) -> None:
                    if active:
                        btn.props("color=primary text-white", remove="flat")
                    else:
                        btn.props("flat", remove="color text-white")

                def set_view(view: str) -> None:
                    style_view_button(structure_btn, view == "structure")
                    style_view_button(category_btn, view == "category")
                    tree_select.set_visibility(view == "category")
                    render_tree(view)

                with ui.row().classes("items-center gap-2 w-full").style("flex-wrap: nowrap"):
                    with ui.button_group().props("dense"):
                        structure_btn = ui.button(icon="account_tree").tooltip(t("grid.structure"))
                        category_btn = ui.button(icon="category").tooltip(t("grid.category"))
                    structure_btn.on_click(lambda: set_view("structure"))
                    category_btn.on_click(lambda: set_view("category"))
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
                        render_tree("category")

                    tree_select.on_value_change(lambda e: on_tree_select(e.value))
                # Line 2: filter + collapse buttons.
                with ui.row().classes("items-center gap-2 w-full").style("flex-wrap: nowrap"):
                    filter_input = ui.input(t("grid.filter")).props("outline dense").classes("grow")
                    if tree_nodes or category_roots:
                        ui.button(icon="unfold_less", on_click=collapse_all).props(
                            "flat dense shrink-0"
                        ).tooltip(t("grid.collapse_all"))
                        ui.button(icon="unfold_more", on_click=expand_all).props(
                            "flat dense shrink-0"
                        ).tooltip(t("grid.expand_all"))
                host = ui.column().classes("w-full grow").style("min-height: 0; overflow-y: auto")
                set_view("structure")
                filter_input.on_value_change(lambda e: apply_tree_filter(e.value or ""))
            # Right: grid area (fills the remaining width and height).
            with splitter.after, ui.column().classes("w-full h-full"):  # noqa: SIM117 - layout
                with ui.row().classes("items-center gap-2 w-full"):
                    # Panel toggle (structure tree): separate function, far left.
                    ui.button(icon="view_sidebar", on_click=toggle_panel).props(
                        "flat dense"
                    ).tooltip(t("grid.toggle_tree"))
                    staged_label = ui.label(t("grid.staged_changes").format(count=0)).classes(
                        "text-caption"
                    )
                    # Right-aligned data actions + view controls.
                    ui.button(t("grid.save"), icon="save", on_click=save).classes(
                        "bg-primary text-white ml-auto"
                    )
                    ui.button(icon="undo", on_click=discard).props("outline").tooltip(
                        t("grid.discard")
                    )
                    ui.button(icon="bookmarks", on_click=views_dialog.open).props(
                        "outline"
                    ).tooltip(t("grid.views"))

                    def make_toggler(field: str):
                        def toggle(event) -> None:
                            grid.run_grid_method("setColumnsVisible", [field], event.value)
                            if event.value:
                                visible_fields.add(field)
                            else:
                                visible_fields.discard(field)

                        return toggle

                    with (
                        ui.button(icon="view_column").props("outline").tooltip(t("grid.columns")),
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
                    html_columns=[
                        i
                        for i, column in enumerate(column_defs)
                        if column["field"] in ("_status", "_completeness")
                    ],
                ).classes("w-full grow")
                grid.on("cellValueChanged", mark_changed, ["data"])
                grid.on("cellClicked", handle_ref_cell_clicked, ["data", "colId"])
