"""Tests for the worker's JetStream job consumer (Phase 5, step 3)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from meadow.worker.jobs import JOBS_NAK_DELAY_S, JobMessage, JobRunner, process_messages


class FakeMsg:
    """Minimal stand-in for a nats-py `Msg` with ack/nak/term bookkeeping."""

    def __init__(self, data: bytes, *, num_delivered: int = 1) -> None:
        self.data = data
        self.metadata = _FakeMetadata(num_delivered)
        self.acked = False
        self.nakked = False
        self.nak_delay: float | None = None
        self.terminated = False

    async def ack(self) -> None:
        self.acked = True

    async def nak(self, delay: float | None = None) -> None:
        self.nakked = True
        self.nak_delay = delay

    async def term(self) -> None:
        self.terminated = True


class _FakeMetadata:
    def __init__(self, num_delivered: int) -> None:
        self.num_delivered = num_delivered


def make_runner(
    responses: list[tuple[int, dict[str, Any]]] | None = None,
) -> tuple[JobRunner, httpx.AsyncClient, list[httpx.Request]]:
    """Builds a `JobRunner` backed by an in-memory transport that records requests."""
    requests: list[httpx.Request] = []
    defaults = responses or [(200, {"id": "job_run:1"})]

    async def transport(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        status, payload = defaults[min(len(requests) - 1, len(defaults) - 1)]
        return httpx.Response(status, json=payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    runner = JobRunner("http://core", client)
    return runner, client, requests


def message(**overrides: Any) -> FakeMsg:
    payload = {
        "run_id": "job_run:1",
        "job_definition_id": "job_definition:1",
        "type": "business_rule",
        "parameters": {},
    }
    payload.update(overrides)
    return FakeMsg(JobMessage.model_validate(payload).model_dump_json().encode())


async def test_handle_success_reports_running_then_success_and_acks() -> None:
    runner, _, requests = make_runner()

    async def handler(msg: JobMessage) -> dict[str, Any]:
        assert msg.run_id == "job_run:1"
        return {"processed": 3}

    runner.register("business_rule", handler)
    msg = message()

    await runner.handle(msg)

    assert msg.acked is True
    assert msg.nakked is False
    statuses = [json.loads(request.content)["status"] for request in requests]
    assert statuses == ["running", "success"]
    success_body = json.loads(requests[1].content)
    assert success_body["result"] == {"processed": 3}
    assert success_body["worker_id"] == "pim-worker"


async def test_handle_unknown_type_fails_and_terminates() -> None:
    runner, _, requests = make_runner()
    msg = message(type="outbound_export")

    await runner.handle(msg)

    assert msg.terminated is True
    assert msg.acked is False
    assert msg.nakked is False
    assert len(requests) == 1
    body = json.loads(requests[0].content)
    assert body["status"] == "failed"
    assert "no handler registered for job type" in body["error"]


async def test_handle_invalid_payload_terminates_without_status() -> None:
    runner, _, requests = make_runner()
    msg = FakeMsg(b"not-json")

    await runner.handle(msg)

    assert msg.terminated is True
    assert msg.acked is False
    assert msg.nakked is False
    assert len(requests) == 0


async def test_handle_handler_failure_fails_and_naks() -> None:
    runner, _, requests = make_runner()

    async def handler(msg: JobMessage) -> dict[str, Any]:
        raise RuntimeError("boom")

    runner.register("business_rule", handler)
    msg = message()

    await runner.handle(msg)

    assert msg.nakked is True
    assert msg.nak_delay == JOBS_NAK_DELAY_S
    assert msg.acked is False
    statuses = [json.loads(request.content)["status"] for request in requests]
    assert statuses == ["running", "failed"]
    failed_body = json.loads(requests[1].content)
    assert failed_body["error"] == "boom"


async def test_set_status_sends_api_key_header() -> None:
    runner, _, requests = make_runner([(200, {"id": "job_run:1"})])
    runner._api_key = "secret-key"

    await runner.set_status("job_run:9", "running")

    assert requests[0].headers["x-api-key"] == "secret-key"
    body = json.loads(requests[0].content)
    assert body == {"status": "running", "worker_id": "pim-worker"}


async def test_job_message_defaults_parameters() -> None:
    parsed = JobMessage.model_validate_json(
        json.dumps(
            {
                "run_id": "job_run:1",
                "job_definition_id": "job_definition:1",
                "type": "bulk_operation",
            }
        )
    )
    assert parsed.parameters == {}
    assert parsed.type == "bulk_operation"


async def test_process_messages_respects_concurrency() -> None:
    state = {"active": 0, "max_active": 0}

    class FakeRunner:
        async def handle(self, msg: Any) -> None:
            state["active"] += 1
            state["max_active"] = max(state["max_active"], state["active"])
            await asyncio.sleep(0.02)
            state["active"] -= 1

    sem = asyncio.Semaphore(2)
    await process_messages(FakeRunner(), sem, [object() for _ in range(6)])

    assert state["max_active"] == 2
    assert state["active"] == 0


async def test_handle_final_failure_dead_letters() -> None:
    dead_letters: list[dict[str, Any]] = []
    requests: list[httpx.Request] = []

    async def transport(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "job_run:1"})

    async def publish_dead_letter(payload: dict[str, Any]) -> None:
        dead_letters.append(payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    runner = JobRunner(
        "http://core",
        client,
        max_deliver=5,
        dead_letter=publish_dead_letter,
    )

    async def handler(msg: JobMessage) -> dict[str, Any]:
        raise RuntimeError("boom")

    runner.register("business_rule", handler)
    msg = message()
    msg.metadata.num_delivered = 5  # final delivery attempt

    await runner.handle(msg)

    assert msg.terminated is True
    assert msg.nakked is False
    assert msg.acked is False
    assert len(dead_letters) == 1
    assert dead_letters[0]["run_id"] == "job_run:1"
    assert dead_letters[0]["error"] == "boom"
    assert dead_letters[0]["num_delivered"] == 5

    statuses = [json.loads(request.content)["status"] for request in requests]
    assert statuses == ["running", "failed"]
    failed_body = json.loads(requests[1].content)
    assert failed_body["error"] == "dead lettered after 5 attempts: boom"
