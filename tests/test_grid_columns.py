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
    sort_field,
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


def test_only_columns_with_a_stored_field_are_sortable() -> None:
    """The core sorts the view, so a column without a stored field must not offer it."""
    definitions = {
        "color": _definition("color"),
        "title": _definition("title", multilingual=True),
    }

    assert sort_field("_type", definitions=definitions, locale="de") == "type"
    assert sort_field("_status", definitions=definitions, locale="de") == "status"
    # The conventional label: the base key works for a string and for a language map.
    assert sort_field("_name", definitions=definitions, locale="de") == "attributes.name"
    assert sort_field("_id", definitions=definitions, locale="de") is None
    assert sort_field("_completeness", definitions=definitions, locale="de") is None
    assert sort_field("_releases", definitions=definitions, locale="de") is None
    assert sort_field("_refs", definitions=definitions, locale="de") is None


def test_a_multilingual_column_sorts_by_its_language() -> None:
    """The column shows one language, so the view must be ordered by that language."""
    definitions = {
        "color": _definition("color"),
        "title": _definition("title", multilingual=True),
    }
    # The declared data languages of the catalog (`GET /languages`) gate the variants.
    declared = ["de", "en"]

    assert (
        sort_field(
            attribute_field("title"), definitions=definitions, locale="de", languages=declared
        )
        == "attributes.title.de"
    )
    # A scalar attribute has no language variants.
    assert (
        sort_field(
            attribute_field("color"), definitions=definitions, locale="de", languages=declared
        )
        == "attributes.color"
    )
    # The per-language columns name their language themselves.
    assert (
        sort_field(
            attribute_field(language_field("title", "en")),
            definitions=definitions,
            locale="de",
            languages=declared,
        )
        == "attributes.title.en"
    )
    assert (
        sort_field(
            language_field("_name", "fr"), definitions=definitions, locale="de", languages=declared
        )
        == "attributes.name.fr"
    )
    # An unknown attribute (a standard's feature without a definition here) has no
    # language knowledge: the base key is the only safe field.
    assert (
        sort_field(
            attribute_field("EF000001"),
            definitions=definitions,
            locale="de",
            languages=declared,
        )
        == "attributes.EF000001"
    )
    # Without a language catalog (a degraded page) the base key stays the safe answer.
    assert (
        sort_field(attribute_field("title"), definitions=definitions, locale="de")
        == "attributes.title"
    )


def test_a_locale_outside_the_catalog_falls_back_to_the_base_language() -> None:
    """The core only sorts declared languages — everything else is a `400`."""
    definitions = {"title": _definition("title", multilingual=True)}

    assert (
        sort_field(
            attribute_field("title"),
            definitions=definitions,
            locale="de-AT",
            languages=["de", "en"],
        )
        == "attributes.title.de"
    )
    assert (
        sort_field(
            attribute_field("title"),
            definitions=definitions,
            locale="pt-BR",
            languages=["de", "en"],
        )
        == "attributes.title"
    )


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
