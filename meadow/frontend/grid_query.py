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

``sort``/``order``/``page`` are the **view** part of the state (like the column set):
they order the view and select its page, travel in the URL so the view is bookmarkable,
and are ignored by `has_filter` — they describe how a filtered view is read, not which
objects belong to it. The grid pages server-side: `GET /objects` serves one page
(`page_size` rows from `page_start`), `GET /objects/count` its total and
`GET /objects/attribute-usage` the attribute names the whole view carries data for (so
the columns do not depend on the page that happens to be loaded).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import quote

from meadow.frontend.relation_roles import PRIMARY_CATEGORY_DEF

#: Every filter the grid knows, in the order the URL lists them. The last three are
#: **not** filters but part of the view (like the column set): `sort`/`order` order the
#: view and `page` selects its page. They travel in the URL so a sorted or paged view
#: is bookmarkable, but `has_filter` ignores them — a plain visit still loads nothing.
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
    "sort",
    "order",
    "page",
    "size",
)

#: How many rows one request of a **view walk** asks for (the export and bulk scopes
#: cover the whole view, not the page on screen). 1000 is the core's documented cap for
#: `GET /objects`.
VIEW_PAGE = 1000

#: How many objects `GET /objects/attribute-usage` inspects for the column set: it
#: answers which attributes carry data **in the view**, so a standard attribute's column
#: does not appear depending on which page happens to be loaded.
USAGE_SAMPLE = 1000

#: Filters that are switches: `1` in the URL means on, everything else is off.
FLAG_KEYS = ("incomplete", "uncategorized")

#: How many rows a list request would load without a filter (the grid stays empty anyway).
PLAIN_LIMIT = 100

#: The page sizes the pager offers (`?size=`). Every row carries every own attribute plus
#: the language and inheritance cells, so the page size is also the payload lever: 1000
#: rows made a single grid page 5,6 MB of HTML (measured 2026-09-22, 1000 products ×
#: ~166 columns). 500 is the largest offer — `GET /objects` caps `limit` at 1000.
PAGE_SIZES = (50, 100, 200, 500)

#: The page size of a filtered view when its URL does not name one. The list asks for
#: exactly this many rows (`GET /objects?limit=<size>&start=<page start>`) and
#: `page_count` divides the total of `GET /objects/count` by it.
FILTERED_LIMIT = 200
DEFAULT_PAGE_SIZE = FILTERED_LIMIT

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
    # The view part: a sorted view stays sorted when it is bookmarked, and a page
    # beyond the first is worth carrying (page 1 is the default and stays out).
    if filters.get("sort"):
        parts.append(f"sort={quote(str(filters['sort']))}")
        if filters.get("order"):
            parts.append(f"order={quote(str(filters['order']))}")
    if page_number(filters) > 1:
        parts.append(f"page={page_number(filters)}")
    if page_size(filters) != DEFAULT_PAGE_SIZE:
        parts.append(f"size={page_size(filters)}")
    return "&".join(parts)


def page_number(filters: Mapping[str, object]) -> int:
    """The 1-based page of the view (1 for absent, empty or nonsense)."""
    try:
        page = int(str(filters.get("page") or "").strip())
    except ValueError:
        return 1
    return max(page, 1)


def page_size(filters: Mapping[str, object]) -> int:
    """How many rows the view shows per page (`?size=`, one of `PAGE_SIZES`)."""
    try:
        size = int(str(filters.get("size") or "").strip())
    except ValueError:
        return DEFAULT_PAGE_SIZE
    return size if size in PAGE_SIZES else DEFAULT_PAGE_SIZE


def page_start(filters: Mapping[str, object]) -> int:
    """The offset the view's current page starts at (`?start=` of `GET /objects`)."""
    return (page_number(filters) - 1) * page_size(filters)


def page_count(total: int, size: int = DEFAULT_PAGE_SIZE) -> int:
    """How many pages `total` rows have (an empty view is page 1 of 1)."""
    return max(1, -(-max(total, 0) // size))


def products_url(filters: Mapping[str, object]) -> str:
    """The URL of the grid view these filters describe (bookmarkable)."""
    query = filters_query(filters)
    return f"/products?{query}" if query else "/products"


def list_limit(filters: Mapping[str, object]) -> int:
    """How many rows a list request asks for (a filtered view asks for a page)."""
    return page_size(filters) if has_filter(filters) else PLAIN_LIMIT


def filter_arguments(filters: Mapping[str, object], *, locale: str) -> dict[str, Any]:
    """The parameters every object query of one view shares.

    `GET /objects` (a page), `GET /objects/count` (its total) and
    `GET /objects/attribute-usage` (its column set) must describe the **same** view, so
    they are built from one function — a page and its total can never disagree.

    `definition_id` (the dimension of a categorisation) is sent **only** for the
    `category`/`uncategorized` filters: for every other view the core would validate it
    and ignore it, so sending it would only pretend it had an effect.
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
        "missing_required": bool(filters.get("incomplete")),
        "where": filters.get("where"),
        "locale": locale,
    }


def sort_arguments(filters: Mapping[str, object]) -> dict[str, Any]:
    """The `sort`/`order` pair of the view (`None` = the core's default order)."""
    return {"sort": filters.get("sort") or None, "order": filters.get("order") or None}


def list_arguments(
    filters: Mapping[str, object],
    *,
    has_channels: bool,
    locale: str,
    start: int | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """The core arguments of a **filtered object list** (`GET /objects`).

    Without `start`/`limit` this is the view's current **page**
    (`[page_start, page_start + `page_size`)`); a caller that walks the whole view — the
    export and the bulk scopes mean the view, not the rows on screen — overrides them
    with an explicit offset and `VIEW_PAGE` rows per request. `releases`/
    `resolve_inheritance` are page rendering, not filters.
    """
    return {
        **filter_arguments(filters, locale=locale),
        "releases": has_channels,
        "resolve_inheritance": True,
        "limit": list_limit(filters) if limit is None else limit,
        "start": page_start(filters) if start is None else start,
        **sort_arguments(filters),
    }


def count_arguments(filters: Mapping[str, object], *, locale: str) -> dict[str, Any]:
    """The core arguments of the view's total (`GET /objects/count`).

    `sort`/`order` travel **with** the count so the core shares one completeness walk
    between a page and its total (it caches the walk per filter *and* sort,
    `crates/AGENTS.md`); `releases`/`resolve_inheritance`/`limit`/`start` describe the
    page, not the view.
    """
    return {**filter_arguments(filters, locale=locale), **sort_arguments(filters)}


def usage_arguments(
    filters: Mapping[str, object], *, locale: str, sample: int = USAGE_SAMPLE
) -> dict[str, Any]:
    """The core arguments of the view's column set (`GET /objects/attribute-usage`)."""
    return {**filter_arguments(filters, locale=locale), "sample": sample}


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
