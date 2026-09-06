"""`business_rule` job handler: recompute rules over a filtered object set (Phase 5, step 4).

A `business_rule` job message carries the rule source plus the object filter
(see `POST /rules/{id}/recompute` in the core):

```json
{
    "rule_id": "business_rule:…",
    "rule": {"name": "…", "type": "computed|bulk", "script": "…"},
    "applies_to": ["product"],
    "object_ids": []
}
```

The handler loads the matching objects from the core, evaluates the rule
against each object in the sandbox (bulk mode), persists the declared-computed
values via `PATCH /objects/{id}/computed`, and returns a per-object report that
is stored in `job_run.result`.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from meadow.rules import Context, execute_rule
from meadow.worker.jobs import JobHandler, JobMessage

logger = logging.getLogger(__name__)

#: Bulk/recompute evaluation budget (generous, per plan: ~30 s).
BULK_TIMEOUT_SECONDS = 30.0

#: Serializes sandbox evaluations. `execute_rule` installs `sys.settrace`
#: (per-thread) and `tracemalloc` (process-global, not thread-safe), so only
#: one evaluation may run at a time even while job I/O is processed in
#: parallel. Shared with the import connector (`meadow.worker.imports`).
evaluation_lock = asyncio.Lock()


def evaluate_object(
    script: str,
    rule_type: str,
    obj: dict[str, Any],
    *,
    timeout_s: float = BULK_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Evaluates one rule against one object's stored view (bulk mode).

    Returns the computed values, errors, warnings, and logs as plain data.
    The caller is expected to run this in a worker thread (`asyncio.to_thread`)
    because `execute_rule` installs `sys.settrace`/`tracemalloc`.
    """
    ctx = Context(
        rule_type=rule_type,
        object_type=str(obj.get("type") or ""),
        mode="bulk",
        actor="system",
    )
    view = {
        "id": obj.get("id"),
        "type": obj.get("type"),
        "status": obj.get("status"),
        "attributes": obj.get("attributes") or {},
    }
    result = execute_rule(script, view, ctx, timeout_s=timeout_s)
    return {
        "computed": result.computed,
        "errors": [
            {"attr": issue.attr, "code": issue.code, "message": issue.message}
            for issue in result.errors
        ],
        "warnings": [
            {"attr": issue.attr, "code": issue.code, "message": issue.message}
            for issue in result.warnings
        ],
        "logs": [{"level": entry.level, "message": entry.message} for entry in result.logs],
    }


async def load_objects(
    client: httpx.AsyncClient,
    core_url: str,
    object_ids: list[str],
    applies_to: list[str],
    *,
    locale: str | None = None,
) -> list[dict[str, Any]]:
    """Loads the objects to recompute: explicit ids, or all of `applies_to` types.

    For explicit ids, the **resolved** view is requested (`resolve_inheritance`)
    so computed rules that read inherited values see them. A locale is threaded
    through when provided, matching the write-path resolution.
    """
    if object_ids:
        params: dict[str, str] = {"resolve_inheritance": "true"}
        if locale:
            params["locale"] = locale
        objects: list[dict[str, Any]] = []
        for object_id in object_ids:
            response = await client.get(f"{core_url}/objects/{object_id}", params=params)
            response.raise_for_status()
            objects.append(response.json())
        return objects

    if applies_to:
        objects = []
        for object_type in applies_to:
            response = await client.get(
                f"{core_url}/objects",
                params={"type": object_type, "limit": 1000},
            )
            response.raise_for_status()
            objects.extend(response.json())
        return objects

    response = await client.get(f"{core_url}/objects", params={"limit": 1000})
    response.raise_for_status()
    return response.json()


async def write_computed(
    client: httpx.AsyncClient,
    core_url: str,
    object_id: str,
    values: dict[str, Any],
) -> None:
    """Persists declared-computed values via the core's internal write-back path."""
    response = await client.patch(
        f"{core_url}/objects/{object_id}/computed",
        json={"values": values},
    )
    response.raise_for_status()


def build_business_rule_handler(
    core_url: str,
    *,
    api_key: str | None = None,
    timeout_s: float = BULK_TIMEOUT_SECONDS,
    client: httpx.AsyncClient | None = None,
) -> JobHandler:
    """Builds the `business_rule` job handler.

    `client` may be injected for tests; otherwise a short-lived client is
    created per job.
    """
    headers = {"x-api-key": api_key} if api_key else {}

    async def run(active: httpx.AsyncClient, message: JobMessage) -> dict[str, Any]:
        parameters = message.parameters
        rule = parameters.get("rule") or {}
        script = rule.get("script")
        rule_type = str(rule.get("type") or "")
        rule_name = str(rule.get("name") or "?")
        if not script:
            raise RuntimeError("business_rule job is missing `parameters.rule.script`")
        object_ids = [str(i) for i in parameters.get("object_ids") or []]
        applies_to = [str(t) for t in parameters.get("applies_to") or []]
        locale = parameters.get("locale")
        locale = str(locale) if locale else None

        objects = await load_objects(active, core_url, object_ids, applies_to, locale=locale)
        results: list[dict[str, Any]] = []
        succeeded = 0
        failed = 0
        for obj in objects:
            object_id = str(obj.get("id") or "<unknown>")
            try:
                async with evaluation_lock:
                    outcome = await asyncio.to_thread(
                        evaluate_object,
                        script,
                        rule_type,
                        obj,
                        timeout_s=timeout_s,
                    )
                if outcome["computed"]:
                    await write_computed(active, core_url, object_id, outcome["computed"])
                results.append(
                    {
                        "object_id": object_id,
                        "status": "succeeded",
                        "computed": outcome["computed"],
                        "errors": outcome["errors"],
                        "warnings": outcome["warnings"],
                    }
                )
                succeeded += 1
            except Exception as exc:  # noqa: BLE001 - per-object isolation
                logger.exception("recompute failed for object %s", object_id)
                results.append({"object_id": object_id, "status": "failed", "error": str(exc)})
                failed += 1

        return {
            "rule": rule_name,
            "processed": len(objects),
            "succeeded": succeeded,
            "failed": failed,
            "results": results,
        }

    async def handle(message: JobMessage) -> dict[str, Any]:
        if client is not None:
            return await run(client, message)
        async with httpx.AsyncClient(timeout=30.0, headers=headers) as active:
            return await run(active, message)

    return handle
