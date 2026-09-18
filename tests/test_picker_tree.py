"""The object picker reaches its targets — tree mode and search parameters.

Two defects made "Alternative to" / "Accessory for" (aggregations on `product`)
unusable although "Fits vehicle" worked (2026-09-17):

- **Tree** — the picker walked the *composition* tree for every non-taxonomy
  target. A `product` is only reachable **below** its part group, and the
  composition tree starts at the vehicle makes, so every row of the picker was a
  greyed-out vehicle. `picker_tree_mode` now lists a restricted non-taxonomy target
  set **flat and paged**.
- **Search** — the core's full-text index matches whole tokens, so a half-typed
  word found nothing (`Brake Clean` → 0 hits, `Brake Cleaner` → 5). The wrapper now
  always sends `label_prefix=true` (partial **label** words) and the picker's
  search field is debounced, because the core scans the chain languages for it.

This module covers the pure helpers and the query parameters the frontend sends.
See `plan/detail/kategorisierung.md` and `plan/detail/api-design.md`.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from meadow.frontend import api as frontend_api
from meadow.frontend.api import CoreClient
from meadow.frontend.object_picker import (
    TREE_COMPOSITION,
    TREE_OBJECTS,
    TREE_TAXONOMY,
    composition_reachable,
    object_type_options,
    picker_tree_mode,
)

TAXONOMIES = ["category", "etim_class", "part_group"]

#: The dev instance's composition type graph, measured 2026-09-17
#: (`GET /objects/composition-types`).
GRAPH: dict[str, Any] = {
    "root_type": "root",
    "edges": [
        {"parent": "category", "child": "category"},
        {"parent": "part_group", "child": "oem_number"},
        {"parent": "part_group", "child": "part_group"},
        {"parent": "part_group", "child": "product"},
        {"parent": "product", "child": "variant"},
        {"parent": "root", "child": "category"},
        {"parent": "root", "child": "etim_class"},
        {"parent": "root", "child": "part_group"},
        {"parent": "root", "child": "vehicle_make"},
        {"parent": "vehicle_make", "child": "vehicle_model"},
        {"parent": "vehicle_model", "child": "vehicle_generation"},
    ],
}

OBJECT_TYPES: list[dict[str, Any]] = [
    {"name": "product", "label": {"de": "Produkt", "en": "Product"}},
    {"name": "variant", "label": {"de": "Variante", "en": "Variant"}},
    {"name": "part_group", "label": {"de": "Baugruppe", "en": "Assembly group"}},
    {"name": "vehicle_make", "label": {"de": "Marke", "en": "Make"}},
]


def test_a_taxonomy_target_walks_one_taxonomy_tree() -> None:
    # Bound to one taxonomy node type: that tree, no switcher needed.
    assert picker_tree_mode({"part_group"}, TAXONOMIES) == TREE_TAXONOMY
    # A generic dimension (every taxonomy type allowed) still walks one tree at a
    # time — the switcher picks it.
    assert picker_tree_mode(set(TAXONOMIES), TAXONOMIES) == TREE_TAXONOMY


def test_a_reachable_type_keeps_the_composition_tree() -> None:
    # "Fits vehicle": make → model → generation is a tree worth walking.
    reachable = composition_reachable({"vehicle_generation"}, GRAPH, TAXONOMIES)
    assert reachable
    assert picker_tree_mode({"vehicle_generation"}, TAXONOMIES, reachable=reachable) == (
        TREE_COMPOSITION
    )


def test_a_hidden_type_is_listed_flat() -> None:
    # "Alternative to"/"Accessory for" point at products, which hang below a part
    # group — a taxonomy node the composition scope does not render, so the tree can
    # never reach them (every row was a greyed-out vehicle).
    for targets in ({"product"}, {"variant"}, {"product", "variant"}, {"oem_number"}):
        reachable = composition_reachable(targets, GRAPH, TAXONOMIES)
        assert not reachable, targets
        assert picker_tree_mode(targets, TAXONOMIES, reachable=reachable) == TREE_OBJECTS
    # One unreachable type in a mixed target set decides the panel: the tree would
    # grey the other one out, the flat list offers both (switcher).
    assert not composition_reachable({"vehicle_generation", "product"}, GRAPH, TAXONOMIES)


def test_every_allowed_type_has_to_be_reachable() -> None:
    assert composition_reachable(set(), GRAPH, TAXONOMIES)
    assert composition_reachable(None, GRAPH, TAXONOMIES)
    # A type that is not in the graph at all (never written) is unreachable.
    assert not composition_reachable({"fresh_type"}, GRAPH, TAXONOMIES)


def test_a_missing_graph_keeps_the_composition_tree() -> None:
    """A failed lookup must not silently change the panel — keep the tree."""
    assert composition_reachable({"product"}, None, TAXONOMIES)
    assert composition_reachable({"product"}, {}, TAXONOMIES)
    assert composition_reachable({"product"}, {"root_type": "", "edges": []}, TAXONOMIES)


def test_the_object_type_switcher_is_labelled_and_sorted() -> None:
    options = object_type_options(OBJECT_TYPES, {"variant", "product"}, "de")
    assert options == {"product": "Produkt", "variant": "Variante"}
    assert object_type_options(OBJECT_TYPES, {"product"}, "en") == {"product": "Product"}


def test_the_object_type_switcher_falls_back_to_the_type_name() -> None:
    # A type that is missing from the catalogue (or carries no label) must still be
    # offered — the switcher may never hide an allowed target type.
    assert object_type_options([], {"product"}, "de") == {"product": "product"}
    assert object_type_options([{"name": "product"}], {"product"}, "de") == {"product": "product"}


@pytest.fixture
def seen_requests(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    """Captures the requests the frontend client sends (mocked transport)."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False)
    monkeypatch.setattr(frontend_api, "_shared_client", client)
    return seen


async def test_the_picker_search_matches_partial_label_words(
    seen_requests: list[httpx.Request],
) -> None:
    client = CoreClient("http://127.0.0.1:8080")

    await client.picker_search("token", "Brake Clean", types=["product"], locale="en")

    params = seen_requests[0].url.params
    # Without `label_prefix` the core's full-text index finds nothing for a
    # half-typed word.
    assert params["label_prefix"] == "true"
    assert params["q"] == "Brake Clean"
    assert params["types"] == "product"
    assert params["locale"] == "en"


async def test_the_picker_search_can_still_be_restricted(
    seen_requests: list[httpx.Request],
) -> None:
    """A caller that wants the strict full-text behaviour can turn the scan off."""
    client = CoreClient("http://127.0.0.1:8080")

    await client.picker_search("token", "EC0123", code_prefix=True, label_prefix=False)

    params = seen_requests[0].url.params
    assert params["code_prefix"] == "true"
    assert "label_prefix" not in params
