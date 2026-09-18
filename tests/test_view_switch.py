"""Tests for the panel view rules (`meadow/frontend/view_switch.py`)."""

from __future__ import annotations

import pytest

from meadow.frontend.view_switch import CATEGORY_WIDGETS, panel_visibility


def test_the_structure_view_offers_the_tree_actions() -> None:
    """Collapsing and creating belong to the structure the tree shows."""
    visible = panel_visibility("structure", has_tree=True, has_create=True)

    assert visible["collapse"] is True
    assert visible["create"] is True
    assert visible["reload"] is False
    assert all(visible[name] is False for name in CATEGORY_WIDGETS)


def test_the_category_view_owns_the_taxonomy_and_dimension_switcher() -> None:
    """The switches steer the category tree — they are dead controls elsewhere."""
    visible = panel_visibility("category", has_tree=True, has_create=False)

    assert all(visible[name] is True for name in CATEGORY_WIDGETS)
    assert visible["create"] is False


def test_the_collection_view_is_flat() -> None:
    """A collection has no hierarchy: only reload remains (and no create)."""
    visible = panel_visibility("collection", has_tree=True, has_create=True)

    assert visible["reload"] is True
    assert visible["collapse"] is False
    assert visible["create"] is False


@pytest.mark.parametrize(("has_tree", "has_create"), [(False, False), (True, False), (False, True)])
def test_an_action_without_its_data_stays_hidden(has_tree: bool, has_create: bool) -> None:
    """No tree to collapse, no type to create: the buttons would be dead."""
    visible = panel_visibility("structure", has_tree=has_tree, has_create=has_create)

    assert visible["collapse"] is has_tree
    assert visible["create"] is has_create
