"""Tests for the MEF relations in the export (`meadow/worker/exports.py`).

A MEF document has to carry the relations of its objects. Without them an import
on a fresh system creates every object on the universal root — measured
2026-09-17: a three-variant export/import round-trip lost the whole structure
tree (`docs/integration/mef-and-events.md` §1.6).
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from meadow.worker.exports import (
    OBJECT_PAGE_SIZE,
    _collect_mef_data,
    _definition_name,
    _load_references,
    _mef_references,
    _mef_releases,
    _resolve_objects,
    _structured_export,
)


def _edge(
    edge_id: str,
    source: str,
    target: str,
    definition: str = "rel_depends_on",
    **attributes: Any,
) -> dict[str, Any]:
    """A raw `reference` row as `POST /references/query` returns it."""
    edge: dict[str, Any] = {
        "id": edge_id,
        "definition_id": definition,
        "in": source,
        "out": target,
    }
    if attributes:
        edge["attributes"] = attributes
    return edge


def test_definition_name_prefers_the_registry_and_strips_the_seed_prefix() -> None:
    names = {"rel_fits_vehicle": "fits_vehicle"}

    assert _definition_name("relation_definition:rel_fits_vehicle", names) == "fits_vehicle"
    # A definition the registry does not know falls back to the seed convention.
    assert _definition_name("rel_depends_on", names) == "depends_on"
    assert _definition_name("relation_definition:rel_category_member", names) == "category_member"


def test_mef_references_keep_only_edges_inside_the_document() -> None:
    edges = {
        "reference:1": _edge("reference:1", "object:child", "object:parent"),
        "reference:2": _edge(
            "reference:2", "object:child", "object:category", "rel_category_member"
        ),
        "reference:3": _edge("reference:3", "object:child", "object:parent", "rel_related", rank=2),
        # An edge to the universal root is dropped without counting: the root
        # exists in every instance and the core attaches top-level objects itself.
        "reference:4": _edge("reference:4", "object:parent", "object:object_root"),
    }
    id_of = {"child": "object:child", "parent": "object:parent"}

    entries, skipped = _mef_references(edges, id_of, {"rel_depends_on": "depends_on"})

    assert skipped == 1
    assert entries == [
        {"in": "object:child", "out": "object:parent", "type": "depends_on"},
        {
            "in": "object:child",
            "out": "object:parent",
            "type": "related",
            "attributes": {"rank": 2},
        },
    ]


async def test_resolve_objects_pages_the_whole_source() -> None:
    """`kind='all'` reads every page and drops the universal root."""
    full_page = [
        {"id": f"object:{index:04d}", "type": "product"} for index in range(OBJECT_PAGE_SIZE)
    ]
    last_page = [{"id": "object:root", "type": "root"}, {"id": "object:last", "type": "product"}]
    pages = {0: full_page, OBJECT_PAGE_SIZE: last_page}
    starts: list[int] = []

    async def transport(request: httpx.Request) -> httpx.Response:
        start = int(request.url.params.get("start") or 0)
        starts.append(start)
        return httpx.Response(200, json=pages.get(start, []))

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    objects = await _resolve_objects(client, "http://core", {"kind": "all"})

    assert starts == [0, OBJECT_PAGE_SIZE]
    assert len(objects) == OBJECT_PAGE_SIZE + 1
    assert objects[-1]["id"] == "object:last"
    assert all(obj["type"] != "root" for obj in objects)


async def test_resolve_objects_pages_collection_members() -> None:
    """A collection with more than one page keeps all of its members."""
    members = [{"id": f"object:{index:04d}"} for index in range(OBJECT_PAGE_SIZE)]
    pages = {0: members, OBJECT_PAGE_SIZE: [{"id": "object:extra"}]}
    member_starts: list[int] = []

    async def transport(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/collections/object:col/members":
            start = int(request.url.params.get("start") or 0)
            member_starts.append(start)
            return httpx.Response(200, json=pages.get(start, []))
        if request.url.path == "/objects":
            # Answer with exactly the requested slice of the chunk, so both the
            # chunking of `?ids=` and the page boundary are covered.
            ids = [item for item in str(request.url.params.get("ids") or "").split(",") if item]
            start = int(request.url.params.get("start") or 0)
            limit = int(request.url.params.get("limit") or 0) or len(ids)
            # The core always answers with the canonical `object:<key>` id, no
            # matter whether the request used a bare key.
            page = [
                item if item.startswith("object:") else f"object:{item}"
                for item in ids[start : start + limit]
            ]
            return httpx.Response(
                200, json=[{"id": object_id, "type": "product"} for object_id in page]
            )
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    objects = await _resolve_objects(
        client, "http://core", {"kind": "collection", "id": "object:col"}
    )

    assert member_starts == [0, OBJECT_PAGE_SIZE]
    assert len(objects) == OBJECT_PAGE_SIZE + 1
    assert objects[-1]["id"] == "object:extra"


async def test_load_references_pages_a_hub_node() -> None:
    """A full page of rows (`truncated`) is followed by the next page."""
    starts: list[int] = []

    async def transport(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        starts.append(int(body["start"]))
        if body["start"] == 0:
            return httpx.Response(
                200,
                json={
                    "references": [_edge("reference:1", "object:a", "object:hub")],
                    "truncated": True,
                },
            )
        return httpx.Response(
            200,
            json={
                "references": [_edge("reference:2", "object:b", "object:hub")],
                "truncated": False,
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    edges = await _load_references(client, "http://core", ["object:a", "object:b"])

    assert starts == [0, 5000]
    assert sorted(edges) == ["reference:1", "reference:2"]


async def test_collect_mef_data_adds_composition_ancestors() -> None:
    child = {"id": "object:child", "type": "variant", "attributes": {"sku": "A"}}
    parent = {"id": "object:parent", "type": "product", "attributes": {"sku": "B"}}

    async def transport(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/relation-definitions":
            return httpx.Response(
                200,
                json=[{"id": "relation_definition:rel_depends_on", "name": "depends_on"}],
            )
        if request.url.path == "/references/query":
            ids = json.loads(request.content)["ids"]
            if "object:child" in ids:
                return httpx.Response(
                    200,
                    json={
                        "references": [_edge("reference:1", "object:child", "object:parent")],
                        "truncated": False,
                    },
                )
            return httpx.Response(
                200,
                json={
                    "references": [_edge("reference:2", "object:parent", "object:object_root")],
                    "truncated": False,
                },
            )
        if request.url.path == "/objects":
            ids = str(request.url.params.get("ids") or "")
            return httpx.Response(200, json=[parent] if "parent" in ids else [])
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    objects, references, counters = await _collect_mef_data(client, "http://core", [child])

    assert [obj["id"] for obj in objects] == ["object:child", "object:parent"]
    assert references == [{"in": "object:child", "out": "object:parent", "type": "depends_on"}]
    assert counters == {"references": 1, "skipped_references": 0, "added_ancestors": 1}


async def test_collect_mef_data_keeps_a_dangling_parent_out_of_the_document() -> None:
    """A parent that does not exist in the system: no object, the edge is counted."""
    child = {"id": "object:child", "type": "variant", "attributes": {}}

    async def transport(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/relation-definitions":
            return httpx.Response(200, json=[])
        if request.url.path == "/references/query":
            return httpx.Response(
                200,
                json={
                    "references": [_edge("reference:1", "object:child", "object:gone")],
                    "truncated": False,
                },
            )
        if request.url.path == "/objects":
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    objects, references, counters = await _collect_mef_data(client, "http://core", [child])

    assert [obj["id"] for obj in objects] == ["object:child"]
    assert references == []
    assert counters == {"references": 0, "skipped_references": 1, "added_ancestors": 0}


async def test_structured_export_keeps_ancestors_and_their_edge() -> None:
    child = {"id": "object:child", "type": "variant", "status": "draft", "attributes": {"sku": "A"}}
    parent = {"id": "object:parent", "type": "product", "status": "active", "attributes": {}}

    async def transport(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/objects":
            ids = str(request.url.params.get("ids") or "")
            if "child" in ids:
                return httpx.Response(200, json=[child])
            return httpx.Response(200, json=[parent] if "parent" in ids else [])
        if path == "/attribute-definitions" or path == "/languages":
            return httpx.Response(200, json=[])
        if path == "/relation-definitions":
            return httpx.Response(200, json=[])
        if path == "/references/query":
            return httpx.Response(
                200,
                json={
                    "references": [_edge("reference:1", "object:child", "object:parent")],
                    "truncated": False,
                },
            )
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    result = await _structured_export(
        client,
        "http://core",
        {
            "source": {"kind": "selection", "object_ids": ["object:child"]},
            "format": "json",
            "fields": ["id"],
            "locales": ["de"],
            "options": {},
        },
    )

    document = result["document"]
    assert result["exported"] == 2
    assert result["added_ancestors"] == 1
    assert [obj["id"] for obj in document["objects"]] == ["object:child", "object:parent"]
    assert document["references"] == [
        {"in": "object:child", "out": "object:parent", "type": "depends_on"}
    ]


async def test_structured_export_can_skip_the_ancestors() -> None:
    child = {"id": "object:child", "type": "variant", "status": "draft", "attributes": {}}
    asked: list[str] = []

    async def transport(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/objects":
            ids = str(request.url.params.get("ids") or "")
            if ids:
                asked.append(ids)
                return httpx.Response(200, json=[child] if "child" in ids else [])
            return httpx.Response(200, json=[])
        if path in ("/attribute-definitions", "/languages"):
            return httpx.Response(200, json=[])
        if path == "/relation-definitions":
            return httpx.Response(200, json=[])
        if path == "/references/query":
            return httpx.Response(
                200,
                json={
                    "references": [_edge("reference:1", "object:child", "object:parent")],
                    "truncated": False,
                },
            )
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    result = await _structured_export(
        client,
        "http://core",
        {
            "source": {"kind": "selection", "object_ids": ["object:child"]},
            "format": "json",
            "fields": ["id"],
            "locales": ["de"],
            "options": {"include_ancestors": False},
        },
    )

    assert result["added_ancestors"] == 0
    assert result["skipped_references"] == 1
    # Only the source lookup itself (bare keys) — the parent is never fetched.
    assert asked == ["child"]


def test_mef_releases_are_copied_from_the_object_projection() -> None:
    assert _mef_releases({"releases": [{"channel": "webshop", "status": "published"}]}) == [
        {"channel": "webshop", "status": "published"}
    ]
    # The projection is only present with `?releases=true`, and partial rows are
    # dropped instead of producing half a release.
    assert _mef_releases({}) == []
    assert _mef_releases({"releases": [{"channel": "webshop"}, "kaputt"]}) == []


async def test_structured_export_carries_releases_and_asks_for_them() -> None:
    """A MEF object with a channel release: the query asks for releases, the
    document carries them (the import replays them)."""
    child = {
        "id": "object:child",
        "type": "variant",
        "status": "published",
        "attributes": {},
        "releases": [{"channel": "webshop", "status": "published"}],
    }
    requested: list[dict[str, str]] = []

    async def transport(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/objects":
            requested.append(dict(request.url.params))
            return httpx.Response(200, json=[child])
        if path in ("/attribute-definitions", "/languages", "/relation-definitions"):
            return httpx.Response(200, json=[])
        if path == "/references/query":
            return httpx.Response(200, json={"references": [], "truncated": False})
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    result = await _structured_export(
        client,
        "http://core",
        {
            "source": {"kind": "type", "type": "variant"},
            "format": "json",
            "fields": ["id"],
            "locales": ["de"],
            "options": {},
        },
    )

    assert requested[0]["releases"] == "true"
    assert result["document"]["objects"][0]["releases"] == [
        {"channel": "webshop", "status": "published"}
    ]
