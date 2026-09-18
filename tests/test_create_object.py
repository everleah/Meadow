"""Tests for the create-object dialog's pure decisions.

`meadow/frontend/create_object.py` validates the create form before the core sees it
(business keys are mandatory, `name` is multilingual by convention). The dialog's
wiring is covered by `tests/test_products_page.py`.
"""

from __future__ import annotations

from meadow.frontend.create_object import build_attributes, missing_key_names, normalized_name


def test_normalized_name_keeps_a_plain_string() -> None:
    assert normalized_name("Brake disc") == "Brake disc"
    assert normalized_name("  ") == ""
    assert normalized_name(None) == ""


def test_normalized_name_drops_empty_languages() -> None:
    assert normalized_name({"de": "Bremsscheibe", "en": "  ", "fr": ""}) == {"de": "Bremsscheibe"}
    assert normalized_name({"en": ""}) == {}


def test_missing_key_names_reports_the_empty_keys_in_order() -> None:
    assert missing_key_names({"sku": "A-1", "gtin": ""}) == ["gtin"]
    assert missing_key_names({"sku": "  ", "gtin": ""}) == ["sku", "gtin"]
    assert missing_key_names({"sku": "A-1"}) == []


def test_build_attributes_puts_the_label_first_and_strips_the_keys() -> None:
    assert build_attributes("name", {"de": "Bremse"}, {"sku": " A-1 "}) == {
        "name": {"de": "Bremse"},
        "sku": "A-1",
    }
