"""Tests for the export wizard's pure field-selection helpers.

`plan/detail/datenexport-import-ui.md` (UI-driven export): the wizard groups the
attribute selection by attribute group and only offers what the chosen object
type allows — a catalogue with an imported classification standard carries
thousands of attributes, so a flat list is unusable.
"""

from __future__ import annotations

from meadow.frontend.export_fields import (
    allowed_names,
    applicable,
    attribute_names,
    group_members,
    group_order,
    selected_fields,
)

DEFINITIONS = [
    {"name": "name", "applicable_types": ["product", "variant"]},
    {"name": "EF000001", "group_ids": ["attribute_group:1"], "applicable_types": ["product"]},
    {"name": "EF000002", "group_ids": ["attribute_group:1", "attribute_group:2"]},
    {"name": "structural", "applicable_types": ["category"]},
]


def test_attribute_names_keeps_order_and_drops_duplicates() -> None:
    assert attribute_names([{"name": "b"}, {"name": ""}, {"name": "a"}, {"name": "b"}, {}]) == [
        "b",
        "a",
    ]


def test_group_members_splits_grouped_and_ungrouped() -> None:
    grouped, ungrouped = group_members(DEFINITIONS)
    assert grouped == {
        "attribute_group:1": ["EF000001", "EF000002"],
        "attribute_group:2": ["EF000002"],
    }
    assert ungrouped == ["name", "structural"]


def test_applicable_honours_the_object_type() -> None:
    assert applicable({"applicable_types": ["product"]}, "product") is True
    assert applicable({"applicable_types": ["product"]}, "category") is False
    # An unrestricted definition stays visible everywhere.
    assert applicable({"applicable_types": []}, "category") is True
    assert applicable({}, "category") is True
    # Without a fixed object type (collection, saved search, …) all stay visible.
    assert applicable({"applicable_types": ["product"]}, None) is True
    assert applicable(None, "product") is True


def test_allowed_names_filters_by_object_type() -> None:
    names = attribute_names(DEFINITIONS)
    by_name = {str(definition["name"]): definition for definition in DEFINITIONS}
    assert allowed_names(names, by_name, "product") == ["name", "EF000001", "EF000002"]
    # `name` is restricted to product/variant; `EF000002` is unrestricted.
    assert allowed_names(names, by_name, "category") == ["EF000002", "structural"]
    assert allowed_names(names, by_name, None) == names


def test_group_order_sorts_by_position_then_title() -> None:
    grouped, _ = group_members(DEFINITIONS)
    groups = {
        "attribute_group:1": {"position": 2},
        "attribute_group:2": {"position": 1},
    }
    titles = {"attribute_group:1": "Electrical", "attribute_group:2": "Zeta"}
    assert group_order(grouped, groups, titles.__getitem__) == [
        "attribute_group:2",
        "attribute_group:1",
    ]
    # An unknown group id (deleted group) sorts first, by its own name.
    assert group_order({"ghost": []}, groups, lambda gid: gid) == ["ghost"]


def test_selected_fields_uses_state_and_base_defaults() -> None:
    visible = ["id", "type", "status", "EF000001", "EF000002"]
    assert selected_fields(visible, {}, ("id", "type", "status")) == ["id", "type", "status"]
    state = {"EF000001": True, "id": False}
    assert selected_fields(visible, state, ("id", "type", "status")) == [
        "type",
        "status",
        "EF000001",
    ]
    # A remembered tick of a filtered-out field is not exported.
    assert selected_fields(["id"], {"EF09": True}, ("id",)) == ["id"]
