"""Tests for the advanced-search dialog's pure decisions.

`meadow/frontend/where_dialog.py` wraps the condition form of `search_form.py`;
the dialog's wiring (open → catalog, apply → reload) is covered by
`tests/test_products_page.py`, while the decision "is this a filter at all?"
belongs here.
"""

from __future__ import annotations

from meadow.frontend.where_dialog import has_conditions, saved_search_name


def test_has_conditions_detects_empty_queries() -> None:
    assert not has_conditions({})
    assert not has_conditions({"where": [], "references": [], "fulltext": {"query": "  "}})
    assert not has_conditions({"fulltext": {}})


def test_has_conditions_detects_every_condition_kind() -> None:
    assert has_conditions({"where": [{"field": "status", "op": "eq", "value": "draft"}]})
    assert has_conditions({"references": [{"role": "part_of", "target": "product:1"}]})
    assert has_conditions({"fulltext": {"query": "brake"}})


def test_saved_search_name_prefers_the_row_name() -> None:
    """A listing row carries `name`, a full object only its attributes."""
    assert saved_search_name({"name": "In review", "id": "saved_search:1"}) == "In review"
    assert saved_search_name({"id": "saved_search:2", "attributes": {"name": "Brakes"}}) == "Brakes"
    assert saved_search_name({"id": "saved_search:3"}) == "saved_search:3"
