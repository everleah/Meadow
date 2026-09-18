"""Tests for the export wizard's attribute-definition load and its recent list (`/export`).

The wizard offers an attribute selection over **every** definition. With an
imported classification standard that is 17 397 definitions (ETIM 10.0), so the
load must not page through `GET /attribute-definitions` in 1000-row chunks — that
cost 18 requests / 4.5 s and a capped page silently truncated the selection.
`load_attribute_definitions` asks the core once
(`GET /attribute-definitions/vocabulary`, the slim projection served from the
core's metadata cache) and has **no** paged fallback: core and frontend ship
together, so the endpoint the wizard needs always exists.

`recent_exports` reads the **summary** of a run, never its result. The export page
asked for the newest 100 runs *with* results; that answer was 20 MB and the next
call reset the SurrealDB connection (`500 Connection reset`, 2026-09-18). The rule
the helper encodes: a listing describes a run from `result_summary`, and a run
without one stays out of the list rather than loading a payload.

See `plan/detail/kategorisierung.md`, `plan/detail/datenexport-import-ui.md` and
`meadow/AGENTS.md`.
"""

from __future__ import annotations

import httpx
import pytest

from meadow.frontend import api as frontend_api
from meadow.frontend.api import CoreClient
from meadow.frontend.export_page import load_attribute_definitions, recent_exports


@pytest.fixture
def vocabulary_requests(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    """Captures the requests while answering the vocabulary endpoint."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json=[
                {
                    "id": "attribute_definition:ef000001",
                    "name": "length",
                    "multilingual": False,
                },
                {"id": "attribute_definition:ef000002", "name": "width", "multilingual": True},
            ],
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False)
    monkeypatch.setattr(frontend_api, "_shared_client", client)
    return seen


async def test_vocabulary_is_loaded_in_one_request(
    vocabulary_requests: list[httpx.Request],
) -> None:
    client = CoreClient("http://127.0.0.1:8080")

    definitions = await load_attribute_definitions(client, "token")

    assert [row["name"] for row in definitions] == ["length", "width"]
    assert len(vocabulary_requests) == 1, "the whole vocabulary costs one request"
    assert vocabulary_requests[0].url.path == "/attribute-definitions/vocabulary"


async def test_missing_vocabulary_endpoint_is_a_hard_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No silent paging: a core without the endpoint fails loudly."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(404, json={"error": "not found"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False)
    monkeypatch.setattr(frontend_api, "_shared_client", client)

    with pytest.raises(httpx.HTTPStatusError):
        await load_attribute_definitions(CoreClient("http://127.0.0.1:8080"), "token")
    assert seen == ["/attribute-definitions/vocabulary"]


def test_recent_exports_reads_the_summary_of_export_runs() -> None:
    runs: list[dict] = [
        {
            "id": "job_run:export1",
            "trigger": "manual",
            "created_at": "2026-09-18T09:00:00Z",
            "result_summary": {"format": "bmecat", "exported": 120},
        },
        {
            "id": "job_run:import1",
            "trigger": "manual",
            "created_at": "2026-09-18T08:00:00Z",
            "result_summary": {"processed": 40, "created": 40},
        },
    ]

    exports = recent_exports(runs)

    assert exports == [
        {
            "id": "job_run:export1",
            "format": "bmecat",
            "exported": 120,
            "created_at": "2026-09-18T09:00:00Z",
            "trigger": "manual",
        }
    ]


def test_recent_exports_never_needs_the_result_payload() -> None:
    """A run whose report was recorded before the core wrote summaries stays out.

    Loading its `result` to find out whether it was an export is exactly what the
    box must not do (that payload is what made a listing reset the DB connection).
    """
    runs: list[dict] = [
        {
            "id": "job_run:old",
            "trigger": "manual",
            "created_at": "2026-09-17T09:00:00Z",
            "result": {"format": "csv", "exported": 5000, "document": {"objects": []}},
        },
        {"id": "job_run:running", "created_at": "2026-09-18T09:00:00Z", "status": "running"},
    ]

    assert recent_exports(runs) == []


def test_recent_exports_keeps_a_zero_count() -> None:
    """`exported: 0` is a result (an empty source), not a missing value."""
    runs: list[dict] = [{"id": "job_run:e", "result_summary": {"format": "csv", "exported": 0}}]

    assert recent_exports(runs)[0]["exported"] == 0
