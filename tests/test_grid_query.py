"""Tests for the grid's filter state and its URL form (`meadow/frontend/grid_query.py`)."""

from __future__ import annotations

from urllib.parse import parse_qs

import pytest

from meadow.frontend.grid_query import (
    FILTERED_LIMIT,
    FLAG_KEYS,
    PAGE_SIZE,
    PLAIN_LIMIT,
    PRIMARY_CATEGORY_DEF,
    VIEW_PAGE,
    count_arguments,
    filters_query,
    has_filter,
    list_arguments,
    list_limit,
    page_count,
    page_number,
    page_start,
    products_url,
    read_filters,
    search_arguments,
    search_types,
    usage_arguments,
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
        "sort": "attributes.name",
        "order": "desc",
        "page": "2",
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


def test_the_view_state_round_trips_through_the_url() -> None:
    """Sort, direction and page are part of the view, so a bookmark keeps them."""
    state = read_filters(
        {"type": "product", "sort": "attributes.name", "order": "desc", "page": "3"}
    )

    assert state["sort"] == "attributes.name"
    assert state["order"] == "desc"
    assert page_number(state) == 3

    url = products_url(state)
    read_back = read_filters(
        {key: values[0] for key, values in parse_qs(url.split("?", 1)[1]).items()}
    )
    assert read_back["sort"] == "attributes.name"
    assert read_back["order"] == "desc"
    assert page_number(read_back) == 3


def test_the_first_page_and_an_unsorted_view_add_nothing_to_the_url() -> None:
    """Defaults stay out of the URL (a plain filtered view stays readable)."""
    assert filters_query({"type": "product", "page": "1"}) == "type=product"
    assert filters_query({"type": "product", "sort": None, "order": "desc"}) == "type=product"


def test_a_sort_or_page_alone_does_not_make_a_visit_filtered() -> None:
    """`?sort=`/`?page=` describe how a view is read, not which objects it holds."""
    assert has_filter({"sort": "attributes.name", "order": "asc", "page": "4"}) is False


def test_pages_are_counted_and_stepped_from_the_total() -> None:
    assert page_start({"page": "1"}) == 0
    assert page_start({"page": "3"}) == 2 * PAGE_SIZE
    assert page_count(0) == 1
    assert page_count(1) == 1
    assert page_count(PAGE_SIZE) == 1
    assert page_count(PAGE_SIZE + 1) == 2
    # Nonsense (or a missing page) is the first page, never a crash.
    assert page_number({"page": "two"}) == 1
    assert page_number({"page": None}) == 1
    assert page_number({"page": "-4"}) == 1


def test_the_list_arguments_carry_the_page_and_the_sort() -> None:
    filters = read_filters(
        {"type": "product", "sort": "attributes.name", "order": "desc", "page": "2"}
    )

    arguments = list_arguments(filters, has_channels=True, locale="de")

    assert arguments["start"] == PAGE_SIZE
    assert arguments["limit"] == PAGE_SIZE
    assert arguments["sort"] == "attributes.name"
    assert arguments["order"] == "desc"


def test_a_view_walk_overrides_the_page_and_reads_full_chunks() -> None:
    """The export/bulk scopes mean the view: explicit offset, `VIEW_PAGE` chunks."""
    filters = read_filters({"type": "product", "page": "2"})

    arguments = list_arguments(
        filters, has_channels=False, locale="de", start=VIEW_PAGE, limit=VIEW_PAGE
    )

    assert arguments["start"] == VIEW_PAGE
    assert arguments["limit"] == VIEW_PAGE


def test_the_count_and_the_usage_share_the_view_but_not_the_page() -> None:
    """One filter chain for page, total and column set — without the page's extras."""
    filters = read_filters({"type": "product", "incomplete": "1", "sort": "status"})

    count = count_arguments(filters, locale="de")
    usage = usage_arguments(filters, locale="de")

    assert count == {key: value for key, value in usage.items() if key != "sample"} | {
        "sort": "status",
        "order": None,
    }
    assert count["object_type"] == "product"
    assert count["missing_required"] is True
    assert "start" not in count
    assert "limit" not in count
    assert "releases" not in count
    assert "resolve_inheritance" not in count
    assert usage["sample"] == 1000
