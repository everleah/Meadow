"""Tests for the pure decisions of the bulk-edit wizard.

`meadow/frontend/bulk_edit.py` decides what a bulk operation needs and what its
payload looks like — the contract `meadow/worker/bulk.py` reads. The wizard page
itself is covered by `tests/test_products_page.py`.
"""

from __future__ import annotations

from meadow.frontend.bulk_edit import (
    approval_supported,
    build_parameters,
    ids_from_rows,
    missing_parameters,
    page_ids,
    row_id,
)


def test_build_parameters_set_attribute_carries_attribute_and_value() -> None:
    parameters = build_parameters("set_attribute", {"attribute": "title", "value": "Hello"}, {})

    assert parameters == {"attribute": "title", "value": "Hello"}


def test_build_parameters_clear_attribute_leaves_out_the_value() -> None:
    parameters = build_parameters("clear_attribute", {"attribute": "title"}, {})

    assert parameters == {"attribute": "title"}
    assert set(build_parameters("clear_attribute", {"attribute": "title"}, {})) == {"attribute"}


def test_build_parameters_reads_picked_objects_for_targets() -> None:
    """Category/parent/relation targets come from the picker, not from a field."""
    assert build_parameters(
        "add_category",
        {"category_definition": "category_type:1"},
        {"category": {"id": "category:9", "label": "Brakes"}},
    ) == {"category": "category:9", "definition_id": "category_type:1"}
    assert build_parameters("change_parent", {}, {"parent": {"id": "product:5"}}) == {
        "parent": "product:5"
    }
    assert build_parameters(
        "add_relation",
        {"relation": "rel:1"},
        {"relation_target": {"id": "product:7"}},
    ) == {"definition_id": "rel:1", "target": "product:7"}


def test_build_parameters_keeps_channel_release_status_only_when_releasing() -> None:
    fields = {"channel": "shop", "release_status": "published"}

    assert build_parameters("release_for_channel", fields, {}) == {
        "channel": "shop",
        "release_status": "published",
    }
    assert build_parameters("withdraw_from_channel", fields, {}) == {"channel": "shop"}


def test_build_parameters_adds_the_approval_flag_only_for_modifier_operations() -> None:
    """`delete`/`change_status`/`send_for_approval` do not offer that switch."""
    fields = {"status": "published", "send_for_approval": True}

    assert build_parameters("change_status", fields, {}) == {"status": "published"}
    assert build_parameters("delete", {"send_for_approval": True}, {}) == {}
    assert build_parameters("set_attribute", {"send_for_approval": True}, {}) == {
        "attribute": None,
        "value": None,
        "send_for_approval": True,
    }


def test_build_parameters_reports_missing_fields_for_every_operation() -> None:
    """An operation without a target must be rejected before the core call."""
    assert missing_parameters(build_parameters("set_attribute", {}, {})) == [
        "attribute",
        "value",
    ]
    assert missing_parameters(build_parameters("add_category", {}, {})) == ["category"]
    assert missing_parameters(build_parameters("change_parent", {}, {"parent": {}})) == ["parent"]
    assert (
        missing_parameters(build_parameters("add_category", {}, {"category": {"id": "category:1"}}))
        == []
    )


def test_approval_supported_lists_the_modifier_operations() -> None:
    assert approval_supported("change_parent")
    assert not approval_supported("change_status")
    assert not approval_supported("send_for_approval")
    assert not approval_supported("delete")
    assert not approval_supported("release_for_channel")


def test_row_id_reads_data_dicts_and_row_nodes() -> None:
    assert row_id({"_id": "product:1"}) == "product:1"
    assert row_id({"data": {"_id": "product:2"}}) == "product:2"
    assert row_id({"data": {}}) is None
    assert row_id("product:3") is None
    assert row_id(None) is None


def test_ids_from_rows_skips_rows_without_an_id() -> None:
    assert ids_from_rows([{"_id": "a"}, {"data": {}}, "b", {"_id": "c"}]) == ["a", "c"]


def test_page_ids_slices_by_page_and_size() -> None:
    rows = [{"_id": f"id{index}"} for index in range(25)]

    assert page_ids(rows, 0, 10) == [f"id{index}" for index in range(10)]
    assert page_ids(rows, 2, 10) == [f"id{index}" for index in range(20, 25)]
    assert page_ids(rows, 3, 10) == []
