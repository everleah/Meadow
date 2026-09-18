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
import logging
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from nicegui import ui

from meadow.frontend.api import CoreClient
from meadow.frontend.display import (
    completeness_badge,
    resolve_value,
    taxonomy_options,
    taxonomy_type_names,
)
from meadow.frontend.i18n import t
from meadow.frontend.lazy_tree import LazyTree
from meadow.frontend.relation_roles import taxonomy_choices
from meadow.frontend.taxonomy_tree import MORE_ID, PAGE_SIZE, append_page, has_more, tree_nodes

log = logging.getLogger(__name__)

#: Universal root of the composition tree (start node of the structure tab).
OBJECT_ROOT_ID = "object:object_root"

#: Tree modes of the picker (see [`picker_tree_mode`]).
TREE_TAXONOMY = "taxonomy"
TREE_OBJECTS = "objects"
TREE_COMPOSITION = "composition"

#: Receives the picked hit (single mode) or the list of hits (multi mode). The
#: payload shape depends on the mode, so the parameter stays untyped.
PickHandler = Callable[[Any], object | Awaitable[object]]
#: Async loader returning the child nodes of a node id.
ChildrenLoader = Callable[[str], Awaitable[list[dict]]]
#: Async search returning the hits for a query.
SearchFn = Callable[[str], Awaitable[list[dict]]]
#: Renders one search hit (button); the second argument is the dialog's pick handler.
HitRenderer = Callable[[dict, Callable[[dict], object]], None]


async def _no_page(_offset: int) -> list[dict]:
    """`paged` stand-in for the hierarchy mode, where no flat loader is given.

    `load_flat` only runs when `paged` was passed, but a nested function cannot see
    that narrowing - a concrete callable keeps the type honest without an assert.
    """
    return []


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
    taxonomy: tuple[dict[str, str], dict] | None = None,
    switcher_label: str | None = None,
    paged: Callable[[int], Awaitable[list[dict]]] | None = None,
) -> None:
    """Opens the picker dialog (search tab if ``search`` is given + structure tree).

    ``load_children(node_id)`` returns the tree nodes of a node as
    ``{id, label, type?, allowed, selectable, lazy}``. With ``multi=True`` a pick
    **toggles** the selection (chips) and ``on_pick`` receives the **list** of hits
    when the user applies; single mode passes one hit and closes immediately.

    ``taxonomy`` adds a **switcher** above the tree for a target set that accepts
    several taxonomy node types: ``(options, state)`` — ``options`` is
    ``{type: label}`` and ``state`` carries the chosen type under ``key``, which the
    caller's ``load_children`` reads. The tree is reloaded on every switch, because
    each taxonomy is its own tree.

    ``paged`` replaces the hierarchy with a **flat, paged list**: ``paged(offset)``
    returns one page of top-level nodes and the panel shows a trailing *"load more"*
    node (`taxonomy_tree.MORE_ID`) while a full page came back — selecting it appends
    the next page. Used for target sets whose objects are **not reachable in the
    composition tree** (a relation on ``product`` finds its targets there, while the
    composition tree above the products shows vehicles and every product row is
    greyed out, 2026-09-17). ``taxonomy`` may be combined with it: the switcher then
    chooses the *type* of the flat list, labelled by ``switcher_label``.
    """
    node_hits: dict[str, dict] = {}
    #: Loaded nodes of the hierarchy mode (the tree renders them directly).
    loaded_nodes: list[dict] = []
    #: Loaded nodes of the flat mode (appended page by page).
    flat_nodes: list[dict] = []
    flat_more = {"value": False}

    def remember(nodes: list[dict]) -> None:
        """Remembers nodes so a selection can be turned into a hit."""
        for node in nodes:
            key = str(node.get("id") or "")
            node_hits[key] = {
                "id": key,
                "label": str(node.get("label") or key),
                "type": str(node.get("type") or ""),
            }

    async def load(node_id: str) -> list[dict]:
        """Loads child nodes and remembers them for the selection lookup."""
        nodes = await load_children(node_id)
        remember(nodes)
        return nodes

    # A flat target list is a list, not a hierarchy — the panel's wording follows
    # what the second tab actually shows.
    flat = paged is not None
    # Bound once: the nested loaders below cannot see the `is not None` narrowing.
    page_loader: Callable[[int], Awaitable[list[dict]]] = paged or _no_page
    default_hint = t("form.picker_list_hint") if flat else t("form.tree_picker_hint")

    with ui.dialog() as dialog, ui.card().classes("w-[36rem] max-w-[92vw]"):
        ui.label(title).classes("text-subtitle1")
        # A target set that accepts several types needs one choice first: which tree
        # (a generic dimension) or which object type (a flat target list). The list is
        # reloaded on every switch, because each type is its own set of nodes.
        taxonomy_select: ui.select | None = None
        if taxonomy is not None:
            taxonomy_select = (
                ui.select(
                    taxonomy[0],
                    value=taxonomy[1].get("key"),
                    label=switcher_label or t("categories.taxonomy"),
                )
                .props("filled dense")
                .classes("w-full pb-1")
            )
        hits: ui.column | None = None
        search_input: ui.input | None = None
        if search is not None:
            with ui.tabs().classes("w-full") as tabs:
                search_tab = ui.tab(t("form.picker_search_tab"))
                tree_tab = ui.tab(t("form.picker_list_tab") if flat else t("form.picker_tree_tab"))
            with ui.tab_panels(tabs, value=search_tab).classes("w-full"):
                with ui.tab_panel(search_tab).classes("p-0"):
                    # The core scans the chain languages for a partial label word, so
                    # the field is debounced (measured ~75 ms per search, ~0.25 s worst
                    # case) instead of firing on every keystroke.
                    search_input = (
                        ui.input(placeholder=t("form.reference_search"))
                        .props("filled dense debounce=400")
                        .classes("w-full")
                    )
                    hits = ui.column().classes("w-full gap-1")
                with ui.tab_panel(tree_tab).classes("p-0"):
                    ui.label(hint or default_hint).classes("text-caption text-gray-500 pb-1")
                    tree_host = ui.column().classes("w-full h-80 min-h-0 overflow-y-auto")
                    tree_loaded = {"value": False}
        else:
            ui.label(hint or default_hint).classes("text-caption text-gray-500 pb-1")
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

        if search is not None and search_input is not None and hits is not None:
            # Bound before the nested handler: the `is not None` narrowing does not
            # survive into a nested function, and NiceGUI calls it later.
            searcher = search

            async def do_search(value: str) -> None:
                query = (value or "").strip()
                hits.clear()
                if not query:
                    return
                try:
                    found = await searcher(query)
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

        async def render_tree() -> None:
            """Renders the tree host from the nodes loaded so far.

            A flat target list appends page by page, so the host is re-rendered
            (instead of only filled once) and carries a trailing "load more" node
            while a full page came back.
            """
            tree_host.clear()
            with tree_host:
                if flat:
                    nodes = tree_nodes(
                        flat_nodes,
                        more_label=t("form.picker_more") if flat_more["value"] else None,
                    )
                    # Every node of the flat list is pickable `node_for` says so; the
                    # trailing "load more" node is too (it loads the next page). The
                    # header slot greys out every node without `allowed`.
                    for node in nodes:
                        node.setdefault("allowed", True)
                    if not nodes:
                        ui.label(t("form.picker_empty")).classes("text-caption text-gray-500")
                        return
                else:
                    nodes = loaded_nodes
                LazyTree(
                    nodes,
                    load_children=load,
                    on_select=on_tree_select,
                ).classes("w-full").add_slot(
                    "default-header",
                    "<span :class=\"props.node.allowed ? '' : 'text-gray-400'\">"
                    "{{ props.node.label }}"
                    '<small v-if="props.node.type" class="text-caption text-gray-500">'
                    " &middot; {{ props.node.type }}</small></span>",
                )

        async def load_flat(next_page: bool) -> None:
            """Loads the first or the next page of the flat target list."""
            offset = 0 if not flat_nodes or not next_page else len(flat_nodes)
            page = await page_loader(offset)
            flat_nodes[:] = page if not next_page else append_page(flat_nodes, page)
            remember(page)
            flat_more["value"] = has_more(len(flat_nodes))

        async def ensure_structure() -> None:
            """Loads the top-level node list once (on first open or after a switch)."""
            if tree_loaded["value"]:
                return
            tree_loaded["value"] = True
            with tree_host:
                ui.label(t("form.loading")).classes("text-caption text-gray-500")
            try:
                if flat:
                    flat_nodes.clear()
                    await load_flat(next_page=False)
                else:
                    loaded_nodes[:] = await load(root_id)
            except Exception:  # noqa: BLE001 - degrade to an empty tree
                # Never silently: a picker that cannot load its tree is a broken
                # dialog, and swallowing the reason makes it undiagnosable.
                log.exception("picker %r: could not load the structure tree", title)
                tree_host.clear()
                with tree_host:
                    ui.label(t("grid.tree_load_failed")).classes("text-caption text-negative")
                return
            await render_tree()

        async def on_tree_select(e) -> None:
            node_id = str(e.value) if getattr(e, "value", None) else ""
            if node_id == MORE_ID:
                # The trailing node of the flat list: append the next page.
                await load_flat(next_page=True)
                await render_tree()
                return
            hit = node_hits.get(node_id)
            if hit is None:
                return  # greyed out (not an allowed target)
            await choose(hit)

        # Both are set together by the caller, so the state is read only when the
        # switcher exists - the guard also hands the type checker the tuple.
        if taxonomy_select is not None and taxonomy is not None:
            taxonomy_state = taxonomy[1]

            async def on_taxonomy_change(event) -> None:
                """Switches the shown taxonomy and reloads its tree.

                The parameter must stay **required**: NiceGUI passes the event only
                when the handler expects an argument without a default
                (`helpers.expects_arguments`), so `event=None` would be called with
                nothing and every switch would be ignored.
                """
                key = str(getattr(event, "value", None) or "")
                if not key or key == taxonomy_state.get("key"):
                    return
                taxonomy_state["key"] = key
                tree_loaded["value"] = False
                node_hits.clear()
                tree_host.clear()
                if hits is not None:
                    hits.clear()
                await ensure_structure()

            taxonomy_select.on_value_change(on_taxonomy_change)

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


def composition_reachable(
    allowed_types: Iterable[str] | None,
    graph: dict | None,
    taxonomy_types: Iterable[str] | None = None,
) -> bool:
    """Whether the allowed target types are reachable in the composition tree.

    ``graph`` is `GET /objects/composition-types` (`{root_type, edges}`). A type is
    reachable when a chain of composition parents leads up to the **universal root**
    — and every hop has to be visible in the picker's tree: the composition scope
    renders the product structure **without** the taxonomy nodes, so a type below a
    taxonomy node is unreachable even though it has a parent. A product (below its
    part group) therefore cannot be browsed, a vehicle generation (below model,
    below make) can (2026-09-17).

    ``True`` when the graph is unknown (the lookup failed): the status quo — the
    composition tree — is then kept instead of silently switching the picker to a
    different panel.
    """
    allowed = [str(name) for name in (allowed_types or []) if name]
    if not allowed or not isinstance(graph, dict):
        return True
    root_type = str(graph.get("root_type") or "")
    parent_types: dict[str, set[str]] = {}
    for edge in graph.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        child = str(edge.get("child") or "")
        parent = str(edge.get("parent") or "")
        if child and parent:
            parent_types.setdefault(child, set()).add(parent)
    hidden = {str(name) for name in (taxonomy_types or []) if name}
    if not root_type or not parent_types:
        return True

    for name in allowed:
        pending = [name]
        seen: set[str] = set()
        reachable = False
        while pending and not reachable:
            current = pending.pop()
            for parent in parent_types.get(current, ()):
                if parent == root_type:
                    reachable = True
                    break
                # A hidden hop cannot be walked through (the tree does not render it).
                if parent in seen or parent in hidden:
                    continue
                seen.add(parent)
                pending.append(parent)
        if not reachable:
            return False
    return True


def picker_tree_mode(
    allowed_types: Iterable[str] | None,
    taxonomy_types: Iterable[str] | None,
    *,
    reachable: bool = True,
) -> str:
    """Which panel the object picker shows for these allowed target types.

    * ``TREE_TAXONOMY`` — every allowed type is a taxonomy node type: the dialog
      walks **one** taxonomy tree, the switcher chooses which one when the dimension
      is generic.
    * ``TREE_COMPOSITION`` — the target set is unrestricted, or its types are
      reachable in the composition tree (``reachable``, the result of
      [`composition_reachable`]): the tree is then the right way to browse
      (vehicle make → model → generation).
    * ``TREE_OBJECTS`` — a restricted target set the composition tree cannot reach
      (a relation on `product`: the products hang below a part group, which the
      composition scope hides, so the tree would show only greyed-out vehicles): the
      allowed objects are listed **flat and paged** instead (2026-09-17).
    """
    if taxonomy_choices(allowed_types, taxonomy_types):
        return TREE_TAXONOMY
    allowed = {str(name) for name in (allowed_types or []) if name}
    if not allowed:
        return TREE_COMPOSITION
    return TREE_COMPOSITION if reachable else TREE_OBJECTS


def object_type_options(
    object_types: list[dict], allowed_types: Iterable[str], locale: str
) -> dict[str, str]:
    """Switcher options `type name -> label` for the allowed **object** types.

    The labels come from the type catalogue (`GET /object-types`), so the switcher of
    a flat target list reads *Product* instead of the raw `product`.
    """
    labels = {
        str(entry.get("name")): resolve_value(entry.get("label"), locale) or str(entry.get("name"))
        for entry in object_types
        if isinstance(entry, dict) and entry.get("name")
    }
    return {name: labels.get(name, name) for name in sorted(str(t) for t in allowed_types if t)}


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
    taxonomy: str | None = None,
) -> None:
    """Picks objects: search via ``GET /objects/picker``, tree via the tree mode.

    ``target_types`` (empty/``None`` = all types allowed) restricts which nodes can
    be selected **and** which hits the search tab returns; ``exclude_ids`` are never
    selectable (e.g. the current object) and are filtered out of the search hits too.
    A hit is ``{id, label, type}`` plus a ``completeness`` value for the working
    language.

    **The tree follows the target set** ([`picker_tree_mode`]): a taxonomy target
    walks **one** taxonomy tree (``?scope=taxonomy&type=<one type>`` — all taxonomy
    trees hang under the same universal root, so without the type filter the dialog
    showed the first page of *that* list: with an installed classification standard,
    100 greyed-out ETIM classes in every dimension, 2026-09-17); a restricted
    **non**-taxonomy target lists its objects **flat and paged**; an unrestricted
    target walks the composition tree. A target set that accepts several types gets a
    switcher: the taxonomy trees for a generic dimension (``taxonomy`` preselects it,
    e.g. the type the object already carries) or the object types of the flat list.
    """
    allowed_types = {str(target) for target in (target_types or []) if target}
    excluded = {str(entry) for entry in exclude_ids if entry}
    try:
        object_types = await client.list_object_types(token, limit=1000)
    except Exception:  # noqa: BLE001 - degrade to "no taxonomy"
        object_types = []
    taxonomy_types = taxonomy_type_names(object_types)
    # The taxonomy trees a target set may point at: one entry = that tree, several =
    # a generic dimension (the user chooses), none = composition scope.
    choices = taxonomy_choices(allowed_types, taxonomy_types)
    # Is the target set reachable in the composition tree? Only asked when the answer
    # can change the panel (a restricted, non-taxonomy target set) — the call is
    # cached in the core, a failure keeps the composition tree (status quo).
    graph: dict | None = None
    if allowed_types and not choices:
        try:
            graph = await client.fetch_composition_types(token)
        except Exception:  # noqa: BLE001 - degrade to the composition tree
            log.exception("picker %r: could not load the composition type graph", title)
    reachable = composition_reachable(allowed_types, graph, taxonomy_types)
    mode = picker_tree_mode(allowed_types, taxonomy_types, reachable=reachable)
    if mode == TREE_OBJECTS:
        switcher: dict[str, str] = object_type_options(object_types, allowed_types, locale)
    else:
        # Only the trees the target set actually accepts: `taxonomy_options` lists
        # *every* taxonomy type, and picking one the dimension rejects would fail in
        # the core (`check_target_types`).
        labels = taxonomy_options(object_types, locale)
        switcher = {name: labels.get(name, name) for name in choices}
    # The switcher's choice drives the tree **and** the search: only the shown type
    # may come back as a hit.
    switch_state: dict = {
        "key": taxonomy
        if taxonomy in switcher
        else (next(iter(switcher), None) if switcher else None)
    }

    def node_for(child: dict) -> dict:
        """Maps an object to a tree node (allowed = selectable)."""
        node_id = str(child.get("id"))
        node_type = str(child.get("type"))
        attributes = child.get("attributes") or {}
        allowed = node_id not in excluded and (not allowed_types or node_type in allowed_types)
        # A taxonomy node (category, ETIM class) carries a `code` instead of a
        # `name`; without the fallback thousands of classes would be labelled with
        # their object type.
        code = attributes.get("code")
        return {
            "id": node_id,
            "label": resolve_value(attributes.get("name"), locale)
            or (str(code) if code else "")
            or node_type,
            "type": node_type,
            "allowed": allowed,
            "selectable": allowed,
            "lazy": bool(child.get("hasChildren")),
        }

    async def load_children(node_id: str) -> list[dict]:
        # A taxonomy target (picking a category, an assembly group or a class) walks
        # the taxonomy tree of **one** type; everything else picks from the
        # composition. Both halves are needed: without the scope the panel shows the
        # products *and* the trees, and without the type filter it shows the first
        # page of the universal root's taxonomy children — a mix that, with an
        # installed standard, is 100 ETIM classes in every dimension (2026-09-17).
        children = await client.list_object_children(
            token,
            node_id,
            limit=100,
            scope="taxonomy" if mode == TREE_TAXONOMY else "composition",
            object_type=switch_state["key"] if mode == TREE_TAXONOMY else None,
        )
        return [node_for(child) for child in children]

    async def load_page(offset: int) -> list[dict]:
        """One page of the flat target list: the objects of the switcher's type.

        The objects of a restricted target set are **not** reachable in the
        composition tree (a product hangs below its part group, and the composition
        tree shows the vehicles above it) — hence the flat list. Paged with the
        shared page size so the "load more" node matches the page arithmetic
        (`taxonomy_tree.has_more`).
        """
        page = await client.list_objects(
            token,
            object_type=switch_state["key"] or None,
            limit=PAGE_SIZE,
            start=offset,
        )
        return [node_for(child) for child in page]

    async def search(query: str) -> list[dict]:
        """Search hits the caller may actually pick.

        The core filters by the requested types, but a hit of an allowed type can
        still be the excluded object itself (e.g. the object being edited) — such
        hits are dropped so the search tab offers the same set as the tree.
        """
        # With a switcher the search follows the shown type; otherwise it covers the
        # whole allowed set (a bound target, or none at all).
        search_types = [switch_state["key"]] if switch_state["key"] else None
        found = await client.picker_search(
            token,
            query,
            types=search_types or sorted(allowed_types) or None,
            locale=locale,
            limit=20,
            # A partial `code` must find the target too (`EC0123`): the core's
            # full-text index matches whole tokens only. A partial *label* word is
            # covered by the wrapper's default (`label_prefix`).
            code_prefix=True,
        )
        return [
            hit
            for hit in found
            if str(hit.get("id") or "") not in excluded
            and (not allowed_types or str(hit.get("type") or "") in allowed_types)
        ]

    await open_structure_picker(
        title=title,
        on_pick=on_pick,
        root_id=root_id,
        load_children=load_children,
        multi=multi,
        search=search,
        render_hit=lambda hit, cb: render_picker_hit(hit, cb, locale),
        taxonomy=(switcher, switch_state) if len(switcher) > 1 else None,
        switcher_label=t("form.picker_object_type") if mode == TREE_OBJECTS else None,
        paged=load_page if mode == TREE_OBJECTS else None,
    )
