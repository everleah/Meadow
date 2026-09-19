"""Tests for `meadow.ai.service` — the generation loop with a mocked provider.

The interesting properties are not "does it produce code" but:

* a suggestion that violates the sandbox policy is **never** returned as success,
* the self-correction attempt happens exactly once (no loop),
* a failing provider, a malformed answer and an exhausted budget each end as
  `ok: false` with a message a human can act on,
* the prompt carries the projection (the own attributes) and **no** standard
  feature — the measured reason for the projection
  (`plan/detail/ki-assistenz.md` §2).
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from meadow.ai.provider import (  # noqa: E402 - after the path fix
    ChatReply,
    ProviderConfig,
    ProviderError,
)
from meadow.ai.service import (  # noqa: E402
    GenerationRequest,
    HourlyBudget,
    ServiceState,
    generate,
    strip_fences,
)

# Imported under an alias on purpose: a plain `test_connection` name in this module
# would be collected by pytest as a test and fail on its missing fixtures.
from meadow.ai.service import test_connection as run_connection_test  # noqa: E402

VALID_SCRIPT = """def run(obj, ctx, result):
    net = obj["attributes"].get("net_price")
    if isinstance(net, (int, float)):
        result.set_value("gross_price", round(net * 1.19, 2))
"""

# `hasattr` passes the RestrictedPython compiler and fails at runtime — exactly
# the class of mistake the AST pre-check exists for.
FORBIDDEN_SCRIPT = """def run(obj, ctx, result):
    if hasattr(obj, "attributes"):
        result.set_value("gross_price", 1)
"""

VOCABULARY: list[dict[str, Any]] = [
    {"name": "net_price", "type": "number", "applicable_types": ["product"]},
    {"name": "gross_price", "type": "number", "applicable_types": ["product"], "computed": True},
    {"name": "name", "type": "text"},
    {"name": "EF000123", "type": "number", "reference_data": True, "applicable_types": ["product"]},
]

CONFIG = ProviderConfig(
    provider="generic",
    endpoint="https://api.example.com/v1",
    model="some-model",
    api_key="sk-test",
)


class Recorder:
    """A fake provider that answers with a scripted sequence of answers."""

    def __init__(self, *answers: str | Exception) -> None:
        self.answers = list(answers)
        self.messages: list[list[dict[str, str]]] = []

    async def __call__(self, config: ProviderConfig, messages: list[dict[str, str]]) -> ChatReply:
        del config
        self.messages.append([dict(message) for message in messages])
        answer = self.answers[min(len(self.messages) - 1, len(self.answers) - 1)]
        if isinstance(answer, Exception):
            raise answer
        return ChatReply(text=answer, model="some-model", latency_ms=10, usage={"total_tokens": 5})


def _request(**overrides: Any) -> GenerationRequest:
    values: dict[str, Any] = {
        "area": "computed_attribute",
        "mode": "create",
        "description": "Gross price from the net price with 19 % VAT",
        "rule_type": "computed",
        "object_type": "product",
        "target_attribute": "gross_price",
    }
    values.update(overrides)
    return GenerationRequest(**values)


def _envelope(script: str, **extra: Any) -> str:
    payload: dict[str, Any] = {
        "script": script,
        "rule_name": "calc_gross_price",
        "dependencies": ["net_price"],
        "computed_reads_inherited": False,
        "description": "Computes the gross price.",
    }
    payload.update(extra)
    return json.dumps(payload)


def test_strip_fences_removes_a_markdown_wrapper() -> None:
    assert strip_fences("```python\ndef run():\n    pass\n```") == "def run():\n    pass"
    assert strip_fences("def run():\n    pass") == "def run():\n    pass"


def test_a_valid_answer_is_returned_with_its_metadata() -> None:
    recorder = Recorder(_envelope(VALID_SCRIPT))
    reply = asyncio.run(generate(CONFIG, _request(), vocabulary_rows=VOCABULARY, chat_fn=recorder))
    assert reply["ok"] is True
    assert reply["attempts"] == 1
    assert reply["script"] == VALID_SCRIPT
    assert reply["rule_name"] == "calc_gross_price"
    assert reply["dependencies"] == ["net_price"]
    assert reply["computed_reads_inherited"] is False
    assert reply["model"] == "some-model"


def test_the_prompt_carries_the_projection_and_the_contract_but_no_standard_feature() -> None:
    recorder = Recorder(_envelope(VALID_SCRIPT))
    asyncio.run(generate(CONFIG, _request(), vocabulary_rows=VOCABULARY, chat_fn=recorder))
    system, user = recorder.messages[0][0]["content"], recorder.messages[0][1]["content"]
    assert "run(obj, ctx, result)" in system
    # The measured projection: own attributes with their type, nothing else.
    assert "gross_price:number(computed)" in user
    assert "net_price:number" in user
    assert "EF000123" not in user  # reference data (the standard's vocabulary) stays out
    # The contract and the policy come from the runtime, not from a copied text.
    assert "result.add_error" in user
    assert "Sandbox policy" in user
    # The house style is shown, and the writable names are named.
    assert "Declared as computed" in user
    assert "gross_price" in user.split("Declared as computed")[1].split("\n")[1]
    assert "result.set_value" in user


def test_a_policy_violation_is_sent_back_once_and_a_second_one_fails() -> None:
    recorder = Recorder(_envelope(FORBIDDEN_SCRIPT), _envelope(FORBIDDEN_SCRIPT))
    reply = asyncio.run(generate(CONFIG, _request(), vocabulary_rows=VOCABULARY, chat_fn=recorder))
    assert reply["ok"] is False
    assert reply["attempts"] == 2  # exactly one self-correction attempt
    assert any("hasattr" in str(message) for message in reply["errors"])
    # The correction message quotes the policy error verbatim.
    correction = recorder.messages[1][-1]["content"]
    assert "hasattr" in correction
    assert len(recorder.messages) == 2


def test_the_self_correction_turn_can_rescue_the_answer() -> None:
    recorder = Recorder(_envelope(FORBIDDEN_SCRIPT), _envelope(VALID_SCRIPT))
    reply = asyncio.run(generate(CONFIG, _request(), vocabulary_rows=VOCABULARY, chat_fn=recorder))
    assert reply["ok"] is True
    assert reply["attempts"] == 2
    assert reply["script"] == VALID_SCRIPT


def test_a_provider_failure_is_reported_and_never_retried() -> None:
    recorder = Recorder(ProviderError("provider answered 401: invalid api key"))
    reply = asyncio.run(generate(CONFIG, _request(), chat_fn=recorder))
    assert reply["ok"] is False
    assert reply["attempts"] == 1
    assert "invalid api key" in str(reply["errors"][0])
    assert len(recorder.messages) == 1


def test_an_answer_without_the_json_envelope_is_a_failure() -> None:
    recorder = Recorder("Here is your rule:\n\n" + VALID_SCRIPT, "still not JSON")
    reply = asyncio.run(generate(CONFIG, _request(), chat_fn=recorder))
    assert reply["ok"] is False
    assert any("JSON" in str(message) for message in reply["errors"])


def test_a_plain_rule_answer_needs_no_envelope() -> None:
    script = (
        'def run(obj, ctx, result):\n    result.add_error("sku", "missing", "SKU is missing")\n'
    )
    recorder = Recorder(f"```python\n{script}```")
    reply = asyncio.run(
        generate(
            CONFIG,
            _request(area="rule", rule_type="validation", target_attribute=None),
            vocabulary_rows=VOCABULARY,
            chat_fn=recorder,
        )
    )
    assert reply["ok"] is True
    assert reply["script"] == script.strip()


def test_explain_returns_prose_instead_of_a_script() -> None:
    recorder = Recorder("The rule fills the gross price from the net price and 19 % VAT.")
    reply = asyncio.run(
        generate(
            CONFIG,
            _request(mode="explain", current_script=VALID_SCRIPT),
            chat_fn=recorder,
        )
    )
    assert reply["ok"] is True
    assert reply["description"].startswith("The rule fills")
    assert "script" not in reply
    # An explain request asks for prose: the system prompt names no artefact format.
    assert "prose" in recorder.messages[0][0]["content"]


def test_an_unknown_area_or_mode_is_rejected_before_any_call() -> None:
    recorder = Recorder(_envelope(VALID_SCRIPT))
    assert asyncio.run(generate(CONFIG, _request(area="search"), chat_fn=recorder))["ok"] is False
    assert asyncio.run(generate(CONFIG, _request(mode="rewrite"), chat_fn=recorder))["ok"] is False
    assert recorder.messages == []


def test_the_hourly_budget_stops_the_calls() -> None:
    budget = HourlyBudget(2)
    assert budget.allow(now=0.0) is True
    assert budget.allow(now=1.0) is True
    assert budget.allow(now=2.0) is False
    # An hour later the oldest call left the window.
    assert budget.allow(now=3601.0) is True
    # Zero means unlimited.
    assert HourlyBudget(0).allow(now=0.0) is True

    recorder = Recorder(_envelope(VALID_SCRIPT))
    state = ServiceState(HourlyBudget(1))
    first = asyncio.run(
        generate(
            CONFIG,
            _request(),
            vocabulary_rows=VOCABULARY,
            chat_fn=recorder,
            state=state,
        )
    )
    second = asyncio.run(
        generate(
            CONFIG,
            _request(),
            vocabulary_rows=VOCABULARY,
            chat_fn=recorder,
            state=state,
        )
    )
    assert first["ok"] is True
    assert second["ok"] is False
    assert "budget" in str(second["errors"][0])


def test_the_connection_test_reports_success_and_missing_settings() -> None:
    recorder = Recorder("ok")
    reply = asyncio.run(run_connection_test(CONFIG, chat_fn=recorder))
    assert reply["ok"] is True
    assert reply["model"] == "some-model"
    assert reply["answer"] == "ok"

    incomplete = ProviderConfig(endpoint="", model="", api_key="")
    reply = asyncio.run(run_connection_test(incomplete, chat_fn=recorder))
    assert reply["ok"] is False
    assert "ai_endpoint" in str(reply["errors"][0])

    failing = Recorder(ProviderError("provider request failed: connection refused"))
    reply = asyncio.run(run_connection_test(CONFIG, chat_fn=failing))
    assert reply["ok"] is False
    assert "connection refused" in str(reply["errors"][0])
