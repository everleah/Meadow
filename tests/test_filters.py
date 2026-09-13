"""Tests for `meadow.frontend.filters`.

Only the pure normalisation is tested (the widget wrapper needs a NiceGUI slot):
a cleared filter must arrive as "no filter" (`""`/`[]`), never as `None` and
never as the string `"None"` — that bug would silently filter out every row.
"""

from __future__ import annotations

from meadow.frontend.filters import ALL, normalised


def test_a_cleared_single_filter_is_the_empty_spelling() -> None:
    assert normalised(None) == ALL
    assert normalised("") == ALL
    assert normalised("draft") == "draft"
    assert normalised(7) == "7"


def test_a_cleared_multiple_filter_is_an_empty_list() -> None:
    assert normalised(None, multiple=True) == []
    assert normalised([], multiple=True) == []
    assert normalised(["a", None, "b"], multiple=True) == ["a", "b"]
    # A single value must not be treated as a sequence of characters.
    assert normalised("draft", multiple=True) == []


def test_the_empty_spelling_is_a_falsy_string() -> None:
    """Callbacks read `if filters["x"]` — so "no filter" must be falsy."""
    assert ALL == ""
    assert not ALL
