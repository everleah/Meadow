"""Reusable picker dialog: optional server-side search + lazy structure tree.

``open_structure_picker`` is the generic dialog; ``open_object_picker`` is the
object-specific wrapper used wherever an **object** is picked:

* product form — graph-edge relations and scalar ``reference`` attributes,
* grid — the reference dialog (``_refs``) and the bulk wizard (category/parent),
* categories admin — "Move" (new parent),
* Rule Lab and export wizard — multi-select object sets.

``languages.py`` uses the generic dialog for the language tree (the parent of a
language). Selection rules are per node: nodes with ``allowed: false`` are greyed
out and cannot be selected, but stay expandable so allowed descendants remain
reachable. The UI stays a thin view over the core — labels/types come from the
core, hits and children are fetched through the core endpoints (no client-side
joins).
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Iterable

from nicegui import ui

from meadow.frontend.api import CoreClient
from meadow.frontend.display import completeness_badge, resolve_value
from meadow.frontend.i18n import t
from meadow.frontend.lazy_tree import LazyTree

#: Universal root of the composition tree (start node of the structure tab).
OBJECT_ROOT_ID = "object:object_root"

#: Receives the picked hit (single mode) or the list of hits (multi mode).
PickHandler = Callable[[dict], object | Awaitable[object]]
#: Async loader returning the child nodes of a node id.
ChildrenLoader = Callable[[str], Awaitable[list[dict]]]
#: Async search returning the hits for a query.
SearchFn = Callable[[str], Awaitable[list[dict]]]
#: Renders one search hit (button); the second argument is the dialog's pick handler.
HitRenderer = Callable[[dict, Callable[[dict], object]], None]


def render_picker_hit(hit: dict, on_pick: Callable[[dict], object], locale: str) -> None:
    """Renders one picker hit: pick button + target completeness badge.

    The badge comes from the core (``GET /objects/picker?locale=``) and shows how
    complete the target is in the working language — so an incomplete target is
    visible before it is linked. ``percent`` is ``None`` when the core returned no
    completeness (no ``locale`` given).
    """
    percent = (hit.get("completeness") or {}).get(locale)
    with ui.row().classes("items-center gap-2 w-full"):
        ui.button(
            f"{hit.get('label')} · {hit.get('type')}",
            on_click=lambda h=hit: on_pick(h),
        ).props("outline dense").classes("grow justify-start")
        if percent is not None:
            ui.html(completeness_badge(int(percent))).classes("text-xs shrink-0").tooltip(
                t("form.target_completeness")
            )


async def open_structure_picker(
    *,
    title: str,
    on_pick: PickHandler,
    root_id: str,
    load_children: ChildrenLoader,
    multi: bool = False,
    search: SearchFn | None = None,
    render_hit: HitRenderer | None = None,
    hint: str | None = None,
) -> None:
    """Opens the picker dialog (search tab if ``search`` is given + structure tree).

    ``load_children(node_id)`` returns the tree nodes of a node as
    ``{id, label, type?, allowed, selectable, lazy}``. With ``multi=True`` a pick
    **toggles** the selection (chips) and ``on_pick`` receives the **list** of hits
    when the user applies; single mode passes one hit and closes immediately.
    """
    node_hits: dict[str, dict] = {}

    async def load(node_id: str) -> list[dict]:
        """Loads child nodes and remembers them for the selection lookup."""
        nodes = await load_children(node_id)
        for node in nodes:
            key = str(node.get("id") or "")
            node_hits[key] = {
                "id": key,
                "label": str(node.get("label") or key),
                "type": str(node.get("type") or ""),
            }
        return nodes

    with ui.dialog() as dialog, ui.card().classes("w-[36rem] max-w-[92vw]"):
        ui.label(title).classes("text-subtitle1")
        hits: ui.column | None = None
        search_input: ui.input | None = None
        if search is not None:
            with ui.tabs().classes("w-full") as tabs:
                search_tab = ui.tab(t("form.picker_search_tab"))
                tree_tab = ui.tab(t("form.picker_tree_tab"))
            with ui.tab_panels(tabs, value=search_tab).classes("w-full"):
                with ui.tab_panel(search_tab).classes("p-0"):
                    search_input = (
                        ui.input(placeholder=t("form.reference_search"))
                        .props("filled dense")
                        .classes("w-full")
                    )
                    hits = ui.column().classes("w-full gap-1")
                with ui.tab_panel(tree_tab).classes("p-0"):
                    ui.label(hint or t("form.tree_picker_hint")).classes(
                        "text-caption text-gray-500 pb-1"
                    )
                    tree_host = ui.column().classes("w-full h-80 min-h-0 overflow-y-auto")
                    tree_loaded = {"value": False}
        else:
            ui.label(hint or t("form.tree_picker_hint")).classes("text-caption text-gray-500 pb-1")
            tree_host = ui.column().classes("w-full h-80 min-h-0 overflow-y-auto")
            tree_loaded = {"value": False}

        if multi:
            ui.label(t("form.picker_selection")).classes("text-caption text-gray-500 pt-2")
            chosen_box = ui.column().classes("w-full gap-1")
            with chosen_box:
                ui.label(t("form.picker_none_selected")).classes("text-caption text-gray-500")

        chosen: dict[str, dict] = {}

        async def choose(hit: dict) -> None:
            """Handles one pick: closes the dialog or toggles the multi selection."""
            if not multi:
                dialog.close()
                result = on_pick(hit)
                if inspect.isawaitable(result):
                    await result
                return
            node_id = str(hit.get("id") or "")
            if not node_id:
                return
            if node_id in chosen:
                chosen.pop(node_id, None)
            else:
                chosen[node_id] = hit
            render_chosen()

        def render_chosen() -> None:
            chosen_box.clear()
            with chosen_box:
                if not chosen:
                    ui.label(t("form.picker_none_selected")).classes("text-caption text-gray-500")
                    return
                for hit in chosen.values():
                    with ui.row().classes("items-center gap-2 w-full"):
                        ui.label(f"{hit.get('label')} · {hit.get('type')}").classes(
                            "bg-neutral px-2 py-0.5 rounded text-xs"
                        )
                        ui.button(icon="close", on_click=lambda h=hit: choose(h)).props(
                            "flat dense size=sm"
                        ).tooltip(t("grid.ref_remove"))

        if search_input is not None and hits is not None:

            async def do_search(value: str) -> None:
                query = (value or "").strip()
                hits.clear()
                if not query:
                    return
                try:
                    found = await search(query)
                except Exception:  # noqa: BLE001 - degrade to no hits
                    return
                for hit in found:
                    with hits:
                        if render_hit is not None:
                            render_hit(hit, choose)
                        else:
                            ui.button(
                                str(hit.get("label") or ""),
                                on_click=lambda h=hit: choose(h),
                            ).props("outline dense").classes("w-full justify-start")

            search_input.on_value_change(lambda e: do_search(e.value))

        async def ensure_structure() -> None:
            """Loads the top-level node list once (on first open)."""
            if tree_loaded["value"]:
                return
            tree_loaded["value"] = True
            with tree_host:
                ui.label(t("form.loading")).classes("text-caption text-gray-500")
            try:
                roots = await load(root_id)
            except Exception:  # noqa: BLE001 - degrade to an empty tree
                tree_host.clear()
                with tree_host:
                    ui.label(t("grid.tree_load_failed")).classes("text-caption text-negative")
                return
            tree_host.clear()
            with tree_host:
                LazyTree(
                    roots,
                    load_children=load,
                    on_select=on_tree_select,
                ).classes("w-full").add_slot(
                    "default-header",
                    "<span :class=\"props.node.allowed ? '' : 'text-gray-400'\">"
                    "{{ props.node.label }}"
                    '<small v-if="props.node.type" class="text-caption text-gray-500">'
                    " &middot; {{ props.node.type }}</small></span>",
                )

        async def on_tree_select(e) -> None:
            node_id = str(e.value) if getattr(e, "value", None) else ""
            hit = node_hits.get(node_id)
            if hit is None:
                return  # greyed out (not an allowed target)
            await choose(hit)

        async def apply_multi() -> None:
            """Hands the accumulated selection to ``on_pick`` and closes."""
            if not chosen:
                return
            dialog.close()
            result = on_pick(list(chosen.values()))
            if inspect.isawaitable(result):
                await result

        with ui.row().classes("w-full justify-end mt-2 gap-2"):
            ui.button(t("form.cancel"), on_click=dialog.close).props("flat")
            if multi:
                ui.button(t("form.apply"), on_click=apply_multi).props("flat")
    dialog.open()
    # The tree is the second tab (or the only content); load its top level right
    # away so switching tabs is instant.
    await ensure_structure()


async def open_object_picker(
    *,
    client: CoreClient,
    token: str,
    locale: str,
    title: str,
    on_pick: PickHandler,
    target_types: Iterable[str] | None = None,
    exclude_ids: Iterable[str] = (),
    root_id: str = OBJECT_ROOT_ID,
    multi: bool = False,
) -> None:
    """Picks objects: search via ``GET /objects/picker``, tree via ``/objects/{id}/children``.

    ``target_types`` (empty/``None`` = all types allowed) restricts which nodes can
    be selected and is forwarded to the search; ``exclude_ids`` are never
    selectable (e.g. the current object). A hit is ``{id, label, type}`` plus a
    ``completeness`` value for the working language.
    """
    allowed_types = {str(target) for target in (target_types or []) if target}
    excluded = {str(entry) for entry in exclude_ids if entry}

    def node_for(child: dict) -> dict:
        """Maps an object to a tree node (allowed = selectable)."""
        node_id = str(child.get("id"))
        node_type = str(child.get("type"))
        attributes = child.get("attributes") or {}
        allowed = node_id not in excluded and (not allowed_types or node_type in allowed_types)
        return {
            "id": node_id,
            "label": resolve_value(attributes.get("name"), locale) or node_type,
            "type": node_type,
            "allowed": allowed,
            "selectable": allowed,
            "lazy": bool(child.get("hasChildren")),
        }

    async def load_children(node_id: str) -> list[dict]:
        children = await client.list_object_children(token, node_id, limit=100)
        return [node_for(child) for child in children]

    async def search(query: str) -> list[dict]:
        return await client.picker_search(
            token, query, types=sorted(allowed_types) or None, locale=locale, limit=20
        )

    await open_structure_picker(
        title=title,
        on_pick=on_pick,
        root_id=root_id,
        load_children=load_children,
        multi=multi,
        search=search,
        render_hit=lambda hit, cb: render_picker_hit(hit, cb, locale),
    )
