"""`collection_refresh` job handler: refreshes a materialized collection.

Phase 9. The core queues a `collection_refresh` job (from a collection's
``refresh_interval`` cron) with ``parameters = {"collection_id": ...}``. This
handler asks the core to re-run the underlying saved search and atomically
replace the collection's ``collection_member`` relations, then reports the
result in ``job_run.result``.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from meadow.worker.http import loopback_host
from meadow.worker.jobs import JobHandler, JobMessage

logger = logging.getLogger(__name__)


def build_collection_refresh_handler(
    core_url: str,
    *,
    api_key: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> JobHandler:
    """Builds the `collection_refresh` job handler."""
    core_url = loopback_host(core_url)
    headers = {"x-api-key": api_key} if api_key else {}

    async def run(active: httpx.AsyncClient, message: JobMessage) -> dict[str, Any]:
        collection_id = str(message.parameters.get("collection_id") or "")
        if not collection_id:
            raise RuntimeError("collection_refresh job is missing `parameters.collection_id`")
        response = await active.post(f"{core_url}/collections/{collection_id}/refresh")
        if response.status_code >= 400:
            raise RuntimeError(
                f"core refused refresh of {collection_id}: HTTP {response.status_code} "
                f"{response.text[:200]}"
            )
        body = response.json()
        logger.info(
            "collection_refresh %s: %d members",
            collection_id,
            body.get("count", 0),
        )
        return {
            "collection_id": collection_id,
            "count": body.get("count", 0),
        }

    async def handle(message: JobMessage) -> dict[str, Any]:
        if client is not None:
            return await run(client, message)
        async with httpx.AsyncClient(timeout=120.0, headers=headers) as active:
            return await run(active, message)

    return handle
