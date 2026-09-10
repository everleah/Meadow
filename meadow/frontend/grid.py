"""Object list grid (AG Grid): the main work area (Phase 7, steps 3-5).

The grid lists objects with human-friendly display (resolved name, status
badge), supports inline editing / batch save, offers a column picker and
personal views, and shows a composition (structure) tree that filters the
grid to the selected subtree. Category & structure navigation with the
Kategorien toggle and the completeness column follow in later steps.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from urllib.parse import quote

from fastapi import Request
from nicegui import app, ui

from meadow.frontend import config
from meadow.frontend.api import CoreClient, is_session_expired
from meadow.frontend.i18n import current_locale, t
from meadow.frontend.layout import build_shell
from meadow.frontend.lazy_tree import LazyTree

# Maximum hits the grid search pulls per query (picker + row load); the search
# is server-side, so the result set stays bounded regardless of dataset size.
SEARCH_LIMIT = 100
# Debounce (ms) for the filter bar's search field: one core request per pause.
SEARCH_DEBOUNCE_MS = 400


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


def _display_value(
    value: object,
    locale: str,
    resolver: dict[str, str] | None,
) -> str:
    """Resolves a cell value to a display string, applying an enum resolver."""
    if value is None:
        return ""
    if isinstance(value, dict):
        code = str(value.get(locale) or next(iter(value.values()), "") or "")
        return resolver.get(code, code) if resolver is not None else resolve_value(value, locale)
    if resolver is not None:
        return resolver.get(str(value), str(value))
    return resolve_value(value, locale)


def row_data(
    object_: dict,
    locale: str,
    attribute_names: list[str] | None = None,
    enum_resolvers: dict[str, dict[str, str]] | None = None,
) -> dict:
    """Maps a core object to an AG Grid row.

    With ``attribute_names``, one ``attr_<name>`` field per attribute is added
    (resolved to a display string for the working locale). ``enum_resolvers``
    maps each enum attribute name to a ``{code: label}`` map so stored codes are
    shown as their localized labels. The ``_completeness`` field mirrors the
    core's ``completeness`` value for the working locale.
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
        value = attributes.get(name)
        row[f"attr_{name}"] = _display_value(value, locale, (enum_resolvers or {}).get(name))
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
    node: dict = {"id": node_id, "label": label, "type": obj_type}
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


async def _wait_export(client: CoreClient, token: str, run_id: str) -> dict:
    """Polls an export job run until it reaches a terminal state."""
    for _ in range(120):
        try:
            runs = await client.list_job_runs(token, ids=[run_id], limit=1)
        except Exception:  # noqa: BLE001 - keep polling on a transient error
            runs = []
        if runs:
            status = str(runs[0].get("status") or "")
            if status in ("success", "failed", "retry"):
                return runs[0]
        await asyncio.sleep(1)
    return {"status": "failed", "error": "timeout"}


#: Mime type per export format, used for the browser download.
_EXPORT_MIME = {
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "json": "application/json",
}


def _save_download(data: bytes, filename: str, format_: str) -> None:
    """Serves export bytes via a temp file (NiceGUI str/bytes src is unreliable)."""
    with tempfile.NamedTemporaryFile("wb", suffix="." + format_, delete=False) as handle:
        handle.write(data)
        tmp_path = handle.name
    ui.download(tmp_path, filename, _EXPORT_MIME.get(format_, "application/octet-stream"))
    asyncio.get_running_loop().call_later(60, os.remove, tmp_path)


@ui.page("/products", response_timeout=10)
async def products_page(request: Request) -> None:
    """Lists objects in the main grid with inline editing + batch save."""
    await build_shell(active="/products")
    locale = current_locale()
    # Active grid filters: seeded from the URL (bookmarkable, e.g. the dashboard
    # shortcut `?incomplete=1&type=product`) and then driven by the filter bar.
    active: dict = {
        "incomplete": request.query_params.get("incomplete") == "1",
        "type": request.query_params.get("type") or None,
        "ancestor": request.query_params.get("ancestor") or None,
        "category": request.query_params.get("category") or None,
        "collection": request.query_params.get("collection") or None,
        "uncategorized": request.query_params.get("uncategorized") == "1",
        "q": request.query_params.get("q") or None,
    }
    # Monotonic reload counter: a slow reload never overwrites a newer result.
    load_seq = {"value": 0}

    def has_filter() -> bool:
        """Whether any filter is active (a plain visit deliberately loads nothing)."""
        return bool(
            active["incomplete"]
            or active["type"]
            or active["ancestor"]
            or active["category"]
            or active["collection"]
            or active["uncategorized"]
            or active["q"]
        )

    with ui.column().classes("w-full p-6"):
        ui.label(t("nav.products")).classes("text-h4")
        # Prompt shown when the grid is empty because no node/filter is active.
        selection_hint = ui.label(t("grid.select_tree_node")).classes(
            "text-subtitle1 text-gray-500 pb-2"
        )
        client = CoreClient(config.CORE_URL)
        token = app.storage.user.get("token", "")

        async def fetch_filtered() -> list[dict]:
            """Loads the objects matching the active filters (see `active`)."""
            if not has_filter():
                return []
            if active["q"]:
                # Server-side full-text search (the grid may be empty otherwise):
                # search the labels, then load the hits as full grid rows.
                hits = await client.picker_search(
                    token,
                    active["q"],
                    types=[active["type"]] if active["type"] else None,
                    locale=locale,
                    limit=SEARCH_LIMIT,
                )
                ids = [str(hit.get("id")) for hit in hits if hit.get("id")]
                if not ids:
                    return []
                return await client.list_objects(
                    token,
                    ids=ids,
                    missing_required=bool(active["incomplete"]),
                    locale=locale,
                    limit=SEARCH_LIMIT,
                )
            return await client.list_objects(
                token,
                object_type=active["type"],
                ancestor=active["ancestor"],
                category=active["category"],
                collection=active["collection"],
                uncategorized=bool(active["uncategorized"]),
                missing_required=bool(active["incomplete"]),
                locale=locale,
                limit=1000 if has_filter() else 100,
            )

        # On a plain /products visit we deliberately avoid loading every object:
        # the grid stays empty and the user picks a node in the tree or searches.
        # A filter (e.g. `?incomplete=1&type=product`, `?ancestor=…`, `?category=…`)
        # loads the targeted objects so shortcuts still work.
        objects: list[dict] = []
        try:
            objects = await fetch_filtered()
        except Exception:  # noqa: BLE001 - keep the page usable (filter bar + grid)
            ui.notify(t("grid.load_failed"), color="negative")
        no_objects_label = ui.label(t("grid.no_objects")).classes("text-subtitle1")
        no_objects_label.set_visibility(has_filter() and not objects)
        selection_hint.set_visibility(not has_filter())

        try:
            definitions = await client.list_attribute_definitions(token)
        except Exception:  # noqa: BLE001 - degrade to base columns on failure
            definitions = []
        attribute_names = [
            str(definition.get("name")) for definition in definitions if definition.get("name")
        ]

        # Resolve enum attribute values to localized labels via their enum sets.
        enum_resolvers: dict[str, dict[str, str]] = {}
        enum_defs = [d for d in definitions if d.get("type") == "enum"]
        if enum_defs:
            try:
                enum_sets = await client.list_enum_sets(token)
            except Exception:  # noqa: BLE001 - degrade to raw codes on failure
                enum_sets = []
            from meadow.frontend.enums import enum_code_label_map

            for defn in enum_defs:
                name = str(defn.get("name"))
                mapping = enum_code_label_map(enum_sets, defn, locale)
                if mapping:
                    enum_resolvers[name] = mapping

        # Column order is fixed: the reference/action column (`_refs`, the link
        # icon that opens the reference picker) is always the **last** column, so
        # attribute columns are appended *before* it (pinned right so it stays
        # visible however far the grid is scrolled).
        column_defs = (
            [
                {"headerName": t("grid.id"), "field": "_id", "width": 100, "pinned": "left"},
                {"headerName": t("grid.name"), "field": "_name", "editable": True},
                {"headerName": t("grid.type"), "field": "_type", "width": 120},
                {"headerName": t("grid.status"), "field": "_status", "width": 140},
                {"headerName": t("grid.completeness"), "field": "_completeness", "width": 140},
            ]
            + attribute_columns(definitions, locale)
            + [
                {"headerName": "", "field": "_refs", "width": 60, "pinned": "right"},
            ]
        )

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

        all_rows = [
            row_data(object_, locale, attribute_names, enum_resolvers) for object_ in objects
        ]

        def sync_url_query() -> None:
            """Keeps the URL in sync with the active filters (bookmarkable)."""
            parts = []
            if active["incomplete"]:
                parts.append("incomplete=1")
            if active["type"]:
                parts.append(f"type={quote(str(active['type']))}")
            if active["ancestor"]:
                parts.append(f"ancestor={quote(str(active['ancestor']))}")
            if active["category"]:
                parts.append(f"category={quote(str(active['category']))}")
            if active["collection"]:
                parts.append(f"collection={quote(str(active['collection']))}")
            if active["uncategorized"]:
                parts.append("uncategorized=1")
            if active["q"]:
                parts.append(f"q={quote(str(active['q']))}")
            query = "&".join(parts)
            ui.run_javascript(
                f"history.replaceState(null, '', '/products{('?' + query) if query else ''}')"
            )

        async def apply_rows(selected_objects: list[dict]) -> None:
            nonlocal original_by_id, all_rows
            original_by_id = {str(o.get("id")): o for o in selected_objects}
            all_rows = [
                row_data(o, locale, attribute_names, enum_resolvers) for o in selected_objects
            ]
            grid.run_grid_method("setGridOption", "rowData", all_rows)
            selection_hint.set_visibility(len(selected_objects) == 0 and not has_filter())
            no_objects_label.set_visibility(len(selected_objects) == 0 and has_filter())

        async def reload_grid() -> None:
            """Re-fetches the grid rows for the active filters (no page reload)."""
            load_seq["value"] += 1
            seq = load_seq["value"]
            try:
                new_objects = await fetch_filtered()
            except Exception:  # noqa: BLE001 - keep the current rows
                ui.notify(t("grid.load_failed"), color="negative")
                return
            if seq != load_seq["value"]:
                return  # a newer reload already applied its rows
            await apply_rows(new_objects)
            sync_url_query()

        async def handle_tree_select(e) -> None:
            selected = e.value if hasattr(e, "value") else None
            active.update(ancestor=selected, category=None, collection=None, uncategorized=False)
            active_collection["id"] = None
            await reload_grid()

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
            selected = e.value if hasattr(e, "value") else None
            active.update(ancestor=None, category=None, collection=None, uncategorized=False)
            if selected == uncategorized_id:
                active["uncategorized"] = True
            elif selected:
                active["category"] = selected
            await reload_grid()

        # Materialized collections (flat, no tree): a collection view filters the
        # grid via `?collection=<id>` (collection_member aggregation).
        collections: list[dict] = []
        try:
            collections = await client.list_collections(token, limit=1000)
        except Exception:  # noqa: BLE001 - degrade to an empty collection list
            collections = []

        def _collection_label(c: dict) -> str:
            return (
                resolve_value((c.get("attributes") or {}).get("name"), locale)
                or str(c.get("name") or "")
                or str(c.get("id") or "")
            )

        def _collection_node(c: dict) -> dict:
            return {
                "id": str(c.get("id")),
                "label": _collection_label(c),
                "type": "collection",
                "count": int(c.get("member_count") or 0),
            }

        # Tracks the currently selected collection so leaving the collection view
        # can clear the `?collection=` filter and URL.
        active_collection: dict = {"id": None}

        async def handle_collection_select(e) -> None:
            selected = e.value if hasattr(e, "value") else None
            active_collection["id"] = selected or None
            active.update(
                ancestor=None,
                category=None,
                uncategorized=False,
                collection=selected or None,
            )
            await reload_grid()

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
                    ui.button(name, on_click=lambda n=name: load_view(n)).props("flat dense")  # type: ignore
                    ui.button(icon="delete", on_click=lambda n=name: delete_view(n)).props(  # type: ignore
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
                                    on_click=lambda rid=chip["id"]: remove_reference(rid),  # type: ignore
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
                        on_click=lambda h=hit: add_reference(h),  # type: ignore
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

        def handle_open_cell(e) -> None:
            """Opens the product form on a row double-click."""
            args = e.args if isinstance(e.args, dict) else {}
            if args.get("colId") == "_refs":
                return
            data = args.get("data")
            if data and data.get("_id"):
                ui.navigate.to(f"/products/{quote(str(data['_id']), safe='')}")

        ref_search.on_value_change(lambda e: search_references(e.value))  # type: ignore

        # --- Bulk-Edit-Wizard (Phase 7) -------------------------------------
        bulk_selected_ids: set[str] = set()
        bulk_selected_label: ui.element | None = None
        bulk_scope: ui.select | None = None
        bulk_dialog: ui.dialog | None = None

        def _row_id(row: object) -> str | None:
            """Extract the object id from a selected row (data dict or row node)."""
            if not isinstance(row, dict):
                return None
            oid = row.get("_id")
            if not oid:
                data = row.get("data")
                if isinstance(data, dict):
                    oid = data.get("_id")
            return str(oid) if oid else None

        async def handle_selection(event) -> None:
            try:
                selected_rows = await grid.get_selected_rows()
            except Exception:  # noqa: BLE001 - degrade if the grid is not ready
                selected_rows = []
            bulk_selected_ids.clear()
            for row in selected_rows:
                oid = _row_id(row)
                if oid:
                    bulk_selected_ids.add(oid)
            bulk_selected_label.set_text(
                t("grid.bulk_selected").format(count=len(bulk_selected_ids))
            )
            if bulk_scope is not None:
                bulk_scope.set_value("selected" if bulk_selected_ids else "filtered")

        # Load category objects and candidate parents for the wizard pickers.
        # Bulk-wizard pickers (category / parent options) are loaded lazily on
        # first use instead of on every /products render, so a large catalog does
        # not require fetching all objects just to render the page.
        category_options: dict[str, str] = {}
        parent_options: dict[str, str] = {}

        async def load_category_options() -> None:
            nonlocal category_options
            try:
                objs = await client.list_objects(token, object_type="category", limit=1000)
            except Exception:  # noqa: BLE001 - degrade to an empty picker
                objs = []
            category_options = {
                str(c.get("id")): (
                    resolve_value((c.get("attributes") or {}).get("name"), locale)
                    or str(c.get("type"))
                    or str(c.get("id"))
                )
                for c in objs
            }

        async def load_parent_options() -> None:
            nonlocal parent_options
            try:
                objs = await client.list_objects(token, limit=1000)
            except Exception:  # noqa: BLE001 - degrade to an empty picker
                objs = []
            parent_options = {
                str(o.get("id")): (
                    resolve_value((o.get("attributes") or {}).get("name"), locale)
                    or str(o.get("type"))
                    or str(o.get("id"))
                )
                for o in objs
            }

        bulk_attr_options = {name: name for name in attribute_names}
        bulk_status_options = {
            "draft": "draft",
            "in_review": "in_review",
            "published": "published",
            "archived": "archived",
        }
        bulk_fields: dict[str, ui.element] = {}

        bulk_dialog = ui.dialog()
        with bulk_dialog, ui.card().classes("w-[36rem] max-w-[92vw]"):
            ui.label(t("grid.bulk_title")).classes("text-h6")
            ui.label(t("grid.bulk_selection")).classes("text-caption text-gray-600")
            bulk_scope = (
                ui.select(
                    {
                        "filtered": t("grid.bulk_scope_filtered").format(count=len(all_rows)),
                        "page": t("grid.bulk_scope_page"),
                        "selected": t("grid.bulk_scope_selected"),
                    },
                    value="selected" if bulk_selected_ids else "filtered",
                )
                .props("filled dense")
                .classes("w-full")
            )
            bulk_selected_label = ui.label("").classes("text-caption")
            ui.label(t("grid.bulk_operation")).classes("text-caption text-gray-600")

            async def _on_bulk_op_change(_event) -> None:
                """Re-renders the wizard config area, loading pickers lazily."""
                await refresh_config()

            bulk_op = (
                ui.select(
                    {
                        "set_attribute": t("grid.op_set_attribute"),
                        "clear_attribute": t("grid.op_clear_attribute"),
                        "change_status": t("grid.op_change_status"),
                        "add_category": t("grid.op_add_category"),
                        "remove_category": t("grid.op_remove_category"),
                        "change_parent": t("grid.op_change_parent"),
                        "delete": t("grid.op_delete"),
                    },
                    value=None,
                    on_change=_on_bulk_op_change,
                )
                .props("filled dense")
                .classes("w-full")
            )
            config_area = ui.column().classes("w-full mt-2 gap-2")

            with ui.row().classes("w-full justify-end mt-2 gap-2"):
                ui.button(t("grid.close"), on_click=bulk_dialog.close).props("flat")
                ui.button(
                    t("grid.bulk_start"), icon="play_arrow", on_click=lambda: start_bulk()
                ).props("flat")

        async def refresh_config(_event=None) -> None:  # noqa: ARG001 - handler arg unused
            config_area.clear()
            bulk_fields.clear()
            op = bulk_op.value or ""
            with config_area:
                if op in ("set_attribute", "clear_attribute"):
                    select = (
                        ui.select(bulk_attr_options, label=t("grid.bulk_attribute"))
                        .props("filled dense")
                        .classes("w-full")
                    )
                    bulk_fields["attribute"] = select
                    if op == "set_attribute":
                        value = (
                            ui.input(label=t("grid.bulk_value"))
                            .props("filled dense")
                            .classes("w-full")
                        )
                        bulk_fields["value"] = value
                elif op == "change_status":
                    select = (
                        ui.select(
                            bulk_status_options, value=None, label=t("grid.bulk_status_label")
                        )
                        .props("filled dense")
                        .classes("w-full")
                    )
                    bulk_fields["status"] = select
                elif op in ("add_category", "remove_category"):
                    if not category_options:
                        await load_category_options()
                    select = (
                        ui.select(category_options, value=None, label=t("grid.bulk_category"))
                        .props("filled dense")
                        .classes("w-full")
                    )
                    bulk_fields["category"] = select
                elif op == "change_parent":
                    if not parent_options:
                        await load_parent_options()
                    select = (
                        ui.select(parent_options, value=None, label=t("grid.bulk_parent"))
                        .props("filled dense")
                        .classes("w-full")
                    )
                    bulk_fields["parent"] = select
                elif op == "delete":
                    ui.label(t("grid.bulk_delete_warn")).classes("text-caption text-negative")

        async def _current_page_ids() -> list[str]:
            """Return the object ids of the rows on the current grid page."""
            try:
                page = int(await grid.run_grid_method("paginationGetCurrentPage") or 0)
                size = int(await grid.run_grid_method("paginationGetPageSize") or 50)
            except Exception:  # noqa: BLE001 - fall back to the configured page size
                page, size = 0, 50
            start = page * size
            end = start + size
            return [oid for oid in (_row_id(r) for r in all_rows[start:end]) if oid]

        async def start_bulk() -> None:
            op = bulk_op.value
            if not op:
                ui.notify(t("grid.bulk_op_required"), color="warning")
                return
            if bulk_scope.value == "selected":
                selected: list = await grid.get_selected_rows()
                ids = [oid for oid in (_row_id(row) for row in selected) if oid]
            elif bulk_scope.value == "page":
                ids = await _current_page_ids()
            else:
                ids = [oid for oid in (_row_id(r) for r in all_rows) if oid]
            ids = [oid for oid in ids if oid]
            if not ids:
                ui.notify(t("grid.bulk_no_objects"), color="warning")
                return
            parameters: dict = {}
            if op in ("set_attribute", "clear_attribute"):
                attribute = bulk_fields.get("attribute")
                parameters["attribute"] = attribute.value if attribute else None
                if op == "set_attribute":
                    value = bulk_fields.get("value")
                    parameters["value"] = value.value if value else None
            elif op == "change_status":
                status = bulk_fields.get("status")
                parameters["status"] = status.value if status else None
            elif op in ("add_category", "remove_category"):
                category = bulk_fields.get("category")
                parameters["category"] = category.value if category else None
            elif op == "change_parent":
                parent = bulk_fields.get("parent")
                parameters["parent"] = parent.value if parent else None

            missing = [k for k, v in parameters.items() if not v]
            if missing:
                ui.notify(t("grid.bulk_config_incomplete"), color="warning")
                return
            try:
                run = await client.post_bulk(token, op=op, object_ids=ids, parameters=parameters)
            except Exception:  # noqa: BLE001 - show a friendly message
                ui.notify(t("grid.bulk_start_failed"), color="negative")
                return
            run_id = str(run.get("id") or "")
            bulk_dialog.close()
            ui.notify(t("grid.bulk_started"), color="positive")
            if run_id:
                await poll_bulk(run_id)

        async def poll_bulk(run_id: str) -> None:
            with ui.dialog() as progress_dialog, ui.card().classes("w-[32rem]"):
                ui.label(t("grid.bulk_running")).classes("text-subtitle1")
                status_label = ui.label("").classes("text-caption")
                ui.linear_progress(value=0.5).classes("w-full")
                with ui.row().classes("w-full justify-end"):
                    ui.button(t("grid.close"), on_click=progress_dialog.close).props("flat")
            progress_dialog.open()
            runs: list[dict] = []
            for _ in range(120):
                try:
                    runs = await client.list_job_runs(token, ids=[run_id], limit=1)
                except Exception:  # noqa: BLE001 - retry on transient errors
                    runs = []
                if not runs:
                    break
                status = str(runs[0].get("status") or "")
                status_label.set_text(t("grid.bulk_status").format(status=status))
                if status in ("success", "failed"):
                    break
                await asyncio.sleep(2)
            progress_dialog.close()
            if runs and runs[0].get("status") == "success":
                _show_bulk_report(runs[0].get("result") or {})
            else:
                ui.notify(t("grid.bulk_failed"), color="negative")

        def _show_bulk_report(result: dict) -> None:
            processed = str(result.get("processed", 0))
            succeeded = str(result.get("succeeded", 0))
            failed = str(result.get("failed", 0))
            with (
                ui.dialog() as report_dialog,
                ui.card().classes("w-[32rem] max-h-[60vh] overflow-y-auto"),
            ):
                ui.label(t("grid.bulk_report")).classes("text-subtitle1")
                ui.label(
                    t("grid.bulk_report_summary").format(
                        processed=processed, succeeded=succeeded, failed=failed
                    )
                ).classes("text-caption")
                errors = [r for r in result.get("results", []) if r.get("status") == "error"]
                if errors:
                    with ui.column().classes("w-full mt-2 gap-1"):
                        for r in errors:
                            ui.label(f"{r.get('object_id')} · {r.get('error')}").classes(
                                "text-caption text-negative"
                            )
                with ui.row().classes("w-full justify-end mt-2"):
                    ui.button(t("grid.close"), on_click=report_dialog.close).props("flat")
            report_dialog.open()

        current_tree: dict = {"tree": None, "nodes": []}
        # Tracks the active left-panel view so the action button (collapse vs.
        # collection reload) and the `?collection=` reset are correct.
        active_view: dict = {"value": "structure"}

        def collapse_all() -> None:
            if current_tree["tree"] is not None:
                current_tree["tree"].collapse()

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
            if view == "collection" and collections:

                async def _no_children(_node_id: str) -> list[dict]:
                    return []

                nodes = [_collection_node(c) for c in collections]
                loader = _no_children
                on_select = handle_collection_select
            elif view == "category" and category_roots:
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

        async def reload_collection() -> None:
            """Reloads the selected collection: re-materializes its members
            (``POST /collections/{id}/refresh``), refreshes the collection list
            (member counts) and re-fetches the grid for the active filter.
            The tree is left untouched while a collection is selected — re-sending
            the node list would corrupt the selection (the node toggles off on the
            next click). Without a selection the tree is re-rendered to update the
            member counts only."""
            nonlocal collections
            col_id = active_collection["id"]
            try:
                if col_id:
                    await client.refresh_collection(token, col_id)
                collections = await client.list_collections(token, limit=1000)
            except Exception:  # noqa: BLE001 - keep current data on failure
                pass
            if active_view["value"] == "collection" and not col_id:
                render_tree("collection")
            if col_id:
                await reload_grid()

        # --- Export (selection / current filter) ---------------------------
        def _export_fields() -> list[str]:
            """Maps the currently visible grid columns to export field names."""
            fields: list[str] = []
            for column in column_defs:
                field = str(column.get("field") or "")
                if field not in visible_fields:
                    continue
                if field == "_id":
                    fields.append("id")
                elif field == "_type":
                    fields.append("type")
                elif field == "_status":
                    fields.append("status")
                elif field == "_name":
                    fields.append("name")
                elif field.startswith("attr_"):
                    fields.append(field[len("attr_") :])
            return fields or ["id", "type", "status"]

        export_dialog = ui.dialog()
        with export_dialog, ui.card().classes("w-[32rem] max-w-[92vw]"):
            ui.label(t("grid.export_title")).classes("text-h6")
            ui.label(t("grid.export_hint")).classes("text-caption text-gray-600")
            export_scope = (
                ui.select(
                    {
                        "filtered": t("grid.export_scope_filtered").format(count=len(all_rows)),
                        "selected": t("grid.export_scope_selected"),
                    },
                    value="selected" if bulk_selected_ids else "filtered",
                )
                .props("filled dense")
                .classes("w-full")
            )
            export_format = (
                ui.select(
                    {"csv": "CSV", "xlsx": "XLSX", "json": "JSON"},
                    value="csv",
                    label=t("grid.export_format"),
                )
                .props("filled dense")
                .classes("w-full")
            )

            async def _do_export() -> None:
                if export_scope.value == "selected":
                    selected: list = await grid.get_selected_rows()
                    ids = [oid for oid in (_row_id(row) for row in selected) if oid]
                else:
                    ids = [oid for oid in (_row_id(row) for row in all_rows) if oid]
                if not ids:
                    ui.notify(t("grid.export_no_objects"), color="warning")
                    return
                fmt = str(export_format.value or "csv")
                try:
                    queued = await client.create_export(
                        token,
                        source={"kind": "selection", "object_ids": ids},
                        fields=_export_fields(),
                        format=fmt,
                        locales=[locale],
                    )
                    run_id = str(queued.get("job_run_id") or "")
                    if not run_id:
                        raise RuntimeError(t("grid.export_failed").format(err="no job id"))
                    run = await _wait_export(client, token, run_id)
                    if run.get("status") != "success":
                        raise RuntimeError(run.get("error") or run.get("status") or "failed")
                    data, filename = await client.get_export_file(token, run_id, format=fmt)
                except Exception as exc:  # noqa: BLE001 - surface a friendly message
                    ui.notify(t("grid.export_failed").format(err=exc), color="negative")
                    return
                exported = (run.get("result") or {}).get("exported", len(ids))
                export_dialog.close()
                _save_download(data, filename, fmt)
                ui.notify(t("grid.export_ok").format(count=exported), color="positive")

            with ui.row().classes("w-full justify-end mt-2 gap-2"):
                ui.button(t("grid.close"), on_click=export_dialog.close).props("flat")
                ui.button(t("grid.export"), icon="download", on_click=_do_export).props("flat")

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
                    nonlocal active_view
                    active_view["value"] = view
                    style_view_button(structure_btn, view == "structure")
                    style_view_button(category_btn, view == "category")
                    style_view_button(collection_btn, view == "collection")
                    tree_select.set_visibility(view == "category")
                    collapse_btn.set_visibility(
                        view != "collection" and bool(tree_nodes or category_roots)
                    )
                    reload_btn.set_visibility(view == "collection")
                    if view != "collection" and active["collection"]:
                        active["collection"] = None
                        active_collection["id"] = None
                        sync_url_query()
                    render_tree(view)

                with ui.row().classes("items-center gap-2 w-full").style("flex-wrap: nowrap"):
                    with ui.button_group().props("dense"):
                        structure_btn = ui.button(icon="account_tree").tooltip(t("grid.structure"))
                        category_btn = ui.button(icon="category").tooltip(t("grid.category"))
                        collection_btn = ui.button(icon="collections_bookmark").tooltip(
                            t("grid.collection")
                        )
                    structure_btn.on_click(lambda: set_view("structure"))
                    category_btn.on_click(lambda: set_view("category"))
                    collection_btn.on_click(lambda: set_view("collection"))
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
                # Line 2: filter + action button (collapse for structure/category,
                # collection reload for the flat Collections view).
                with ui.row().classes("items-center gap-2 w-full").style("flex-wrap: nowrap"):
                    filter_input = ui.input(t("grid.filter")).props("outline dense").classes("grow")
                    collapse_btn = (
                        ui.button(icon="unfold_less", on_click=collapse_all)
                        .props("flat dense shrink-0")
                        .tooltip(t("grid.collapse_all"))
                    )
                    reload_btn = (
                        ui.button(icon="refresh", on_click=reload_collection)
                        .props("flat dense shrink-0")
                        .tooltip(t("grid.collection_reload"))
                    )
                host = ui.column().classes("w-full grow").style("min-height: 0; overflow-y: auto")
                set_view("structure")
                filter_input.on_value_change(lambda e: apply_tree_filter(e.value or ""))
            # Right: grid area (fills the remaining width and height).
            with splitter.after, ui.column().classes("w-full h-full pl-3"):  # noqa: SIM117 - layout
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
                    ui.button(icon="fact_check", on_click=bulk_dialog.open).props(
                        "outline"
                    ).tooltip(t("grid.bulk_actions"))
                    ui.button(icon="download", on_click=export_dialog.open).props(
                        "outline"
                    ).tooltip(t("grid.export"))

                    def make_toggler(field: str):
                        def toggle(event) -> None:
                            grid.run_grid_method("setColumnsVisible", [field], event.value)
                            if event.value:
                                visible_fields.add(field)
                            else:
                                visible_fields.discard(field)

                        return toggle

                    # Column picker as a dialog (not a `ui.menu`): Quasar's QMenu
                    # auto-open doesn't fire in Firefox/Zen, while `ui.dialog` opens
                    # reliably in all browsers (same pattern as the working "views").
                    columns_dialog = ui.dialog()
                    with columns_dialog, ui.card().classes("w-[22rem] max-w-[92vw]"):
                        ui.label(t("grid.columns")).classes("text-h6")
                        with ui.column().classes("w-full gap-1 max-h-80 overflow-y-auto"):
                            for column in column_defs:
                                checkbox = ui.checkbox(
                                    column["headerName"], value=not column.get("hide")
                                ).on_value_change(make_toggler(column["field"]))
                                checkbox_refs[column["field"]] = checkbox
                        with ui.row().classes("w-full justify-end gap-2"):
                            ui.button(t("grid.close"), on_click=columns_dialog.close).props("flat")
                    ui.button(icon="view_column").props("outline").tooltip(
                        t("grid.columns")
                    ).on_click(columns_dialog.open)

                # --- Filter bar (server-side filters) --------------------------
                # The grid can be empty (no tree node picked), so both filters are
                # resolved in the core: the search via `/objects/picker` +
                # `/objects?ids=`, "only incomplete" via `?missing_required=true`.
                suppress_change = {"value": False}

                async def on_search_change(value: str | None) -> None:
                    if suppress_change["value"]:
                        return
                    text = (value or "").strip() or None
                    if text == active["q"]:
                        return
                    active["q"] = text
                    await reload_grid()

                async def on_incomplete_change(event) -> None:
                    if suppress_change["value"]:
                        return
                    active["incomplete"] = bool(event.value)
                    await reload_grid()

                async def reset_filters() -> None:
                    """Clears the filter bar; the tree/category/collection stays."""
                    active.update(incomplete=False, q=None)
                    suppress_change["value"] = True
                    try:
                        incomplete_switch.value = False
                        search_input.value = ""
                    finally:
                        suppress_change["value"] = False
                    await reload_grid()

                with ui.row().classes("items-center gap-2 w-full pb-1").style("flex-wrap: nowrap"):
                    search_input = (
                        ui.input(
                            placeholder=t("grid.filter_search"),
                            value=active["q"] or "",
                        )
                        .props(f"outline dense clearable debounce={SEARCH_DEBOUNCE_MS}")
                        .classes("grow")
                    )
                    search_input.on_value_change(lambda e: on_search_change(e.value))
                    incomplete_switch = ui.switch(
                        t("grid.filter_incomplete"), value=bool(active["incomplete"])
                    ).props("dense shrink-0")
                    incomplete_switch.on_value_change(on_incomplete_change)
                    ui.button(icon="filter_alt_off", on_click=reset_filters).props(
                        "flat dense shrink-0"
                    ).tooltip(t("grid.filter_reset"))

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
                grid.on("cellDoubleClicked", handle_open_cell, ["data", "colId"])
                grid.on("selectionChanged", handle_selection, ["selectedRows"])
