"""Pure helpers for the `range` attribute type (two numeric bounds).

A range is stored as ``{"from": <number>, "to": <number>}`` — the same shape the
Rust core uses (`crates/pim-core/src/attribute_definition_types.rs`), which is
also what the ETIM/BMEcat export needs (a range feature becomes two `FVALUE`
entries). The module stays free of NiceGUI so the form, the grid and the tests
can share it.

Range values are numeric and therefore **not** multilingual; the canonical text
form is ``from..to`` (two dots keep the separator apart from a negative sign).
"""

from __future__ import annotations

FROM_KEY = "from"
TO_KEY = "to"

#: Types that store numbers and may carry a unit.
NUMERIC_TYPES = ("number", "range")


def _bound(value: object) -> float | int | None:
    """Returns a numeric bound, ignoring booleans (they are `int` in Python)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def is_range(value: object) -> bool:
    """Whether the value is a range with two numeric bounds."""
    if not isinstance(value, dict):
        return False
    return _bound(value.get(FROM_KEY)) is not None and _bound(value.get(TO_KEY)) is not None


def range_bounds(value: object) -> tuple[float | int | None, float | int | None]:
    """Returns the two bounds (``None`` each when the value is not a range)."""
    if not is_range(value):
        return (None, None)
    assert isinstance(value, dict)  # narrowed by `is_range`
    return (_bound(value.get(FROM_KEY)), _bound(value.get(TO_KEY)))


def range_value(from_: object, to: object) -> dict | None:
    """Builds a range value, ordering the bounds; `None` when one is missing."""
    low, high = _bound(from_), _bound(to)
    if low is None or high is None:
        return None
    if low > high:
        low, high = high, low
    return {FROM_KEY: low, TO_KEY: high}


def format_bound(number: object) -> str:
    """Renders one bound without a needless decimal part (`4.0` → `4`)."""
    value = _bound(number)
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def format_range(value: object, suffix: str = "") -> str:
    """Renders a range as `from – to` (empty string when it is not a range)."""
    low, high = range_bounds(value)
    if low is None or high is None:
        return ""
    return f"{format_bound(low)} – {format_bound(high)}{suffix}"


def parse_range(text: object) -> dict | None:
    """Parses the canonical text form `from..to` (`None` when it does not match)."""
    raw = str(text or "").strip()
    if ".." not in raw:
        return None
    left, right = raw.split("..", 1)
    try:
        return range_value(float(left.strip()), float(right.strip()))
    except ValueError:
        return None
