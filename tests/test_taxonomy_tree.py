"""Tests for the taxonomy tree paging/search helpers (pure, no UI)."""

from __future__ import annotations

from meadow.frontend import taxonomy_tree
from meadow.frontend.taxonomy_tree import (
    MORE_ID,
    PAGE_SIZE,
    append_page,
    has_more,
    more_node,
    search_nodes,
    tree_nodes,
    uses_server_search,
)


def test_has_more_only_after_a_full_page() -> None:
    """A full page means "maybe" — a short page is the end of the list."""
    assert has_more(0) is False
    assert has_more(PAGE_SIZE - 1) is False
    assert has_more(PAGE_SIZE) is True
    assert has_more(PAGE_SIZE * 3) is True
    assert has_more(PAGE_SIZE * 3 + 7) is False
    assert has_more(5, page_size=5) is True


def test_append_page_skips_duplicates_and_keeps_the_loaded_ones() -> None:
    """Re-fetching a page must not render a node twice."""
    loaded = [{"id": "object:a"}, {"id": "object:b"}]
    merged = append_page(loaded, [{"id": "object:b"}, {"id": "object:c"}])
    assert [node["id"] for node in merged] == ["object:a", "object:b", "object:c"]


def test_append_page_does_not_mutate_the_loaded_list() -> None:
    """The caller rebinds the result, so the old list stays untouched."""
    loaded: list[dict] = [{"id": "object:a"}]
    append_page(loaded, [{"id": "object:b"}])
    assert [node["id"] for node in loaded] == ["object:a"]


def test_tree_nodes_puts_uncategorized_first_and_more_last() -> None:
    """The "not categorized" entry stays on top, "load more" at the bottom."""
    uncategorized = {"id": "uncategorized", "label": "Not categorized"}
    roots = [{"id": "object:a", "label": "A"}]
    nodes = tree_nodes(roots, more_label="Load more", remaining=42, uncategorized=uncategorized)
    assert [node["id"] for node in nodes] == ["uncategorized", "object:a", MORE_ID]
    assert nodes[-1]["more"] is True
    assert nodes[-1]["remaining"] == 42


def test_tree_nodes_without_more_label_has_no_marker() -> None:
    """A complete tree (or a filtered one) carries no "load more" node."""
    nodes = tree_nodes([{"id": "object:a", "label": "A"}])
    assert [node["id"] for node in nodes] == ["object:a"]


def test_more_node_is_not_an_object_id() -> None:
    """The marker is a sentinel: it must not collide with an object id."""
    node = more_node("More", -1)
    assert node["id"] == MORE_ID
    assert node["label"] == "More"
    assert node["type"] == ""
    assert node["more"] is True


def test_search_nodes_maps_hits_and_skips_empty_ids() -> None:
    """Hits become flat tree nodes; the core's label is used as-is."""
    hits = [
        {"id": "object:ec1", "label": "EC000012", "type": "etim_class"},
        {"id": "", "label": "ignored"},
        {"id": "object:ec2", "type": "etim_class"},
    ]
    nodes = search_nodes(hits)
    assert nodes[0] == {"id": "object:ec1", "label": "EC000012", "type": "etim_class"}
    # No label in the hit: fall back to the type rather than to an empty row.
    assert nodes[1]["id"] == "object:ec2"
    assert nodes[1]["label"] == "etim_class"


def test_uses_server_search_only_for_a_paged_tree_with_a_query() -> None:
    """An empty query never searches; a complete tree filters client-side."""
    assert uses_server_search("", paged=True) is False
    assert uses_server_search("   ", paged=True) is False
    assert uses_server_search("EC012373", paged=False) is False
    assert uses_server_search("EC012373", paged=True) is True
    assert taxonomy_tree.PAGE_SIZE == PAGE_SIZE
