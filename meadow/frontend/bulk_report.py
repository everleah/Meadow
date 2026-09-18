"""Pure reductions behind the `/products` bulk actions.

Both the bulk wizard and the find & replace action run as worker jobs
(`plan/detail/bulk-wizard.md`, `replace_text`), so the page only lays out what the
worker reported. *What* that report says — the counts, which extra summary line
applies, which entries failed and which were blocked by a workflow guard — and
which attributes a find & replace may target are data decisions, not layout.

They live here so they can be tested without a browser
(`tests/test_bulk_report.py`); `grid.py` keeps the dialogs.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from meadow.frontend.display import resolve_value
from meadow.frontend.reference_data import visible_in_picker

#: Bulk operation whose report carries the find & replace summary line.
REPLACE_OPS = frozenset({"replace_text"})
#: Bulk operations whose report carries the relation summary line.
RELATION_OPS = frozenset({"add_relation", "remove_relation"})
#: Attribute types a find & replace may rewrite (the core's `replace_text` scope).
REPLACE_TYPES = ("text", "url")


@dataclass(frozen=True)
class BulkReport:
    """The numbers and rows the report dialog shows for one bulk run."""

    op: str = ""
    processed: int = 0
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0
    replacements: int = 0
    relations: int = 0
    #: `(object id, message)` of the entries the worker reported as failed.
    errors: tuple[tuple[str, str], ...] = ()
    #: `(object id, message)` of the entries whose approval was rejected (the data
    #: change is saved; only the workflow transition failed).
    blocked: tuple[tuple[str, str], ...] = ()

    @property
    def is_replace(self) -> bool:
        """Whether the run was a find & replace (`replace_text`)."""
        return self.op in REPLACE_OPS

    @property
    def is_relation(self) -> bool:
        """Whether the run added or removed relations."""
        return self.op in RELATION_OPS


def _count(value: object) -> int:
    """A reported count as `int`; the worker sends numbers and nothing else counts."""
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _entries(result: Mapping[str, Any]) -> list[Any]:
    """The per-object results of the report (`[]` when the field is unusable)."""
    entries = result.get("results")
    return entries if isinstance(entries, list) else []


def _row(entry: object, key: str) -> tuple[str, str]:
    """One `(object id, message)` row of the report."""
    row = entry if isinstance(entry, dict) else {}
    return str(row.get("object_id")), str(row.get(key))


def read_bulk_report(result: Mapping[str, Any]) -> BulkReport:
    """Reduces a `bulk_operation` result into what the report dialog shows."""
    entries = _entries(result)
    return BulkReport(
        op=str(result.get("op") or ""),
        processed=_count(result.get("processed")),
        succeeded=_count(result.get("succeeded")),
        failed=_count(result.get("failed")),
        skipped=_count(result.get("skipped")),
        replacements=_count(result.get("replacements")),
        relations=_count(result.get("relations")),
        errors=tuple(
            _row(entry, "error")
            for entry in entries
            if isinstance(entry, dict) and entry.get("status") == "error"
        ),
        blocked=tuple(
            _row(entry, "approval_error")
            for entry in entries
            if isinstance(entry, dict) and entry.get("approval_error")
        ),
    )


def replace_target_options(
    definitions: Iterable[Mapping[str, object]], locale: str
) -> dict[str, str]:
    """The find & replace targets: visible `text`/`url` attributes, labelled.

    The label is decorated with the attribute name (`Label (name)`) whenever it
    differs, so the select shows a readable entry that still names the target.
    """
    options: dict[str, str] = {}
    for definition in definitions:
        name = str(definition.get("name") or "")
        if not name or definition.get("computed"):
            continue
        if str(definition.get("type") or "") not in REPLACE_TYPES:
            continue
        if not visible_in_picker(definition):
            continue
        label = str(resolve_value(definition.get("label"), locale) or name)
        options[name] = label if label == name else f"{label} ({name})"
    return options
