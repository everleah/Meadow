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
    contract_block,
    declared_computed,
    few_shot_examples,
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
