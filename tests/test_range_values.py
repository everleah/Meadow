"""Tests for the `range` attribute type (Phase 9).

A range stores two numeric bounds (``{"from": <number>, "to": <number>}``); the
Rust core owns the type and its conversions
(`crates/pim-core/src/attribute_definition_types.rs`), the frontend shares the
pure helpers in `meadow/frontend/range_values.py` between the form, the grid and
these tests. The type exists because ETIM/BMEcat range features (`Range`, 931 of
the 17377 ETIM 10.0 features) are transmitted as two `FVALUE` entries — see
`plan/detail/bmecat-export.md` §11.4.
"""

from __future__ import annotations

from meadow.frontend.attribute_definitions import ATTR_TYPES
from meadow.frontend.grid import EDITABLE_TYPES, is_editable_definition
from meadow.frontend.range_values import (
    NUMERIC_TYPES,
    format_bound,
    format_range,
    is_range,
    parse_range,
    range_bounds,
    range_value,
)


def test_range_is_a_known_attribute_type() -> None:
    assert "range" in ATTR_TYPES
    assert NUMERIC_TYPES == ("number", "range")
    # The grid edits single values only, so a pair stays read-only there.
    assert "range" not in EDITABLE_TYPES
    assert not is_editable_definition({"name": "size", "type": "range"})


def test_is_range_requires_two_numeric_bounds() -> None:
    assert is_range({"from": 1, "to": 2})
    assert not is_range({"from": "1", "to": 2})
    assert not is_range({"de": "x"}), "a language map is not a range"
    assert not is_range({"from": True, "to": 2}), "a boolean is not a bound"
    assert not is_range(None)
    assert not is_range(4)


def test_range_bounds_and_value_order_the_bounds() -> None:
    assert range_bounds(None) == (None, None)
    assert range_bounds({"from": 4, "to": 12.5}) == (4, 12.5)
    assert range_value(12.5, 4) == {"from": 4, "to": 12.5}
    assert range_value(4, 12.5) == {"from": 4, "to": 12.5}
    assert range_value(None, 4) is None


def test_format_bound_drops_the_decimal_part_when_it_is_integral() -> None:
    assert format_bound(4.0) == "4"
    assert format_bound(12.5) == "12.5"
    assert format_bound(None) == ""
    assert format_bound(True) == ""


def test_format_range_renders_the_canonical_form() -> None:
    assert format_range({"from": 4, "to": 12.5}) == "4 – 12.5"
    assert format_range({"from": 4, "to": 4.0}) == "4 – 4"
    assert format_range({"from": 4, "to": 12.5}, " mm") == "4 – 12.5 mm"
    assert format_range({"de": "x"}) == ""
    assert format_range(None) == ""


def test_parse_range_accepts_the_canonical_text_form() -> None:
    assert parse_range("12.5..4") == {"from": 4.0, "to": 12.5}
    assert parse_range(" 7 .. 9 ") == {"from": 7.0, "to": 9.0}
    assert parse_range("1.5") is None
    assert parse_range("a..b") is None
    assert parse_range(None) is None
