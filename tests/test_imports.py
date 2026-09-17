"""Tests for the MEF reference import (`meadow/worker/imports.py`).

Pins the write path of `_import_references`: a batch of edges goes to
`POST /references/batch` in **one** request (the per-edge round trips dominated an
import — `plan/detail/demodaten-generator.md` §4.3), plain aggregation edges as
`references`, `depends_on` as `reparents` (the composition invariant allows
exactly one parent edge, and a new object is attached to the universal root on
create). An object that has **no** composition edge cannot be moved, so a rejected
`depends_on` move is re-applied as the initial attach. The endpoint answers per
entry, so one bad edge never fails the rest.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from meadow.worker import imports as imports_module
from meadow.worker.imports import _import_references

CORE = "http://127.0.0.1:8080"

IDS = {"child": "object:child", "parent": "object:parent", "other": "object:other"}


def batch_body(request: httpx.Request) -> dict[str, Any]:
    """The decoded body of a `/references/batch` request."""
    return json.loads(request.content)


def core_like_response(
    payload: dict[str, Any],
    *,
    existing_targets: set[str] | None = None,
    failing_targets: set[str] | None = None,
    unattached_sources: set[str] | None = None,
) -> httpx.Response:
    """Answers a batch request like the core does (one result per entry).

    `unattached_sources` are the objects without a composition edge: their
    `depends_on` move is rejected, their initial attach succeeds.
    """
    existing_targets = existing_targets or set()
    failing_targets = failing_targets or set()
    unattached_sources = unattached_sources or set()
    results: list[dict[str, Any]] = []
    counts = {"created": 0, "existing": 0, "reparented": 0, "failed": 0}
    for index, item in enumerate(payload.get("references") or []):
        target = str(item.get("target"))
        if target in failing_targets:
            action, error = "failed", "edge rejected"
        elif target in existing_targets:
            action, error = "existing", None
        else:
            action, error = "created", None
        counts[action] += 1
        entry: dict[str, Any] = {
            "kind": "reference",
            "index": index,
            "source": item.get("source"),
            "target": target,
            "action": action,
        }
        if error:
            entry["error"] = error
        results.append(entry)
    for index, item in enumerate(payload.get("reparents") or []):
        if str(item.get("source")) in unattached_sources:
            counts["failed"] += 1
            results.append(
                {
                    "kind": "reparent",
                    "index": index,
                    "source": item.get("source"),
                    "target": item.get("parent"),
                    "action": "failed",
                    "error": "object has no composition parent",
                }
            )
            continue
        counts["reparented"] += 1
        results.append(
            {
                "kind": "reparent",
                "index": index,
                "source": item.get("source"),
                "target": item.get("parent"),
                "action": "reparented",
            }
        )
    return httpx.Response(200, json={**counts, "results": results})


def handler(
    requests: list[httpx.Request],
    *,
    existing_targets: set[str] | None = None,
    failing_targets: set[str] | None = None,
    unattached_sources: set[str] | None = None,
    status: int | None = None,
    error: str = "boom",
    by_source: dict[str, str] | None = None,
):
    """Builds a MockTransport handler that records requests and mimics the core."""

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/objects/by-source":
            source_id = request.url.params.get("source_id") or ""
            resolved = (by_source or {}).get(source_id)
            if resolved:
                return httpx.Response(200, json={"id": resolved})
            return httpx.Response(404, json={"error": "not found"})
        if request.url.path == "/references/batch":
            if status is not None:
                return httpx.Response(status, json={"error": error})
            return core_like_response(
                batch_body(request),
                existing_targets=existing_targets,
                failing_targets=failing_targets,
                unattached_sources=unattached_sources,
            )
        return httpx.Response(200, json={"ok": True})

    return handle


async def run(
    references: list[dict[str, Any]],
    *,
    emit_events: bool = False,
    ids: dict[str, str] | None = None,
    source_label: str | None = None,
    **kwargs: Any,
):
    requests: list[httpx.Request] = []
    transport = httpx.MockTransport(handler(requests, **kwargs))
    async with httpx.AsyncClient(transport=transport) as client:
        results = await _import_references(
            client,
            CORE,
            references,
            dict(IDS if ids is None else ids),
            emit_events,
            source_label,
        )
    return results, requests


def batch_requests(requests: list[httpx.Request]) -> list[httpx.Request]:
    return [request for request in requests if request.url.path == "/references/batch"]


async def test_depends_on_is_applied_as_a_reparent() -> None:
    results, requests = await run([{"in": "child", "out": "parent", "type": "depends_on"}])
    assert [result["status"] for result in results] == ["reparented"]
    assert len(requests) == 1, "one request per batch, not per edge"
    request = requests[0]
    assert request.method == "POST"
    assert request.url.path == "/references/batch"
    body = batch_body(request)
    assert body["references"] == []
    assert body["reparents"] == [{"source": "object:child", "parent": "object:parent"}]
    assert request.url.params.get("emit_events") == "false"


async def test_prefixed_composition_definition_is_recognised() -> None:
    results, requests = await run([{"in": "child", "out": "parent", "type": "rel_depends_on"}])
    assert results[0]["status"] == "reparented"
    assert batch_body(requests[0])["reparents"] == [
        {"source": "object:child", "parent": "object:parent"}
    ]


async def test_aggregation_edges_share_one_batch_request() -> None:
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
    assert len(requests) == 1
    body = batch_body(requests[0])
    assert body["reparents"] == []
    assert body["references"] == [
        {
            "source": "object:child",
            "target": "object:parent",
            "definition_id": "rel_related",
            "attributes": {},
        },
        {
            "source": "object:child",
            "target": "object:other",
            "definition_id": "rel_part_group_member",
            "attributes": {"position": "front"},
        },
    ]


async def test_references_and_reparents_travel_in_one_request() -> None:
    """A chunk sends both kinds together: one round trip for the whole batch."""
    results, requests = await run(
        [
            {"in": "child", "out": "parent", "type": "depends_on"},
            {"in": "child", "out": "other", "type": "related"},
        ]
    )
    assert [result["status"] for result in results] == ["reparented", "created"]
    assert len(requests) == 1
    body = batch_body(requests[0])
    assert len(body["reparents"]) == 1
    assert len(body["references"]) == 1


async def test_a_batch_is_split_into_batches_of_the_configured_size(monkeypatch) -> None:
    monkeypatch.setattr(imports_module, "BATCH_REFERENCE_SIZE", 2)
    results, requests = await run(
        [
            {"in": "child", "out": "parent", "type": "related"},
            {"in": "child", "out": "other", "type": "related"},
            {"in": "parent", "out": "other", "type": "related"},
        ]
    )
    assert [result["status"] for result in results] == ["created"] * 3
    batches = batch_requests(requests)
    assert [len(batch_body(batch)["references"]) for batch in batches] == [2, 1]


async def test_emit_events_true_drops_the_quiet_parameter() -> None:
    _, requests = await run([{"in": "child", "out": "other", "type": "related"}], emit_events=True)
    assert requests[0].url.params.get("emit_events") is None


async def test_unresolved_endpoints_are_reported_per_reference() -> None:
    results, requests = await run(
        [
            {"in": "child", "out": "missing", "type": "related"},
            {"in": "child", "out": "parent", "type": "related"},
        ]
    )
    assert results[0]["status"] == "failed"
    assert "cannot resolve reference" in results[0]["error"]
    assert results[1]["status"] == "created"
    batches = batch_requests(requests)
    assert len(batches) == 1
    assert len(batch_body(batches[0])["references"]) == 1, "the unresolvable edge must not be sent"


async def test_existing_edge_is_reported_as_existing() -> None:
    """A repeated import must stay idempotent: an existing edge is not an error."""
    results, _ = await run(
        [
            {"in": "child", "out": "other", "type": "related"},
            {"in": "child", "out": "parent", "type": "depends_on"},
        ],
        existing_targets={"object:other"},
    )
    assert [result["status"] for result in results] == ["existing", "reparented"]
    assert "error" not in results[0]


async def test_per_entry_failures_are_mapped_by_position() -> None:
    """The core answers per entry; a rejected edge does not fail its neighbours."""
    results, _ = await run(
        [
            {"in": "child", "out": "parent", "type": "related"},
            {"in": "child", "out": "other", "type": "related"},
        ],
        failing_targets={"object:other"},
    )
    assert [result["status"] for result in results] == ["created", "failed"]
    assert results[1]["error"] == "edge rejected"


async def test_a_rejected_batch_is_reported_per_entry() -> None:
    """A `409`/`500` on the whole batch stays retryable: every entry is `failed`.

    The core maps a SurrealDB transaction conflict to `409` (the honest answer for
    a lost race), so the batch is rejected as a whole. Treating that as success
    would silently drop the edges; reporting them as `failed` lets the retry pass
    re-apply exactly those references.
    """
    results, requests = await run(
        [
            {"in": "child", "out": "other", "type": "related"},
            {"in": "child", "out": "parent", "type": "depends_on"},
        ],
        status=409,
        error="concurrent write conflict, please retry: failed transaction",
    )
    assert [result["status"] for result in results] == ["failed", "failed"]
    assert all("conflict" in str(result["error"]) for result in results)
    assert len(batch_requests(requests)) == 1


async def test_missing_endpoint_is_resolved_via_its_source_reference() -> None:
    """An endpoint that this document's map lacks is looked up by source reference.

    A shared object of a chunked import exists in several documents; if its own
    entry failed (a concurrent write) or another chunk is importing it right now,
    the object still exists. Without the lookup every reference to it fails with
    "cannot resolve" and the retry pass cannot repair those edges (found live:
    one conflicted category cost 17 `category_member` edges).
    """
    results, requests = await run(
        [
            {"in": "product", "out": "missing-category", "type": "category_member"},
            {"in": "product", "out": "missing-category", "type": "part_group_member"},
            {"in": "product", "out": "unknown-category", "type": "category_member"},
        ],
        ids={"product": "object:product"},
        source_label="meadow-demo",
        by_source={"missing-category": "object:category"},
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
    body = batch_body(batch_requests(requests)[0])
    assert [item["target"] for item in body["references"]] == [
        "object:category",
        "object:category",
    ]


@pytest.mark.parametrize("reference_type", ["", None])
async def test_missing_reference_type_is_reported(reference_type: str | None) -> None:
    results, requests = await run([{"in": "child", "out": "other", "type": reference_type}])
    assert results[0]["status"] == "failed"
    assert "missing `type`" in results[0]["error"]
    assert requests == []


async def test_an_edgeless_object_is_attached_instead_of_moved() -> None:
    """A `depends_on` entry for an object without a composition edge is an attach.

    Found live 2026-09-17: 65 demo variants had no composition edge at all (their
    edge had been removed, the object itself is live). The core rejects the move
    with "object has no composition parent" — correct for a move, wrong for a MEF
    document, which states the desired parent. Because every later import sent the
    same move, the objects stayed parent-less forever: invisible in the structure
    tree and counted as top-level nodes. The entry is re-applied as the initial
    attach (the `references` side of the endpoint) instead.
    """
    results, requests = await run(
        [{"in": "child", "out": "parent", "type": "depends_on"}],
        unattached_sources={"object:child"},
    )
    assert [result["status"] for result in results] == ["created"]
    batches = batch_requests(requests)
    assert len(batches) == 2, "the rejected move is retried as an attach"
    assert batch_body(batches[0]) == {
        "references": [],
        "reparents": [{"source": "object:child", "parent": "object:parent"}],
    }
    assert batch_body(batches[1]) == {
        "references": [
            {
                "source": "object:child",
                "target": "object:parent",
                "definition_id": "rel_depends_on",
                "attributes": {},
            }
        ],
        "reparents": [],
    }


async def test_only_the_edgeless_entries_go_into_the_attach_request() -> None:
    """Entries whose move succeeded stay untouched, and a rejected attach stays failed."""
    results, requests = await run(
        [
            {"in": "parent", "out": "other", "type": "depends_on"},
            {"in": "child", "out": "parent", "type": "depends_on"},
        ],
        unattached_sources={"object:parent"},
        failing_targets={"object:other"},
    )
    assert [result["status"] for result in results] == ["failed", "reparented"]
    assert results[0]["error"] == "edge rejected"
    batches = batch_requests(requests)
    assert [item["source"] for item in batch_body(batches[0])["reparents"]] == [
        "object:parent",
        "object:child",
    ]
    assert [item["source"] for item in batch_body(batches[1])["references"]] == ["object:parent"]
