"""Tests for the export wizard's attribute-definition load (`/export`).

The wizard offers an attribute selection over **every** definition. With an
imported classification standard that is 17 379 definitions (ETIM 10.0), so the
load must not page through `GET /attribute-definitions` in 1000-row chunks —
measured: 18 requests / ~9 s. `load_attribute_definitions` therefore asks the
core once (`GET /attribute-definitions/vocabulary`, the slim projection served
from the core's metadata cache) and keeps the paged load as the fallback for a
core without that endpoint.

See `plan/detail/datenexport-import-ui.md` and `meadow/AGENTS.md`.
"""

from __future__ import annotations

import httpx
import pytest

from meadow.frontend import api as frontend_api
from meadow.frontend.api import CoreClient
from meadow.frontend.export_page import DEFINITION_PAGE, load_attribute_definitions


@pytest.fixture
def vocabulary_requests(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    """Captures the requests while answering the vocabulary endpoint."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/vocabulary"):
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
        return httpx.Response(200, json=[])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False)
    monkeypatch.setattr(frontend_api, "_shared_client", client)
    return seen


@pytest.fixture
def legacy_core_requests(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    """Answers 404 on the vocabulary endpoint and pages the list endpoint."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/vocabulary"):
            return httpx.Response(404, json={"error": "not found"})
        start = int(request.url.params.get("start", "0"))
        # A full page then a short one: the loader must stop at the short page.
        rows = [{"name": f"attr-{start + index}"} for index in range(DEFINITION_PAGE)]
        if start > 0:
            rows = rows[:7]
        return httpx.Response(200, json=rows)

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


async def test_paged_load_is_the_fallback(legacy_core_requests: list[httpx.Request]) -> None:
    client = CoreClient("http://127.0.0.1:8080")

    definitions = await load_attribute_definitions(client, "token")

    # 404 on the vocabulary endpoint, then one full page + one short page.
    assert [request.url.path for request in legacy_core_requests] == [
        "/attribute-definitions/vocabulary",
        "/attribute-definitions",
        "/attribute-definitions",
    ]
    assert definitions[0]["name"] == "attr-0"
    assert definitions[-1]["name"] == "attr-1006"
    assert len(definitions) == DEFINITION_PAGE + 7
    start_params = [request.url.params["start"] for request in legacy_core_requests[1:]]
    assert start_params == ["0", str(DEFINITION_PAGE)]
