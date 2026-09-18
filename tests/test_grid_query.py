"""Tests for the grid's filter state and its URL form (`meadow/frontend/grid_query.py`)."""

from __future__ import annotations

from urllib.parse import parse_qs

import pytest

from meadow.frontend.grid_query import (
    FILTERED_LIMIT,
    FLAG_KEYS,
    PLAIN_LIMIT,
    PRIMARY_CATEGORY_DEF,
    filters_query,
    has_filter,
    list_arguments,
    list_limit,
    products_url,
    read_filters,
    search_arguments,
    search_types,
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


def test_a_filtered_list_asks_for_more_rows_than_a_plain_one() -> None:
    """The limit follows the filters (a bare visit would load a small teaser)."""
    assert list_limit({}) == PLAIN_LIMIT
    assert list_limit({"type": "product"}) == FILTERED_LIMIT


def test_the_list_arguments_carry_every_filter() -> None:
    """What the core gets: the filters as they stand, inheritance resolved."""
    filters = read_filters({"type": "product", "ancestor": "product:1", "released_for": "shop"})

    arguments = list_arguments(filters, has_channels=True, locale="de")

    assert arguments["object_type"] == "product"
    assert arguments["ancestor"] == "product:1"
    assert arguments["released_for"] == "shop"
    assert arguments["releases"] is True, "the release badges need the releases"
    assert arguments["locale"] == "de"
    assert arguments["resolve_inheritance"] is True
    assert arguments["limit"] == FILTERED_LIMIT


def test_the_dimension_is_only_sent_with_the_category_filter() -> None:
    """Without its categorisation the core would validate the dimension and ignore it."""
    dimension = {"definition_id": "rel:1"}

    assert list_arguments(dimension, has_channels=False, locale="de")["definition_id"] is None
    assert (
        list_arguments({**dimension, "category": "category:1"}, has_channels=False, locale="de")[
            "definition_id"
        ]
        == "rel:1"
    )
    assert (
        list_arguments({**dimension, "uncategorized": True}, has_channels=False, locale="de")[
            "definition_id"
        ]
        == "rel:1"
    )


def test_the_switches_become_booleans_for_the_core() -> None:
    """The core expects real flags: `missing_required` and `uncategorized`."""
    arguments = list_arguments(
        read_filters({"incomplete": "1", "uncategorized": "1"}), has_channels=False, locale="de"
    )

    assert arguments["missing_required"] is True
    assert arguments["uncategorized"] is True
    assert list_arguments({}, has_channels=False, locale="de")["missing_required"] is False


def test_the_search_arguments_load_the_hits_by_id() -> None:
    """The search path reloads full rows for the hit ids, badge included there."""
    arguments = search_arguments(
        read_filters({"q": "Brake", "incomplete": "1", "where": "{}"}),
        ids=["product:1", "product:2"],
        limit=200,
        locale="de",
    )

    assert arguments["ids"] == ["product:1", "product:2"]
    assert arguments["missing_required"] is True
    assert arguments["where"] == "{}"
    assert arguments["limit"] == 200


def test_the_search_looks_in_the_chosen_type_only() -> None:
    """A type filter narrows the search; without one every type is searched."""
    assert search_types({"type": "product"}) == ["product"]
    assert search_types({}) is None
