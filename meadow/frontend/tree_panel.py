"""The navigation tree of the products page (structure, categories, collections).

The left panel shows one hierarchy at a time. Depending on the view it is

- the **composition structure** (the root objects and their children, loaded lazily),
- a **taxonomy** (categories or classification classes) — either as the loaded tree, as
  the flat list of server-side search hits, or as the ancestor branch of an opened hit,
- the **saved collections** (a flat list, they have no hierarchy).

The page owns the state (`collections`, `category_roots`, the loaded nodes, the hits, the
ancestor branch, the selection). This module decides what to draw into the host and
returns the rendered tree so the page can keep it for the search filter, the collapse
button and the selection.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from nicegui import ui

from meadow.frontend import taxonomy_tree
from meadow.frontend.display import resolve_value
from meadow.frontend.i18n import t
from meadow.frontend.lazy_tree import LazyTree

#: Loads the children of a tree node (structure or taxonomy).
NodeLoader = Callable[[str], Awaitable[list[dict]]]
#: Selection handler a tree calls with NiceGUI's change event.
SelectHandler = Callable[[Any], Awaitable[None]]


def collection_label(collection: Mapping[str, Any], locale: str) -> str:
    """The label a collection node shows (its name attribute, then name, then id)."""
    return str(
        resolve_value(((collection.get("attributes") or {}).get("name")), locale)
        or collection.get("name")
        or collection.get("id")
        or ""
    )


def collection_node(collection: Mapping[str, Any], locale: str) -> dict[str, Any]:
    """The tree node of one saved collection (id, label, member count)."""
    return {
        "id": str(collection.get("id")),
        "label": collection_label(collection, locale),
        "type": "collection",
        "count": int(collection.get("member_count") or 0),
    }


@dataclass(frozen=True)
class TreeState:
    """What the panel needs for one render (read fresh so it never holds stale data)."""

    #: Saved collections (the collection view lists them).
    collections: list[dict]
    #: Root nodes of the shown taxonomy (loaded page).
    category_roots: list[dict]
    #: Whether the taxonomy has more roots than the loaded page.
    roots_have_more: bool
    #: The "uncategorized" node appended to a category tree.
    uncategorized: dict
    #: The currently selected node id (a taxonomy selection narrows the roots).
    selected_id: str | None
    #: Root nodes of the structure tree.
    structure_nodes: list[dict]
    #: Server-side search hits of the shown taxonomy (replaces the tree).
    hits: list[dict]
    #: The query the current hits were loaded for.
    hits_query: str
    #: The ancestor branch of a hit the user opened in the tree.
    path: list[dict]


@dataclass(frozen=True)
class RenderedTree:
    """What the page keeps from a render: the tree widget and its top-level nodes."""

    tree: LazyTree | None
    nodes: list[dict]


class TreePanel:
    """Draws the tree of the active view into the panel's host column."""

    def __init__(
        self,
        *,
        host: ui.column,
        locale: str,
        state: Callable[[], TreeState],
        load_children: NodeLoader,
        load_category_children: NodeLoader,
        on_tree_select: SelectHandler,
        on_category_select: SelectHandler,
        on_collection_select: SelectHandler,
        select_category: Callable[[str], Awaitable[None]],
        show_in_tree: Callable[[str], Awaitable[None]],
    ) -> None:
        self.host = host
        self.locale = locale
        self.state = state
        self.load_children = load_children
        self.load_category_children = load_category_children
        self.on_tree_select = on_tree_select
        self.on_category_select = on_category_select
        self.on_collection_select = on_collection_select
        self.select_category = select_category
        self.show_in_tree = show_in_tree

    def render(self, view: str) -> RenderedTree:
        """Renders the tree of `view` into the host and returns it for the page."""
        state = self.state()
        self.host.clear()
        # A tree is only built when the panel shows the hierarchy; the flat
        # search hit list is rendered as rows (each hit can be selected or
        # opened in the tree).
        tree: LazyTree | None = None
        hit_list = bool(state.hits) and not state.path
        # The child loader follows the shown view; annotated once so every branch
        # can assign its own loader.
        loader: NodeLoader
        if view == "collection" and state.collections:

            async def _no_children(_node_id: str) -> list[dict]:
                return []

            nodes = [collection_node(collection, self.locale) for collection in state.collections]
            loader = _no_children
            on_select = self.on_collection_select
        elif view == "category" and (state.category_roots or state.hits or state.path):
            if state.path:
                # Ancestor branch of the hit the user opened in the tree.
                nodes = state.path
            elif hit_list:
                # Server-side search hits: flat, no hierarchy to load.
                nodes = state.hits
            else:
                selected_id = state.selected_id
                roots = state.category_roots
                if selected_id:
                    filtered = [root for root in state.category_roots if root["id"] == selected_id]
                    if filtered:
                        roots = filtered
                nodes = taxonomy_tree.tree_nodes(
                    roots,
                    more_label=(
                        t("grid.taxonomy_more")
                        if state.roots_have_more and not selected_id
                        else None
                    ),
                    uncategorized=state.uncategorized,
                )
            loader = self.load_category_children
            on_select = self.on_category_select
        elif state.structure_nodes:
            nodes = state.structure_nodes
            loader = self.load_children
            on_select = self.on_tree_select
        else:
            with self.host:
                ui.label(t("grid.tree_load_failed")).classes("text-caption text-negative")
            return RenderedTree(tree=None, nodes=[])
        with self.host:
            chain = taxonomy_tree.path_chain(state.path) if state.path else []
            if chain:
                ui.label(t("grid.taxonomy_path").format(label=chain[-1]["label"])).classes(
                    "text-caption text-orange-600"
                )
            elif hit_list:
                ui.label(
                    t("grid.taxonomy_results").format(count=len(state.hits), query=state.hits_query)
                ).classes("text-caption text-orange-600")
            if hit_list:
                with ui.column().classes("w-full gap-0"):
                    for hit in state.hits:
                        with (
                            ui.row()
                            .classes("items-center gap-1 w-full py-0")
                            .style("flex-wrap: nowrap")
                        ):
                            ui.button(
                                str(hit["label"]),
                                on_click=lambda n=hit: self.select_category(str(n["id"])),
                            ).props("flat dense no-caps align=left").classes(
                                "grow justify-start text-left"
                            )
                            ui.button(
                                icon="account_tree",
                                on_click=lambda n=hit: self.show_in_tree(str(n["id"])),
                            ).props("flat dense").tooltip(t("grid.taxonomy_in_tree"))
            else:
                tree = (
                    LazyTree(
                        nodes,
                        on_select=on_select,
                        load_children=loader,
                        virtual_scroll=True,
                    )
                    .classes("w-full")
                    .mark("structure-tree")
                )
                tree.add_slot(
                    "default-header",
                    '<span :props="props">{{ props.node.label }}'
                    '<small v-if="props.node.type" class="text-caption text-gray-500">'
                    " &middot; {{ props.node.type }}</small>"
                    '<small v-if="props.node.count !== undefined" '
                    'class="text-caption text-gray-500">'
                    " &middot; {{ props.node.count }}</small></span>",
                )
                if state.path:
                    tree.set_path(nodes, [str(node["id"]) for node in chain[:-1]])
        return RenderedTree(tree=tree, nodes=nodes)
