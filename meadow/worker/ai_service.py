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

#: How long a fetched core document is reused (seconds).
CACHE_TTL_SECONDS = 300.0


class JsonCache:
    """A JSON document from the core, cached with a TTL.

    The documents this service needs are large — the attribute vocabulary is
    6 MB, the search-field catalog 6.5 MB — so one fetch serves every request in
    the TTL window. A fetch failure raises: a prompt built without its context
    would produce confident nonsense, and the caller reports the error instead.
    """

    def __init__(
        self,
        url: str,
        api_key: str | None,
        *,
        label: str,
        ttl_seconds: float = CACHE_TTL_SECONDS,
    ) -> None:
        self._url = url
        self._api_key = api_key
        self._label = label
        self._ttl = ttl_seconds
        self._payload: Any = None
        self._fetched_at = 0.0
        self._lock = asyncio.Lock()

    async def get(self) -> Any:
        """The cached payload, refetched when the TTL expired."""
        now = time.monotonic()
        if self._payload is not None and now - self._fetched_at < self._ttl:
            return self._payload
        async with self._lock:
            now = time.monotonic()
            if self._payload is not None and now - self._fetched_at < self._ttl:
                return self._payload
            headers = {"x-api-key": self._api_key} if self._api_key else {}
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(self._url, headers=headers)
                response.raise_for_status()
                self._payload = response.json()
            self._fetched_at = time.monotonic()
            logger.info("loaded %s for the AI context", self._label)
            return self._payload


def vocabulary_uri(core_url: str) -> str:
    """URL of the attribute vocabulary (owned by the attribute-definition API)."""
    return f"{loopback_host(core_url).rstrip('/')}/attribute-definitions/vocabulary"


def search_catalog_uri(core_url: str) -> str:
    """URL of the search-field catalog (owned by the saved-search API)."""
    return f"{loopback_host(core_url).rstrip('/')}/search-fields"


async def preview_query(
    core_url: str, api_key: str | None, query: dict[str, Any]
) -> tuple[bool, str, dict[str, Any] | None]:
    """Validates a generated query with the core (`POST /saved-searches/preview`).

    The core owns the DSL: it checks fields, operators, value arity and the
    limits, and answers with the live hit count. A rejection (`400`) comes back as
    `(False, message, None)` so the service can hand it to the model for the one
    correction attempt; a transport failure raises, because that is not a verdict.
    """
    url = f"{loopback_host(core_url).rstrip('/')}/saved-searches/preview"
    headers = {"x-api-key": api_key} if api_key else {}
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(url, json=query, headers=headers)
    if response.status_code == 400:
        payload = response.json() if response.content else {}
        message = payload.get("error") if isinstance(payload, dict) else None
        return False, str(message or "the core rejected the query"), None
    response.raise_for_status()
    payload = response.json()
    count = payload.get("count") if isinstance(payload, dict) else None
    return True, f"{count} hits" if count is not None else "accepted", payload


async def handle_generate(
    envelope: AiGenerateEnvelope,
    *,
    core_url: str,
    api_key: str | None,
    vocabulary: JsonCache,
    search_catalog: JsonCache,
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
    # Only what the area needs: a search request has no use for the attribute
    # vocabulary, a rule has none for the field catalog.
    vocabulary_rows: list[dict[str, Any]] = []
    catalog: dict[str, Any] = {}
    try:
        if request.area == "search":
            payload = await search_catalog.get()
            catalog = payload if isinstance(payload, dict) else {}
        else:
            payload = await vocabulary.get()
            vocabulary_rows = (
                [row for row in payload if isinstance(row, dict)]
                if isinstance(payload, list)
                else []
            )
    except httpx.HTTPError as error:
        return {
            "ok": False,
            "attempts": 0,
            "errors": [f"the AI context could not be loaded from the core: {error}"],
        }
    validator = None
    if request.area == "search":

        async def validator(
            query: dict[str, Any],
        ) -> tuple[bool, str, dict[str, Any] | None]:
            return await preview_query(core_url, api_key, query)

    try:
        return await generate(
            config,
            request,
            vocabulary_rows=vocabulary_rows,
            search_catalog=catalog,
            validate_query=validator,
            state=state,
        )
    except (ProviderError, httpx.HTTPError) as error:
        # A transport failure while validating is not a verdict about the query.
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
    vocabulary = JsonCache(vocabulary_uri(core_url), api_key, label="the attribute vocabulary")
    search_catalog = JsonCache(
        search_catalog_uri(core_url), api_key, label="the search-field catalog"
    )
    state = ServiceState()

    async def on_generate(data: bytes) -> dict[str, Any]:
        envelope = AiGenerateEnvelope.model_validate_json(data)
        return await handle_generate(
            envelope,
            core_url=core_url,
            api_key=api_key,
            vocabulary=vocabulary,
            search_catalog=search_catalog,
            state=state,
        )

    async def on_test(data: bytes) -> dict[str, Any]:
        return await handle_test(AiTestEnvelope.model_validate_json(data))

    await asyncio.gather(
        _serve_subject(nats_url, generate_subject, on_generate, log_sender=log_sender),
        _serve_subject(nats_url, test_subject, on_test, log_sender=log_sender),
    )
