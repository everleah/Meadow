"""Tests for the export dialog's payload (`meadow/frontend/export_dialog.py`)."""

from __future__ import annotations

from meadow.frontend.export_dialog import EXPORT_FORMATS, EXPORT_MIME, export_source


def test_the_export_source_is_an_explicit_object_list() -> None:
    """Both scopes resolve to ids on the page: what the user saw is what is exported."""
    assert export_source(["product:1", "product:2"]) == {
        "kind": "selection",
        "object_ids": ["product:1", "product:2"],
    }


def test_an_empty_scope_still_forms_a_valid_source() -> None:
    """The dialog never sends it (it refuses without ids), but the shape stays valid."""
    assert export_source([]) == {"kind": "selection", "object_ids": []}


def test_every_offered_format_has_a_mime_type() -> None:
    """The download needs a mime type; a new format must not fall back silently."""
    assert set(EXPORT_FORMATS) == set(EXPORT_MIME)
