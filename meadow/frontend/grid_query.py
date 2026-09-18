"""The product grid's filter state and its URL form.

The grid's filters are **seeded from the URL** (a dashboard shortcut such as
`?incomplete=1&type=product` is bookmarkable) and then driven by the filter bar. The
page keeps them in one dict (``active``) and passes it to the core; this module owns the
two directions of the URL mapping: reading the query parameters and building the query
string back.

Two rules are part of the contract:

- A filter that is not set is ``None`` (the core treats it as "unfiltered"), the two
  switches are ``False``. `has_filter` is what decides whether a plain visit loads
  anything at all — without a filter the grid deliberately stays empty.
- ``definition_id`` is the **dimension** of a categorisation. Read, it stands on its own:
  it selects the dimension the page works in (the category view follows it). Written, it
  travels **only** with the ``category``/``uncategorized`` filter it belongs to — a data
  filter without the taxonomy it counts would be dropped by the core, and a dimension
  alone is not a filter (`has_filter` ignores it).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import quote

from meadow.frontend.relation_roles import PRIMARY_CATEGORY_DEF

#: Every filter the grid knows, in the order the URL lists them.
FILTER_KEYS = (
    "incomplete",
    "type",
    "ancestor",
    "category",
    "definition_id",
    "collection",
    "uncategorized",
    "released_for",
    "q",
    "where",
)

#: Filters that are switches: `1` in the URL means on, everything else is off.
FLAG_KEYS = ("incomplete", "uncategorized")

#: How many rows a **filtered** view loads.
FILTERED_LIMIT = 1000
#: How many rows a list request would load without a filter (the grid stays empty anyway).
PLAIN_LIMIT = 100

#: Filters that make a visit "filtered" (the grid then loads objects by itself).
#: `definition_id` is missing on purpose: it only belongs to a category filter.
MEANINGFUL_KEYS = (
    "incomplete",
    "type",
    "ancestor",
    "category",
    "collection",
    "uncategorized",
    "released_for",
    "q",
    "where",
)


def read_filters(query_params: Mapping[str, str]) -> dict[str, object]:
    """The filter state a URL describes (the seed of the page's filter dict)."""
    filters: dict[str, object] = {}
    for key in FILTER_KEYS:
        raw = query_params.get(key)
        filters[key] = raw == "1" if key in FLAG_KEYS else (raw or None)
    return filters


def has_filter(filters: Mapping[str, object]) -> bool:
    """Whether any filter is active (a plain visit deliberately loads nothing)."""
    return any(filters.get(key) for key in MEANINGFUL_KEYS)


def filters_query(filters: Mapping[str, object]) -> str:
    """The query string of the active filters (URL form, values quoted)."""
    parts: list[str] = []
    if filters.get("incomplete"):
        parts.append("incomplete=1")
    if filters.get("type"):
        parts.append(f"type={quote(str(filters['type']))}")
    if filters.get("ancestor"):
        parts.append(f"ancestor={quote(str(filters['ancestor']))}")
    if filters.get("category"):
        parts.append(f"category={quote(str(filters['category']))}")
        parts.extend(_dimension_part(filters))
    if filters.get("collection"):
        parts.append(f"collection={quote(str(filters['collection']))}")
    if filters.get("released_for"):
        parts.append(f"released_for={quote(str(filters['released_for']))}")
    if filters.get("uncategorized"):
        parts.append("uncategorized=1")
        parts.extend(_dimension_part(filters))
    if filters.get("q"):
        parts.append(f"q={quote(str(filters['q']))}")
    if filters.get("where"):
        parts.append(f"where={quote(str(filters['where']))}")
    return "&".join(parts)


def products_url(filters: Mapping[str, object]) -> str:
    """The URL of the grid view these filters describe (bookmarkable)."""
    query = filters_query(filters)
    return f"/products?{query}" if query else "/products"


def list_limit(filters: Mapping[str, object]) -> int:
    """How many rows a list request asks for (a filtered view asks for more)."""
    return FILTERED_LIMIT if has_filter(filters) else PLAIN_LIMIT


def list_arguments(
    filters: Mapping[str, object], *, has_channels: bool, locale: str
) -> dict[str, Any]:
    """The core arguments of a **filtered object list** (`GET /objects`).

    `definition_id` (the dimension of a categorisation) is sent **only** for the
    `category` and `uncategorized` filters: for every other view the core would validate
    it and ignore it, so sending it would only pretend it had an effect.
    """
    return {
        "object_type": filters.get("type"),
        "ancestor": filters.get("ancestor"),
        "category": filters.get("category"),
        "definition_id": (
            filters.get("definition_id")
            if filters.get("category") or filters.get("uncategorized")
            else None
        ),
        "collection": filters.get("collection"),
        "uncategorized": bool(filters.get("uncategorized")),
        "released_for": filters.get("released_for"),
        "releases": has_channels,
        "missing_required": bool(filters.get("incomplete")),
        "where": filters.get("where"),
        "locale": locale,
        "resolve_inheritance": True,
        "limit": list_limit(filters),
    }


def search_arguments(
    filters: Mapping[str, object], *, ids: Sequence[str], limit: int, locale: str
) -> dict[str, Any]:
    """The core arguments for loading the **hits** of a server-side search.

    The hits only supply the ids — the rows carry the completeness — so the badge is
    skipped in the search itself and the ids are loaded as full grid rows here.
    """
    return {
        "ids": list(ids),
        "missing_required": bool(filters.get("incomplete")),
        "where": filters.get("where"),
        "locale": locale,
        "resolve_inheritance": True,
        "limit": limit,
    }


def search_types(filters: Mapping[str, object]) -> list[str] | None:
    """The object types a server-side search looks in (`None` = every type)."""
    object_type = filters.get("type")
    return [str(object_type)] if object_type else None


def _dimension_part(filters: Mapping[str, object]) -> list[str]:
    """The `definition_id` parameter, unless it is the built-in primary dimension."""
    dimension = filters.get("definition_id")
    if dimension and dimension != PRIMARY_CATEGORY_DEF:
        return [f"definition_id={quote(str(dimension))}"]
    return []
