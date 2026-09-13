"""Tests for `meadow.frontend.reference_data`.

The rules decide what an imported classification standard may bury: the shipped
ETIM 10.0 bundle brings 380 attribute definitions, the full standard 17 377, so
"which of my attributes do I want?" must not be answered with the standard's
vocabulary (`plan/detail/kategorisierung.md`, "Referenzdaten").
"""

from __future__ import annotations

from meadow.frontend.reference_data import (
    has_value,
    is_reference,
    reference_names,
    visible_in_form,
    visible_in_picker,
)

OWN = {"name": "width", "type": "number"}
REFERENCE = {"name": "EF000040", "type": "number", "reference_data": True}


def test_only_flagged_definitions_are_reference_data() -> None:
    assert is_reference(REFERENCE)
    assert not is_reference(OWN)
    assert not is_reference({"name": "x", "reference_data": False})
    assert not is_reference(None)


def test_a_reference_attribute_is_hidden_in_the_form_until_it_has_a_value() -> None:
    assert visible_in_form(OWN, {}, "width")
    assert not visible_in_form(REFERENCE, {}, "EF000040")
    assert not visible_in_form(REFERENCE, {"width": 5}, "EF000040")
    assert visible_in_form(REFERENCE, {"EF000040": 12.5}, "EF000040")


def test_an_empty_or_cleared_value_does_not_reveal_it_again() -> None:
    assert not visible_in_form(REFERENCE, {"EF000040": None}, "EF000040")
    assert not visible_in_form(REFERENCE, {"EF000040": ""}, "EF000040")
    assert not visible_in_form(REFERENCE, {"EF000040": {"en": "", "de": None}}, "EF000040")
    assert not visible_in_form(REFERENCE, {"EF000040": []}, "EF000040")


def test_filled_values_count_regardless_of_their_type() -> None:
    assert has_value({"a": 0}, "a")
    assert has_value({"a": False}, "a")
    assert has_value({"a": {"from": 1, "to": 3}}, "a")
    assert has_value({"a": {"en": "Width"}}, "a")
    assert not has_value({}, "a")
    assert not has_value({"a": 0}, "b")


def test_the_export_switch_brings_the_standard_attributes_back() -> None:
    assert not visible_in_picker(REFERENCE)
    assert visible_in_picker(REFERENCE, include_reference=True)
    assert visible_in_picker(OWN)
    assert visible_in_picker(None)


def test_reference_names_lists_only_the_standard_vocabulary() -> None:
    assert reference_names([OWN, REFERENCE, {"name": "height"}]) == ["EF000040"]
