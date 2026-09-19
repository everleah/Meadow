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
    attribute_facts,
    contract_block,
    declared_computed,
    few_shot_examples,
    policy_block,
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

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> GenerationRequest:
        """Builds the request from the core's `request` object (tolerant of `null`)."""

        def text(key: str) -> str | None:
            value = payload.get(key)
            return value if isinstance(value, str) and value.strip() else None

        declared = payload.get("declared_computed")
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


def _parse_artefact(area: str, text: str) -> dict[str, Any]:
    """Reads the model's answer into the fields the editor fills.

    A `rule` answer is the script itself; a `computed_attribute` answer is the
    JSON envelope with the script plus its metadata. A malformed envelope is an
    error, not a silent empty suggestion.
    """
    cleaned = strip_fences(text)
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
    chat_fn: ChatFn = provider.chat,
    state: ServiceState | None = None,
) -> dict[str, Any]:
    """Produces one suggestion (or reports why there is none).

    The reply is JSON-ready for the core, which forwards it to the editor:
    `{"ok": true, "script": ..., "attempts": 1, ...}` on success and
    `{"ok": false, "errors": [...], "attempts": 2, ...}` after a failed retry.
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

        ok, message = check_script(artefact["script"])
        if ok:
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
