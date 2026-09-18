"""`connector_test` job handler: dry-run a connector configuration.

The core has no HTTP client of its own, so the connection test runs here
(`POST /connectors/{id}/test` queues this job and the dialog polls the run):
it proves that the **stored parameters** work, without touching any data.

* `webhook`: sends one synthetic event to the configured URL (the middleware
  sees a normal delivery attempt) and reports status, latency and a short
  excerpt of the response body. No outbox event is written and the dead-letter
  queue is untouched.
* `json_import`/`json_export`: resolves the profile values that
  `POST /imports`/`POST /exports` would use (dry run, no writes).

The result is a plain dict in `job_run.result`; the UI renders the messages
(so the texts stay translatable in the frontend).
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from meadow.worker.connectors import CONNECTOR_TYPE_WEBHOOK, _webhook_config_from_row
from meadow.worker.jobs import JobMessage, ReportingJobHandler

logger = logging.getLogger(__name__)

#: Maximum number of response characters reported back to the UI.
EXCERPT_LIMIT = 300


def build_connector_test_handler(*, client: httpx.AsyncClient | None = None) -> ReportingJobHandler:
    """Builds the `connector_test` job handler."""

    async def run(active: httpx.AsyncClient, message: JobMessage) -> dict[str, Any]:
        connector_type = str(
            message.parameters.get("connector_type") or message.parameters.get("type") or ""
        )
        parameters = message.parameters.get("parameters")
        if not isinstance(parameters, dict):
            parameters = {}
        name = str(message.parameters.get("name") or message.parameters.get("connector_id") or "")

        if connector_type == CONNECTOR_TYPE_WEBHOOK:
            return await _test_webhook(active, name, parameters)
        if connector_type in ("json_import", "json_export"):
            return _test_profile(connector_type, parameters)
        return {
            "ok": False,
            "connector_type": connector_type,
            "error": f"unsupported connector type `{connector_type}`",
        }

    async def handle(message: JobMessage) -> dict[str, Any]:
        if client is not None:
            return await run(client, message)
        # The synthetic delivery must not be retried by the core's client
        # defaults; a short timeout keeps the dialog responsive.
        async with httpx.AsyncClient(timeout=30.0) as active:
            return await run(active, message)

    return handle


async def _test_webhook(
    active: httpx.AsyncClient,
    name: str,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    """Sends one synthetic event to the webhook target."""
    config = _webhook_config_from_row(
        {"name": name, "type": CONNECTOR_TYPE_WEBHOOK, "parameters": parameters}
    )
    if config is None:
        return {
            "ok": False,
            "connector_type": CONNECTOR_TYPE_WEBHOOK,
            "error": "the webhook parameters are incomplete (a URL is required)",
        }

    event = synthetic_event(name)
    started = time.perf_counter()
    try:
        response = await active.request(
            config.method,
            config.url,
            json=event,
            headers=config.headers or None,
            timeout=config.timeout_s,
        )
    except Exception as exc:  # noqa: BLE001 - transport failures are the result
        return {
            "ok": False,
            "connector_type": CONNECTOR_TYPE_WEBHOOK,
            "url": config.url,
            "method": config.method,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "error": f"{type(exc).__name__}: {exc}",
            "reachable": False,
        }

    latency_ms = round((time.perf_counter() - started) * 1000, 1)
    ok = response.is_success
    logger.info(
        "connector_test %s: %s %s -> HTTP %s in %.1f ms",
        name,
        config.method,
        config.url,
        response.status_code,
        latency_ms,
    )
    return {
        "ok": ok,
        "connector_type": CONNECTOR_TYPE_WEBHOOK,
        "url": config.url,
        "method": config.method,
        "status_code": response.status_code,
        "latency_ms": latency_ms,
        "reachable": True,
        "excerpt": excerpt(response),
        "event_id": event["event_id"],
    }


def _test_profile(connector_type: str, parameters: dict[str, Any]) -> dict[str, Any]:
    """Reports the profile values the job endpoints would apply (dry run)."""
    if connector_type == "json_import":
        resolved = {
            "match_by_key": parameters.get("match_by_key"),
            "match_by_id": parameters.get("match_by_id", False),
            "emit_events": parameters.get("emit_events", True),
            "source": parameters.get("source"),
        }
    else:
        options = parameters.get("options")
        if not isinstance(options, dict):
            options = {}
        resolved = {
            "format": parameters.get("format") or "json",
            "fields": parameters.get("fields") or [],
            "locales": parameters.get("locales") or [],
            "email_to": options.get("email_to") or [],
        }
    return {
        "ok": True,
        "connector_type": connector_type,
        "dry_run": True,
        "resolved": resolved,
    }


def synthetic_event(name: str) -> dict[str, Any]:
    """Builds the test event sent to a webhook target.

    It carries `test: true` so the middleware can recognise (and ignore) a
    probe, and mirrors the shape of a real `object.updated` event.
    """
    return {
        "event_id": "connector_test",
        "event_type": "object.updated",
        "object_id": "object:connector_test",
        "object_type": "connector_test",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "test": True,
        "connector": name,
        "changes": {"attributes": {"test": True}},
        "metadata": {"source": "pim-worker", "kind": "connector_test"},
    }


def excerpt(response: httpx.Response, limit: int = EXCERPT_LIMIT) -> str:
    """Short, whitespace-collapsed excerpt of a response body."""
    try:
        text = response.text
    except Exception:  # noqa: BLE001 - a binary body has no text
        return ""
    collapsed = " ".join(text.split())
    return collapsed[:limit]


__all__ = [
    "EXCERPT_LIMIT",
    "build_connector_test_handler",
    "excerpt",
    "synthetic_event",
]
