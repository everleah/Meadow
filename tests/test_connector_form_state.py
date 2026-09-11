"""Tests for the change detection that drives form-based connector updates."""

from __future__ import annotations

from meadow.frontend.connector_form import build_patch, project_fields

_SCHEMA = {
    "wired": True,
    "groups": [
        {
            "key": "g",
            "fields": [
                {"key": "url", "type": "string", "widget": "text", "required": True},
                {"key": "method", "type": "string", "widget": "select", "default": "POST"},
                {"key": "timeout_s", "type": "number", "widget": "number"},
                {"key": "coalesce.enabled", "type": "boolean", "widget": "switch"},
                {"key": "coalesce.window_seconds", "type": "number", "widget": "number"},
                {"key": "filter.event_types", "type": "string_list", "widget": "multiselect"},
            ],
        }
    ],
}


def test_projection_mirrors_the_stored_parameters() -> None:
    stored = {
        "url": "https://x.example",
        "method": "PUT",
        "coalesce": {"enabled": True, "window_seconds": 45.0},
        "filter": {"event_types": ["object.created"]},
        "undeclared": {"a": 1},
    }
    projected = project_fields(_SCHEMA, stored)
    assert projected == {
        "url": "https://x.example",
        "method": "PUT",
        "timeout_s": None,
        "coalesce.enabled": True,
        "coalesce.window_seconds": 45.0,
        "filter.event_types": ["object.created"],
    }


def test_json_tab_edits_become_part_of_the_patch() -> None:
    """Editing the JSON tab must not be swallowed by the form re-render.

    The baseline is the *stored* projection, so a value changed in the JSON tab
    (here the coalescing window) is part of the patch even though the form was
    filled from the edited values afterwards.
    """
    stored = {"url": "https://x.example", "coalesce": {"enabled": True, "window_seconds": 30.0}}
    baseline = project_fields(_SCHEMA, stored)

    # The user edits the JSON tab: window 30 -> 45, plus an undeclared key.
    working = {
        "url": "https://x.example",
        "coalesce": {"enabled": True, "window_seconds": 45.0},
        "x_extra": 1,
    }
    from_json = project_fields(_SCHEMA, working)

    assert build_patch(baseline, from_json) == {"coalesce": {"window_seconds": 45.0}}


def test_projection_of_a_missing_parameters_object_is_empty() -> None:
    """An empty parameters object projects to the widgets' empty values."""
    projected = project_fields(_SCHEMA, {})
    assert projected["url"] is None
    assert projected["coalesce.enabled"] is False
    assert projected["filter.event_types"] == []
    # An empty baseline compares equal to itself: nothing to save.
    assert build_patch(projected, projected) == {}
