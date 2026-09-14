"""Paging and server search for the taxonomy tree panel.

A taxonomy can carry **thousands** of nodes (the shipped ETIM 10.0 bundle: 5640
classes), so the tree panel must not load or filter all of them:

* **Paging** — the roots of the shown taxonomy are loaded page by page. The tree
  carries a trailing *"more"* node while nodes are unloaded; selecting it appends
  the next page. Nothing is truncated silently.
* **Server search** — while a filter query is typed, the hits come from the core
  (`GET /objects/picker`, full text over the object's attributes) rather than
  from filtering the nodes that happen to be loaded. That is what makes a class
  findable by its code (`EC012373`) or its description without paging through 28
  pages.

The module is **pure** (no NiceGUI, no HTTP): it builds node dicts, merges pages
and decides which of the two paths a query takes. `grid.py` (category view of
`/products`) and `categories.py` render them, both with the same behaviour.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

#: Identifier of the trailing "load more" node (never an object id).
MORE_ID = "__more__"

#: Roots loaded per page. Small enough to render instantly, large enough that a
#: plain category tree (a handful of nodes) is complete after one request.
PAGE_SIZE = 200


def has_more(loaded: int, page_size: int = PAGE_SIZE) -> bool:
    """Whether another page may follow, given the number of loaded nodes.

    A full page means "maybe": the caller learns the truth when the next page
    comes back short (and drops the marker then). A short page is the end.
    """
    return loaded > 0 and loaded % page_size == 0


def more_node(label: str, remaining: int) -> dict:
    """The trailing node that loads the next page.

    ``label`` is already translated by the caller (the panel owns the wording);
    ``remaining`` is the number of nodes not loaded yet — or ``-1`` when the total
    is unknown (a full page, so at least one more node exists).
    """
    return {
        "id": MORE_ID,
        "label": label,
        "type": "",
        "more": True,
        "remaining": remaining,
    }


def append_page(loaded: Iterable[dict], page: Iterable[dict]) -> list[dict]:
    """Appends a page to the loaded roots, skipping nodes that are already there.

    The core pages by ``ORDER BY in`` (a stable id order), so a node cannot move
    between pages while a panel is open; the guard is about duplicates from a
    re-fetch, which would render twice.
    """
    merged = list(loaded)
    known = {str(node.get("id")) for node in merged}
    for node in page:
        node_id = str(node.get("id"))
        if node_id in known:
            continue
        known.add(node_id)
        merged.append(node)
    return merged


def tree_nodes(
    roots: Iterable[dict],
    *,
    more_label: str | None = None,
    remaining: int = -1,
    uncategorized: Mapping | None = None,
) -> list[dict]:
    """The node list of the taxonomy tree: optional "not categorized" first, the
    loaded roots, then the "load more" node while nodes are unloaded."""
    nodes: list[dict] = []
    if uncategorized is not None:
        nodes.append(dict(uncategorized))
    nodes.extend(roots)
    if more_label:
        nodes.append(more_node(more_label, remaining))
    return nodes


def search_nodes(hits: Iterable[Mapping]) -> list[dict]:
    """Tree nodes for server-side search hits (flat: a hit has no children).

    The label comes from the core (it resolves `name` and falls back to `code`,
    so a class shows its `EC…` code instead of its object type).
    """
    nodes: list[dict] = []
    for hit in hits:
        node_id = str(hit.get("id") or "")
        if not node_id:
            continue
        nodes.append(
            {
                "id": node_id,
                "label": str(hit.get("label") or hit.get("type") or node_id),
                "type": str(hit.get("type") or ""),
            }
        )
    return nodes


def path_nodes(path: Iterable[dict]) -> list[dict]:
    """Nests a root-first node path so the tree shows it as one branch.

    The nodes are the ancestor path of a search hit (root-most first, the hit
    itself last — `GET /objects/{id}/ancestors`). Every node carries the next one
    as its inline ``children``, so the branch is visible without a server
    round-trip; only the last node keeps its ``lazy`` flag, and its subtree is
    loaded on demand like any other tree node. Intermediate nodes are
    deliberately **not** lazy: a lazy expansion would fetch that node's *paged*
    children from the core and replace the branch.
    """
    nodes = [dict(node) for node in path]
    if not nodes:
        return []
    for parent, child in zip(nodes, nodes[1:], strict=False):
        parent.pop("lazy", None)
        parent["children"] = [child]
    return [nodes[0]]


def path_chain(nodes: Iterable[dict]) -> list[dict]:
    """The nodes of a nested [`path_nodes`] branch, root-most first.

    The caller expands every node but the last one (its keys, so the target node
    of the path is visible without clicking through the hierarchy) and labels the
    caption with the last node — the hit.
    """
    chain: list[dict] = []
    node: Mapping | None = next(iter(nodes), None)
    while node is not None:
        chain.append(dict(node))
        children = node.get("children") or []
        node = children[0] if children else None
    return chain


def uses_server_search(query: str, *, paged: bool) -> bool:
    """Whether a typed query must be answered by the server.

    Only when the tree is **paged** (nodes are unloaded): for a complete, small
    tree the client-side filter is faster and keeps the hierarchy visible. An
    empty query never searches.
    """
    return bool(query.strip()) and paged
