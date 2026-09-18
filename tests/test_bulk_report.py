"""Tests for the pure reductions behind the `/products` bulk actions.

`meadow/frontend/bulk_report.py` turns the worker's `bulk_operation` result into
what the report dialog shows and decides which attributes a find & replace may
target. Both are data decisions of a 2700-line page function, so they are pinned
here — the page wiring itself is covered by `tests/test_products_page.py`.
"""

from __future__ import annotations

from meadow.frontend.bulk_report import read_bulk_report, replace_target_options

#: A result shaped like `meadow/worker/bulk.py` reports it.
WORKER_RESULT = {
    "op": "replace_text",
    "processed": 12,
    "succeeded": 10,
    "skipped": 1,
    "failed": 1,
    "replacements": 7,
    "relations": 0,
    "results": [
        {"object_id": "product:1", "status": "ok", "replacements": 7},
        {"object_id": "product:2", "status": "skipped"},
        {"object_id": "product:3", "status": "error", "error": "attribute is not text"},
        {"object_id": "product:4", "status": "ok", "approval_error": "rule blocked in_review"},
    ],
}


def test_read_bulk_report_keeps_the_counts_of_the_worker() -> None:
    report = read_bulk_report(WORKER_RESULT)

    assert (report.processed, report.succeeded, report.skipped, report.failed) == (12, 10, 1, 1)
    assert report.replacements == 7
    assert report.is_replace
    assert not report.is_relation


def test_read_bulk_report_flags_the_relation_operations() -> None:
    report = read_bulk_report({"op": "add_relation"})

    assert report.is_relation
    assert not report.is_replace
    assert not read_bulk_report({"op": "change_status"}).is_relation


def test_read_bulk_report_splits_failed_and_blocked_entries() -> None:
    report = read_bulk_report(WORKER_RESULT)

    assert report.errors == (("product:3", "attribute is not text"),)
    assert report.blocked == (("product:4", "rule blocked in_review"),)


def test_read_bulk_report_ignores_unusable_results_and_counts() -> None:
    """A malformed result must not break the dialog (it shows zeros/empty)."""
    report = read_bulk_report(
        {
            "processed": None,
            "succeeded": "10",
            "failed": True,
            "results": "not a list",
        }
    )

    assert (report.processed, report.succeeded, report.failed) == (0, 0, 0)
    assert report.errors == ()
    assert report.blocked == ()
    assert read_bulk_report({}).op == ""


DEFINITIONS: list[dict[str, object]] = [
    {"name": "name", "type": "text", "label": {"en": "Name"}},
    {"name": "title", "type": "text", "label": {"en": "Title"}},
    {"name": "homepage", "type": "url"},
    {"name": "weight", "type": "number"},
    {"name": "internal", "type": "text", "computed": True},
    {"name": "EF000001", "type": "text", "reference_data": True},
    {"name": "", "type": "text"},
]


def test_replace_target_options_offers_visible_text_and_url_attributes() -> None:
    """Only own `text`/`url` attributes: no numbers, no computed, no standard codes."""
    assert replace_target_options(DEFINITIONS, "en") == {
        "name": "Name (name)",
        "title": "Title (title)",
        "homepage": "homepage",
    }


def test_replace_target_options_keeps_the_name_when_there_is_no_label() -> None:
    options = replace_target_options([{"name": "notes", "type": "text"}], "de")

    assert options == {"notes": "notes"}
