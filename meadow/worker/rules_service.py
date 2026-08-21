"""Synchronous rule evaluation over NATS request/reply (Phase 4, step 2).

The worker subscribes to `rules.evaluate`; on each request it executes the
provided rule scripts in the RestrictedPython sandbox and replies with the
aggregated result. Evaluations run sequentially in a worker thread to avoid
`sys.settrace`/`tracemalloc` interactions between concurrent rules.
"""

from __future__ import annotations

import asyncio
import logging

from meadow.rules import Context, execute_rule
from meadow.rules.errors import RuleError
from meadow.rules.sandbox import DEFAULT_MAX_MEMORY_BYTES, DEFAULT_TIMEOUT_SECONDS
from meadow.worker.protocol import (
    RULES_EVALUATE_SUBJECT,
    EvaluationIssue,
    EvaluationLog,
    EvaluationRequest,
    EvaluationResponse,
)

logger = logging.getLogger(__name__)


def evaluate(
    request: EvaluationRequest,
    *,
    timeout_s: float | None = DEFAULT_TIMEOUT_SECONDS,
    max_memory_bytes: int | None = DEFAULT_MAX_MEMORY_BYTES,
) -> EvaluationResponse:
    """Executes all rules of a request and aggregates the outcome.

    Fail-closed: a rule that raises (compile error, timeout, memory, or any
    unexpected exception) is recorded in `response.failed`; the caller (the
    Rust core) rejects the write when `failed` is non-empty.
    """
    response = EvaluationResponse()
    obj = request.object.model_dump()

    for rule in request.rules:
        ctx = Context(
            rule_type=rule.type,
            object_type=request.object.type,
            language=request.language,
            mode=request.mode,
            actor=request.actor,
        )
        try:
            result = execute_rule(
                rule.script,
                obj,
                ctx,
                timeout_s=timeout_s,
                max_memory_bytes=max_memory_bytes,
            )
        except RuleError as exc:
            response.failed.append(rule.name)
            response.logs.append(EvaluationLog(level="ERROR", message=f"{rule.name}: {exc}"))
            continue
        except Exception as exc:  # noqa: BLE001 - fail-closed on any failure
            response.failed.append(rule.name)
            response.logs.append(EvaluationLog(level="ERROR", message=f"{rule.name}: {exc}"))
            continue

        response.errors.extend(
            EvaluationIssue(attr=issue.attr, code=issue.code, message=issue.message)
            for issue in result.errors
        )
        response.warnings.extend(
            EvaluationIssue(attr=issue.attr, code=issue.code, message=issue.message)
            for issue in result.warnings
        )
        response.computed.update(result.computed)
        if result.allowed is not None:
            if response.allowed is None:
                response.allowed = result.allowed
                response.deny_reason = result.deny_reason
            elif not result.allowed:
                response.allowed = False
                response.deny_reason = result.deny_reason
        response.logs.extend(
            EvaluationLog(level=entry.level, message=entry.message) for entry in result.logs
        )

    return response


async def _handle(
    msg,
    *,
    timeout_s: float | None,
    max_memory_bytes: int | None,
) -> None:
    try:
        request = EvaluationRequest.model_validate_json(msg.data)
    except Exception as exc:  # noqa: BLE001 - reply with an error, never crash
        reply = EvaluationResponse(
            failed=["invalid_request"],
            logs=[EvaluationLog(level="ERROR", message=f"invalid request: {exc}")],
        )
        await msg.respond(reply.model_dump_json().encode())
        return

    response = await asyncio.to_thread(
        evaluate,
        request,
        timeout_s=timeout_s,
        max_memory_bytes=max_memory_bytes,
    )
    await msg.respond(response.model_dump_json().encode())


async def serve(
    nats_url: str,
    subject: str = RULES_EVALUATE_SUBJECT,
    *,
    timeout_s: float | None = DEFAULT_TIMEOUT_SECONDS,
    max_memory_bytes: int | None = DEFAULT_MAX_MEMORY_BYTES,
) -> None:
    """Connects to NATS and serves rule-evaluation requests until cancelled."""
    import nats

    nc = await nats.connect(nats_url)
    logger.info("rule evaluation service subscribed to %r on %s", subject, nats_url)
    sub = await nc.subscribe(subject)
    try:
        async for msg in sub.messages:
            await _handle(
                msg,
                timeout_s=timeout_s,
                max_memory_bytes=max_memory_bytes,
            )
    finally:
        await nc.close()
