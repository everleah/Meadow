"""The context blocks: contract, policy and the attribute **projection**.

The projection is the reason the whole AI-assist plan is feasible at all — measured
2026-09-19 (`plan/detail/ki-assistenz.md` §2): 17 463 attribute definitions (8.35 MB) are
installed, 17 443 of them apply to `product`, and their names alone are 174 654
characters; the **own** attributes of that type are 54 (1 107 characters). These tests
pin the projection rule, the generated contract text and the freshness of the generated
reference (`docs/developer/rule-context.*`).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from meadow.ai.context import (  # noqa: E402 - after the path fix
    AttributeFact,
    attribute_facts,
    attribute_names_text,
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
    job_type_facts,
    job_types_text,
    key_attribute_names,
    language_codes,
    mapping_columns_text,
    mapping_targets_text,
    parameter_facts,
    parameters_text,
    policy_block,
    search_field_facts,
    search_fields_text,
    search_limits_text,
    search_operators_text,
)
from meadow.ai.examples import EXAMPLES  # noqa: E402


def _standard_features(count: int) -> list[dict[str, object]]:
    """Many `reference_data` definitions — the imported standard's feature vocabulary."""
    return [
        {
            "name": f"EF{index:06d}",
            "type": "number",
            "applicable_types": ["product"],
            "reference_data": True,
        }
        for index in range(2, count + 2)
    ]


VOCABULARY: list[dict[str, object]] = [
    {"name": "net_price", "type": "number", "applicable_types": ["product"]},
    {
        "name": "gross_price",
        "type": "number",
        "applicable_types": ["product"],
        "computed": True,
    },
    {"name": "sku", "type": "text", "applicable_types": ["product"]},
    {"name": "name", "type": "text", "multilingual": True},
    {"name": "generation_code", "type": "text", "applicable_types": ["vehicle_generation"]},
    {
        "name": "EF000001",
        "type": "number",
        "applicable_types": ["product"],
        "reference_data": True,
    },
]
#: … plus a thousand more standard features: the ratio that forces the projection.
VOCABULARY.extend(_standard_features(1000))


def test_the_projection_keeps_own_attributes_only() -> None:
    facts = attribute_facts(VOCABULARY, "product")
    # Sorted, without the standard's features — `name` has no `applicable_types`, so it
    # applies to every type and stays.
    assert [fact.name for fact in facts] == ["gross_price", "name", "net_price", "sku"]


def test_the_projection_follows_applicable_types() -> None:
    # An attribute without `applicable_types` applies to every type; one with a list
    # only to the types it names.
    assert "name" in [fact.name for fact in attribute_facts(VOCABULARY, "vehicle_generation")]
    assert "generation_code" in [
        fact.name for fact in attribute_facts(VOCABULARY, "vehicle_generation")
    ]
    assert "generation_code" not in [fact.name for fact in attribute_facts(VOCABULARY, "product")]
    # Without a type the caller gets everything own (the panel's "no type selected" case
    # is handled by the caller, not by silently dropping attributes).
    assert "generation_code" in [fact.name for fact in attribute_facts(VOCABULARY, "")]


def test_the_projection_is_what_makes_the_block_small() -> None:
    own = attribute_names_text(attribute_facts(VOCABULARY, "product"))
    unprojected = attribute_names_text(
        [
            AttributeFact(
                name=str(entry["name"]),
                value_type=str(entry["type"]),
                computed=bool(entry.get("computed")),
            )
            for entry in VOCABULARY
        ]
    )
    assert len(own) < 120
    assert len(unprojected) > 10_000  # the same list without the projection


def test_the_computed_flag_drives_the_writable_names() -> None:
    facts = attribute_facts(VOCABULARY, "product")
    assert declared_computed(facts) == ("gross_price",)
    assert "gross_price:number(computed)" in attribute_names_text(facts)


def test_the_contract_block_is_generated_from_the_contract_module() -> None:
    block = contract_block()
    assert "def run(obj, ctx, result)" in block
    # Signatures come from `inspect`, so a changed helper shows up here.
    assert "result.set_value(attr: str, value: Any)" in block
    assert "result.add_error(attr: str, code: str, message: str)" in block
    assert "result.allow(allowed: bool, reason: str | None = None)" in block
    assert "result.log(message: str, level: str = 'INFO')" in block
    # The `ctx` fields are the dataclass fields, not a hand-written list.
    for field in ("rule_type", "object_type", "mode", "missing_features"):
        assert field in block


def test_the_policy_block_names_imports_limits_and_the_hasattr_trap() -> None:
    block = policy_block()
    assert "importable modules" in block and "json" in block
    assert "65536 bytes script" in block
    assert "hasattr" in block and "getattr(value, 'name', None)" in block
    assert "additionally injected: (none today)" in block


def test_few_shot_examples_follow_the_rule_type() -> None:
    computed = few_shot_examples("computed")
    assert computed and all(example.rule_type == "computed" for example in computed)
    assert few_shot_examples("bulk") == []  # no example for that type: say so, do not fake one
    assert len(EXAMPLES) >= 4


# --- search projection (stage 2) ------------------------------------------------


def _search_catalog(extra_fields: int = 0) -> dict[str, object]:
    """A catalog like `GET /search-fields`, with many reference-data fields."""
    fields: list[dict[str, object]] = [
        {"key": "type", "value_type": "string", "group": "system", "operators": ["eq", "ne"]},
        {
            "key": "attributes.net_price",
            "value_type": "number",
            "group": "attribute",
            "operators": ["eq", "gt", "between"],
        },
        {"key": "attributes.name.de", "value_type": "text", "operators": ["eq", "contains"]},
    ]
    fields.extend(
        {
            "key": f"attributes.EF{index:06d}",
            "value_type": "number",
            "reference_data": True,
            "operators": ["eq"],
        }
        for index in range(extra_fields)
    )
    return {
        "fields": fields,
        "groups": [{"key": "system"}, {"key": "attribute"}],
        "operators": [
            {"type": "eq", "arity": 1, "multi": False},
            {"type": "between", "arity": 2, "multi": True},
        ],
        "limits": {"default": 1000, "max": 10000, "max_conditions": 50, "max_depth": 6, "min": 1},
    }


def test_the_search_projection_keeps_own_fields_and_groups_them_by_operator_set() -> None:
    facts = search_field_facts(_search_catalog(extra_fields=500))
    assert [fact.key for fact in facts] == [
        "attributes.name.de",
        "attributes.net_price",
        "type",
    ]
    assert facts[1].operators == ("eq", "gt", "between")
    assert all(not fact.key.startswith("attributes.EF") for fact in facts)

    text = search_fields_text(facts)
    # One heading per distinct operator set (three here), each followed by its fields.
    assert text.count("[") == 3
    assert "[eq, gt, between]" in text
    assert "attributes.net_price:number" in text
    # The size budget: 500 standard features must not reach the prompt.
    assert len(text) < 300
    assert "EF000000" not in text


def test_the_search_limits_and_operator_table_are_words_not_raw_json() -> None:
    catalog = _search_catalog()
    assert search_limits_text(catalog) == (
        "Limits: `limit` between 1 and 10000 (default 1000), "
        "at most 50 conditions per level, nesting depth at most 6."
    )
    operators = search_operators_text(catalog)
    assert "eq: arity 1" in operators
    assert "between: arity 2 (multi)" in operators
    # Missing parts degrade to an empty string instead of raising.
    assert search_limits_text({}) == ""
    assert search_operators_text({}) == ""
    assert search_field_facts({}) == ()


# --- connector schema projection (stage 2, JSON configuration) ------------------


def _connector_schema() -> list[dict[str, object]]:
    """A catalog like `GET /connector-types`, trimmed to what the projection reads."""
    return [
        {
            "type": "webhook",
            "direction": "outbound",
            "label_key": "connectors.type_webhook",
            "wired": True,
            "groups": [
                {
                    "key": "target",
                    "fields": [
                        {
                            "key": "url",
                            "type": "string",
                            "required": True,
                            "label_key": "connector_fields.webhook.url.label",
                            "help_key": "connector_fields.webhook.url.help",
                        },
                        {
                            "key": "method",
                            "type": "string",
                            "enum": ["POST", "PUT"],
                            "default": "POST",
                        },
                        {"key": "timeout_s", "type": "number", "minimum": 1.0, "maximum": 600.0},
                    ],
                },
                {
                    "key": "headers",
                    "fields": [{"key": "headers", "type": "string_map", "secret": True}],
                },
                {
                    "key": "filter",
                    "fields": [
                        {
                            "key": "filter.event_types",
                            "type": "string_list",
                            "enum": ["object.created"],
                        }
                    ],
                },
                {
                    "key": "coalesce",
                    "fields": [
                        {
                            "key": "coalesce.window_seconds",
                            "type": "number",
                            "minimum": 1.0,
                            "maximum": 3600.0,
                            "show_if": {"key": "coalesce.enabled", "equals": True},
                        }
                    ],
                },
            ],
        },
        {"type": "json_import", "direction": "inbound", "groups": []},
    ]


def test_the_connector_projection_flattens_the_groups_and_keeps_the_constraints() -> None:
    facts = connector_type_facts(_connector_schema(), "webhook")
    assert [fact.key for fact in facts] == [
        "url",
        "method",
        "timeout_s",
        "headers",
        "filter.event_types",
        "coalesce.window_seconds",
    ]
    assert facts[0].required is True
    assert facts[1].enum == ("POST", "PUT")
    assert facts[1].default == "POST"
    assert (facts[2].minimum, facts[2].maximum) == (1.0, 600.0)
    assert facts[3].secret is True
    assert facts[5].show_if == "only when coalesce.enabled = true"
    # An unknown type yields no facts instead of every field of every type.
    assert connector_type_facts(_connector_schema(), "sftp") == ()
    assert connector_type_facts(None, "webhook") == ()


def test_the_connector_text_names_the_constraints_and_the_ui_wording() -> None:
    labels = {
        "connector_fields.webhook.url.label": "Target URL",
        "connector_fields.webhook.url.help": "Where the event is sent",
        "connectors.type_webhook": "Webhook",
    }
    facts = connector_type_facts(_connector_schema(), "webhook", labels)
    text = connector_fields_text(facts)
    assert "url: string, required — Target URL" in text
    assert "method: string, one of POST | PUT, default POST" in text
    assert "timeout_s: number, 1–600" in text
    assert "headers: string_map, secret" in text
    assert "coalesce.window_seconds: number, 1–3600, only when coalesce.enabled = true" in text
    # The type list names the alternatives (the model may be asked for a new type).
    types = connector_types_text(_connector_schema(), labels)
    assert "webhook (outbound): Webhook" in types
    assert "json_import (inbound)" in types


def test_the_shipped_search_examples_are_valid_dsl_objects() -> None:
    from meadow.ai.examples import SEARCH_EXAMPLES, search_examples

    for example in SEARCH_EXAMPLES:
        assert isinstance(example["where"], dict)
        assert isinstance(example["where"]["conditions"], list)
        assert all("field" in condition for condition in example["where"]["conditions"])
    rendered = search_examples(1)
    assert len(rendered) == 1
    # The prompt shows formatted JSON — the format the model has to produce.
    assert rendered[0].startswith("{")
    assert "\n  " in rendered[0]
    assert json.loads(rendered[0]) == SEARCH_EXAMPLES[0]


# --- job-definition schema projection (stage 2, JSON configuration) --------------


def _job_schema() -> dict[str, object]:
    """A contract like `GET /jobs/schema`, trimmed to what the projection reads."""
    return {
        "job_types": [
            {
                "type": "inbound_import",
                "description": "Import a stored MEF document (parents first)",
                "parameters": [
                    {
                        "key": "document_id",
                        "shape": "string",
                        "required": True,
                        "description": "Stored import document",
                    },
                    {
                        "key": "match_by_key",
                        "shape": "string",
                        "required": False,
                        "description": "Attribute name to match existing objects by",
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
                        "description": "Objects to change",
                    },
                ],
                "operations": [
                    {
                        "op": "replace_text",
                        "description": "Find & replace inside one attribute",
                        "parameters": [
                            {
                                "key": "search",
                                "shape": "string",
                                "required": True,
                                "description": "Literal text to find",
                            },
                            {
                                "key": "case_sensitive",
                                "shape": "boolean",
                                "required": False,
                                "description": "Match case (default: off)",
                            },
                        ],
                    },
                    {
                        "op": "delete",
                        "description": "Delete the selected objects",
                        "parameters": [],
                    },
                ],
            },
        ],
        "schedule": "Cron expression with six fields (seconds first)",
    }


def test_the_job_projection_names_the_types_with_their_meaning() -> None:
    assert job_type_facts(_job_schema()) == (
        ("inbound_import", "Import a stored MEF document (parents first)"),
        ("bulk_operation", "Run one bulk operation over a selection"),
    )
    assert job_types_text(_job_schema()).startswith("inbound_import — Import")
    # Nothing to project without the schema or for an unknown type.
    assert job_types_text({}) == ""
    assert job_type_facts(None) == ()


def test_the_job_projection_marks_required_fields_and_closed_values() -> None:
    facts = job_parameter_facts(_job_schema(), "inbound_import")
    assert [fact.key for fact in facts] == ["document_id", "match_by_key"]
    assert facts[0].required is True
    assert facts[1].required is False
    text = parameters_text(facts)
    assert "document_id (string, required) — Stored import document" in text
    assert "match_by_key (string) — Attribute name" in text
    # An unknown type yields no parameters instead of the fields of every type.
    assert job_parameter_facts(_job_schema(), "send_mails") == ()
    assert job_parameter_facts({"job_types": "nope"}, "inbound_import") == ()


def test_the_job_projection_carries_the_operations_with_their_own_parameters() -> None:
    operations = job_operation_facts(_job_schema(), "bulk_operation")
    assert [operation.op for operation in operations] == ["replace_text", "delete"]
    assert [parameter.key for parameter in operations[0].parameters] == [
        "search",
        "case_sensitive",
    ]
    text = job_operations_text(operations)
    assert "`replace_text` — Find & replace inside one attribute" in text
    assert "  search (string, required) — Literal text to find" in text
    # An operation without parameters stays a one-line block.
    assert "`delete` — Delete the selected objects" in text
    # Only `bulk_operation` has operations; the schedule line comes from the schema.
    assert job_operation_facts(_job_schema(), "inbound_import") == ()
    assert job_schedule_text(_job_schema()).startswith("Cron expression")
    assert job_schedule_text({}) == ""


def test_the_export_profile_projection_stays_unchanged_by_the_job_fields() -> None:
    """`required`/`allowed` are new; the export-profile rendering must not move."""
    schema = {
        "parameters": [
            {"key": "fields", "shape": "string_list", "description": "Attributes to export"},
        ]
    }
    assert parameters_text(parameter_facts(schema)) == "fields (string_list) — Attributes to export"


# --- import mapping (stage 2) ---------------------------------------------------


def _object_types() -> list[dict[str, object]]:
    return [
        {
            "name": "product",
            "label_attribute": "name",
            "key_attributes": ["sku"],
        },
        {"name": "variant", "label_attribute": "name", "key_attributes": []},
    ]


def test_the_mapping_columns_block_lists_headers_and_shapes() -> None:
    facts = column_facts(
        [
            {"name": "Kunden-Nr", "hint": "text, 6 characters"},
            {"name": "Leer", "hint": ""},
            "Nur-Kopf",  # a plain header without a hint is accepted too
            {"hint": "without a name"},
        ]
    )
    assert [fact.name for fact in facts] == ["Kunden-Nr", "Leer", "Nur-Kopf"]
    assert mapping_columns_text(facts) == "Kunden-Nr — text, 6 characters\nLeer\nNur-Kopf"
    assert column_facts(None) == ()
    assert column_facts("Kunden-Nr") == ()


def test_the_mapping_targets_mark_multilingual_computed_and_conventions() -> None:
    vocabulary: list[dict[str, object]] = [
        {"name": "sku", "type": "text", "applicable_types": ["product"]},
        {"name": "title", "type": "text", "multilingual": True, "applicable_types": ["product"]},
        {
            "name": "gross_price",
            "type": "number",
            "computed": True,
            "applicable_types": ["product"],
        },
        {"name": "EF000001", "type": "number", "reference_data": True},
        {"name": "weight", "type": "number", "applicable_types": ["variant"]},
    ]
    facts = attribute_facts(vocabulary, "product")
    # The projection stays the own attributes of the type, with the new flag.
    assert [fact.name for fact in facts] == ["gross_price", "sku", "title"]
    assert [fact.multilingual for fact in facts] == [False, False, True]
    text = mapping_targets_text(facts, conventional=("name",), languages=("de", "en"))
    assert "sku (text)" in text
    assert "title (text, multilingual)" in text
    assert "gross_price (number, computed, a rule overwrites imported values)" in text
    assert "name (multilingual by convention — write `name|<language>`)" in text
    assert "EF000001" not in text
    assert "weight" not in text


def test_the_conventional_and_key_names_come_from_the_object_types() -> None:
    assert conventional_names(_object_types(), "product") == ("name", "sku")
    assert conventional_names(_object_types(), "variant") == ("name",)
    assert conventional_names(_object_types(), "unknown") == ()
    assert conventional_names({"not": "a list"}, "product") == ()
    assert key_attribute_names(_object_types(), "product") == ("sku",)
    assert key_attribute_names(_object_types(), "variant") == ()
    assert key_attribute_names(_object_types(), "unknown") == ()


def test_the_language_codes_ignore_anything_that_is_not_a_node() -> None:
    assert language_codes([{"code": "de"}, {"code": "en"}, {"label": "no code"}, "de"]) == (
        "de",
        "en",
    )
    assert language_codes(None) == ()
    assert language_codes("de") == ()


def _load_script(name: str):
    """Loads a script module by file name (the scripts are not a package)."""
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None, f"cannot load {name}.py"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_generated_reference_is_up_to_date() -> None:
    """`docs/developer/rule-context.*` must match what the code says right now."""
    module = _load_script("ai_context")
    assert module.main(["--check"]) == 0
