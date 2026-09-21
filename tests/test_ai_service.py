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

from meadow.ai.context import ColumnFact  # noqa: E402 - after the path fix
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
            _request(mode="explain", current_script=VALID_SCRIPT, language="de"),
            chat_fn=recorder,
        )
    )
    assert reply["ok"] is True
    assert reply["description"].startswith("The rule fills")
    assert "script" not in reply
    # An explain request asks for prose: the system prompt names no artefact format.
    assert "prose" in recorder.messages[0][0]["content"]
    # The prose follows the user's language — the sentence applies to the answer
    # itself here, not only to a description field (measured on a real model).
    assert "language `de`" in recorder.messages[0][1]["content"]


def test_an_unknown_area_or_mode_is_rejected_before_any_call() -> None:
    recorder = Recorder(_envelope(VALID_SCRIPT))
    # A bogus area must not cost a model call (the core validates the same list).
    unknown_area = asyncio.run(generate(CONFIG, _request(area="report"), chat_fn=recorder))
    assert unknown_area["ok"] is False
    unknown_mode = asyncio.run(generate(CONFIG, _request(mode="rewrite"), chat_fn=recorder))
    assert unknown_mode["ok"] is False
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


SEARCH_CATALOG: dict[str, Any] = {
    "fields": [
        {"key": "type", "value_type": "string", "group": "system", "operators": ["eq", "ne"]},
        {
            "key": "attributes.net_price",
            "value_type": "number",
            "group": "attribute",
            "operators": ["eq", "gt", "between"],
        },
        {
            "key": "attributes.EF000123",
            "value_type": "number",
            "group": "attribute",
            "reference_data": True,
            "operators": ["eq"],
        },
    ],
    "operators": [
        {"type": "eq", "arity": 1, "group": "compare", "multi": False},
        {"type": "between", "arity": 2, "group": "compare", "multi": True},
    ],
    "limits": {"default": 1000, "max": 10000, "max_conditions": 50, "max_depth": 6, "min": 1},
}

GOOD_QUERY = {
    "where": {
        "op": "and",
        "conditions": [{"field": "type", "operator": "eq", "value": "product"}],
    },
    "limit": 500,
}


class QueryChecking:
    """A fake core validator that answers with a scripted sequence."""

    def __init__(self, *answers: tuple[bool, str]) -> None:
        self.answers = list(answers)
        self.queries: list[dict[str, Any]] = []

    async def __call__(self, query: dict[str, Any]) -> tuple[bool, str, dict[str, Any] | None]:
        self.queries.append(query)
        ok, message = self.answers[min(len(self.queries) - 1, len(self.answers) - 1)]
        return ok, message, {"count": 1234} if ok else None


def test_a_search_suggestion_is_checked_by_the_core_validator() -> None:
    recorder = Recorder(json.dumps(GOOD_QUERY))
    validator = QueryChecking((True, "1234 hits"))
    reply = asyncio.run(
        generate(
            CONFIG,
            _request(area="search", rule_type=None, target_attribute=None),
            search_catalog=SEARCH_CATALOG,
            validate_query=validator,
            chat_fn=recorder,
        )
    )
    assert reply["ok"] is True
    assert reply["query"] == GOOD_QUERY
    assert reply["preview"] == {"count": 1234}
    assert reply["check"] == "1234 hits"
    assert validator.queries == [GOOD_QUERY]
    # The prompt carries the projected catalogue, not the raw one.
    user = recorder.messages[0][1]["content"]
    assert "attributes.net_price:number" in user
    assert "EF000123" not in user
    assert "between" in user
    assert "`limit` between 1 and 10000" in user
    # The search system prompt asks for JSON, not for a rule.
    assert "search DSL" in recorder.messages[0][0]["content"]


def test_a_rejected_query_gets_one_correction_turn_with_the_core_message() -> None:
    recorder = Recorder(json.dumps(GOOD_QUERY), json.dumps(GOOD_QUERY))
    validator = QueryChecking(
        (False, "unknown field `attributes.nope`"),
        (True, "12 hits"),
    )
    reply = asyncio.run(
        generate(
            CONFIG,
            _request(area="search"),
            search_catalog=SEARCH_CATALOG,
            validate_query=validator,
            chat_fn=recorder,
        )
    )
    assert reply["ok"] is True
    assert reply["attempts"] == 2
    assert "unknown field" in recorder.messages[1][-1]["content"]

    recorder = Recorder(json.dumps(GOOD_QUERY), json.dumps(GOOD_QUERY))
    failing = QueryChecking((False, "unknown field `attributes.nope`"))
    reply = asyncio.run(
        generate(
            CONFIG,
            _request(area="search"),
            search_catalog=SEARCH_CATALOG,
            validate_query=failing,
            chat_fn=recorder,
        )
    )
    assert reply["ok"] is False
    assert reply["attempts"] == 2
    assert reply["errors"] == ["unknown field `attributes.nope`"]


def test_a_search_answer_that_is_not_json_fails() -> None:
    recorder = Recorder("Here is your query:", "still not JSON")
    reply = asyncio.run(
        generate(
            CONFIG,
            _request(area="search"),
            search_catalog=SEARCH_CATALOG,
            chat_fn=recorder,
        )
    )
    assert reply["ok"] is False
    assert any("JSON" in str(message) for message in reply["errors"])


def test_without_a_validator_the_search_check_says_so() -> None:
    """No core reachable: the reply must not claim the query was validated."""
    recorder = Recorder(json.dumps(GOOD_QUERY))
    reply = asyncio.run(
        generate(
            CONFIG,
            _request(area="search"),
            search_catalog=SEARCH_CATALOG,
            chat_fn=recorder,
        )
    )
    assert reply["ok"] is True
    assert "not validated" in str(reply["check"])


# --- JSON-configuration areas (connector, export profile, job) ------------------

#: A contract like `GET /jobs/schema`, trimmed to the two types the tests use.
JOB_SCHEMA: dict[str, Any] = {
    "job_types": [
        {
            "type": "inbound_import",
            "description": "Import a stored MEF document (parents first)",
            "parameters": [
                {"key": "document_id", "shape": "string", "required": True, "description": "Doc"},
                {
                    "key": "match_by_key",
                    "shape": "string",
                    "required": False,
                    "description": "Match attribute",
                },
            ],
        },
        {
            "type": "bulk_operation",
            "description": "Run one bulk operation over a selection",
            "parameters": [
                {"key": "op", "shape": "string", "required": True, "description": "Operation"},
                {
                    "key": "object_ids",
                    "shape": "string_list",
                    "required": True,
                    "description": "Ids",
                },
                {
                    "key": "parameters",
                    "shape": "object",
                    "required": False,
                    "description": "Per op",
                },
            ],
            "operations": [
                {
                    "op": "change_status",
                    "description": "Set the global object status",
                    "parameters": [
                        {
                            "key": "status",
                            "shape": "string",
                            "required": True,
                            "description": "Target status",
                        }
                    ],
                }
            ],
        },
    ],
    "schedule": "Cron expression with six fields (seconds first)",
}

GOOD_JOB: dict[str, Any] = {
    "type": "bulk_operation",
    "schedule": "0 0 3 * * *",
    "parameters": {
        "op": "change_status",
        "object_ids": ["object:1"],
        "parameters": {"status": "approved"},
    },
}


class ConfigChecking:
    """A fake core validator for a configuration object (the `/jobs/validate` shape)."""

    def __init__(self, *answers: tuple[bool, str]) -> None:
        self.answers = list(answers)
        self.payloads: list[dict[str, Any]] = []

    async def __call__(self, payload: dict[str, Any]) -> tuple[bool, str, dict[str, Any] | None]:
        self.payloads.append(payload)
        ok, message = self.answers[min(len(self.payloads) - 1, len(self.answers) - 1)]
        return ok, message, None


def test_a_job_suggestion_is_checked_against_the_contract_of_its_type() -> None:
    recorder = Recorder(json.dumps(GOOD_JOB))
    validator = ConfigChecking((True, "accepted by the core schema"))
    reply = asyncio.run(
        generate(
            CONFIG,
            _request(
                area="job", rule_type=None, target_attribute=None, config_kind="bulk_operation"
            ),
            json_context={"job_schema": JOB_SCHEMA},
            validate_config=validator,
            chat_fn=recorder,
        )
    )
    assert reply["ok"] is True
    assert reply["job"] == GOOD_JOB
    # The validator gets the whole definition — type, schedule and parameters.
    assert validator.payloads == [GOOD_JOB]
    user = recorder.messages[0][1]["content"]
    # The prompt carries the type's parameters, the operations and the schedule rule.
    assert "op (string, required) — Operation" in user
    assert "object_ids (string_list, required) — Ids" in user
    assert "`change_status` — Set the global object status" in user
    assert "status (string, required) — Target status" in user
    assert "six fields" in user
    # …and the system prompt names the artefact, not a rule.
    assert "job definition" in recorder.messages[0][0]["content"]


def test_a_job_suggestion_is_sent_back_once_with_the_contract_errors() -> None:
    recorder = Recorder(json.dumps(GOOD_JOB), json.dumps(GOOD_JOB))
    validator = ConfigChecking(
        (False, "`parameters.parameters.status` is required for `change_status`"),
        (True, "accepted by the core schema"),
    )
    reply = asyncio.run(
        generate(
            CONFIG,
            _request(
                area="job", rule_type=None, target_attribute=None, config_kind="bulk_operation"
            ),
            json_context={"job_schema": JOB_SCHEMA},
            validate_config=validator,
            chat_fn=recorder,
        )
    )
    assert reply["ok"] is True
    assert reply["attempts"] == 2
    assert "is required for `change_status`" in recorder.messages[1][-1]["content"]


def test_a_job_answer_is_kept_apart_from_an_unknown_type_or_missing_contract() -> None:
    recorder = Recorder(json.dumps(GOOD_JOB))
    # An unknown type never reaches the provider: the schema names what exists.
    reply = asyncio.run(
        generate(
            CONFIG,
            _request(area="job", config_kind="send_mails"),
            json_context={"job_schema": JOB_SCHEMA},
            chat_fn=recorder,
        )
    )
    assert reply["ok"] is False
    assert reply["attempts"] == 0
    assert "unknown job type `send_mails`" in str(reply["errors"][0])
    # An empty contract is reported as such, not as "unknown type".
    reply = asyncio.run(
        generate(
            CONFIG,
            _request(area="job", config_kind="bulk_operation"),
            json_context={"job_schema": {}},
            chat_fn=recorder,
        )
    )
    assert reply["ok"] is False
    assert "could not be loaded" in str(reply["errors"][0])
    assert recorder.messages == []


def test_a_job_answer_without_the_json_object_fails() -> None:
    recorder = Recorder("I would schedule that daily.", "still prose")
    reply = asyncio.run(
        generate(
            CONFIG,
            _request(area="job", config_kind="inbound_import"),
            json_context={"job_schema": JOB_SCHEMA},
            chat_fn=recorder,
        )
    )
    assert reply["ok"] is False
    assert any("JSON" in str(message) for message in reply["errors"])


def test_without_a_validator_a_job_check_says_it_was_not_validated() -> None:
    recorder = Recorder(json.dumps(GOOD_JOB))
    reply = asyncio.run(
        generate(
            CONFIG,
            _request(area="job", config_kind="bulk_operation"),
            json_context={"job_schema": JOB_SCHEMA},
            chat_fn=recorder,
        )
    )
    assert reply["ok"] is True
    assert "not validated" in str(reply["check"])


# --- import mapping (stage 2) ---------------------------------------------------

#: The model data the worker fetches from the core for a mapping check.
MAPPING_VOCABULARY: list[dict[str, Any]] = [
    {"name": "article_number", "type": "text", "applicable_types": ["product"]},
    {"name": "title", "type": "text", "multilingual": True, "applicable_types": ["product"]},
    {"name": "weight", "type": "number", "applicable_types": ["product"]},
]

MAPPING_TYPES: list[dict[str, Any]] = [
    {"name": "product", "label_attribute": "name", "key_attributes": ["article_number"]},
]

MAPPING_LANGUAGES: list[dict[str, Any]] = [{"code": "de"}, {"code": "en"}]

#: The columns the editor sends: header plus a value-free shape hint.
COLUMNS: list[dict[str, str]] = [
    {"name": "Kunden-Nr", "hint": "text, 8 characters, all values different"},
    {"name": "Bezeichnung", "hint": "text, up to 40 characters"},
    {"name": "Preis", "hint": "number, 4-5 characters"},
]

GOOD_MAPPING: dict[str, str] = {
    "Kunden-Nr": "article_number",
    "Bezeichnung": "title|de",
    "Preis": "weight",
}

MAPPING_CONTEXT: dict[str, Any] = {
    "columns": COLUMNS,
    "object_types": MAPPING_TYPES,
    "languages": MAPPING_LANGUAGES,
}


def _mapping_request(**overrides: Any) -> GenerationRequest:
    """A mapping request: the columns travel with it, as the import page sends them."""
    values: dict[str, Any] = {
        "area": "import_mapping",
        "object_type": "product",
        "columns": tuple(ColumnFact(row["name"], row["hint"]) for row in COLUMNS),
    }
    values.update(overrides)
    return _request(**values)


def test_an_import_mapping_suggestion_is_checked_by_the_tabular_module() -> None:
    recorder = Recorder(json.dumps({"mapping": GOOD_MAPPING}))
    checks: list[dict[str, Any]] = []

    async def validator(mapping: dict[str, Any]) -> tuple[bool, str, dict[str, Any] | None]:
        checks.append(mapping)
        return True, "accepted", {"mapped": len(mapping), "ignored": [], "warnings": []}

    reply = asyncio.run(
        generate(
            CONFIG,
            _mapping_request(),
            vocabulary_rows=MAPPING_VOCABULARY,
            json_context=MAPPING_CONTEXT,
            validate_config=validator,
            chat_fn=recorder,
        )
    )
    assert reply["ok"] is True
    assert reply["mapping"] == GOOD_MAPPING
    assert reply["preview"] == {"mapped": 3, "ignored": [], "warnings": []}
    assert checks == [GOOD_MAPPING]

    user = recorder.messages[0][1]["content"]
    assert "Object type: product" in user
    assert "Business key of this type: article_number" in user
    assert "Data languages: de, en" in user
    assert "article_number (text)" in user
    assert "title (text, multilingual)" in user
    assert "name (multilingual by convention" in user
    assert "Kunden-Nr — text, 8 characters, all values different" in user
    assert '{"mapping": {"<column>": "<target>"}}' in user
    # The system prompt names the artefact, and the file's values are not in it.
    assert "map the columns of an uploaded" in recorder.messages[0][0]["content"]
    for cell in ("A-1001", "Schraube"):
        assert cell not in user


def test_an_import_mapping_suggestion_is_sent_back_once_with_the_check_errors() -> None:
    recorder = Recorder(
        json.dumps({"mapping": {"Kunden-Nr": "artikelnummer"}}),
        json.dumps({"mapping": GOOD_MAPPING}),
    )
    answers = [
        (
            False,
            "`artikelnummer` has no attribute definition — the value is written, "
            "but the attribute stays untyped",
        ),
        (True, "accepted"),
    ]

    async def validator(mapping: dict[str, Any]) -> tuple[bool, str, dict[str, Any] | None]:
        del mapping
        ok, message = answers[min(len(answers) - 1, 0)]
        answers.pop(0)
        return ok, message, None

    reply = asyncio.run(
        generate(
            CONFIG,
            _mapping_request(),
            vocabulary_rows=MAPPING_VOCABULARY,
            json_context=MAPPING_CONTEXT,
            validate_config=validator,
            chat_fn=recorder,
        )
    )
    assert reply["ok"] is True
    assert reply["attempts"] == 2
    assert "has no attribute definition" in recorder.messages[1][-1]["content"]


def test_an_import_mapping_without_columns_never_calls_the_model() -> None:
    recorder = Recorder(json.dumps({"mapping": GOOD_MAPPING}))
    reply = asyncio.run(
        generate(
            CONFIG,
            _mapping_request(columns=()),
            vocabulary_rows=MAPPING_VOCABULARY,
            json_context={"columns": [], "object_types": MAPPING_TYPES, "languages": []},
            chat_fn=recorder,
        )
    )
    assert reply["ok"] is False
    assert reply["attempts"] == 0
    assert "no columns" in str(reply["errors"][0])
    assert recorder.messages == []


def test_an_import_mapping_answer_without_the_mapping_object_fails() -> None:
    recorder = Recorder('{"columns": {"Kunden-Nr": "article_number"}}', '{"columns": {}}')
    reply = asyncio.run(
        generate(
            CONFIG,
            _mapping_request(),
            vocabulary_rows=MAPPING_VOCABULARY,
            json_context=MAPPING_CONTEXT,
            chat_fn=recorder,
        )
    )
    assert reply["ok"] is False
    assert any("`mapping`" in str(message) for message in reply["errors"])


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
