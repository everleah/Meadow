"""The generation service: context → provider call → policy check → one retry.

This is the whole loop, and it is deliberately small — no agent framework, no
tool chain, no memory (`plan/detail/ki-assistenz.md` §3). Two properties matter:

* **A suggestion is never trusted.** Whatever the model returns goes through
  [`check_script`][meadow.ai.policy.check_script] — the AST pre-check plus the
  real sandbox compile policy, the same gate a hand-written script passes. The
  first failure is reported back to the model once; a second failure ends the
  call with the error list.
* **Nothing is stored.** The result travels back to the editor, where the user
  reviews it and saves through the normal path.

Object **values** never enter a prompt — only names, types and the flag whether
an attribute is computed (§6.8).
"""

from __future__ import annotations

import json
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from meadow.ai import prompts, provider
from meadow.ai.context import (
    ColumnFact,
    allowed_values_text,
    attribute_facts,
    column_facts,
    connector_fields_text,
    connector_type_facts,
    connector_types_text,
    contract_block,
    conventional_names,
    declared_computed,
    few_shot_examples,
    job_operation_facts,
    job_operations_text,
    job_parameter_facts,
    job_schedule_text,
    job_types_text,
    key_attribute_names,
    language_codes,
    mapping_columns_text,
    mapping_targets_text,
    parameter_facts,
    parameters_text,
    permission_facts,
    permissions_text,
    policy_block,
    scope_schema_text,
    search_field_facts,
    search_fields_text,
    search_limits_text,
    search_operators_text,
)
from meadow.ai.policy import check_script
from meadow.ai.provider import ChatReply, ProviderConfig, ProviderError

#: The self-correction attempt: one, then the user sees the error (§6.5).
MAX_ATTEMPTS = 2

_FENCE = re.compile(r"^\s*```[A-Za-z]*\s*\n(?P<body>.*?)\n?\s*```\s*$", re.DOTALL)


def strip_fences(text: str) -> str:
    """Removes a Markdown code fence around the whole answer (models add them)."""
    match = _FENCE.match(text.strip())
    return (match.group("body") if match else text).strip()


@dataclass(frozen=True)
class GenerationRequest:
    """One generation request, as the editor sent it (mirrors `api::ai::GenerateRequest`)."""

    area: str
    mode: str
    description: str = ""
    rule_type: str | None = None
    object_type: str | None = None
    current_script: str | None = None
    target_attribute: str | None = None
    declared_computed: tuple[str, ...] = ()
    language: str | None = None
    #: Concrete target of a JSON-configuration area (`connector` → the connector
    #: type key, `job` → the job type key, `import_mapping` → the default object type).
    config_kind: str | None = None
    #: The columns of the uploaded file (`import_mapping`), in file order.
    columns: tuple[ColumnFact, ...] = ()
    #: The workflow states the scope card offers (`group_permissions`): the editor's own
    #: option list, so a suggestion can never name a state the card cannot show.
    statuses: tuple[str, ...] = ()

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> GenerationRequest:
        """Builds the request from the core's `request` object (tolerant of `null`)."""

        def text(key: str) -> str | None:
            value = payload.get(key)
            return value if isinstance(value, str) and value.strip() else None

        declared = payload.get("declared_computed")
        statuses = payload.get("statuses")
        return cls(
            area=str(payload.get("area") or ""),
            mode=str(payload.get("mode") or ""),
            description=str(payload.get("description") or ""),
            rule_type=text("rule_type"),
            object_type=text("object_type"),
            current_script=text("current_script"),
            target_attribute=text("target_attribute"),
            declared_computed=tuple(str(name) for name in declared if isinstance(name, str))
            if isinstance(declared, list)
            else (),
            language=text("language"),
            config_kind=text("config_kind"),
            columns=column_facts(payload.get("columns")),
            statuses=tuple(str(state) for state in statuses if isinstance(state, str))
            if isinstance(statuses, list)
            else (),
        )


class HourlyBudget:
    """The cost brake: at most `limit` calls per rolling hour (`0` = unlimited).

    In-memory on purpose — it protects the provider bill of *this* worker
    instance, and losing the counter on a restart is harmless.
    """

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._calls: deque[float] = deque()

    def allow(self, now: float | None = None) -> bool:
        """Whether one more call fits into the window (and counts it if so)."""
        if self.limit <= 0:
            return True
        current = time.monotonic() if now is None else now
        while self._calls and current - self._calls[0] >= 3600:
            self._calls.popleft()
        if len(self._calls) >= self.limit:
            return False
        self._calls.append(current)
        return True


@dataclass
class ServiceState:
    """Stateful bits the service keeps between calls (rate budget only)."""

    budget: HourlyBudget = field(default_factory=lambda: HourlyBudget(0))

    def sync_limit(self, limit: int) -> None:
        """Applies the configured limit (the settings page can change it live)."""
        if self.budget.limit != limit:
            self.budget = HourlyBudget(limit)


#: `async (config, messages) -> ChatReply` — injectable so tests need no network.
ChatFn = Callable[[ProviderConfig, list[dict[str, str]]], Awaitable[ChatReply]]

#: `async (payload) -> (ok, message, preview)` — checks one artefact in the core
#: (`/saved-searches/preview`, `/connectors/validate`, `/export-profiles/validate`,
#: `/jobs/validate`) or against the data model (the import-mapping check in
#: `meadow.tabular`), injectable so tests need no core.
PayloadValidator = Callable[[dict[str, Any]], Awaitable[tuple[bool, str, dict[str, Any] | None]]]


def _parse_artefact(area: str, text: str) -> dict[str, Any]:
    """Reads the model's answer into the fields the editor fills.

    A `rule` answer is the script itself; a `computed_attribute` answer is the
    JSON envelope with the script plus its metadata; a `search` answer is the
    query object; a `connector`, `export_profile`, `job` or `import_mapping`
    answer is the JSON object the editor fills in; a `group_permissions` answer is
    the group document (both permission lists and the scope) plus an optional
    `note`. A malformed answer is an error, not a silent empty suggestion.
    """
    cleaned = strip_fences(text)
    if area == "group_permissions":
        try:
            payload = json.loads(cleaned)
        except ValueError as error:
            raise ProviderError(f"the model did not answer with JSON: {error}") from error
        if not isinstance(payload, dict):
            raise ProviderError("the answer is not a JSON object")
        scope = payload.get("scope")
        rules = scope.get("rules") if isinstance(scope, dict) else None
        if rules is not None and not isinstance(rules, list):
            raise ProviderError("`scope.rules` must be a list of rules")
        note = payload.get("note")
        return {
            "group": {
                "grant_permissions": _as_str_list(payload.get("grant_permissions")),
                "deny_permissions": _as_str_list(payload.get("deny_permissions")),
                "scope": {"rules": rules} if isinstance(rules, list) else {},
            },
            "note": note if isinstance(note, str) else "",
        }
    if area == "import_mapping":
        try:
            payload = json.loads(cleaned)
        except ValueError as error:
            raise ProviderError(f"the model did not answer with JSON: {error}") from error
        mapping = payload.get("mapping") if isinstance(payload, dict) else None
        if not isinstance(mapping, dict) or not mapping:
            raise ProviderError("the answer JSON has no `mapping` object")
        return {
            "mapping": {
                str(column): str(target or "").strip() for column, target in mapping.items()
            }
        }
    if area in ("search", "connector", "job"):
        try:
            payload = json.loads(cleaned)
        except ValueError as error:
            raise ProviderError(f"the model did not answer with JSON: {error}") from error
        if not isinstance(payload, dict) or not payload:
            raise ProviderError("the answer is not a JSON object")
        if area == "search":
            return {"query": payload}
        if area == "connector":
            return {"config": payload}
        # A job definition always carries `type` and `parameters`; the schedule is
        # optional and `null` is the same as absent.
        parameters = payload.get("parameters")
        if parameters is not None and not isinstance(parameters, dict):
            raise ProviderError("`parameters` must be an object")
        return {
            "job": {
                "type": str(payload.get("type") or ""),
                "schedule": payload.get("schedule")
                if isinstance(payload.get("schedule"), str)
                else None,
                "parameters": parameters or {},
            }
        }
    if area == "export_profile":
        try:
            payload = json.loads(cleaned)
        except ValueError as error:
            raise ProviderError(f"the model did not answer with JSON: {error}") from error
        if not isinstance(payload, dict) or not isinstance(payload.get("format"), str):
            raise ProviderError("the answer JSON has no `format` field")
        parameters = payload.get("parameters")
        if parameters is not None and not isinstance(parameters, dict):
            raise ProviderError("`parameters` must be an object")
        return {"profile": {"format": payload["format"], "parameters": parameters or {}}}
    if area != "computed_attribute":
        return {"script": cleaned}
    try:
        payload = json.loads(cleaned)
    except ValueError as error:
        raise ProviderError(f"the model did not answer with a JSON object: {error}") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("script"), str):
        raise ProviderError("the answer JSON has no `script` field")
    return payload


def _as_str_list(value: Any) -> list[str]:
    """A JSON value as a list of strings (anything else becomes empty)."""
    if not isinstance(value, list):
        return []
    return [
        str(item) for item in value if isinstance(item, (str, int, float)) and str(item).strip()
    ]


async def generate(
    config: ProviderConfig,
    request: GenerationRequest,
    *,
    vocabulary_rows: list[dict[str, Any]] | None = None,
    search_catalog: dict[str, Any] | None = None,
    json_context: dict[str, Any] | None = None,
    validate_query: PayloadValidator | None = None,
    validate_config: PayloadValidator | None = None,
    chat_fn: ChatFn = provider.chat,
    state: ServiceState | None = None,
) -> dict[str, Any]:
    """Produces one suggestion (or reports why there is none).

    The reply is JSON-ready for the core, which forwards it to the editor:
    `{"ok": true, "script": ..., "attempts": 1, ...}` on success and
    `{"ok": false, "errors": [...], "attempts": 2, ...}` after a failed retry.

    The check differs by area: a script goes through the sandbox policy
    (`check_script`), a search query through the **core's own validator**
    (`validate_query` → `POST /saved-searches/preview`), a JSON configuration and a
    group document through the core's matching endpoint (`validate_config`), which is
    the component that would accept or refuse the save.
    """
    if request.area not in prompts.AREAS:
        return {"ok": False, "errors": [f"unknown area `{request.area}`"], "attempts": 0}
    if request.mode not in prompts.MODES:
        return {"ok": False, "errors": [f"unknown mode `{request.mode}`"], "attempts": 0}

    service_state = state or ServiceState()
    if not service_state.budget.allow():
        return {
            "ok": False,
            "attempts": 0,
            "errors": [
                f"the hourly budget of {config.max_requests_per_hour} AI requests is used up"
            ],
        }

    if request.area == "search":
        catalog = search_catalog or {}
        task = prompts.search_user_prompt(
            mode=request.mode,
            description=request.description,
            fields=search_fields_text(search_field_facts(catalog)),
            operators=search_operators_text(catalog),
            limits=search_limits_text(catalog),
            current_query=request.current_script,
            language=request.language,
        )
    elif request.area == "connector":
        schema = (json_context or {}).get("connector_types")
        labels = (json_context or {}).get("labels")
        type_key = request.config_kind or ""
        connector_facts = connector_type_facts(schema, type_key, labels)
        subject = f"Connector type: {type_key or '(unknown)'}"
        types = connector_types_text(schema, labels)
        if type_key and not connector_facts:
            return {
                "ok": False,
                "attempts": 0,
                "errors": [f"unknown connector type `{type_key}`"],
            }
        task = prompts.config_user_prompt(
            area=request.area,
            mode=request.mode,
            description=request.description,
            subject=subject + ("\n" + types if types else ""),
            fields=connector_fields_text(connector_facts),
            current_config=request.current_script,
            language=request.language,
        )
    elif request.area == "export_profile":
        schema = (json_context or {}).get("export_profile_schema")
        task = prompts.config_user_prompt(
            area=request.area,
            mode=request.mode,
            description=request.description,
            subject=allowed_values_text(schema) or "Export profile",
            fields=parameters_text(parameter_facts(schema)),
            current_config=request.current_script,
            language=request.language,
        )
    elif request.area == "job":
        schema = (json_context or {}).get("job_schema")
        job_type = request.config_kind or ""
        if not job_types_text(schema):
            return {
                "ok": False,
                "attempts": 0,
                "errors": ["the job contract could not be loaded from the core"],
            }
        parameters = job_parameter_facts(schema, job_type)
        if not parameters:
            return {
                "ok": False,
                "attempts": 0,
                "errors": [f"unknown job type `{job_type}`"],
            }
        # The subject carries everything that is not a `parameters` field: the type
        # with its meaning, the operations of `bulk_operation` and the schedule rule.
        subject_parts = [f"Job type: {job_type}"]
        operations = job_operation_facts(schema, job_type)
        if operations:
            subject_parts.append(
                "Operations of this type, with the parameters each reads:\n"
                + job_operations_text(operations)
            )
        schedule = job_schedule_text(schema)
        if schedule:
            subject_parts.append(f"Schedule: {schedule}")
        task = prompts.config_user_prompt(
            area=request.area,
            mode=request.mode,
            description=request.description,
            subject="\n".join(subject_parts),
            fields=parameters_text(parameters),
            current_config=request.current_script,
            language=request.language,
        )
    elif request.area == "import_mapping":
        object_types = (json_context or {}).get("object_types")
        languages = language_codes((json_context or {}).get("languages"))
        object_type = request.object_type or ""
        conventional = conventional_names(object_types, object_type)
        keys = key_attribute_names(object_types, object_type)
        facts = attribute_facts(vocabulary_rows or [], object_type)
        if not request.columns:
            return {
                "ok": False,
                "attempts": 0,
                "errors": ["no columns: the file has not been read"],
            }
        subject_parts = [
            f"Object type: {object_type}" if object_type else "Object type: not chosen",
        ]
        if keys:
            subject_parts.append(
                "Business key of this type: " + ", ".join(keys) + " — a column must fill it"
            )
        if languages:
            subject_parts.append("Data languages: " + ", ".join(languages))
        task = prompts.mapping_user_prompt(
            mode=request.mode,
            description=request.description,
            subject="\n".join(subject_parts),
            targets=mapping_targets_text(facts, conventional=conventional, languages=languages),
            columns=mapping_columns_text(request.columns),
            current_mapping=request.current_script,
            language=request.language,
        )
    elif request.area == "group_permissions":
        permission_rows = permission_facts((json_context or {}).get("permission_catalog"))
        if not permission_rows:
            return {
                "ok": False,
                "attempts": 0,
                "errors": ["the permission catalog could not be loaded from the core"],
            }
        task = prompts.group_permissions_user_prompt(
            mode=request.mode,
            description=request.description,
            permissions=permissions_text(permission_rows),
            scope_schema=scope_schema_text(
                (json_context or {}).get("object_types"), request.statuses
            ),
            current_state=request.current_script,
            language=request.language,
        )
    else:
        facts = attribute_facts(vocabulary_rows or [], request.object_type or "")
        declared = declared_computed(facts)
        # The editor knows the declared names too; its list wins when it sends one,
        # so a suggestion can never write a name the dialog does not offer.
        writable = request.declared_computed or declared
        examples = few_shot_examples(request.rule_type or "validation")
        task = prompts.user_prompt(
            area=request.area,
            mode=request.mode,
            description=request.description,
            rule_type=request.rule_type,
            object_type=request.object_type,
            facts=facts,
            declared=writable,
            examples=examples,
            contract=contract_block(),
            policy=policy_block(),
            current_script=request.current_script,
            target_attribute=request.target_attribute,
            language=request.language,
        )

    messages: list[dict[str, str]] = [
        {"role": "system", "content": prompts.system_prompt(request.area, request.mode)},
        {"role": "user", "content": task},
    ]
    attempts = 0
    errors: list[str] = []
    model = config.model
    latency_ms = 0
    usage: dict[str, Any] = {}

    while attempts < MAX_ATTEMPTS:
        attempts += 1
        try:
            reply = await chat_fn(config, messages)
        except ProviderError as error:
            return {
                "ok": False,
                "attempts": attempts,
                "errors": [str(error)],
                "model": model,
                "latency_ms": latency_ms,
            }
        model = reply.model or model
        latency_ms += reply.latency_ms
        usage = reply.usage
        if request.mode == "explain":
            return {
                "ok": True,
                "attempts": attempts,
                "description": strip_fences(reply.text),
                "model": model,
                "latency_ms": latency_ms,
                "usage": usage,
            }
        try:
            artefact = _parse_artefact(request.area, reply.text)
        except ProviderError as error:
            errors = [str(error)]
            messages = [
                *messages,
                {"role": "assistant", "content": reply.text},
                {"role": "user", "content": prompts.retry_prompt(str(error))},
            ]
            continue

        ok, message, extra = await _check_artefact(
            request.area,
            artefact,
            validate_query=validate_query,
            validate_config=validate_config,
        )
        if ok:
            if request.area == "search":
                return {
                    "ok": True,
                    "attempts": attempts,
                    "query": artefact["query"],
                    "preview": extra,
                    "check": message,
                    "model": model,
                    "latency_ms": latency_ms,
                    "usage": usage,
                }
            if request.area == "connector":
                return {
                    "ok": True,
                    "attempts": attempts,
                    "config": artefact["config"],
                    "check": message,
                    "model": model,
                    "latency_ms": latency_ms,
                    "usage": usage,
                }
            if request.area == "export_profile":
                return {
                    "ok": True,
                    "attempts": attempts,
                    "profile": artefact["profile"],
                    "check": message,
                    "model": model,
                    "latency_ms": latency_ms,
                    "usage": usage,
                }
            if request.area == "job":
                return {
                    "ok": True,
                    "attempts": attempts,
                    "job": artefact["job"],
                    "check": message,
                    "model": model,
                    "latency_ms": latency_ms,
                    "usage": usage,
                }
            if request.area == "import_mapping":
                return {
                    "ok": True,
                    "attempts": attempts,
                    "mapping": artefact["mapping"],
                    "preview": extra,
                    "check": message,
                    "model": model,
                    "latency_ms": latency_ms,
                    "usage": usage,
                }
            if request.area == "group_permissions":
                return {
                    "ok": True,
                    "attempts": attempts,
                    "permissions": artefact["group"],
                    "note": artefact.get("note") or "",
                    "check": message,
                    "model": model,
                    "latency_ms": latency_ms,
                    "usage": usage,
                }
            return {
                "ok": True,
                "attempts": attempts,
                "script": artefact["script"],
                "rule_name": _name(artefact.get("rule_name")),
                "description": (
                    artefact.get("description")
                    if isinstance(artefact.get("description"), str)
                    else ""
                ),
                "dependencies": _as_str_list(artefact.get("dependencies")),
                "computed_reads_inherited": artefact.get("computed_reads_inherited") is True,
                "check": message,
                "model": model,
                "latency_ms": latency_ms,
                "usage": usage,
            }
        errors = [message]
        messages = [
            *messages,
            {"role": "assistant", "content": reply.text},
            {"role": "user", "content": prompts.retry_prompt(message)},
        ]

    return {
        "ok": False,
        "attempts": attempts,
        "errors": errors,
        "script": None,
        "model": model,
        "latency_ms": latency_ms,
        "usage": usage,
    }


async def _check_artefact(
    area: str,
    artefact: dict[str, Any],
    *,
    validate_query: PayloadValidator | None,
    validate_config: PayloadValidator | None,
) -> tuple[bool, str, dict[str, Any] | None]:
    """Checks one artefact with the component that will execute it.

    A script passes the sandbox policy (`check_script`). A search query and a
    configuration object (`connector`, `export_profile`, `job`) pass the **core's
    own validation** — the worker wires the matching endpoint in
    (`/saved-searches/preview`, `/connectors/validate`, `/export-profiles/validate`,
    `/jobs/validate`). An import mapping passes the check in `meadow.tabular`, the
    module that builds the MEF document the import writes from. A group document
    passes `POST /groups/validate`, the write path's own check (catalog + scope).

    Only for connectors, export profiles, groups and imports is that the save path's
    own check; a job definition survives a mapping the contract rejects (see
    `api::job_schema`), so there a green answer means "matches the contract", not
    "the save will succeed".
    """
    if area in ("connector", "export_profile", "job", "import_mapping", "group_permissions"):
        if area == "connector":
            payload = artefact["config"]
        elif area == "job":
            payload = artefact["job"]
        elif area == "import_mapping":
            payload = artefact["mapping"]
        elif area == "group_permissions":
            payload = artefact["group"]
        else:
            payload = artefact["profile"]
        if validate_config is None:
            return True, "well-formed JSON (not validated against the core)", None
        return await validate_config(payload)
    if area != "search":
        ok, message = check_script(artefact["script"])
        return ok, message, None
    if validate_query is None:
        # Without a validator the query is only well-formed JSON; say so instead
        # of claiming it was checked.
        return True, "well-formed JSON (not validated against the core)", None
    ok, message, preview = await validate_query(artefact["query"])
    return ok, message, preview


def _name(value: Any) -> str:
    """The suggested rule name, reduced to a safe identifier."""
    if not isinstance(value, str):
        return ""
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", value.strip()).strip("_").lower()
    return cleaned[:64]


async def test_connection(
    config: ProviderConfig, *, chat_fn: ChatFn = provider.chat
) -> dict[str, Any]:
    """A minimal round trip for the settings page's "test connection" button."""
    missing = config.missing()
    if missing:
        return {"ok": False, "errors": [f"missing settings: {', '.join(missing)}"]}
    try:
        reply = await chat_fn(
            config, [{"role": "user", "content": "Reply with the single word: ok"}]
        )
    except ProviderError as error:
        return {"ok": False, "errors": [str(error)], "model": config.model}
    return {
        "ok": True,
        "model": reply.model or config.model,
        "latency_ms": reply.latency_ms,
        "answer": reply.text.strip()[:120],
    }
