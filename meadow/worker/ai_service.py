"""AI generation over NATS request/reply (stage 1, `plan/detail/ki-assistenz.md`).

The core sends a request to `ai.generate` carrying **both** the task from the
editor and the resolved configuration (including the API key — the key travels
only here, never to the browser). The worker builds the prompt from the runtime
sources, calls the provider, checks the answer and replies. `ai.test` is the same
path with a one-line prompt, for the settings page's connection test.

The vocabulary the projection needs (`GET /attribute-definitions/vocabulary`) is
fetched from the core and cached — it is a multi-megabyte response, so it must
not travel on every suggestion.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from meadow.ai.provider import ProviderConfig, ProviderError
from meadow.ai.service import (
    GenerationRequest,
    ServiceState,
    generate,
    test_connection,
)
from meadow.worker.http import loopback_host
from meadow.worker.log_sender import LogSender
from meadow.worker.protocol import (
    AI_GENERATE_SUBJECT,
    AI_TEST_SUBJECT,
    AiGenerateEnvelope,
    AiTestEnvelope,
)

logger = logging.getLogger(__name__)

#: How long the fetched attribute vocabulary is reused (seconds).
VOCABULARY_TTL_SECONDS = 300.0


class VocabularyCache:
    """The attribute vocabulary from the core, cached with a TTL.

    Only the projection needs it (`meadow.ai.context.attribute_facts`), and the
    raw response is large: one fetch serves every request in the TTL window.
    """

    def __init__(
        self,
        core_url: str,
        api_key: str | None,
        *,
        ttl_seconds: float = VOCABULARY_TTL_SECONDS,
    ) -> None:
        self._url = f"{loopback_host(core_url).rstrip('/')}/attribute-definitions/vocabulary"
        self._api_key = api_key
        self._ttl = ttl_seconds
        self._rows: list[dict[str, Any]] = []
        self._fetched_at = 0.0
        self._lock = asyncio.Lock()

    async def rows(self) -> list[dict[str, Any]]:
        """The cached vocabulary rows, refetched when the TTL expired.

        A fetch failure raises: a prompt without the attribute list would produce
        confident nonsense, so the caller reports the error instead.
        """
        now = time.monotonic()
        if self._rows and now - self._fetched_at < self._ttl:
            return self._rows
        async with self._lock:
            now = time.monotonic()
            if self._rows and now - self._fetched_at < self._ttl:
                return self._rows
            headers = {"x-api-key": self._api_key} if self._api_key else {}
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(self._url, headers=headers)
                response.raise_for_status()
                payload = response.json()
            rows = (
                [row for row in payload if isinstance(row, dict)]
                if isinstance(payload, list)
                else []
            )
            self._rows = rows
            self._fetched_at = time.monotonic()
            logger.info("loaded %d attribute definitions for the AI context", len(rows))
            return self._rows


async def handle_generate(
    envelope: AiGenerateEnvelope,
    *,
    vocabulary: VocabularyCache,
    state: ServiceState,
) -> dict[str, Any]:
    """Runs one generation request and returns the reply payload."""
    try:
        config = ProviderConfig.from_payload(envelope.config)
    except Exception as error:  # noqa: BLE001 - a malformed config must not crash the worker
        return {"ok": False, "errors": [f"invalid configuration: {error}"], "attempts": 0}
    missing = config.missing()
    if missing:
        return {
            "ok": False,
            "errors": [f"the AI provider is not configured (missing: {', '.join(missing)})"],
            "attempts": 0,
        }
    # The budget belongs to the worker instance, the limit comes from the settings.
    state.sync_limit(config.max_requests_per_hour)

    request = GenerationRequest.from_payload(envelope.request)
    try:
        rows = await vocabulary.rows()
    except httpx.HTTPError as error:
        return {
            "ok": False,
            "attempts": 0,
            "errors": [f"the attribute vocabulary could not be loaded: {error}"],
        }
    try:
        return await generate(config, request, vocabulary_rows=rows, state=state)
    except ProviderError as error:  # defensive: `generate` reports these itself
        return {"ok": False, "attempts": 0, "errors": [str(error)]}


async def handle_test(envelope: AiTestEnvelope) -> dict[str, Any]:
    """Runs the connection test for the settings page."""
    try:
        config = ProviderConfig.from_payload(envelope.config)
    except Exception as error:  # noqa: BLE001 - a malformed config must not crash the worker
        return {"ok": False, "errors": [f"invalid configuration: {error}"]}
    try:
        return await test_connection(config)
    except ProviderError as error:  # defensive: `test_connection` reports these itself
        return {"ok": False, "errors": [str(error)]}


#: One NATS handler: the raw message payload in, the reply payload out.
Handler = Callable[[bytes], Awaitable[dict[str, Any]]]


async def _serve_subject(
    nats_url: str,
    subject: str,
    handler: Handler,
    *,
    log_sender: LogSender | None,
) -> None:
    """Subscribes one subject and answers every message (never crashes)."""
    import nats  # imported here so the module stays importable without a broker

    client = await nats.connect(nats_url)
    subscription = await client.subscribe(subject)
    logger.info("AI service subscribed to %s", subject)
    async for message in subscription.messages:
        try:
            reply = await handler(message.data)
        except Exception as error:  # noqa: BLE001 - reply with an error instead of dying
            logger.exception("AI request failed")
            if log_sender is not None:
                log_sender.enqueue(
                    service="pim-worker",
                    level="ERROR",
                    category="app",
                    message=f"AI request on `{subject}` failed: {error}",
                )
            reply = {"ok": False, "attempts": 0, "errors": [str(error)]}
        await message.respond(json.dumps(reply).encode())


async def serve(
    nats_url: str,
    *,
    core_url: str,
    api_key: str | None,
    generate_subject: str = AI_GENERATE_SUBJECT,
    test_subject: str = AI_TEST_SUBJECT,
    log_sender: LogSender | None = None,
) -> None:
    """Serves `ai.generate` and `ai.test` until cancelled."""
    vocabulary = VocabularyCache(core_url, api_key)
    state = ServiceState()

    async def on_generate(data: bytes) -> dict[str, Any]:
        envelope = AiGenerateEnvelope.model_validate_json(data)
        return await handle_generate(envelope, vocabulary=vocabulary, state=state)

    async def on_test(data: bytes) -> dict[str, Any]:
        return await handle_test(AiTestEnvelope.model_validate_json(data))

    await asyncio.gather(
        _serve_subject(nats_url, generate_subject, on_generate, log_sender=log_sender),
        _serve_subject(nats_url, test_subject, on_test, log_sender=log_sender),
    )
