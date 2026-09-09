"""`config_import` job handler: asynchronous Meadow Configuration Format (MCF) import.

The handler receives `parameters.document_id` (the stored MCF document) and
`parameters.mode` (`dry_run` | `merge` | `replace`). It fetches the document
from the core (`GET /imports/{document_id}`), then re-applies it through the
core's synchronous import endpoint (`POST /config/import?mode=...`) so the
report is stored in `job_run.result`. The worker uses the machine-to-machine
`X-Api-Key` path; the core resolves it to the `system` actor with admin role.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from meadow.worker.http import loopback_host
from meadow.worker.jobs import JobHandler, JobMessage

logger = logging.getLogger(__name__)


async def _fetch_document(
    client: httpx.AsyncClient,
    core_url: str,
    document_id: str,
) -> dict[str, Any]:
    response = await client.get(f"{core_url}/imports/{document_id}")
    response.raise_for_status()
    record = response.json()
    document = record.get("document")
    if not isinstance(document, dict):
        raise RuntimeError(f"import document `{document_id}` has no `document` object")
    return document


def build_config_import_handler(
    core_url: str,
    *,
    api_key: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> JobHandler:
    """Builds the `config_import` job handler.

    `client` may be injected for tests; otherwise a short-lived client is
    created per job (with the `X-Api-Key` header for machine-to-machine auth).
    """
    core_url = loopback_host(core_url)
    headers = {"x-api-key": api_key} if api_key else {}

    async def run(active: httpx.AsyncClient, message: JobMessage) -> dict[str, Any]:
        parameters = message.parameters
        document_id = parameters.get("document_id")
        if not document_id:
            raise RuntimeError("config_import job is missing `parameters.document_id`")
        mode = str(parameters.get("mode") or "merge")

        document = await _fetch_document(active, core_url, str(document_id))

        # Re-apply through the core's synchronous import endpoint (X-Api-Key).
        response = await active.post(
            f"{core_url}/config/import?mode={mode}",
            json=document,
        )
        response.raise_for_status()
        report = response.json()

        return {"mode": mode, "report": report}

    async def handle(message: JobMessage) -> dict[str, Any]:
        if client is not None:
            return await run(client, message)
        async with httpx.AsyncClient(timeout=120.0, headers=headers) as active:
            return await run(active, message)

    return handle


__all__ = ["build_config_import_handler"]
