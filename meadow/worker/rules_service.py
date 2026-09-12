"""Synchronous rule evaluation over NATS request/reply (Phase 4, step 2).

The worker subscribes to `rules.evaluate`; on each request it executes the
provided rule scripts in the RestrictedPython sandbox and replies with the
aggregated result. Evaluations run sequentially in a worker thread to avoid
`sys.settrace`/`tracemalloc` interactions between concurrent rules.
"""

from __future__ import annotations

import asyncio
import logging

from meadow.rules import Context, compile_rule, execute_rule
from meadow.rules.errors import RuleCompileError, RuleError
from meadow.rules.sandbox import DEFAULT_MAX_MEMORY_BYTES, DEFAULT_TIMEOUT_SECONDS
from meadow.rules.whitelist import DEFAULT_WHITELIST
from meadow.worker.log_sender import LogSender
from meadow.worker.protocol import (
    RULES_COMPILE_SUBJECT,
    RULES_EVALUATE_SUBJECT,
    EvaluationCompileRequest,
    EvaluationCompileResponse,
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
            channel=request.channel,
            release_status=request.release_status,
            missing_features=tuple(request.missing_features),
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
    log_sender: LogSender | None = None,
) -> None:
    try:
        request = EvaluationRequest.model_validate_json(msg.data)
    except Exception as exc:  # noqa: BLE001 - reply with an error, never crash
        if log_sender is not None:
            log_sender.enqueue(
                service="pim-worker",
                level="ERROR",
                category="app",
                message=f"invalid rule-evaluation request: {exc}",
            )
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
    if log_sender is not None:
        for name in response.failed:
            log_sender.enqueue(
                service="pim-worker",
                level="ERROR",
                category="rule",
                message=f"rule `{name}` failed during evaluation",
            )
    await msg.respond(response.model_dump_json().encode())


def compile_script(request: EvaluationCompileRequest) -> EvaluationCompileResponse:
    """Validates a rule script against the sandbox (compile + import whitelist).

    Returns `ok=True` when the script compiles and only imports whitelisted
    modules; otherwise `ok=False` with the compile errors.
    """
    whitelist = frozenset(request.whitelist) if request.whitelist is not None else DEFAULT_WHITELIST
    try:
        compile_rule(request.script, whitelist=whitelist)
    except (RuleCompileError, SyntaxError) as exc:
        return EvaluationCompileResponse(ok=False, errors=[str(exc)])
    return EvaluationCompileResponse(ok=True)


async def _handle_compile(msg) -> None:
    try:
        request = EvaluationCompileRequest.model_validate_json(msg.data)
    except Exception as exc:  # noqa: BLE001 - reply with an error, never crash
        reply = EvaluationCompileResponse(ok=False, errors=[f"invalid request: {exc}"])
        await msg.respond(reply.model_dump_json().encode())
        return
    reply = compile_script(request)
    await msg.respond(reply.model_dump_json().encode())


async def serve(
    nats_url: str,
    subject: str = RULES_EVALUATE_SUBJECT,
    compile_subject: str = RULES_COMPILE_SUBJECT,
    *,
    timeout_s: float | None = DEFAULT_TIMEOUT_SECONDS,
    max_memory_bytes: int | None = DEFAULT_MAX_MEMORY_BYTES,
    log_sender: LogSender | None = None,
) -> None:
    """Connects to NATS and serves rule-evaluation requests until cancelled."""
    import nats

    nc = await nats.connect(nats_url)
    if log_sender is not None:
        log_sender.enqueue(
            service="pim-worker",
            level="INFO",
            category="app",
            message=f"rule evaluation service subscribed to {subject}",
        )
    logger.info("rule evaluation service subscribed to %r on %s", subject, nats_url)
    eval_sub = await nc.subscribe(subject)
    compile_sub = await nc.subscribe(compile_subject)

    async def eval_loop() -> None:
        async for msg in eval_sub.messages:
            await _handle(
                msg,
                timeout_s=timeout_s,
                max_memory_bytes=max_memory_bytes,
                log_sender=log_sender,
            )

    async def compile_loop() -> None:
        async for msg in compile_sub.messages:
            await _handle_compile(msg)

    try:
        await asyncio.gather(eval_loop(), compile_loop())
    finally:
        await nc.close()
