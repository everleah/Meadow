"""Guards against duplicated element names inside a NiceGUI page builder.

A page builder is one long function scope: every element variable it creates
stays bound to that same name for the rest of the function. Assigning a name a
second time — easy to do when a dialog is built in the same scope as the page
body — silently rebinds it, and every handler that closes over the *first*
element then updates the wrong widget. That failure is invisible on the server
(no exception) and shows up only in the browser as a stale/absent update.

`/attribute-definitions` hit exactly this: the editor dialog's `reference_hint`
overwrote the grid's hidden-count hint, so the standard switch updated the
dialog's label instead of the counter (see `meadow/frontend/attribute_definitions.py`).

The check is structural (AST): per page function, a NiceGUI element may be bound
to a given name once. Names that are *not* elements (`units = await …` with an
`except: units = []` fallback) are not touched — only `ui.<element>(…)` calls and
`with ui.<container>(…) as name:` bindings are collected.
"""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

FRONTEND = Path(__file__).resolve().parent.parent / "meadow" / "frontend"


def _is_element_call(node: ast.expr) -> bool:
    """Whether an expression builds a NiceGUI element (`ui.x(…).props(…)`)."""
    while isinstance(node, ast.Call | ast.Await):
        node = node.func if isinstance(node, ast.Call) else node.value
    if isinstance(node, ast.Attribute):
        return _is_element_call(node.value)
    return isinstance(node, ast.Name) and node.id == "ui"


def _bound_names(node: ast.expr, line: int, found: list[tuple[str, int]]) -> None:
    for target in ast.walk(node):
        if isinstance(target, ast.Name):
            found.append((target.id, line))


class _ElementBinder(ast.NodeVisitor):
    """Collects `name -> line` of element bindings in one function scope.

    Nested functions are skipped: they have their own scope, so a helper may
    reuse a name without touching the page body.
    """

    def __init__(self) -> None:
        self.bindings: list[tuple[str, int]] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return  # a nested scope, not the page body

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def visit_Assign(self, node: ast.Assign) -> None:
        if _is_element_call(node.value):
            for target in node.targets:
                _bound_names(target, node.lineno, self.bindings)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None and _is_element_call(node.value):
            _bound_names(node.target, node.lineno, self.bindings)
        self.generic_visit(node)

    def visit_With(self, node: ast.With) -> None:
        for item in node.items:
            if item.optional_vars is not None and _is_element_call(item.context_expr):
                _bound_names(item.optional_vars, node.lineno, self.bindings)
        self.generic_visit(node)

    visit_AsyncWith = visit_With


def _is_page_function(node: ast.AST) -> bool:
    if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
        return False
    return any(_is_element_call(dec) for dec in node.decorator_list)


def duplicate_element_bindings(source: str) -> dict[str, list[int]]:
    """Element names a page builder binds more than once, with their lines."""
    tree = ast.parse(source)
    duplicates: dict[str, list[int]] = {}
    for node in ast.walk(tree):
        if not _is_page_function(node):
            continue
        assert isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        binder = _ElementBinder()
        for statement in node.body:
            binder.visit(statement)
        counts = Counter(name for name, _ in binder.bindings)
        for name, count in counts.items():
            if count > 1:
                lines = [line for bound, line in binder.bindings if bound == name]
                duplicates[f"{node.name}:{name}"] = lines
    return duplicates


def test_page_builders_bind_every_element_name_once() -> None:
    problems: dict[str, dict[str, list[int]]] = {}
    for path in sorted(FRONTEND.glob("*.py")):
        found = duplicate_element_bindings(path.read_text(encoding="utf-8"))
        if found:
            problems[path.name] = found
    assert not problems, (
        "a NiceGUI page builds the same element name twice — the later binding wins and "
        f"handlers update the wrong widget: {problems}"
    )


def test_the_check_detects_a_rebinding() -> None:
    """The detector itself works (a silently passing guard is worthless)."""
    source = """
import nicegui.ui as ui


@ui.page("/x")
def page() -> None:
    hint = ui.label()
    dialog = ui.dialog()
    with dialog, ui.card():
        hint = ui.label()
    ui.button(on_click=lambda: hint.set_text("x"))
"""
    assert duplicate_element_bindings(source) == {"page:hint": [7, 10]}


def test_non_element_assignments_may_repeat() -> None:
    """Fallback patterns stay allowed: `rows = await …` / `except: rows = []`."""
    source = """
import nicegui.ui as ui


@ui.page("/y")
def page() -> None:
    rows: list = []
    try:
        rows = [1]
    except Exception:
        rows = []
    grid = ui.aggrid({"rowData": rows})
    grid.run_grid_method("setGridOption", "rowData", rows)
"""
    assert duplicate_element_bindings(source) == {}
