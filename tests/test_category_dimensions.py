"""Tests for the category dimensions and taxonomy node types (Phase 9).

Two independent axes drive the category views:

- **Taxonomy (node type)** — *which* objects are the nodes: the built-in
  `category` plus every object type flagged `taxonomy: true` (e.g. a
  classification standard's class hierarchy). See `taxonomy_type_names`.
- **Dimension (relation type)** — *which* assignments are counted/filtered: the
  built-in primary categorisation plus custom relation types with the usage
  `category` (`?count_members=true&definition_id=…`, `?category=…&definition_id=…`).

This module covers the pure helpers and the query/payload parameters the
frontend sends. See `plan/detail/kategorisierung.md` and `plan/detail/relationen.md`.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from meadow.frontend import api as frontend_api
from meadow.frontend.api import CoreClient
from meadow.frontend.display import dimension_options, taxonomy_options, taxonomy_type_names
from meadow.frontend.relation_roles import PRIMARY_CATEGORY_DEF, PRIMARY_CATEGORY_TYPE

_PRIMARY_LABEL = "Categories (system)"

_DEFINITIONS: list[dict[str, Any]] = [
    {
        "id": "relation_definition:rel_category_member",
        "name": "category_member",
        "kind": "aggregation",
        "role": "category",
        "system": True,
    },
    {
        "id": "relation_definition:rel_marke",
        "name": "marke",
        "kind": "aggregation",
        "role": "category",
        "label": {"de": "Marke", "en": "Brand"},
        "system": False,
    },
    {
        "id": "relation_definition:rel_depends_on",
        "name": "depends_on",
        "kind": "composition",
        "role": "composition",
        "system": True,
    },
]


def _options(definitions: list[dict[str, Any]], locale: str = "de") -> dict[str, str]:
    return dimension_options(definitions, locale, primary_label=_PRIMARY_LABEL)


def test_primary_dimension_comes_first_and_keeps_the_page_label() -> None:
    options = _options(_DEFINITIONS)

    assert list(options) == [PRIMARY_CATEGORY_DEF, "rel_marke"]
    assert options[PRIMARY_CATEGORY_DEF] == _PRIMARY_LABEL


def test_custom_dimensions_use_their_multilingual_label() -> None:
    definitions = [d for d in _DEFINITIONS if d["name"] == "marke"]

    assert _options(definitions, "de") == {"rel_marke": "Marke"}
    assert _options(definitions, "en") == {"rel_marke": "Brand"}


def test_custom_dimensions_fall_back_to_name_then_key() -> None:
    generated = {"id": "relation_definition:rel_farbe", "name": "farbe", "role": "category"}

    # No label → the definition's name; neither label nor name → the record key.
    assert _options([generated]) == {"rel_farbe": "farbe"}
    assert _options([{"id": "relation_definition:rel_x", "role": "category"}]) == {"rel_x": "rel_x"}


def test_non_category_types_are_not_dimensions() -> None:
    options = _options(_DEFINITIONS)

    assert "rel_depends_on" not in options


def test_without_definitions_the_primary_dimension_alone_is_offered() -> None:
    """The switcher must never be empty, even if the catalog request failed."""
    assert _options([]) == {PRIMARY_CATEGORY_DEF: _PRIMARY_LABEL}


_NODE_TYPES: list[dict[str, Any]] = [
    {"name": "product", "label": {"de": "Produkt"}},
    {"name": "category", "label": {"de": "Kategorie", "en": "Category"}, "taxonomy": True},
    {"name": "etim_class", "label": {"de": "ETIM-Klasse"}, "taxonomy": True},
    {"name": "grouping", "taxonomy": True},
    {"name": "collection", "taxonomy": False},
]


def test_taxonomy_type_names_lists_the_node_types_sorted() -> None:
    assert taxonomy_type_names(_NODE_TYPES) == ["category", "etim_class", "grouping"]


def test_taxonomy_type_names_falls_back_to_the_builtin_type() -> None:
    """Without a catalog (or without any flagged type) the built-in type remains."""
    assert taxonomy_type_names([]) == [PRIMARY_CATEGORY_TYPE]
    assert taxonomy_type_names([{"name": "product"}]) == [PRIMARY_CATEGORY_TYPE]


def test_taxonomy_options_resolve_labels_and_fall_back_to_the_name() -> None:
    options = taxonomy_options(_NODE_TYPES, "de")

    assert options == {
        "category": "Kategorie",
        "etim_class": "ETIM-Klasse",
        "grouping": "grouping",
    }
    assert taxonomy_options(_NODE_TYPES, "en")["etim_class"] == "ETIM-Klasse"
    assert taxonomy_options([], "de") == {PRIMARY_CATEGORY_TYPE: PRIMARY_CATEGORY_TYPE}


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


async def test_children_counts_follow_the_selected_dimension(
    seen_requests: list[httpx.Request],
) -> None:
    client = CoreClient("http://127.0.0.1:8080")

    await client.list_object_children(
        "token", "object:object_root", count_members=True, definition_id="rel_marke"
    )
    await client.list_object_children("token", "object:object_root", count_members=True)

    first, second = (request.url.params for request in seen_requests)
    assert first["count_members"] == "true"
    assert first["definition_id"] == "rel_marke"
    # Without a dimension the core counts the built-in primary type.
    assert "definition_id" not in second


async def test_the_grid_filter_carries_the_dimension(seen_requests: list[httpx.Request]) -> None:
    client = CoreClient("http://127.0.0.1:8080")

    await client.list_objects(
        "token", category="object:marken", definition_id="rel_marke", limit=100
    )

    params = seen_requests[0].url.params
    assert params["category"] == "object:marken"
    assert params["definition_id"] == "rel_marke"
    assert params["limit"] == "100"


async def test_object_types_carry_the_taxonomy_flag(
    seen_requests: list[httpx.Request],
) -> None:
    """The flag is only sent when set, so existing call sites stay unchanged."""
    client = CoreClient("http://127.0.0.1:8080")

    await client.create_object_type("token", name="etim_class", taxonomy=True)
    await client.create_object_type("token", name="grouping")
    await client.update_object_type("token", "grouping", taxonomy=True)
    await client.update_object_type("token", "category", taxonomy=None)

    bodies = [json.loads(request.content) for request in seen_requests]
    assert bodies[0] == {"name": "etim_class", "taxonomy": True}
    assert "taxonomy" not in bodies[1]
    assert bodies[2] == {"taxonomy": True}
    # `None` means \"unchanged\" (system types keep the flag the seed owns).
    assert bodies[3] == {}
