"""`outbound_export` job handler: `json_export` connector (MEF export, Phase 6).

The handler receives `parameters.filter` (`type` and/or `status`), fetches the
matching objects from the core, builds a Meadow Exchange Format (MEF) document,
and stores it in `job_run.result`.

Quantified values are currently exported as the stored canonical-SI numbers;
re-emitting them as `{"value": …, "unit": …}` (in the attribute's display unit)
is a follow-up step.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from meadow.worker.jobs import JobHandler, JobMessage

logger = logging.getLogger(__name__)

#: Default cap for exported objects per run.
DEFAULT_EXPORT_LIMIT = 1000


def _now_iso_utc() -> str:
    """Returns the current time as an ISO-8601 UTC string."""
    return datetime.now(UTC).isoformat()


def build_export_handler(
    core_url: str,
    *,
    api_key: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> JobHandler:
    """Builds the `outbound_export` (json_export) job handler.

    `client` may be injected for tests; otherwise a short-lived client is
    created per job.
    """
    headers = {"x-api-key": api_key} if api_key else {}

    async def run(active: httpx.AsyncClient, message: JobMessage) -> dict[str, Any]:
        parameters = message.parameters
        filter_ = parameters.get("filter") or {}
        if not isinstance(filter_, dict):
            raise RuntimeError("`parameters.filter` must be an object")
        object_type = filter_.get("type")
        status = filter_.get("status")
        limit = int(parameters.get("limit") or DEFAULT_EXPORT_LIMIT)

        query: dict[str, Any] = {"limit": min(max(limit, 1), 1000)}
        if object_type:
            query["type"] = str(object_type)
        response = await active.get(f"{core_url}/objects", params=query)
        response.raise_for_status()
        objects = response.json()

        if status:
            objects = [obj for obj in objects if obj.get("status") == str(status)]

        mef_objects: list[dict[str, Any]] = []
        for obj in objects:
            attributes = obj.get("attributes")
            mef_objects.append(
                {
                    "id": obj.get("id"),
                    "type": obj.get("type"),
                    "attributes": attributes if isinstance(attributes, dict) else {},
                    "status": obj.get("status") or "draft",
                }
            )

        document: dict[str, Any] = {
            "format_version": "1.0",
            "source": "meadow",
            "created_at": _now_iso_utc(),
            "objects": mef_objects,
            "references": [],
        }

        logger.info("json_export exported %d objects", len(mef_objects))
        return {
            "connector": "json_export",
            "exported": len(mef_objects),
            "document": document,
        }

    async def handle(message: JobMessage) -> dict[str, Any]:
        if client is not None:
            return await run(client, message)
        async with httpx.AsyncClient(timeout=60.0, headers=headers) as active:
            return await run(active, message)

    return handle


__all__ = ["build_export_handler"]
