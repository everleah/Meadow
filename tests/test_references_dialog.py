"""Tests for the pure part of the reference dialog (`meadow/frontend/references_dialog.py`)."""

from __future__ import annotations

from meadow.frontend.references_dialog import (
    COMPOSITION_DEFINITION,
    reference_chip,
    reference_groups,
    relation_options,
)


def test_a_reference_chip_describes_the_object_on_the_other_side() -> None:
    """A reference is directional; the chip always shows the far end."""
    reference = {
        "id": "ref:1",
        "definition_id": "rel_category_member",
        "out": "product:1",
        "in_object": {"label": "Brakes", "type": "category"},
    }

    chip = reference_chip(reference, "product:1")

    assert (chip.label, chip.type, chip.definition_id) == (
        "Brakes",
        "category",
        "rel_category_member",
    )
    assert chip.removable is True


def test_the_other_direction_reads_the_out_side() -> None:
    """Shown object is the `in` side: the reference points *at* it, the chip shows `out`."""
    reference = {
        "id": "ref:2",
        "definition_id": COMPOSITION_DEFINITION,
        "out": "product:9",
        "out_object": {"label": "Wheel", "type": "product"},
        "in_object": {"label": "must not win"},
    }

    chip = reference_chip(reference, "product:1")

    assert chip.label == "Wheel"
    assert chip.removable is False, "the composition edge cannot be cut here"


def test_groups_put_the_composition_first_then_sort_by_label() -> None:
    """Reading order: the composition parent, then the aggregations by their label."""
    references = [
        {"id": "ref:1", "definition_id": "rel_b", "out": "product:1", "in_object": {"label": "B"}},
        {
            "id": "ref:2",
            "definition_id": COMPOSITION_DEFINITION,
            "out": "product:1",
            "in_object": {"label": "Z"},
        },
        {"id": "ref:3", "definition_id": "rel_a", "out": "product:1", "in_object": {"label": "A"}},
    ]

    groups = reference_groups(references, "product:1", {"rel_a": "Alpha", "rel_b": "Beta"})

    assert [key for key, _, _ in groups] == [COMPOSITION_DEFINITION, "rel_a", "rel_b"]
    assert [heading for _, heading, _ in groups] == [COMPOSITION_DEFINITION, "Alpha", "Beta"]


def test_a_group_without_a_label_falls_back_to_its_key() -> None:
    """An unresolved label must not hide a group."""
    references = [{"id": "ref:1", "definition_id": "rel_x", "out": "product:1", "in_object": {}}]

    groups = reference_groups(references, "product:1", {})

    key, heading, chips = groups[0]
    assert (key, heading) == ("rel_x", "rel_x")
    assert [chip.label for chip in chips] == [""]


def test_chips_inside_a_group_are_sorted_by_label_then_type() -> None:
    """The list keeps a stable order between reloads."""
    references = [
        {
            "id": "ref:1",
            "definition_id": "rel_a",
            "out": "product:1",
            "in_object": {"label": "b", "type": "product"},
        },
        {
            "id": "ref:2",
            "definition_id": "rel_a",
            "out": "product:1",
            "in_object": {"label": "a", "type": "product"},
        },
        {
            "id": "ref:3",
            "definition_id": "rel_a",
            "out": "product:1",
            "in_object": {"label": "a", "type": "asset"},
        },
    ]

    _, _, chips = reference_groups(references, "product:1", {"rel_a": "A"})[0]

    assert [(chip.label, chip.type) for chip in chips] == [
        ("a", "asset"),
        ("a", "product"),
        ("b", "product"),
    ]


def test_relation_options_use_the_record_key_and_the_resolved_label() -> None:
    """The dialog offers the short record key — that is what the core expects."""
    definitions = {
        "rel_category_member": {"label": {"de": "Kategorie", "en": "Category"}},
        "rel_plain": {},
    }

    assert relation_options(definitions, "de") == {
        "rel_category_member": "Kategorie",
        "rel_plain": "rel_plain",
    }
    assert relation_options(definitions, "en")["rel_category_member"] == "Category"
