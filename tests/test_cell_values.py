"""Tests for the grid's cell values and the staged-edit patch (`cell_values.py`)."""

from __future__ import annotations

from typing import Any

import pytest

from meadow.frontend.cell_values import (
    as_bool,
    as_number,
    build_patch,
    is_multilingual_map,
    staged_patches,
    storable_cell_value,
)

ENUM = {"type": "enum"}
NUMBER = {"type": "number"}
TEXT = {"type": "text"}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (12, 12),
        (12.5, 12.5),
        ("12,5", 12.5),
        ("42", 42),
        ("", None),
        ("   ", None),
        (True, True),
        ("nonsense", "nonsense"),
    ],
)
def test_as_number_parses_cells_and_keeps_unparsable_text(value: object, expected: object) -> None:
    """A cell value becomes a number where it can; anything else stays as it is."""
    assert as_number(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        (True, True),
        ("yes", True),
        ("ON", True),
        ("1", True),
        ("no", False),
        (0, False),
    ],
)
def test_as_bool_parses_cells(value: object, expected: object) -> None:
    """Checked and unchecked boxes survive the round trip through the cell."""
    assert as_bool(value) == expected


def test_multilingual_map_is_told_apart_from_a_quantified_value() -> None:
    """A `{language: value}` map is multilingual; a `unit`-carrying dict is not."""
    assert is_multilingual_map({"de": "Rad", "en": "Wheel"}) is True
    assert is_multilingual_map({"value": 5, "unit": "mm"}) is False
    assert is_multilingual_map("Rad") is False


def test_storable_cell_value_restores_codes_and_merges_languages() -> None:
    """Every cell type finds its storage form again; languages stay independent."""
    enum = {"name": "color", "type": "enum"}
    text = {"name": "title", "type": "text"}
    number = {"name": "price", "type": "number"}
    boolean = {"name": "flag", "type": "boolean"}

    assert storable_cell_value(enum, "Rot", "red", "de", {"red": "Rot"}) == "red"
    assert storable_cell_value(enum, "Neu", "red", "de", {"red": "Rot"}) == "Neu"
    assert storable_cell_value(number, "12,5", "1", "de") == 12.5
    assert storable_cell_value(number, "12", "1", "de") == 12
    assert storable_cell_value(boolean, "true", None, "de") is True
    assert storable_cell_value(text, "", "x", "de") is None
    assert storable_cell_value(text, "Mouse", "Maus", "en") == "Mouse"
    # Multilingual: only the working language changes, the others are preserved.
    assert storable_cell_value(text, "Mouse", {"de": "Maus"}, "en") == {
        "de": "Maus",
        "en": "Mouse",
    }
    assert storable_cell_value(text, "Neu", {"de": "Maus"}, "de") == {"de": "Neu"}
    assert storable_cell_value(text, "", {"de": "Maus"}, "de") == {}


def test_build_patch_keeps_the_other_locales_of_a_name() -> None:
    """The display value is multilingual too — the patch merges, it never replaces."""
    original = {"attributes": {"name": {"de": "Rad", "en": "Wheel"}}}

    assert build_patch("product:1", "Bremsscheibe", original, "de") == {
        "id": "product:1",
        "attributes": {"name": {"de": "Bremsscheibe", "en": "Wheel"}},
    }
    assert build_patch("product:1", "Disc", {"attributes": {"name": "Rad"}}, "de") == {
        "id": "product:1",
        "attributes": {"name": "Disc"},
    }


def _patch(
    *,
    staged: dict[str, set[str]],
    originals: dict[str, dict[str, Any]] | None = None,
    live: dict[str, dict[str, Any]] | None = None,
    loaded: dict[str, dict[str, Any]] | None = None,
    definitions: dict[str, dict[str, Any]] | None = None,
    resolvers: dict[str, dict[str, str] | None] | None = None,
    locale: str = "de",
) -> list[dict[str, Any]]:
    """Calls `staged_patches` with the row maps a save needs."""
    return staged_patches(
        staged=staged,
        originals=originals or {},
        live_rows=live or {},
        loaded_rows=loaded or {},
        definitions=definitions or {},
        locale=locale,
        enum_resolvers=resolvers or {},
    )


def test_a_staged_attribute_becomes_a_patch_of_its_object() -> None:
    """`attr_<name>` cells are stored under the attribute name, grouped per object."""
    patches = _patch(
        staged={"product:1": {"attr_weight"}},
        originals={"product:1": {"id": "product:1", "attributes": {"weight": 1}}},
        live={"product:1": {"attr_weight": 2.5}},
        loaded={"product:1": {"attr_weight": 1}},
        definitions={"weight": NUMBER},
    )

    assert patches == [{"id": "product:1", "attributes": {"weight": 2.5}}]


def test_a_reverted_cell_produces_no_patch() -> None:
    """Staged but equal to the loaded row: nothing to save (the core merges)."""
    patches = _patch(
        staged={"product:1": {"attr_weight", "_name"}},
        originals={"product:1": {"attributes": {"weight": 1, "name": "Rad"}}},
        live={"product:1": {"attr_weight": 1, "_name": "Rad"}},
        loaded={"product:1": {"attr_weight": 1, "_name": "Rad"}},
        definitions={"weight": NUMBER},
    )

    assert patches == []


def test_read_only_columns_and_vanished_objects_are_skipped() -> None:
    """A language column has no definition; a row that is gone has no counterpart."""
    patches = _patch(
        staged={"product:1": {"attr_name_en"}, "product:2": {"attr_weight"}},
        originals={"product:1": {"attributes": {}}},
        live={"product:1": {"attr_name_en": "Wheel"}, "product:2": {"attr_weight": 5}},
        loaded={"product:1": {"attr_name_en": "Disc"}, "product:2": {"attr_weight": 1}},
        definitions={"weight": NUMBER},
    )

    assert patches == []


def test_the_name_and_an_attribute_travel_in_one_patch() -> None:
    """One object, two edited cells: a single item with both attributes."""
    patches = _patch(
        staged={"product:1": {"_name", "attr_weight"}},
        originals={"product:1": {"attributes": {"name": "Rad", "weight": 1}}},
        live={"product:1": {"_name": "Bremsscheibe", "attr_weight": 2}},
        loaded={"product:1": {"_name": "Rad", "attr_weight": 1}},
        definitions={"weight": NUMBER},
    )

    assert patches == [
        {"id": "product:1", "attributes": {"name": "Bremsscheibe", "weight": 2}},
    ]


def test_enum_cells_are_stored_as_codes_per_object() -> None:
    """The resolver of the attribute decides which code a label means."""
    patches = _patch(
        staged={"product:1": {"attr_status"}},
        originals={"product:1": {"attributes": {"status": "draft"}}},
        live={"product:1": {"attr_status": "Veröffentlicht"}},
        loaded={"product:1": {"attr_status": "Entwurf"}},
        definitions={"status": {"type": "enum"}},
        resolvers={"status": {"published": "Veröffentlicht", "draft": "Entwurf"}},
    )

    assert patches == [{"id": "product:1", "attributes": {"status": "published"}}]
