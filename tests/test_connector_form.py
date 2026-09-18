"""Tests for the schema-driven connector parameter form.

The careful part is the round trip: schema + parameters → widgets → a partial
patch the core merges (`parameters_mode: merge`). The pure helpers are tested
here; the widgets themselves are verified in the browser.

`load_option_sources` (the admin page's dynamic field options) is covered too: its
sources are optional, so the degradation has to stay visible instead of silent.
"""

from __future__ import annotations

import logging
from typing import Any

from meadow.frontend.connector_form import (
    MISSING,
    apply_patch,
    build_patch,
    connector_test_hint,
    connector_test_lines,
    default_values,
    fields_of,
    format_value,
    get_path,
    is_visible,
    nest,
    parse_value,
    summary_parts,
    summary_text,
    unknown_keys,
    validate_values,
)
from meadow.frontend.connectors import load_option_sources
from meadow.frontend.i18n import t


def _webhook_schema() -> dict:
    """The `webhook` schema as the core delivers it (shape verified via API)."""
    return {
        "type": "webhook",
        "label_key": "connectors.type_webhook",
        "direction": "outbound",
        "wired": True,
        "groups": [
            {
                "key": "target",
                "label_key": "connector_fields.group_target",
                "fields": [
                    {
                        "key": "url",
                        "type": "string",
                        "widget": "text",
                        "required": True,
                        "label_key": "connector_fields.webhook.url.label",
                    },
                    {
                        "key": "method",
                        "type": "string",
                        "widget": "select",
                        "default": "POST",
                        "enum": ["POST", "PUT", "PATCH"],
                        "label_key": "connector_fields.webhook.method.label",
                    },
                    {
                        "key": "timeout_s",
                        "type": "number",
                        "widget": "number",
                        "default": 30.0,
                        "minimum": 1.0,
                        "maximum": 600.0,
                        "label_key": "connector_fields.webhook.timeout_s.label",
                    },
                ],
            },
            {
                "key": "filter",
                "label_key": "connector_fields.group_filter",
                "fields": [
                    {
                        "key": "filter.event_types",
                        "type": "string_list",
                        "widget": "multiselect",
                        "default": [],
                        "enum": ["object.created", "object.updated"],
                        "label_key": "connector_fields.webhook.filter_event_types.label",
                    },
                ],
            },
            {
                "key": "coalesce",
                "label_key": "connector_fields.group_coalesce",
                "fields": [
                    {
                        "key": "coalesce.enabled",
                        "type": "boolean",
                        "widget": "switch",
                        "default": False,
                        "label_key": "connector_fields.webhook.coalesce_enabled.label",
                    },
                    {
                        "key": "coalesce.window_seconds",
                        "type": "number",
                        "widget": "number",
                        "default": 30.0,
                        "label_key": "connector_fields.webhook.coalesce_window_seconds.label",
                        "show_if": {"key": "coalesce.enabled", "equals": True},
                    },
                ],
            },
        ],
    }


def _unwired_schema() -> dict:
    return {
        "type": "json_import",
        "label_key": "connectors.type_json_import",
        "wired": False,
        "note_key": "connector_fields.unwired",
        "groups": [],
    }


def test_fields_are_flattened_per_group() -> None:
    keys = [field["key"] for field in fields_of(_webhook_schema())]
    assert keys == [
        "url",
        "method",
        "timeout_s",
        "filter.event_types",
        "coalesce.enabled",
        "coalesce.window_seconds",
    ]
    assert fields_of(_unwired_schema()) == []
    assert fields_of(None) == []


def test_path_access_round_trip() -> None:
    parameters: dict[str, Any] = {}
    nest_values = nest({"filter.event_types": ["object.created"], "url": "https://x"})
    assert nest_values == {"filter": {"event_types": ["object.created"]}, "url": "https://x"}

    assert get_path(nest_values, "filter.event_types") == ["object.created"]
    assert get_path(nest_values, "filter.object_types") is MISSING
    assert get_path(nest_values, "nope.deep") is MISSING
    assert get_path(parameters, "url") is MISSING


def test_defaults_are_flat_paths() -> None:
    assert default_values(_webhook_schema()) == {
        "method": "POST",
        "timeout_s": 30.0,
        "filter.event_types": [],
        "coalesce.enabled": False,
        "coalesce.window_seconds": 30.0,
    }


def test_unknown_keys_are_top_level_only() -> None:
    known = {"url": "https://x", "filter": {"event_types": []}}
    assert unknown_keys(_webhook_schema(), known) == []

    with_unknown = {
        "url": "https://x",
        "filter": {"event_types": []},
        "retry_policy": {"max_attempts": 5},
    }
    assert unknown_keys(_webhook_schema(), with_unknown) == ["retry_policy"]
    # An unwired type declares nothing, so everything counts as undeclared.
    assert unknown_keys(_unwired_schema(), {"a": 1}) == ["a"]


def test_show_if_controls_visibility() -> None:
    window = fields_of(_webhook_schema())[-1]
    assert is_visible({"key": "url"}, {"coalesce.enabled": True}) is True
    assert is_visible(window, {"coalesce.enabled": True}) is True
    assert is_visible(window, {"coalesce.enabled": False}) is False
    assert is_visible(window, {}) is False


def test_value_formatting_and_parsing() -> None:
    url, method, timeout, event_types, enabled = fields_of(_webhook_schema())[:5]

    assert format_value(url, MISSING) == ""
    assert format_value(method, "PUT") == "PUT"
    assert format_value(enabled, MISSING) is False
    assert format_value(event_types, MISSING) == []
    # An empty number must reach `ui.number` as None, not as "".
    assert format_value(timeout, MISSING) is None
    assert format_value(timeout, 30.0) == 30.0

    assert parse_value(url, "  https://x.example  ") == "https://x.example"
    assert parse_value(url, "   ") is None
    assert parse_value(enabled, True) is True
    assert parse_value(event_types, ["object.created", " ", "object.updated"]) == [
        "object.created",
        "object.updated",
    ]
    assert parse_value(event_types, None) == []
    assert parse_value(timeout, "12.5") == 12.5
    assert parse_value(timeout, "") is None
    assert parse_value(
        {"key": "x", "type": "string_map", "widget": "key_value"}, {" A ": " 1 "}
    ) == {"A": " 1 "}


def test_validation_mirrors_the_core_rules() -> None:
    schema = _webhook_schema()
    assert validate_values(schema, {"url": "", "method": "POST"}, locale="en") == [
        "url is required"
    ]
    assert validate_values(schema, {"url": "https://x", "method": "GETX"}, locale="en") == [
        "method must be one of POST, PUT, PATCH"
    ]
    assert validate_values(schema, {"url": "https://x", "timeout_s": 900}, locale="en") == [
        "timeout_s must be at most 600.0"
    ]
    assert validate_values(schema, {"url": "https://x", "timeout_s": 0}, locale="en") == [
        "timeout_s must be at least 1.0"
    ]
    assert validate_values(schema, {"url": "https://x", "method": "POST"}, locale="en") == []
    # Multi-selects are validated element-wise.
    assert (
        validate_values(
            schema, {"url": "https://x", "filter.event_types": ["object.created"]}, locale="en"
        )
        == []
    )
    assert validate_values(
        schema, {"url": "https://x", "filter.event_types": ["object.bogus"]}, locale="en"
    ) == [
        "filter.event_types must be one of object.created, object.updated",
    ]


def test_validation_skips_hidden_required_fields() -> None:
    schema = {
        "wired": True,
        "groups": [
            {
                "key": "g",
                "fields": [
                    {
                        "key": "a",
                        "type": "boolean",
                        "widget": "switch",
                        "default": False,
                    },
                    {
                        "key": "b",
                        "type": "string",
                        "widget": "text",
                        "required": True,
                        "show_if": {"key": "a", "equals": True},
                    },
                ],
            }
        ],
    }
    assert validate_values(schema, {"a": False}, locale="en") == []
    assert validate_values(schema, {"a": True}, locale="en") == ["b is required"]


def test_patch_contains_only_changed_fields() -> None:
    initial = {
        "url": "https://x.example",
        "method": "POST",
        "coalesce.enabled": False,
        "coalesce.window_seconds": 30.0,
    }
    current = dict(initial)
    current["coalesce.enabled"] = True
    assert build_patch(initial, current) == {"coalesce": {"enabled": True}}

    # A cleared field is sent as `null` so the core deletes the key.
    cleared = dict(initial)
    cleared["coalesce.window_seconds"] = None
    assert build_patch(initial, cleared) == {"coalesce": {"window_seconds": None}}

    # An untouched, never-set field is not sent at all.
    untouched = dict(initial)
    untouched["filter.event_types"] = []
    assert build_patch(initial, untouched) == {"filter": {"event_types": []}}

    assert build_patch(initial, dict(initial)) == {}


def test_apply_patch_matches_the_core_merge_mode() -> None:
    current = {
        "url": "https://x.example",
        "headers": {"Authorization": "Bearer token"},
        "coalesce": {"enabled": False, "window_seconds": 30.0},
        "retry_policy": {"max_attempts": 5},
    }
    patched = apply_patch(current, {"coalesce": {"enabled": True}})
    assert patched["coalesce"] == {"enabled": True, "window_seconds": 30.0}
    assert patched["headers"] == {"Authorization": "Bearer token"}
    assert patched["retry_policy"] == {"max_attempts": 5}

    deleted = apply_patch(current, {"coalesce": {"window_seconds": None}})
    assert deleted["coalesce"] == {"enabled": False}

    # The input object is never modified in place.
    apply_patch(current, {"url": None})
    assert current["url"] == "https://x.example"


def test_summary_parts_describe_the_configuration() -> None:
    parameters = {
        "url": "https://middleware.example/events",
        "filter": {"event_types": ["object.created", "object.updated"], "object_types": []},
        "coalesce": {"enabled": True, "window_seconds": 30.0},
    }
    parts = dict(summary_parts(parameters, locale="en"))
    assert parts["connectors.summary_url"] == "https://middleware.example/events"
    assert "2 event types" in parts["connectors.summary_filter"]
    assert parts["connectors.summary_coalesce"] == "30"
    assert summary_parts({}, locale="en") == []


def test_summary_parts_cover_the_import_and_export_profiles() -> None:
    imported = {
        "match_by_key": "sku",
        "source": "erp",
        "emit_events": False,
    }
    parts = dict(summary_parts(imported, locale="en"))
    assert parts["connectors.summary_match_key"] == "sku"
    assert parts["connectors.summary_source"] == "erp"

    exported = {
        "format": "xlsx",
        "fields": ["name", "brand"],
        "locales": ["de", "en"],
        "options": {"email_to": ["ops@example.com"]},
    }
    parts = dict(summary_parts(exported, locale="en"))
    assert parts["connectors.summary_format"] == "xlsx"
    assert parts["connectors.summary_fields"] == "2"
    assert parts["connectors.summary_locales"] == "de, en"
    assert parts["connectors.summary_recipients"] == "1"


def test_summary_lists_undeclared_keys_without_other_values() -> None:
    """Only-undeclared parameters must not read as "no parameters yet"."""
    schema = _webhook_schema()
    summary = summary_text(schema, {"retry_policy": {"max_attempts": 2}}, locale="en")
    assert summary == "1 not evaluated"


def test_summary_text_mentions_undeclared_and_unwired() -> None:
    schema = _webhook_schema()
    assert summary_text(schema, {}, locale="en") == "No parameters yet"

    with_unknown = {"url": "https://x.example", "retry_policy": {}}
    summary = summary_text(schema, with_unknown, locale="en")
    assert "Target: https://x.example" in summary
    assert "1 not evaluated" in summary
    assert summary_text(_unwired_schema(), {"anything": 1}, locale="en") == (
        "Parameters are not evaluated yet"
    )


def test_connector_connector_test_lines_render_the_outcome() -> None:
    lines = connector_test_lines(
        {
            "ok": True,
            "url": "https://middleware.example/events",
            "method": "PUT",
            "status_code": 200,
            "latency_ms": 12.3,
            "excerpt": "accepted",
        },
        locale="en",
    )
    assert lines[0] == "Connection successful"
    assert any("PUT https://middleware.example/events" in line for line in lines)
    assert any("HTTP status: 200" in line for line in lines)
    assert any("12.3" in line for line in lines)
    assert any("accepted" in line for line in lines)


def test_connector_connector_test_lines_render_a_dry_run() -> None:
    lines = connector_test_lines(
        {
            "ok": True,
            "dry_run": True,
            "resolved": {"format": "csv", "fields": ["name"], "locales": []},
        },
        locale="en",
    )
    assert lines[0] == "Connection successful"
    assert "format=csv" in lines[1]
    assert "fields=['name']" in lines[1]


def test_connector_test_hint_matches_the_failure() -> None:
    assert connector_test_hint({"ok": True}) == ""
    assert connector_test_hint({"ok": False, "reachable": False}) == (
        "connector_test.hint_unreachable"
    )
    assert connector_test_hint({"ok": False, "status_code": 403}) == "connector_test.hint_auth"
    assert connector_test_hint({"ok": False, "status_code": 404}) == "connector_test.hint_url"
    assert connector_test_hint({"ok": False, "status_code": 503}) == "connector_test.hint_server"
    assert connector_test_hint({"ok": False, "status_code": 422}) == "connector_test.hint_client"
    assert connector_test_hint({"ok": False}) == "connector_test.hint_generic"


def test_connector_test_lines_include_the_hint() -> None:
    lines = connector_test_lines({"ok": False, "status_code": 401}, locale="en")
    assert lines[0] == "Connection failed"
    assert any("Authorization" in line for line in lines)


def test_i18n_keys_of_the_schema_exist() -> None:
    """Guards against typos in the core's label/help keys (all three locales)."""
    keys = [
        "connector_fields.unwired",
        "connector_fields.group_target",
        "connector_fields.group_headers",
        "connector_fields.group_filter",
        "connector_fields.group_coalesce",
        "connector_fields.group_retry",
        "connector_fields.group_import",
        "connector_fields.group_export",
        "connector_fields.json_import.match_by_key.label",
        "connector_fields.json_import.match_by_key.help",
        "connector_fields.json_import.match_by_id.label",
        "connector_fields.json_import.match_by_id.help",
        "connector_fields.json_import.emit_events.label",
        "connector_fields.json_import.emit_events.help",
        "connector_fields.json_import.source.label",
        "connector_fields.json_import.source.help",
        "connector_fields.json_export.format.label",
        "connector_fields.json_export.format.help",
        "connector_fields.json_export.fields.label",
        "connector_fields.json_export.fields.help",
        "connector_fields.json_export.locales.label",
        "connector_fields.json_export.locales.help",
        "connector_fields.json_export.options_email_to.label",
        "connector_fields.json_export.options_email_to.help",
        "connector_fields.webhook.url.label",
        "connector_fields.webhook.url.help",
        "connector_fields.webhook.method.label",
        "connector_fields.webhook.timeout_s.label",
        "connector_fields.webhook.timeout_s.help",
        "connector_fields.webhook.headers.label",
        "connector_fields.webhook.headers.help",
        "connector_fields.webhook.filter_event_types.label",
        "connector_fields.webhook.filter_event_types.help",
        "connector_fields.webhook.filter_object_types.label",
        "connector_fields.webhook.filter_object_types.help",
        "connector_fields.webhook.filter_attribute_changes.label",
        "connector_fields.webhook.filter_attribute_changes.help",
        "connector_fields.webhook.coalesce_enabled.label",
        "connector_fields.webhook.coalesce_enabled.help",
        "connector_fields.webhook.coalesce_window_seconds.label",
        "connector_fields.webhook.coalesce_window_seconds.help",
        "connector_fields.webhook.retry_max_attempts.label",
        "connector_fields.webhook.retry_max_attempts.help",
        "connector_fields.webhook.retry_backoff_seconds.label",
        "connector_fields.webhook.retry_backoff_seconds.help",
        "connectors.tab_form",
        "connectors.tab_json",
        "connectors.unwired_hint",
        "connectors.unknown_parameters",
        "connectors.reset_parameters",
        "connectors.reset_parameters_hint",
        "connectors.summary_plain",
        "connectors.summary_unwired",
        "connectors.summary_url",
        "connectors.summary_filter",
        "connectors.summary_coalesce",
        "connector_form.add_row",
        "connector_form.row_key",
        "connector_form.row_value",
        "connector_form.field_required",
        "connector_form.field_enum",
        "connectors.test",
        "connectors.test_hint",
        "connectors.test_unsaved",
        "connectors.test_failed",
        "connectors.test_title",
        "connectors.test_running",
        "connectors.test_timeout",
        "connector_test.ok",
        "connector_test.failed",
        "connector_test.resolved",
        "connector_test.target",
        "connector_test.unreachable",
        "connector_test.status",
        "connector_test.latency",
        "connector_test.body",
        "connector_test.error",
        "connector_test.hint_unreachable",
        "connector_test.hint_auth",
        "connector_test.hint_url",
        "connector_test.hint_server",
        "connector_test.hint_client",
        "connector_test.hint_generic",
    ]
    for locale in ("en", "de", "fr"):
        for key in keys:
            assert t(key, locale=locale) != key, (locale, key)


class _OptionClient:
    """A client stub with the four option sources of the form (`OptionSourceClient`)."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    def _check(self) -> None:
        if self.fail:
            raise RuntimeError("core unavailable")

    async def list_object_types(self, token: str, limit: int = 1000) -> list[dict]:
        self._check()
        return [{"name": "product"}, {"name": ""}, {"other": "ignored"}]

    async def list_attribute_vocabulary(self, token: str) -> list[dict]:
        self._check()
        return [{"name": "sku"}, {"name": "color"}]

    async def list_languages(self, token: str) -> list[dict]:
        self._check()
        return [
            {"code": "de", "active": True},
            {"code": "root", "active": True},
            {"code": "fr", "active": False},
            {"code": None, "active": True},
        ]

    async def list_channels(
        self, token: str, *, active: bool | None = None, limit: int = 1000
    ) -> list[dict]:
        self._check()
        return [{"code": "webshop"}, {"code": ""}]


async def test_option_sources_are_mapped_and_filtered() -> None:
    sources = await load_option_sources(_OptionClient(), "token")
    # Empty names, the technical `root` language and inactive languages are dropped.
    assert sources == {
        "object_types": ["product"],
        "attribute_names": ["sku", "color"],
        "languages": ["de"],
        "channels": ["webshop"],
    }


async def test_option_sources_degrade_visibly(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="meadow.frontend.connectors"):
        sources = await load_option_sources(_OptionClient(fail=True), "token")
    # The form degrades to the values the user typed himself - and says so.
    assert sources == {"object_types": [], "attribute_names": [], "languages": [], "channels": []}
    messages = " ".join(record.getMessage() for record in caplog.records)
    for source in ("object types", "attribute names", "languages", "channels"):
        assert f"{source} unavailable" in messages
