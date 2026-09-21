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
from pathlib import Path
from typing import Any

import httpx

from meadow import tabular
from meadow.ai.context import conventional_names, key_attribute_names, language_codes
from meadow.ai.provider import ProviderConfig, ProviderError
from meadow.ai.service import (
    GenerationRequest,
    PayloadValidator,
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


def connector_types_uri(core_url: str) -> str:
    """URL of the connector type catalog (schema of every connector type)."""
    return f"{loopback_host(core_url).rstrip('/')}/connector-types"


def export_profile_schema_uri(core_url: str) -> str:
    """URL of the export-profile contract (formats + parameter shapes)."""
    return f"{loopback_host(core_url).rstrip('/')}/export-profiles/schema"


def job_schema_uri(core_url: str) -> str:
    """URL of the job contract (job types + their parameters)."""
    return f"{loopback_host(core_url).rstrip('/')}/jobs/schema"


def object_types_uri(core_url: str) -> str:
    """URL of the object type registry (business keys and label attributes)."""
    return f"{loopback_host(core_url).rstrip('/')}/object-types"


def languages_uri(core_url: str) -> str:
    """URL of the language tree (the data languages an import may write)."""
    return f"{loopback_host(core_url).rstrip('/')}/languages"


def ui_labels() -> dict[str, str]:
    """The English UI texts, keyed by i18n key (for the schema's `label_key`).

    The connector schema carries i18n **keys**, not words — the words live in the
    frontend translations, which ship in the same Python package. Passing the
    English text into the prompt is what lets the model understand a field like
    `match_by_key`; a missing file simply means no labels.
    """
    path = Path(__file__).resolve().parents[1] / "frontend" / "locales" / "en.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError:  # a missing or broken locale must not break the worker
        logger.warning("could not read the UI labels from %s", path)
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(key): str(value) for key, value in payload.items() if isinstance(value, str)}


async def post_core(
    core_url: str, api_key: str | None, path: str, body: dict[str, Any]
) -> httpx.Response:
    """POSTs to the core with the machine key (the worker's normal channel)."""
    url = f"{loopback_host(core_url).rstrip('/')}{path}"
    headers = {"x-api-key": api_key} if api_key else {}
    async with httpx.AsyncClient(timeout=30.0) as client:
        return await client.post(url, json=body, headers=headers)


def _verdict(response: httpx.Response) -> tuple[bool, str, dict[str, Any] | None]:
    """Reads a `{ok, errors}` answer (or the core's `{error}`) into the check shape."""
    if response.status_code == 400:
        payload = response.json() if response.content else {}
        message = payload.get("error") if isinstance(payload, dict) else None
        return False, str(message or "the core rejected the configuration"), None
    response.raise_for_status()
    payload = response.json()
    errors = payload.get("errors") if isinstance(payload, dict) else None
    if isinstance(errors, list) and errors:
        return False, "; ".join(str(error) for error in errors), None
    return True, "accepted by the core schema", None


async def validate_connector_config(
    core_url: str, api_key: str | None, connector_type: str, parameters: dict[str, Any]
) -> tuple[bool, str, dict[str, Any] | None]:
    """Validates connector parameters with the core (`POST /connectors/validate`).

    Nothing is written: the core runs exactly the check its write path would run,
    so a suggestion is judged by the component that would reject it later.
    """
    response = await post_core(
        core_url,
        api_key,
        "/connectors/validate",
        {"connector_type": connector_type, "parameters": parameters},
    )
    return _verdict(response)


async def validate_export_profile(
    core_url: str, api_key: str | None, profile: dict[str, Any]
) -> tuple[bool, str, dict[str, Any] | None]:
    """Validates an export profile with the core (``POST /export-profiles/validate``)."""
    response = await post_core(
        core_url,
        api_key,
        "/export-profiles/validate",
        {"format": profile.get("format"), "parameters": profile.get("parameters")},
    )
    return _verdict(response)


def check_import_mapping(
    mapping: dict[str, str],
    *,
    columns: list[dict[str, Any]],
    vocabulary: Any,
    object_types: Any,
    languages: Any,
    object_type: str,
) -> tuple[bool, str, dict[str, Any] | None]:
    """Checks a column→target mapping against the file and the data model.

    The mapping is executed by `meadow.tabular.build_mef`, which builds the MEF
    document the import writes — so the judge is the same module
    (`meadow.tabular.check_mapping`) and the model data the core owns: the attribute
    vocabulary, the object types (business key, label attribute) and the languages.

    Neither the mapping nor this check carries an object value: the columns arrive as
    headers plus a value-free shape hint.
    """
    names = [
        str(entry.get("name") or "")
        for entry in columns
        if isinstance(entry, dict) and entry.get("name")
    ]
    attributes: dict[str, bool] = {}
    if isinstance(vocabulary, list):
        for entry in vocabulary:
            if isinstance(entry, dict) and entry.get("name"):
                attributes[str(entry["name"])] = bool(entry.get("multilingual"))
    verdict = tabular.check_mapping(
        mapping,
        columns=names,
        attributes=attributes,
        languages=language_codes(languages),
        conventional=conventional_names(object_types, object_type),
        key_attributes=key_attribute_names(object_types, object_type),
        default_type=object_type,
    )
    ignored = [column for column in names if not str(mapping.get(column) or "").strip()]
    return (
        verdict.ok,
        verdict.message(),
        {
            "mapped": len(names) - len(ignored),
            "ignored": ignored,
            "warnings": verdict.warnings,
        },
    )


async def validate_job_definition(
    core_url: str, api_key: str | None, job: dict[str, Any]
) -> tuple[bool, str, dict[str, Any] | None]:
    """Validates a job definition with the core (``POST /jobs/validate``).

    The core checks the definition against the declared parameter contract and the
    cron parser the scheduler uses. It is a contract check, **not** the write
    path's check: `POST`/`PATCH /jobs` still accept any parameters object, because
    job definitions also travel through MCF documents (see `api::job_schema`).
    """
    response = await post_core(
        core_url,
        api_key,
        "/jobs/validate",
        {
            "type": job.get("type"),
            "schedule": job.get("schedule"),
            "parameters": job.get("parameters"),
        },
    )
    ok, message, preview = _verdict(response)
    if ok:
        # Say what was checked: the save path stays permissive (see above), so
        # "accepted by the core schema" would promise more than it can hold.
        message = "matches the job parameter contract"
    return ok, message, preview


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
    connector_types: JsonCache,
    export_profile_schema: JsonCache,
    job_schema: JsonCache,
    object_types: JsonCache,
    languages: JsonCache,
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
    json_context: dict[str, Any] = {}
    try:
        if request.area == "search":
            payload = await search_catalog.get()
            catalog = payload if isinstance(payload, dict) else {}
        elif request.area == "connector":
            payload = await connector_types.get()
            json_context = {
                "connector_types": payload if isinstance(payload, list) else [],
                "labels": ui_labels(),
            }
        elif request.area == "export_profile":
            payload = await export_profile_schema.get()
            json_context = {"export_profile_schema": payload if isinstance(payload, dict) else {}}
        elif request.area == "job":
            payload = await job_schema.get()
            json_context = {"job_schema": payload if isinstance(payload, dict) else {}}
        elif request.area == "import_mapping":
            payload = await vocabulary.get()
            vocabulary_rows = (
                [row for row in payload if isinstance(row, dict)]
                if isinstance(payload, list)
                else []
            )
            # The mapping check needs the whole model: the vocabulary for the target
            # names, the object types for business key and label attribute, the
            # language tree for the data languages.
            json_context = {
                "columns": [
                    {"name": column.name, "hint": column.hint} for column in request.columns
                ],
                "object_types": await object_types.get(),
                "languages": await languages.get(),
            }
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
    validator: PayloadValidator | None = None
    config_validator: PayloadValidator | None = None
    if request.area == "search":

        async def validator(
            query: dict[str, Any],
        ) -> tuple[bool, str, dict[str, Any] | None]:
            return await preview_query(core_url, api_key, query)

    if request.area == "connector":
        connector_type = request.config_kind or ""

        async def validate_connector(
            parameters: dict[str, Any],
        ) -> tuple[bool, str, dict[str, Any] | None]:
            return await validate_connector_config(core_url, api_key, connector_type, parameters)

        config_validator = validate_connector
    elif request.area == "export_profile":

        async def validate_profile(
            profile: dict[str, Any],
        ) -> tuple[bool, str, dict[str, Any] | None]:
            return await validate_export_profile(core_url, api_key, profile)

        config_validator = validate_profile
    elif request.area == "job":

        async def validate_job(
            job: dict[str, Any],
        ) -> tuple[bool, str, dict[str, Any] | None]:
            return await validate_job_definition(core_url, api_key, job)

        config_validator = validate_job
    elif request.area == "import_mapping":
        mapping_columns = json_context.get("columns") or []
        model_types = json_context.get("object_types")
        data_languages = json_context.get("languages")

        async def validate_mapping(
            mapping: dict[str, Any],
        ) -> tuple[bool, str, dict[str, Any] | None]:
            return check_import_mapping(
                {str(column): str(target) for column, target in mapping.items()},
                columns=mapping_columns,
                vocabulary=vocabulary_rows,
                object_types=model_types,
                languages=data_languages,
                object_type=request.object_type or "",
            )

        config_validator = validate_mapping

    try:
        return await generate(
            config,
            request,
            vocabulary_rows=vocabulary_rows,
            search_catalog=catalog,
            json_context=json_context,
            validate_query=validator,
            validate_config=config_validator,
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
    connector_types = JsonCache(
        connector_types_uri(core_url), api_key, label="the connector type catalog"
    )
    export_profile_schema = JsonCache(
        export_profile_schema_uri(core_url), api_key, label="the export-profile schema"
    )
    job_schema = JsonCache(job_schema_uri(core_url), api_key, label="the job schema")
    object_types = JsonCache(object_types_uri(core_url), api_key, label="the object type registry")
    languages = JsonCache(languages_uri(core_url), api_key, label="the language tree")
    state = ServiceState()

    async def on_generate(data: bytes) -> dict[str, Any]:
        envelope = AiGenerateEnvelope.model_validate_json(data)
        return await handle_generate(
            envelope,
            core_url=core_url,
            api_key=api_key,
            vocabulary=vocabulary,
            search_catalog=search_catalog,
            connector_types=connector_types,
            export_profile_schema=export_profile_schema,
            job_schema=job_schema,
            object_types=object_types,
            languages=languages,
            state=state,
        )

    async def on_test(data: bytes) -> dict[str, Any]:
        return await handle_test(AiTestEnvelope.model_validate_json(data))

    await asyncio.gather(
        _serve_subject(nats_url, generate_subject, on_generate, log_sender=log_sender),
        _serve_subject(nats_url, test_subject, on_test, log_sender=log_sender),
    )
