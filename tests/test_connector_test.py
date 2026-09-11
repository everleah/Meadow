"""Tests for the `connector_test` job handler (Stage 2c)."""

from __future__ import annotations

import json
from typing import Any

import httpx

from meadow.worker.connector_test import (
    build_connector_test_handler,
    excerpt,
    synthetic_event,
)
from meadow.worker.jobs import JobMessage


def message(parameters: dict[str, Any]) -> JobMessage:
    return JobMessage.model_validate(
        {
            "run_id": "job_run:test",
            "type": "connector_test",
            "parameters": parameters,
        }
    )


async def run_with(transport: Any, parameters: dict[str, Any]) -> dict[str, Any]:
    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    try:
        handler = build_connector_test_handler(client=client)
        return await handler(message(parameters))
    finally:
        await client.aclose()


def webhook_parameters(**overrides: Any) -> dict[str, Any]:
    parameters: dict[str, Any] = {"url": "https://middleware.example/events"}
    parameters.update(overrides)
    return parameters


def test_synthetic_event_is_marked_as_a_test() -> None:
    event = synthetic_event("Middleware")
    assert event["test"] is True
    assert event["event_id"] == "connector_test"
    assert event["connector"] == "Middleware"
    assert event["event_type"] == "object.updated"


async def test_successful_webhook_test_reports_status_and_latency() -> None:
    seen: list[httpx.Request] = []

    async def transport(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text="  accepted\n by middleware  ")

    result = await run_with(
        transport,
        {
            "name": "Middleware",
            "connector_type": "webhook",
            "parameters": webhook_parameters(headers={"Authorization": "Bearer x"}, method="PUT"),
        },
    )

    assert result["ok"] is True
    assert result["status_code"] == 200
    assert result["latency_ms"] >= 0
    assert result["excerpt"] == "accepted by middleware"
    assert result["url"] == "https://middleware.example/events"
    assert seen[0].method == "PUT"
    assert seen[0].headers["authorization"] == "Bearer x"
    assert json.loads(seen[0].content)["test"] is True


async def test_rejected_webhook_test_reports_the_status() -> None:
    async def transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="invalid token")

    result = await run_with(
        transport,
        {"name": "Middleware", "connector_type": "webhook", "parameters": webhook_parameters()},
    )

    assert result["ok"] is False
    assert result["status_code"] == 401
    assert result["reachable"] is True
    assert result["excerpt"] == "invalid token"


async def test_unreachable_webhook_test_reports_the_error() -> None:
    async def transport(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    result = await run_with(
        transport,
        {"name": "Middleware", "connector_type": "webhook", "parameters": webhook_parameters()},
    )

    assert result["ok"] is False
    assert result["reachable"] is False
    assert "ConnectError" in result["error"]


async def test_webhook_without_url_is_reported() -> None:
    async def transport(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        return httpx.Response(200)

    result = await run_with(
        transport,
        {"name": "Broken", "connector_type": "webhook", "parameters": {}},
    )

    assert result["ok"] is False
    assert "URL is required" in result["error"]


async def test_import_profile_is_a_dry_run() -> None:
    async def transport(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("a profile test must not send a request")

    result = await run_with(
        transport,
        {
            "name": "ERP import",
            "connector_type": "json_import",
            "parameters": {"match_by_key": "sku", "emit_events": False, "source": "erp"},
        },
    )

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["resolved"] == {
        "match_by_key": "sku",
        "match_by_id": False,
        "emit_events": False,
        "source": "erp",
    }


async def test_export_profile_falls_back_to_defaults() -> None:
    async def transport(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("a profile test must not send a request")

    result = await run_with(
        transport,
        {
            "name": "Export",
            "connector_type": "json_export",
            "parameters": {"format": "xlsx", "options": {"email_to": ["ops@example.com"]}},
        },
    )

    assert result["resolved"]["format"] == "xlsx"
    assert result["resolved"]["email_to"] == ["ops@example.com"]
    assert result["resolved"]["fields"] == []


async def test_unknown_connector_type_is_reported() -> None:
    async def transport(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        return httpx.Response(200)

    result = await run_with(transport, {"name": "?", "connector_type": "ftp", "parameters": {}})
    assert result["ok"] is False
    assert "ftp" in result["error"]


def test_excerpt_collapses_and_truncates() -> None:
    response = httpx.Response(200, content=b"a  b\n\nc" * 200)
    assert excerpt(response, limit=5) == "a b c"
    assert len(excerpt(response)) <= 300
