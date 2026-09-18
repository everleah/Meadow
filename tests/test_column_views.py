"""Tests for the stored column views of the products grid.

`meadow/frontend/column_views.py` keeps the named column sets in the user's session
storage; the dialog itself is covered by `tests/test_products_page.py`, while the
reading of stored (user-writable) data is pinned here.
"""

from __future__ import annotations

from meadow.frontend.column_views import read_views


def test_read_views_keeps_well_formed_views() -> None:
    stored = {"Brakes": ["_id", "attr_title"], "empty": []}

    assert read_views(stored) == {"Brakes": ["_id", "attr_title"], "empty": []}


def test_read_views_ignores_malformed_storage() -> None:
    """Storage survives upgrades and is user-writable: junk must not break the grid."""
    assert read_views(None) == {}
    assert read_views("Brakes") == {}
    assert read_views(["Brakes"]) == {}
    assert read_views({"Brakes": "attr_title", "ok": ["attr_title"]}) == {"ok": ["attr_title"]}
    assert read_views({1: [2]}) == {"1": ["2"]}
