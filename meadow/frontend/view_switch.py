"""The left panel's view switch of the products page.

The panel shows one hierarchy at a time — the composition structure, a category
taxonomy or the saved collections — and each view needs different controls: the taxonomy
and dimension switcher and the tree filter steer the category tree, the collapse button
needs a tree, the reload button re-materializes a collection, and creating an object
belongs to the structure (categories have their own page).

`panel_visibility` is the table of that contract (pure, so the rules are testable);
`ViewSwitch` applies a view change to the widgets the page registered.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from nicegui import ui

#: Widgets that only the **category** view shows (they steer that tree and nothing else).
CATEGORY_WIDGETS = (
    "taxonomy_select",
    "taxonomy_help",
    "tree_select",
    "dimension_select",
    "dimension_help",
)


def panel_visibility(view: str, *, has_tree: bool, has_create: bool) -> dict[str, bool]:
    """Which panel widget each view shows (pure: this table is the contract).

    `has_tree` and `has_create` are the two facts about the data the rules need — a
    collapse button without a tree (or a create button without a type to create) would
    be a dead control.
    """
    visibility = {name: view == "category" for name in CATEGORY_WIDGETS}
    # Collapsing has no meaning in the flat collection list.
    visibility["collapse"] = view != "collection" and has_tree
    # Re-materializing a collection is the collection view's own action.
    visibility["reload"] = view == "collection"
    # A new object hangs in the composition tree, so the action belongs to it.
    visibility["create"] = view == "structure" and has_create
    return visibility


class ViewSwitch:
    """Applies a view change to the active button and the panel widgets."""

    def __init__(self) -> None:
        self._buttons: dict[str, ui.button] = {}
        self._widgets: dict[str, ui.element] = {}
        self._has_tree: Callable[[], bool] = lambda: False
        self._has_create: Callable[[], bool] = lambda: False

    def bind(
        self,
        *,
        buttons: Mapping[str, ui.button],
        widgets: Mapping[str, ui.element],
        has_tree: Callable[[], bool],
        has_create: Callable[[], bool],
    ) -> None:
        """Registers the buttons and widgets this switch drives.

        The widgets are built *after* the handlers that use them, so the page hands
        them over once; `has_tree`/`has_create` are read at every switch, because the
        data behind them arrives later than the widgets.
        """
        self._buttons = dict(buttons)
        self._widgets = dict(widgets)
        self._has_tree = has_tree
        self._has_create = has_create

    def apply(self, view: str) -> None:
        """Highlights the view's button and shows only the controls that view needs."""
        for name, button in self._buttons.items():
            if name == view:
                button.props("color=primary text-white", remove="flat")
            else:
                button.props("flat", remove="color text-white")
        visibility = panel_visibility(
            view, has_tree=self._has_tree(), has_create=self._has_create()
        )
        for name, visible in visibility.items():
            widget = self._widgets.get(name)
            if widget is not None:
                widget.set_visibility(visible)
