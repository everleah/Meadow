"""Lazy-loading QTree for the structure panel.

Quasar's QTree supports loading children on demand via the ``lazy: true`` node
flag and the ``@lazy-load`` event, which hand the child-list-populating
``done(children)`` callback to the client only. NiceGUI does not forward that
callback to the server, so this element wires the event through a client-side
``js_handler`` that captures ``done`` and emits the node key to the server; the
server then fetches the children and invokes ``done`` via ``run_javascript``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Self

from nicegui.elements.tree import Tree
from nicegui.events import GenericEventArguments

_JS_HANDLER = """(...args) => {
    const d = args[0];
    window.__meadowLazy = window.__meadowLazy || {};
    window.__meadowLazy[d.node.id] = d.done;
    emit({ nodeKey: d.node.id });
}"""


class LazyTree(Tree):
    """A QTree that loads child nodes on demand.

    :param nodes: top-level node objects; nodes with children should be marked
        ``lazy: True`` and carry no ``children`` key so QTree emits
        ``@lazy-load`` when they are expanded.
    :param load_children: async callable ``(node_id: str) -> list[dict]`` that
        returns the child node objects (``id``/``label``/``type``, ``lazy`` for
        nodes with children).
    :param virtual_scroll: render only visible rows (needs a bounded height).

    NiceGUI re-sends the whole ``nodes`` prop on *every* prop change (including
    ``selected``). Re-sending a pure lazy node list would discard the client-side
    lazy children and collapse the tree. ``update()`` is therefore a no-op: the
    node list lives client-side and is only replaced explicitly via
    ``set_nodes()`` (used by the tree's search filter).
    """

    def __init__(
        self,
        nodes: list[dict],
        *,
        load_children: Callable[[str], Any],
        virtual_scroll: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(nodes, **kwargs)
        self._load_children = load_children
        if virtual_scroll:
            self._props["virtual-scroll"] = True
        self.on("lazy-load", self._handle_lazy_load, js_handler=_JS_HANDLER)

    def update(self) -> None:
        """Do not re-send the node tree on prop changes (prevents collapse)."""

    def set_nodes(self, nodes: list[dict]) -> None:
        """Replace the node list on the client for the tree's search filter.

        ``update()`` is a no-op to keep the lazy children across selection
        changes, so an explicit re-send is performed here.
        """
        self._props["nodes"] = nodes
        super().update()

    def set_path(self, nodes: list[dict], expanded: list[str]) -> None:
        """Show a nested node list with the given nodes expanded.

        Used by the "show a search hit in the tree" path: the nodes are the
        hit's ancestor branch (nested, see `taxonomy_tree.path_nodes`) and must
        be visible without clicking through the hierarchy, so the path nodes are
        passed through the ``expanded`` prop as well — the same explicit re-send
        as in [`set_nodes`], because `update()` is a no-op here.
        """
        self._props["nodes"] = nodes
        self._props["expanded"] = list(expanded)
        super().update()

    def collapse(self, node_keys: list[str] | None = None) -> Self:
        """Collapse the tree on the client without re-sending the node list.

        The base ``Tree.collapse`` only mutates the ``expanded`` prop; because
        ``update()`` is a no-op for lazy trees, calling it would never push the
        change. Re-sending the whole node list (via ``update()``) would discard
        the client-side lazy children, so we invoke Quasar's ``collapseAll``
        directly and let the ``update:expanded`` event sync the server prop.

        There is deliberately no ``expand`` override: Quasar's ``expandAll``
        only iterates nodes that already carry a ``children`` key, so it would
        silently skip every lazy (not-yet-loaded) node. Expanding a lazy tree
        therefore happens on demand per node, which is the intended behaviour.
        """
        self.run_method("collapseAll")
        return self

    async def _handle_lazy_load(self, e: GenericEventArguments) -> None:
        try:
            node_key = e.args["nodeKey"]
            children = await self._load_children(node_key)
        except Exception:  # noqa: BLE001 - dispatches into an injected loader, no narrow surface
            node_key = e.args.get("nodeKey") if isinstance(e.args, dict) else None
            children = []
        if not node_key:
            return
        # Quasar's `done` callback was captured on the client; feed it the children.
        self.client.run_javascript(
            f"window.__meadowLazy && window.__meadowLazy[{json.dumps(node_key)}]"
            f" && window.__meadowLazy[{json.dumps(node_key)}]({json.dumps(children)})"
        )
