"""Tests for the persisted connector configuration loader (Phase 6)."""

from __future__ import annotations

from meadow.worker.connectors import _string_list, _webhook_config_from_row


def test_row_to_webhook_config() -> None:
    row = {
        "id": "connector:abc123",
        "name": "middleware-webhook",
        "type": "webhook",
        "enabled": True,
        "parameters": {
            "url": "https://middleware.example/events",
            "method": "PUT",
            "headers": {"Authorization": "Bearer token"},
            "filter": {
                "event_types": ["object.updated"],
                "object_types": ["product"],
                "attribute_changes": ["price", "status"],
            },
            "coalesce": {"enabled": True, "window_seconds": 15},
            "timeout_s": 10.0,
        },
    }
    config = _webhook_config_from_row(row)
    assert config is not None
    assert config.key == "middleware-webhook"
    assert config.url == "https://middleware.example/events"
    assert config.method == "PUT"
    assert config.headers == {"Authorization": "Bearer token"}
    assert config.filter.event_types == ["object.updated"]
    assert config.filter.object_types == ["product"]
    assert config.filter.attribute_changes == ["price", "status"]
    assert config.coalesce.enabled is True
    assert config.coalesce.window_seconds == 15
    assert config.timeout_s == 10.0


def test_row_without_url_is_skipped() -> None:
    assert _webhook_config_from_row({"id": "connector:1", "parameters": {}}) is None


def test_row_with_defaults() -> None:
    row = {"id": "connector:1", "parameters": {"url": "https://example.com"}}
    config = _webhook_config_from_row(row)
    assert config is not None
    assert config.key == "connector:1"
    assert config.method == "POST"
    assert config.filter.event_types == []
    assert config.coalesce.enabled is False


def test_string_list_normalizes() -> None:
    assert _string_list(["a", "b", ""]) == ["a", "b"]
    assert _string_list("a, b ,c") == ["a", "b", "c"]
    assert _string_list(None) == []
