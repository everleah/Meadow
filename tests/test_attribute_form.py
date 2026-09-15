"""Tests for the attribute-definition dialog's field rules.

`meadow/frontend/attribute_form.py` decides which blocks the dialog offers and
which parts of the payload a save may send. The core stores whatever it receives
(`computed_rule` next to `computed: false` stays in the record) and rejects a few
combinations outright (`is_key` needs `type: text` and no multilingual), so these
rules keep the UI from producing meaningless records.

See `plan/detail/api-design.md` ("Attributkatalog") and `meadow/AGENTS.md`.
"""

from __future__ import annotations

from meadow.frontend.attribute_form import cleaned_payload, field_state


def test_units_and_enum_set_follow_the_data_type() -> None:
    assert field_state("number").units is True
    assert field_state("range").units is True
    assert field_state("text").units is False
    assert field_state("enum").enum_set is True
    assert field_state("number").enum_set is False
    assert field_state("enum").units is False


def test_business_key_is_locked_for_non_text_and_multilingual() -> None:
    assert field_state("text", multilingual=False).key is True
    assert field_state("text", multilingual=False).key_reason == ""

    locked_type = field_state("number")
    assert locked_type.key is False
    assert locked_type.key_reason == "attr_def.key_needs_text"

    locked_language = field_state("text", multilingual=True)
    assert locked_language.key is False
    assert locked_language.key_reason == "attr_def.key_not_multilingual"


def test_a_business_key_blocks_inheritance() -> None:
    state = field_state("text", is_key=True)
    assert state.inheritable is False
    assert state.inheritable_reason == "attr_def.inheritable_blocked_by_key"
    assert field_state("text", is_key=False).inheritable is True


def test_computed_block_and_reference_switch_follow_the_flags() -> None:
    assert field_state("number", computed=True).computed is True
    assert field_state("number", computed=False).computed is False
    # Reference data is created by a standard import: only offered while editing.
    assert field_state("text", editing=True).reference is True
    assert field_state("text", editing=False).reference is False


def test_computed_fields_are_cleared_without_computed() -> None:
    payload = cleaned_payload(
        definition_type="number",
        is_key=False,
        multilingual=False,
        computed=False,
        inheritable=True,
        computed_rule="gross_price",
        dependencies=["net_price"],
        reads_inherited=True,
    )
    assert payload["computed_rule"] == "", "an empty string clears the rule"
    assert payload["dependencies"] == []
    assert payload["computed_reads_inherited"] is False


def test_computed_dependencies_drop_the_attribute_itself() -> None:
    payload = cleaned_payload(
        definition_type="number",
        is_key=False,
        multilingual=False,
        computed=True,
        inheritable=False,
        computed_rule=" gross_price ",
        dependencies=["net_price", "gross_price", "net_price", "", None],
        own_name="gross_price",
    )
    assert payload["computed_rule"] == "gross_price"
    assert payload["dependencies"] == ["net_price"]


def test_units_are_cleared_for_non_numeric_types() -> None:
    numeric = cleaned_payload(
        definition_type="range",
        is_key=False,
        multilingual=False,
        computed=False,
        inheritable=False,
        unit_id="unit_mm",
        display_unit_id="unit_cm",
    )
    assert numeric["unit_id"] == "unit_mm"
    assert numeric["display_unit_id"] == "unit_cm"

    text = cleaned_payload(
        definition_type="text",
        is_key=False,
        multilingual=False,
        computed=False,
        inheritable=False,
        unit_id="unit_mm",
        display_unit_id="unit_cm",
    )
    assert text["unit_id"] == ""
    assert text["display_unit_id"] == ""


def test_enum_set_only_for_enum_and_cleaned_on_switch_away() -> None:
    enum = cleaned_payload(
        definition_type="enum",
        is_key=False,
        multilingual=False,
        computed=False,
        inheritable=False,
        enum_set_id="enum_set:colours",
    )
    assert enum["enum_set_id"] == "enum_set:colours"
    assert (
        cleaned_payload(
            definition_type="text",
            is_key=False,
            multilingual=False,
            computed=False,
            inheritable=False,
            enum_set_id="enum_set:colours",
        )["enum_set_id"]
        == ""
    )


def test_business_key_is_cleaned_when_type_or_language_forbids_it() -> None:
    payload = cleaned_payload(
        definition_type="number",
        is_key=True,
        multilingual=False,
        computed=False,
        inheritable=True,
    )
    assert payload["is_key"] is False
    assert payload["inheritable"] is True

    multilingual = cleaned_payload(
        definition_type="text",
        is_key=True,
        multilingual=True,
        computed=False,
        inheritable=True,
    )
    assert multilingual["is_key"] is False

    key = cleaned_payload(
        definition_type="text",
        is_key=True,
        multilingual=False,
        computed=False,
        inheritable=True,
    )
    assert key["is_key"] is True
    assert key["inheritable"] is False


def test_required_types_stay_within_the_applicable_types() -> None:
    limited = cleaned_payload(
        definition_type="text",
        is_key=False,
        multilingual=False,
        computed=False,
        inheritable=False,
        applicable=["product", "variant"],
        required=["product", "category", "product"],
    )
    assert limited["required_types"] == ["product"]

    # An empty `applicable_types` means "every type", so nothing is dropped.
    every_type = cleaned_payload(
        definition_type="text",
        is_key=False,
        multilingual=False,
        computed=False,
        inheritable=False,
        applicable=[],
        required=["product", "category"],
    )
    assert every_type["required_types"] == ["product", "category"]
