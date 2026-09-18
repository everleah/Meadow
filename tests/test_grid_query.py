"""Tests for the grid's filter state and its URL form (`meadow/frontend/grid_query.py`)."""

from __future__ import annotations

from urllib.parse import parse_qs

import pytest

from meadow.frontend.grid_query import (
    FLAG_KEYS,
    PRIMARY_CATEGORY_DEF,
    filters_query,
    has_filter,
    products_url,
    read_filters,
)


def test_read_filters_seeds_an_empty_state_from_a_plain_visit() -> None:
    """No parameters: no filter is set, both switches are off."""
    filters = read_filters({})

    assert all(filters[key] is False for key in FLAG_KEYS)
    assert all(value is None for key, value in filters.items() if key not in set(FLAG_KEYS))
    assert has_filter(filters) is False


def test_read_filters_reads_the_switches_and_the_values() -> None:
    """`…=1` is the switch, everything else is the value as it stands."""
    filters = read_filters({"incomplete": "1", "type": "product", "uncategorized": "0"})

    assert filters["incomplete"] is True
    assert filters["type"] == "product"
    assert filters["uncategorized"] is False
    assert has_filter(filters) is True


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("incomplete", True),
        ("type", "product"),
        ("ancestor", "product:1"),
        ("category", "category:1"),
        ("collection", "collection:1"),
        ("uncategorized", True),
        ("released_for", "shop"),
        ("q", "Brake"),
        ("where", '{"where": {}}'),
    ],
)
def test_every_filter_makes_a_visit_filtered(key: str, value: object) -> None:
    """Each filter on its own is enough to load objects."""
    assert has_filter({key: value}) is True


def test_definition_id_alone_is_not_a_filter() -> None:
    """A dimension without the categorisation it counts stays a display choice."""
    assert has_filter({"definition_id": "rel:1"}) is False
    assert filters_query({"definition_id": "rel:1"}) == ""
    assert products_url({"definition_id": "rel:1"}) == "/products"


def test_products_url_round_trips_a_full_filter_state() -> None:
    """URL → state → URL keeps every filter (the bookmark is the contract)."""
    state = {
        "incomplete": True,
        "type": "product",
        "ancestor": None,
        "category": "category:1",
        "definition_id": "rel:1",
        "collection": None,
        "uncategorized": False,
        "released_for": "shop",
        "q": "Brake disc",
        "where": '{"where": {"op": "eq", "field": "status", "value": "draft"}}',
    }

    url = products_url(state)
    read_back = read_filters(
        {key: values[0] for key, values in parse_qs(url.split("?", 1)[1]).items()}
    )

    assert read_back == state


def test_products_url_quotes_the_search_and_the_query_document() -> None:
    """Spaces, braces and quotes survive the URL form."""
    url = products_url({"q": "Brake disc", "where": '{"where": {"op": "eq"}}'})

    assert " " not in url
    assert parse_qs(url.split("?", 1)[1])["q"] == ["Brake disc"]
    assert parse_qs(url.split("?", 1)[1])["where"] == ['{"where": {"op": "eq"}}']


def test_the_primary_dimension_is_not_written_into_the_url() -> None:
    """The built-in primary dimension is the default — it needs no parameter."""
    assert filters_query({"category": "category:1"}) == "category=category%3A1"
    assert (
        filters_query({"category": "category:1", "definition_id": PRIMARY_CATEGORY_DEF})
        == "category=category%3A1"
    )
