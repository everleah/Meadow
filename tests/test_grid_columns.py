"""Tests for the product grid's column selection (`meadow/frontend/grid.py`).

The column picker must list **every own attribute** while the vocabulary of an
imported classification standard (17 377 features in the full ETIM standard)
would become 17 377 columns and picker rows. Two pure helpers decide the cut:

- `offered_definitions` gives own attributes a column and offers a standard one
  only when objects on the page carry a value for it (data is *read*) or when a
  restored personal view already has it (`reference_data.py`).
- `present_attribute_names` finds those data-carrying attributes in the loaded
  objects.

See `meadow/AGENTS.md` (grid columns) and `tests/test_frontend.py`.
"""

from __future__ import annotations

from typing import Any

from meadow.frontend.grid import (
    attribute_field,
    language_field,
    offered_definitions,
    present_attribute_names,
)


def _definition(name: str, *, reference: bool = False, multilingual: bool = False) -> dict:
    return {"name": name, "reference_data": reference, "multilingual": multilingual}


def test_present_names_need_a_real_value() -> None:
    objects: list[dict[str, Any]] = [
        {"attributes": {"brand": "ACME", "color": "", "title": {"de": "Hallo"}}},
        {"attributes": {"brand": "Other", "weight": 0, "empty_map": {}, "flag": False}},
    ]
    names = present_attribute_names(objects)
    assert names == {"brand", "title", "weight", "flag"}
    # An empty string / an empty map is "not maintained", 0 and false are values.
    assert "color" not in names
    assert "empty_map" not in names


def test_present_names_ignore_rows_without_attributes() -> None:
    assert present_attribute_names([{}, {"attributes": None}, {"attributes": "brand"}]) == set()


def test_own_definitions_always_get_a_column() -> None:
    own = _definition("color")
    offered = offered_definitions([own], visible_fields=set(), present_names=set())
    assert [definition["name"] for definition in offered] == ["color"]


def test_standard_columns_are_offered_for_present_data() -> None:
    own = _definition("color")
    standard = _definition("EF000001", reference=True)
    offered = offered_definitions([standard, own], visible_fields=set(), present_names={"EF000001"})
    # Own attributes keep their place in front, the standard follows.
    assert [definition["name"] for definition in offered] == ["color", "EF000001"]


def test_standard_columns_stay_offered_when_a_view_restores_them() -> None:
    standard = _definition("EF000001", reference=True)
    offered = offered_definitions(
        [standard],
        visible_fields={attribute_field("EF000001")},
        present_names=set(),
    )
    assert [definition["name"] for definition in offered] == ["EF000001"]


def test_hidden_standard_vocabulary_is_not_offered() -> None:
    definitions = [_definition("color")] + [
        _definition(f"EF{index:06d}", reference=True) for index in range(1, 50)
    ]
    offered = offered_definitions(definitions, visible_fields=set(), present_names=set())
    assert [definition["name"] for definition in offered] == ["color"]


def test_language_columns_are_per_attribute() -> None:
    """The hybrid language control groups the columns of one attribute."""
    languages = ["de", "en"]
    title = [language_field("attr_title", language) for language in languages]
    assert title == ["attr_title__de", "attr_title__en"]
    # The working-language column is the attribute's own box, the language
    # columns share the same prefix (`language_field` round trip).
    assert language_field(attribute_field("title"), "de") == "attr_title__de"
