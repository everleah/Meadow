"""Tests for the pure rules of the column picker (`meadow/frontend/column_picker.py`)."""

from __future__ import annotations

import pytest

from meadow.frontend.column_picker import group_checked, language_columns_state


@pytest.mark.parametrize(
    ("members", "visible", "expected"),
    [
        (["a", "b"], {"a", "b"}, True),
        (["a", "b"], {"a"}, False),
        (["a"], set(), False),
        ([], {"a"}, False),
    ],
)
def test_a_group_box_is_ticked_when_every_member_is_visible(
    members: list[str], visible: set[str], expected: bool
) -> None:
    """A group is only "on" as a whole — a half-visible group is not ticked."""
    assert group_checked(members, visible) is expected


def test_an_empty_group_is_never_ticked() -> None:
    """A section without rows would be a dead toggle."""
    assert group_checked([], set()) is False


def test_language_columns_state_counts_the_visible_ones() -> None:
    """The counter text is `on` of `total` language columns of one attribute."""
    fields = {"de": "attr_name__de", "en": "attr_name__en"}

    assert language_columns_state(fields, {"attr_name__de"}) == (1, 2)
    assert language_columns_state(fields, set(fields.values())) == (2, 2)
    assert language_columns_state(fields, set()) == (0, 2)
    assert language_columns_state({}, set()) == (0, 0)
