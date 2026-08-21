"""Import whitelist for business rules (plan/detail/business-rules.md)."""

from __future__ import annotations

import ast

# Standard whitelist from the plan. Administrative configuration can extend it
# later (worker/frontend pass a custom whitelist into the sandbox).
DEFAULT_WHITELIST = frozenset(
    {
        "json",
        "datetime",
        "math",
        "re",
        "statistics",
        "decimal",
        "typing",
        "uuid",
        "collections",
    }
)


def collect_imports(source: str) -> list[str]:
    """Returns the top-level module names imported by `source`.

    Only absolute imports are considered; relative imports (`from . import x`)
    are rejected by RestrictedPython at runtime and therefore ignored here.
    """
    tree = ast.parse(source)
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imports.append(node.module)
    return imports


def validate_imports(source: str, whitelist: frozenset[str]) -> list[str]:
    """Returns the imported top-level modules that are not whitelisted."""
    return [name for name in collect_imports(source) if name.split(".")[0] not in whitelist]
