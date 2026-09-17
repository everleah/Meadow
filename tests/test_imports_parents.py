"""Tests for the composition handling of the MEF import (parents first, fail closed).

An imported object must **never** end up on the universal root because its intended
parent is missing: the parent is written first (the document order does not have to
respect that) and the child is created *with* that parent in one write
(`POST /objects/batch-upsert` `parent`). If the parent is neither written in this
document nor present in the system, the object is not created at all and reported
as `failed` (measured 2026-09-17: before this, the object was created, hung on
`object:object_root` and the run still reported `success`).
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from meadow.worker.imports import (
    _composition_parents,
    _parents_first_levels,
    build_import_handler,
)
from meadow.worker.jobs import JobMessage

CORE = "http://127.0.0.1:8080"
DOCUMENT_ID = "import_document:doc-1"


def object_entry(identifier: str, object_type: str = "product") -> dict[str, Any]:
    return {
        "id": identifier,
        "type": object_type,
        "status": "draft",
        "attributes": {"sku": f"SKU-{identifier}"},
    }


def depends_on(child: str, parent: str) -> dict[str, Any]:
    return {"in": child, "out": parent, "type": "depends_on"}


class FakeCore:
    """Minimal core stand-in: records the writes and answers like the core."""

    def __init__(self, *, known_sources: dict[str, str] | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self.known_sources = known_sources or {}
        self.created = 0

    def handler(
        self,
        document: dict[str, Any],
        references_result: list[dict[str, Any]] | None = None,
    ):
        def handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            path = request.url.path
            if path.startswith("/imports/"):
                method = request.method
                if method == "GET":
                    return httpx.Response(200, json={"document": document})
                return httpx.Response(200, json={"ok": True})
            if path == "/rules":
                return httpx.Response(200, json=[])
            if path == "/objects/by-source":
                source_id = request.url.params.get("source_id") or ""
                resolved = self.known_sources.get(source_id)
                if resolved:
                    return httpx.Response(200, json={"id": resolved})
                return httpx.Response(404, json={"error": "not found"})
            if path == "/objects/batch-upsert":
                payload = json.loads(request.content)
                results = []
                for index, _ in enumerate(payload.get("objects") or []):
                    self.created += 1
                    results.append(
                        {
                            "index": index,
                            "id": f"object:made-{self.created}",
                            "action": "created",
                        }
                    )
                return httpx.Response(
                    200,
                    json={
                        "created": len(results),
                        "updated": 0,
                        "failed": 0,
                        "results": results,
                    },
                )
            if path == "/references/batch":
                return httpx.Response(
                    200,
                    json={
                        "created": 0,
                        "existing": 0,
                        "reparented": 0,
                        "failed": 0,
                        "results": references_result or [],
                    },
                )
            return httpx.Response(200, json={})

        return handle

    def batch_bodies(self) -> list[dict[str, Any]]:
        return [
            json.loads(request.content)
            for request in self.requests
            if request.url.path == "/objects/batch-upsert"
        ]

    def one_batch(self) -> dict[str, Any]:
        bodies = self.batch_bodies()
        assert len(bodies) == 1, f"expected one write batch, got {len(bodies)}"
        return bodies[0]


async def run_import(
    document: dict[str, Any],
    *,
    known_sources: dict[str, str] | None = None,
    parameters: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], FakeCore]:
    core = FakeCore(known_sources=known_sources)
    transport = httpx.MockTransport(core.handler(document))
    async with httpx.AsyncClient(transport=transport) as client:
        # The injected client is the mock transport; without it the handler would
        # open a real connection to the core URL.
        handler = build_import_handler(CORE, api_key="test-key", client=client)
        result = await handler(
            JobMessage(
                run_id="job_run:run-1",
                type="inbound_import",
                parameters={
                    "document_id": DOCUMENT_ID,
                    "match_by_key": "sku",
                    "source": "test-source",
                    **(parameters or {}),
                },
            )
        )
    return result, core


# --------------------------------------------------------------------------- #
# pure helpers
# --------------------------------------------------------------------------- #


def test_composition_parents_reads_only_depends_on() -> None:
    objects = [object_entry("a"), object_entry("b")]
    references = [
        depends_on("b", "a"),
        {"in": "b", "out": "c", "type": "part_group_member"},
        {"in": "not-in-document", "out": "a", "type": "depends_on"},
        {"in": "b", "out": "a", "type": "rel_depends_on"},
    ]
    parents, conflicts = _composition_parents(objects, references)
    assert parents == {"b": "a"}
    assert conflicts == {}


def test_conflicting_composition_parents_are_reported() -> None:
    objects = [object_entry("child"), object_entry("p1"), object_entry("p2")]
    parents, conflicts = _composition_parents(
        objects, [depends_on("child", "p1"), depends_on("child", "p2")]
    )
    assert parents == {"child": "p1"}
    assert "child" in conflicts
    assert "p1" in conflicts["child"] and "p2" in conflicts["child"]


def test_parents_first_levels_write_the_tree_breadth_first() -> None:
    objects = [object_entry("grandchild"), object_entry("child"), object_entry("parent")]
    parents = {"child": "parent", "grandchild": "child"}
    levels, errors = _parents_first_levels(objects, parents)
    assert errors == {}
    written = [[objects[index]["id"] for index in level] for level in levels]
    assert written == [["parent"], ["child"], ["grandchild"]]


def test_parents_first_levels_keep_unrelated_documents_in_one_level() -> None:
    objects = [object_entry("a"), object_entry("b"), object_entry("c")]
    levels, errors = _parents_first_levels(objects, {})
    assert errors == {}
    assert [[objects[index]["id"] for index in level] for level in levels] == [["a", "b", "c"]]


def test_siblings_share_their_level() -> None:
    """One parent with two children: two levels, siblings in the same write batch."""
    objects = [object_entry("p"), object_entry("c1"), object_entry("c2")]
    levels, errors = _parents_first_levels(objects, {"c1": "p", "c2": "p"})
    assert errors == {}
    assert [[objects[index]["id"] for index in level] for level in levels] == [["p"], ["c1", "c2"]]


def test_a_cycle_inside_the_document_is_reported_and_leaves_the_rest_ordered() -> None:
    objects = [object_entry("x"), object_entry("y"), object_entry("z")]
    levels, errors = _parents_first_levels(objects, {"x": "y", "y": "x"})
    assert set(errors) == {"x", "y"}
    assert "cycle" in errors["x"]
    written = [objects[index]["id"] for level in levels for index in level]
    assert written == ["z", "x", "y"]


def test_a_duplicate_id_is_reported_for_every_occurrence() -> None:
    """A document that names the same id twice is ambiguous: nothing is written."""
    objects = [object_entry("dup"), object_entry("other"), object_entry("dup")]
    levels, errors = _parents_first_levels(objects, {})
    assert set(errors) == {"dup"}
    assert "duplicate" in errors["dup"]
    # Every index is visited (the second occurrence is not silently dropped).
    assert [objects[index]["id"] for level in levels for index in level] == [
        "dup",
        "other",
        "dup",
    ]


# --------------------------------------------------------------------------- #
# handler behaviour
# --------------------------------------------------------------------------- #


async def test_parent_is_written_before_the_child_and_passed_to_the_write() -> None:
    """The document lists the variant first; the parent has to be written first."""
    document = {
        "source": "test-source",
        "objects": [object_entry("variant-1", "variant"), object_entry("product-1")],
        "references": [depends_on("variant-1", "product-1")],
    }
    result, core = await run_import(document)

    assert result["failed"] == 0
    # Two writes: the parent is flushed before its child, because the child needs
    # the parent's record id.
    batches = core.batch_bodies()
    assert [entry["attributes"]["sku"] for body in batches for entry in body["objects"]] == [
        "SKU-product-1",
        "SKU-variant-1",
    ], "parents first"
    assert "parent" not in batches[0]["objects"][0]
    assert batches[1]["objects"][0]["parent"] == "object:made-1"
    # The report keeps the document's order, not the write order.
    assert [row["id"] for row in result["results"]] == ["object:made-2", "object:made-1"]


async def test_an_object_whose_parent_is_missing_is_not_created() -> None:
    """A dangling `depends_on` parent must fail the object, not park it on the root."""
    document = {
        "source": "test-source",
        "objects": [object_entry("variant-1", "variant")],
        "references": [depends_on("variant-1", "nowhere")],
    }
    result, core = await run_import(document)

    assert core.batch_bodies() == [], "the object must not be written"
    assert result["created"] == 0
    assert result["failed"] == 1
    assert result["results"][0]["status"] == "failed"
    assert "neither written in this document nor present in the system" in str(
        result["results"][0]["error"]
    )


async def test_a_parent_that_exists_in_the_system_is_used() -> None:
    document = {
        "source": "test-source",
        "objects": [object_entry("variant-1", "variant")],
        "references": [depends_on("variant-1", "product-1")],
    }
    result, core = await run_import(document, known_sources={"product-1": "object:existing"})

    assert result["failed"] == 0
    batch = core.one_batch()
    assert batch["objects"][0]["parent"] == "object:existing"
    assert [request.url.path for request in core.requests].count("/objects/by-source") == 1


async def test_children_of_a_failed_parent_are_skipped_too() -> None:
    """The failure cascades: a child of a skipped object cannot be created either."""
    document = {
        "source": "test-source",
        "objects": [
            object_entry("parent-1"),
            object_entry("child-1", "variant"),
            object_entry("grandchild-1", "variant"),
        ],
        "references": [
            depends_on("parent-1", "nowhere"),
            depends_on("child-1", "parent-1"),
            depends_on("grandchild-1", "child-1"),
        ],
    }
    result, core = await run_import(document)

    assert core.batch_bodies() == [], "nothing may be written"
    assert result["failed"] == 3
    assert [row["status"] for row in result["results"]] == ["failed"] * 3


async def test_a_cycle_in_the_document_writes_neither_object() -> None:
    document = {
        "source": "test-source",
        "objects": [object_entry("a"), object_entry("b")],
        "references": [depends_on("a", "b"), depends_on("b", "a")],
    }
    result, core = await run_import(document)

    assert core.batch_bodies() == []
    assert result["failed"] == 2
    assert all("cycle" in str(row["error"]) for row in result["results"])


@pytest.mark.parametrize("parent_mef_id", ["missing-parent"])
async def test_the_failed_parent_lookup_runs_once(parent_mef_id: str) -> None:
    """Two children of the same unknown parent trigger one lookup, not two.

    The two extra lookups are for the children themselves: they were not written,
    so the reference phase cannot resolve their `in` endpoint either. What matters
    is that the *parent* is looked up once (shared negative cache) even though this
    run asks about it from both phases.
    """
    document = {
        "source": "test-source",
        "objects": [object_entry("c1", "variant"), object_entry("c2", "variant")],
        "references": [depends_on("c1", parent_mef_id), depends_on("c2", parent_mef_id)],
    }
    result, core = await run_import(document)

    assert result["failed"] == 2
    lookups = [
        request.url.params.get("source_id")
        for request in core.requests
        if request.url.path == "/objects/by-source"
    ]
    assert lookups.count(parent_mef_id) == 1
    assert sorted(lookups) == sorted([parent_mef_id, "c1", "c2"])
