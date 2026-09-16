"""Tests for the MEF reference import (`meadow/worker/imports.py`).

Pins the two write paths of `_import_references`: plain aggregation edges go to
`POST /references`, while `depends_on` is applied as a **re-parenting** (the
composition invariant allows exactly one parent edge, and a new object is
attached to the universal root on create).
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from meadow.worker.imports import _import_references

CORE = "http://127.0.0.1:8080"


def handler(requests: list[httpx.Request], *, fail: str | None = None):
    """Builds a MockTransport handler that records the requests it receives."""

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if fail and fail in str(request.url):
            return httpx.Response(400, json={"error": "boom"})
        return httpx.Response(200, json={"ok": True})

    return handle


async def run(references: list[dict[str, Any]], *, emit_events: bool = False):
    requests: list[httpx.Request] = []
    transport = httpx.MockTransport(handler(requests))
    async with httpx.AsyncClient(transport=transport) as client:
        results = await _import_references(
            client,
            CORE,
            references,
            {"child": "object:child", "parent": "object:parent", "other": "object:other"},
            emit_events,
        )
    return results, requests


async def test_depends_on_is_applied_as_reparenting() -> None:
    results, requests = await run([{"in": "child", "out": "parent", "type": "depends_on"}])
    assert [result["status"] for result in results] == ["reparented"]
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "PATCH"
    assert str(request.url).startswith(f"{CORE}/objects/object:child/parent")
    assert json.loads(request.content) == {"parent": "object:parent"}
    assert request.url.params.get("emit_events") == "false"


async def test_prefixed_composition_definition_is_recognised() -> None:
    results, requests = await run([{"in": "child", "out": "parent", "type": "rel_depends_on"}])
    assert results[0]["status"] == "reparented"
    assert requests[0].method == "PATCH"


async def test_aggregation_edges_still_use_the_reference_endpoint() -> None:
    results, requests = await run(
        [
            {"in": "child", "out": "parent", "type": "related"},
            {
                "in": "child",
                "out": "other",
                "type": "part_group_member",
                "attributes": {"position": "front"},
            },
        ]
    )
    assert [result["status"] for result in results] == ["created", "created"]
    assert all(request.method == "POST" for request in requests)
    assert all(str(request.url).startswith(f"{CORE}/references") for request in requests)
    body = json.loads(requests[1].content)
    assert body["definition_id"] == "rel_part_group_member"
    assert body["attributes"] == {"position": "front"}


async def test_emit_events_true_drops_the_quiet_parameter() -> None:
    _, requests = await run([{"in": "child", "out": "other", "type": "related"}], emit_events=True)
    assert requests[0].url.params.get("emit_events") is None


async def test_unresolved_endpoints_are_reported_per_reference() -> None:
    results, requests = await run(
        [
            {"in": "child", "out": "missing", "type": "related"},
            {"in": "child", "out": "parent", "type": "depends_on"},
        ]
    )
    assert results[0]["status"] == "failed"
    assert "cannot resolve reference" in results[0]["error"]
    assert results[1]["status"] == "reparented"
    assert len(requests) == 1, "the failed reference must not send a request"


async def test_failures_are_isolated_per_reference() -> None:
    requests: list[httpx.Request] = []
    transport = httpx.MockTransport(handler(requests, fail="/objects/"))
    async with httpx.AsyncClient(transport=transport) as client:
        results = await _import_references(
            client,
            CORE,
            [
                {"in": "child", "out": "parent", "type": "depends_on"},
                {"in": "child", "out": "other", "type": "related"},
            ],
            {"child": "object:child", "parent": "object:parent", "other": "object:other"},
            False,
        )
    assert results[0]["status"] == "failed"
    assert results[1]["status"] == "created"


async def test_existing_edge_is_reported_as_existing() -> None:
    """The core rejects a duplicate edge (`409`); the import must not fail on it.

    A repeated MEF import stays idempotent that way: the reference exists already,
    which is the expected outcome, not an error
    (`plan/detail/demodaten-generator.md` §9).
    """
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if str(request.url).startswith(f"{CORE}/references"):
            return httpx.Response(409, json={"error": "already exists"})
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handle)
    async with httpx.AsyncClient(transport=transport) as client:
        results = await _import_references(
            client,
            CORE,
            [
                {"in": "child", "out": "other", "type": "related"},
                {"in": "child", "out": "parent", "type": "depends_on"},
            ],
            {"child": "object:child", "parent": "object:parent", "other": "object:other"},
            False,
        )
    assert [result["status"] for result in results] == ["existing", "reparented"]
    assert "error" not in results[0]


async def test_concurrent_write_conflict_is_retryable() -> None:
    """`409` also means "concurrent write conflict" — that one must not be swallowed.

    The core maps a SurrealDB transaction conflict to `409` (the honest answer for
    a lost race). Treating every `409` as "edge exists already" would silently drop
    the reference, so the conflict is reported as `failed` and the importer's retry
    pass re-applies it.
    """

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409,
            json={"error": "concurrent write conflict, please retry: failed transaction"},
        )

    transport = httpx.MockTransport(handle)
    async with httpx.AsyncClient(transport=transport) as client:
        results = await _import_references(
            client,
            CORE,
            [{"in": "child", "out": "other", "type": "related"}],
            {"child": "object:child", "other": "object:other"},
            False,
        )
    assert results[0]["status"] == "failed"
    assert "conflict" in results[0]["error"]


async def test_missing_endpoint_is_resolved_via_its_source_reference() -> None:
    """An endpoint that this document's map lacks is looked up by source reference.

    A shared object of a chunked import exists in several documents; if its own
    entry failed (a concurrent write) or another chunk is importing it right now,
    the object still exists. Without the lookup every reference to it fails with
    "cannot resolve" and the retry pass cannot repair those edges (found live:
    one conflicted category cost 17 `category_member` edges).
    """
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/objects/by-source":
            source_id = request.url.params.get("source_id")
            if source_id == "missing-category":
                return httpx.Response(200, json={"id": "object:category"})
            return httpx.Response(404, json={"error": "not found"})
        if request.url.path == "/references":
            return httpx.Response(201, json={"id": "reference:1"})
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handle)
    async with httpx.AsyncClient(transport=transport) as client:
        results = await _import_references(
            client,
            CORE,
            [
                {"in": "product", "out": "missing-category", "type": "category_member"},
                {"in": "product", "out": "missing-category", "type": "part_group_member"},
                {"in": "product", "out": "unknown-category", "type": "category_member"},
            ],
            {"product": "object:product"},
            False,
            "meadow-demo",
        )
    assert [result["status"] for result in results] == ["created", "created", "failed"]
    lookups = [
        request.url.params.get("source_id")
        for request in requests
        if request.url.path == "/objects/by-source"
    ]
    # Once per unknown id (cached in the map), not once per reference.
    assert lookups == ["missing-category", "unknown-category"]
    assert results[2]["out"] == "unknown-category"


@pytest.mark.parametrize("reference_type", ["", None])
async def test_missing_reference_type_is_reported(reference_type: str | None) -> None:
    results, requests = await run([{"in": "child", "out": "other", "type": reference_type}])
    assert results[0]["status"] == "failed"
    assert "missing `type`" in results[0]["error"]
    assert requests == []
