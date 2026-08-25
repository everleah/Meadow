"""Tests for the persisted connector configuration loader (Phase 6)."""

from __future__ import annotations

from meadow.worker import connectors
from meadow.worker.connectors import (
    _string_list,
    _webhook_config_from_row,
    env_bootstrap_config,
    load_webhook_configs,
)


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


def test_env_bootstrap_none_without_url(monkeypatch) -> None:
    monkeypatch.delenv("WEBHOOK_URL", raising=False)
    assert env_bootstrap_config() is None


def test_env_bootstrap_builds_config(monkeypatch) -> None:
    monkeypatch.setenv("WEBHOOK_URL", "https://legacy.example/events")
    monkeypatch.setenv("WEBHOOK_METHOD", "PUT")
    monkeypatch.setenv("WEBHOOK_FILTER_EVENT_TYPES", "object.created, object.updated")
    monkeypatch.setenv("WEBHOOK_COALESCE_ENABLED", "true")
    monkeypatch.setenv("WEBHOOK_COALESCE_WINDOW_S", "45")
    monkeypatch.setenv("WEBHOOK_HEADERS", '{"X-Custom": "1"}')
    config = env_bootstrap_config()
    assert config is not None
    assert config.key == "env"
    assert config.url == "https://legacy.example/events"
    assert config.method == "PUT"
    assert config.filter.event_types == ["object.created", "object.updated"]
    assert config.coalesce.enabled is True
    assert config.coalesce.window_seconds == 45.0
    assert config.headers == {"X-Custom": "1"}


async def test_load_webhook_configs_prefers_db(monkeypatch) -> None:
    async def fake_fetch(core_url, api_key):
        return [
            _webhook_config_from_row(
                {"id": "connector:1", "parameters": {"url": "https://db.example"}}
            )
        ]

    monkeypatch.setattr(connectors, "fetch_webhook_configs", fake_fetch)
    configs = await load_webhook_configs("http://core", "key")
    assert [config.url for config in configs] == ["https://db.example"]


async def test_load_webhook_configs_falls_back_to_env(monkeypatch) -> None:
    async def fake_fetch(core_url, api_key):
        return []

    monkeypatch.setattr(connectors, "fetch_webhook_configs", fake_fetch)
    monkeypatch.setenv("WEBHOOK_URL", "https://legacy.example/events")
    configs = await load_webhook_configs("http://core", "key")
    assert [config.key for config in configs] == ["env"]


async def test_load_webhook_configs_empty_without_env(monkeypatch) -> None:
    async def fake_fetch(core_url, api_key):
        return []

    monkeypatch.setattr(connectors, "fetch_webhook_configs", fake_fetch)
    monkeypatch.delenv("WEBHOOK_URL", raising=False)
    assert await load_webhook_configs("http://core", "key") == []
