"""`bulk_operation` job handler: applies a mass operation to a selection.

Phase 7 (bulk-edit wizard). The core queues a `bulk_operation` job with
`parameters = {"op": ..., "object_ids": [...], "parameters": {...}}`; the
frontend resolves the selection (all filter hits / page / checked rows) into an
explicit `object_ids` list. This handler loads each object, applies the
operation, and reports a per-object status in `job_run.result`. Object failures
are isolated (one failing object does not abort the run).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from meadow.worker.http import loopback_host
from meadow.worker.jobs import JobHandler, JobMessage

logger = logging.getLogger(__name__)

_ATTR_OPS = {"set_attribute", "clear_attribute"}


def build_bulk_handler(
    core_url: str,
    *,
    api_key: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> JobHandler:
    """Builds the `bulk_operation` job handler.

    `client` may be injected for tests; otherwise a short-lived client is
    created per job.
    """
    core_url = loopback_host(core_url)
    headers = {"x-api-key": api_key} if api_key else {}

    async def run(active: httpx.AsyncClient, message: JobMessage) -> dict[str, Any]:
        parameters = message.parameters
        op = str(parameters.get("op") or "")
        object_ids = parameters.get("object_ids") or []
        op_params = parameters.get("parameters") or {}
        if not isinstance(op_params, dict):
            raise RuntimeError("`parameters.parameters` must be an object")
        if not object_ids:
            raise RuntimeError("bulk job is missing `parameters.object_ids`")

        results: list[dict[str, Any]] = []
        succeeded = 0
        failed = 0
        for oid in object_ids:
            record: dict[str, Any] = {"object_id": str(oid), "status": "skipped"}
            try:
                detail = await _apply(active, core_url, op, str(oid), op_params)
                record["status"] = "ok"
                if detail:
                    record.update(detail)
                succeeded += 1
            except Exception as exc:  # noqa: BLE001 - isolate per-object failures
                record["status"] = "error"
                record["error"] = str(exc)
                failed += 1
            results.append(record)

        logger.info(
            "bulk_operation %s: %d ok, %d failed of %d objects",
            op,
            succeeded,
            failed,
            len(object_ids),
        )
        return {
            "op": op,
            "processed": len(object_ids),
            "succeeded": succeeded,
            "failed": failed,
            "results": results,
        }

    async def handle(message: JobMessage) -> dict[str, Any]:
        if client is not None:
            return await run(client, message)
        async with httpx.AsyncClient(timeout=60.0, headers=headers) as active:
            return await run(active, message)

    return handle


async def _apply(
    active: httpx.AsyncClient,
    core_url: str,
    op: str,
    object_id: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Applies one bulk operation to a single object and returns extra detail."""
    if op in _ATTR_OPS:
        attribute = str(params.get("attribute") or "")
        if not attribute:
            raise RuntimeError("`parameters.attribute` is required")
        obj = (await active.get(f"{core_url}/objects/{object_id}")).json()
        attributes = dict(obj.get("attributes") or {})
        if op == "set_attribute":
            value = params.get("value")
            if value in (None, ""):
                attributes.pop(attribute, None)
            else:
                attributes[attribute] = value
        else:  # clear_attribute
            attributes.pop(attribute, None)
        response = await active.patch(
            f"{core_url}/objects/{object_id}", json={"attributes": attributes}
        )
        response.raise_for_status()
        return {"attribute": attribute}

    if op == "change_status":
        status = str(params.get("status") or "")
        if not status:
            raise RuntimeError("`parameters.status` is required")
        response = await active.patch(f"{core_url}/objects/{object_id}", json={"status": status})
        response.raise_for_status()
        return {"status": status}

    if op in ("add_category", "remove_category"):
        category = str(params.get("category") or "")
        if not category:
            raise RuntimeError("`parameters.category` is required")
        if op == "add_category":
            response = await active.post(
                f"{core_url}/references",
                json={
                    "source": object_id,
                    "target": category,
                    "definition_id": "rel_category_member",
                },
            )
        else:
            ref_id = await _find_category_member(active, core_url, object_id, category)
            if ref_id is None:
                raise RuntimeError("category membership not found")
            response = await active.delete(f"{core_url}/references/{ref_id}")
        response.raise_for_status()
        return {"category": category}

    if op == "change_parent":
        parent = str(params.get("parent") or "")
        if not parent:
            raise RuntimeError("`parameters.parent` is required")
        response = await active.patch(
            f"{core_url}/objects/{object_id}/parent", json={"parent": parent}
        )
        response.raise_for_status()
        return {"parent": parent}

    if op == "delete":
        response = await active.delete(f"{core_url}/objects/{object_id}")
        response.raise_for_status()
        return {}

    raise RuntimeError(f"unknown bulk operation `{op}`")


async def _find_category_member(
    active: httpx.AsyncClient,
    core_url: str,
    object_id: str,
    category: str,
) -> str | None:
    """Returns the `category_member` reference id linking object <-> category."""
    refs = (await active.get(f"{core_url}/objects/{object_id}/references")).json()
    for ref in refs:
        if ref.get("definition_id") != "rel_category_member":
            continue
        if ref.get("out") == category or ref.get("in") == category:
            return ref.get("id")
    return None
