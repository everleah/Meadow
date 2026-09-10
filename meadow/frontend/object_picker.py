"""Reusable object picker: server-side label search + lazy structure tree.

Both pickers of the product form (scalar `reference` attributes and graph-edge
relations) and the grid's reference dialog use this dialog, so object selection
behaves the same everywhere:

* **Search** — server-side full-text search via ``GET /objects/picker``
  (optionally restricted to allowed target types); each hit shows the target's
  completeness badge for the working language.
* **Structure** — a lazily loaded composition tree (``GET /objects/{id}/children``)
  starting at the universal root. Nodes whose type is not an allowed target type
  (and ids in ``exclude_ids``, e.g. the object being edited) are **greyed out and
  not selectable**, but stay expandable so allowed descendants remain reachable.

The UI stays a thin view over the graph: labels/types come from the core, hits
and children are fetched through the core endpoints (no client-side joins).
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

PickHandler = Callable[[dict], object | Awaitable[object]]


def render_picker_hit(hit: dict, on_pick: PickHandler, locale: str) -> None:
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
    """Opens the Search/Structure picker dialog and calls ``on_pick`` with a hit.

    ``target_types`` (empty/``None`` = all types allowed) restricts which nodes can
    be selected and is forwarded to the search; ``exclude_ids`` are never
    selectable (e.g. the current object). A hit is ``{id, label, type}``.

    With ``multi=True`` a pick **toggles** the selection instead of closing the
    dialog: picks accumulate as chips and ``on_pick`` receives the **list** of hits
    when the user applies. Single mode passes one hit and closes immediately.
    """
    allowed_types = {str(t) for t in (target_types or []) if t}
    excluded = {str(i) for i in exclude_ids if i}
    type_list = sorted(allowed_types) or None

    def is_allowed(node_type: str, node_id: str) -> bool:
        if node_id in excluded:
            return False
        return not allowed_types or node_type in allowed_types

    with ui.dialog() as dialog, ui.card().classes("w-[36rem] max-w-[92vw]"):
        ui.label(title).classes("text-subtitle1")
        with ui.tabs().classes("w-full") as tabs:
            search_tab = ui.tab(t("form.picker_search_tab"))
            tree_tab = ui.tab(t("form.picker_tree_tab"))
        with ui.tab_panels(tabs, value=search_tab).classes("w-full"):
            with ui.tab_panel(search_tab).classes("p-0"):
                search = (
                    ui.input(placeholder=t("form.reference_search"))
                    .props("filled dense")
                    .classes("w-full")
                )
                hits = ui.column().classes("w-full gap-1")
            with ui.tab_panel(tree_tab).classes("p-0"):
                ui.label(t("form.tree_picker_hint")).classes("text-caption text-gray-500 pb-1")
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

        async def do_search(value: str) -> None:
            query = (value or "").strip()
            hits.clear()
            if not query:
                return
            try:
                found = await client.picker_search(
                    token, query, types=type_list, locale=locale, limit=20
                )
            except Exception:  # noqa: BLE001 - degrade to no hits
                return
            for hit in found:
                with hits:
                    render_picker_hit(hit, choose, locale)

        search.on_value_change(lambda e: do_search(e.value))

        node_hits: dict[str, dict] = {}

        def picker_node(child: dict) -> dict:
            """Maps an object to a structure-tree node (allowed = selectable)."""
            node_id = str(child.get("id"))
            node_type = str(child.get("type"))
            attributes = child.get("attributes") or {}
            allowed = is_allowed(node_type, node_id)
            node_hits[node_id] = {
                "id": node_id,
                "label": resolve_value(attributes.get("name"), locale) or node_type,
                "type": node_type,
            }
            return {
                "id": node_id,
                "label": node_hits[node_id]["label"],
                "type": node_type,
                "allowed": allowed,
                "selectable": allowed,
                "lazy": bool(child.get("hasChildren")),
            }

        async def load_children(node_id: str) -> list[dict]:
            children = await client.list_object_children(token, node_id, limit=100)
            return [picker_node(child) for child in children]

        async def ensure_structure() -> None:
            """Loads the top-level structure nodes once (on first open)."""
            if tree_loaded["value"]:
                return
            tree_loaded["value"] = True
            with tree_host:
                ui.label(t("form.loading")).classes("text-caption text-gray-500")
            try:
                roots = await load_children(root_id)
            except Exception:  # noqa: BLE001 - degrade to an empty tree
                tree_host.clear()
                with tree_host:
                    ui.label(t("grid.tree_load_failed")).classes("text-caption text-negative")
                return
            tree_host.clear()
            with tree_host:
                LazyTree(
                    roots,
                    load_children=load_children,
                    on_select=on_tree_select,
                ).classes("w-full").add_slot(
                    "default-header",
                    "<span :class=\"props.node.allowed ? '' : 'text-gray-400'\">"
                    "{{ props.node.label }}"
                    '<small class="text-caption text-gray-500">'
                    " &middot; {{ props.node.type }}</small></span>",
                )

        async def on_tree_select(e) -> None:
            node_id = str(e.value) if getattr(e, "value", None) else ""
            hit = node_hits.get(node_id)
            if hit is None:
                return  # greyed out (not an allowed target type)
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
    # The tree lives in the second tab; load its top level right away so switching
    # tabs is instant (the search tab is the default view).
    await ensure_structure()
