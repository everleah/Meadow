"""Measures one page function's structure for the `products_page` refactor.

`meadow/frontend/grid.py` builds the whole products page inside a single function
(`products_page`), so the refactor needs a number that moves: this prints the
function's line count, how many nested functions it holds, how many of those lines
are inside nested functions and how much is linear widget code, plus the largest
nested functions. Re-run it after every increment —
`plan/detail/ai-code-quality.md` §4.11.

Usage: `uv run python scripts/grid_structure.py [path] [--function products_page]`
"""

from __future__ import annotations

import argparse
import ast
from pathlib import Path

#: Functions nested deeper than this are counted inside their parent, not separately.
DEFAULT_FUNCTION = "products_page"


def _contains(outer: ast.stmt | ast.expr, inner: ast.stmt | ast.expr) -> bool:
    """Whether `outer` fully spans `inner` (the AST nodes are line-ordered)."""
    return (
        outer is not inner
        and outer.lineno <= inner.lineno
        and (outer.end_lineno or 0) >= (inner.end_lineno or 0)
    )


def measure(path: Path, function: str) -> int:
    """Prints the structure of `function` in `path`; returns the process exit code."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    page = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function
        ),
        None,
    )
    if page is None:
        print(f"{function} not found in {path}")
        return 1

    total = (page.end_lineno or 0) - page.lineno + 1
    nested = [
        node
        for node in ast.walk(page)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node is not page
    ]
    outermost = [node for node in nested if not any(_contains(other, node) for other in nested)]
    inside = sum((node.end_lineno or 0) - node.lineno + 1 for node in outermost)
    print(f"{path}: {function} lines {page.lineno}-{page.end_lineno} = {total}")
    print(f"  nested functions: {len(nested)} ({len(outermost)} outermost)")
    print(f"  lines inside them: {inside}")
    print(f"  linear page code: {total - inside}")
    print("  largest nested functions (lines, start, name):")
    for size, node in sorted(
        (((node.end_lineno or 0) - node.lineno + 1, node) for node in outermost),
        key=lambda item: (-item[0], item[1].lineno),
    )[:12]:
        print(f"    {size:5} {node.lineno:6}  {node.name}")
    return 0


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", default="meadow/frontend/grid.py")
    parser.add_argument("--function", default=DEFAULT_FUNCTION)
    args = parser.parse_args()
    return measure(Path(args.path), args.function)


if __name__ == "__main__":
    raise SystemExit(main())
