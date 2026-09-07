"""JetStream job consumer (Phase 5, step 3).

The worker consumes job messages from the `JOBS` stream (subject `jobs.*`),
updates the job-run status in the core via REST (`PATCH /jobs/runs/{run_id}`),
and dispatches each message to a handler registered by job type.

The concrete job handlers (business-rule recompute, import/export) are added in
later steps. This module provides the consumer framework: parsing, status
transitions (`running` -> `success`/`failed`), ack/negative-ack semantics, the
handler registry, and bounded parallel processing.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import nats
import nats.js.api as jsapi
from pydantic import BaseModel, Field

from meadow.worker.http import loopback_host
from meadow.worker.log_sender import LogSender

logger = logging.getLogger(__name__)

#: Default NATS subject pattern for job messages.
JOBS_SUBJECT = "jobs.*"
#: Default JetStream stream that carries job messages.
JOBS_STREAM = "JOBS"
#: Default durable consumer name.
JOBS_DURABLE = "job-worker"
#: Ack-wait window in seconds: a job must ack before JetStream redelivers.
JOBS_ACK_WAIT_S = 300.0
#: Maximum delivery attempts before JetStream stops redelivering.
JOBS_MAX_DELIVER = 5
#: Delay (seconds) before a failed message is redelivered after a negative ack.
JOBS_NAK_DELAY_S = 5.0
#: Default maximum number of job messages processed concurrently.
JOBS_CONCURRENCY = 4
#: Dead-letter stream: holds jobs that exhausted all delivery attempts.
JOBS_DLQ_STREAM = "JOBS_DLQ"
#: Subject pattern of the dead-letter stream.
JOBS_DLQ_SUBJECT = "jobs.dlq.*"


class JobMessage(BaseModel):
    """A job message published by the core on `jobs.<type>`."""

    run_id: str
    job_definition_id: str | None = None
    type: str
    parameters: dict[str, Any] = Field(default_factory=dict)


#: A handler receives the parsed message and returns an optional `result`
#: object that is stored in `job_run.result` on success.
JobHandler = Callable[[JobMessage], Awaitable[dict[str, Any] | None]]

#: Publishes a dead-letter payload (plain dict) to the DLQ stream.
DeadLetterPublisher = Callable[[dict[str, Any]], Awaitable[None]]


class JobRunner:
    """Dispatches job messages and reports job-run status to the core.

    Status updates are best-effort: a failure to reach the core is logged but
    never crashes the consumer loop. The ack/negative-ack decision is driven
    only by the handler outcome.
    """

    def __init__(
        self,
        core_url: str,
        client: httpx.AsyncClient,
        *,
        api_key: str | None = None,
        worker_id: str = "pim-worker",
        nak_delay: float = JOBS_NAK_DELAY_S,
        max_deliver: int = JOBS_MAX_DELIVER,
        dead_letter: DeadLetterPublisher | None = None,
    ) -> None:
        self._core_url = core_url.rstrip("/")
        self._client = client
        self._api_key = api_key
        self._worker_id = worker_id
        self._nak_delay = nak_delay
        self._max_deliver = max(1, max_deliver)
        self._dead_letter = dead_letter
        self._handlers: dict[str, JobHandler] = {}

    def register(self, job_type: str, handler: JobHandler) -> None:
        """Registers a handler for a job type."""
        self._handlers[job_type] = handler

    def _headers(self) -> dict[str, str]:
        headers = {"content-type": "application/json"}
        if self._api_key:
            headers["x-api-key"] = self._api_key
        return headers

    async def set_status(
        self,
        run_id: str,
        status: str,
        *,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        """Reports a job-run status to the core (best-effort)."""
        body: dict[str, Any] = {"status": status, "worker_id": self._worker_id}
        if result is not None:
            body["result"] = result
        if error is not None:
            body["error"] = error
        try:
            response = await self._client.patch(
                f"{self._core_url}/jobs/runs/{run_id}",
                json=body,
                headers=self._headers(),
            )
            if response.status_code >= 300:
                logger.warning(
                    "job-run status update rejected: %s %s",
                    response.status_code,
                    response.text[:200],
                )
        except httpx.HTTPError as exc:
            logger.warning("job-run status update failed: %s", exc)

    async def handle(self, msg) -> None:
        """Handles one job message: running -> dispatch -> success/failed."""
        try:
            message = JobMessage.model_validate_json(msg.data)
        except Exception as exc:  # noqa: BLE001 - invalid payload is permanent
            logger.error("invalid job message: %s", exc)
            await msg.term()
            return

        handler = self._handlers.get(message.type)
        if handler is None:
            logger.error("no handler registered for job type %r", message.type)
            await self.set_status(
                message.run_id,
                "failed",
                error=f"no handler registered for job type {message.type!r}",
            )
            await msg.term()
            return

        await self.set_status(message.run_id, "running")
        try:
            result = await handler(message)
            await self.set_status(message.run_id, "success", result=result)
            await msg.ack()
        except Exception as exc:  # noqa: BLE001 - transient failure, retry
            logger.exception("job %s failed: %s", message.run_id, exc)
            await self._fail_or_dead_letter(message, msg, str(exc))

    @staticmethod
    def _num_delivered(msg: Any) -> int:
        metadata = getattr(msg, "metadata", None)
        num = getattr(metadata, "num_delivered", 0)
        return int(num) if num else 1

    async def _fail_or_dead_letter(
        self,
        message: JobMessage,
        msg: Any,
        error: str,
    ) -> None:
        """Retries via negative-ack, or dead-letters on the final delivery."""
        num_delivered = self._num_delivered(msg)
        if num_delivered >= self._max_deliver and self._dead_letter is not None:
            payload = {
                "run_id": message.run_id,
                "job_definition_id": message.job_definition_id,
                "type": message.type,
                "parameters": message.parameters,
                "error": error,
                "num_delivered": num_delivered,
            }
            try:
                await self._dead_letter(payload)
                await self.set_status(
                    message.run_id,
                    "failed",
                    error=f"dead lettered after {num_delivered} attempts: {error}",
                )
                await msg.term()
                return
            except Exception:  # noqa: BLE001 - fall back to a retry
                logger.exception("dead-letter publish failed for %s", message.run_id)
        await self.set_status(message.run_id, "failed", error=error)
        await msg.nak(delay=self._nak_delay)


async def process_messages(
    runner: JobRunner,
    sem: asyncio.Semaphore,
    msgs: list[Any],
) -> None:
    """Processes a batch of messages concurrently, bounded by `sem`."""

    async def run_one(msg: Any) -> None:
        async with sem:
            await runner.handle(msg)

    await asyncio.gather(*(run_one(msg) for msg in msgs))


async def ensure_dlq_stream(js) -> None:
    """Ensures the dead-letter stream exists (idempotent)."""
    try:
        await js.stream_info(JOBS_DLQ_STREAM)
    except nats.js.errors.NotFoundError:
        await js.add_stream(name=JOBS_DLQ_STREAM, subjects=[JOBS_DLQ_SUBJECT])


async def serve_jobs(
    nats_url: str,
    core_url: str,
    *,
    api_key: str | None = None,
    worker_id: str = "pim-worker",
    subject: str = JOBS_SUBJECT,
    stream: str = JOBS_STREAM,
    durable: str = JOBS_DURABLE,
    ack_wait: float = JOBS_ACK_WAIT_S,
    max_deliver: int = JOBS_MAX_DELIVER,
    nak_delay: float = JOBS_NAK_DELAY_S,
    concurrency: int = JOBS_CONCURRENCY,
    handlers: dict[str, JobHandler] | None = None,
    log_sender: LogSender | None = None,
) -> None:
    """Connects to NATS and consumes job messages from JetStream until cancelled.

    Up to `concurrency` messages are fetched and processed concurrently.
    """
    core_url = loopback_host(core_url)
    sem = asyncio.Semaphore(max(1, concurrency))
    nc = await nats.connect(nats_url)
    js = nc.jetstream()
    config = jsapi.ConsumerConfig(ack_wait=ack_wait, max_deliver=max_deliver)
    psub = await js.pull_subscribe(
        subject,
        durable=durable,
        stream=stream,
        config=config,
    )

    async def publish_dead_letter(payload: dict[str, Any]) -> None:
        await ensure_dlq_stream(js)
        await js.publish(
            f"jobs.dlq.{payload.get('type', 'unknown')}",
            json.dumps(payload).encode(),
        )

    async with httpx.AsyncClient(timeout=30.0) as client:
        runner = JobRunner(
            core_url,
            client,
            api_key=api_key,
            worker_id=worker_id,
            nak_delay=nak_delay,
            max_deliver=max_deliver,
            dead_letter=publish_dead_letter,
        )
        for job_type, handler in (handlers or {}).items():
            runner.register(job_type, handler)
        if log_sender is not None:
            log_sender.enqueue(
                service="pim-worker",
                level="INFO",
                category="app",
                message=f"job consumer subscribed to {subject} (stream {stream})",
            )
        logger.info("job consumer subscribed to %r on %s", subject, nats_url)
        try:
            while True:
                try:
                    msgs = await psub.fetch(max(1, concurrency), timeout=5)
                except TimeoutError:
                    continue
                await process_messages(runner, sem, msgs)
        finally:
            await nc.close()


__all__ = [
    "JOBS_ACK_WAIT_S",
    "JOBS_CONCURRENCY",
    "JOBS_DLQ_STREAM",
    "JOBS_DLQ_SUBJECT",
    "JOBS_DURABLE",
    "JOBS_MAX_DELIVER",
    "JOBS_NAK_DELAY_S",
    "JOBS_STREAM",
    "JOBS_SUBJECT",
    "JobMessage",
    "JobHandler",
    "JobRunner",
    "ensure_dlq_stream",
    "process_messages",
    "serve_jobs",
]
