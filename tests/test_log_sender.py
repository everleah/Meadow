"""Tests for the worker's curated log sender (Phase 4, step: Log-Sender)."""

from __future__ import annotations

from meadow.worker.log_sender import LogSender


def test_accepts_filters_by_level_and_category() -> None:
    sender = LogSender("http://core")
    # Defaults: persist level WARN, categories {"app"}.
    assert sender._accepts("INFO", "app") is False
    assert sender._accepts("ERROR", "app") is True
    assert sender._accepts("ERROR", "rule") is False


def test_apply_config_updates_level_and_categories() -> None:
    sender = LogSender("http://core")
    sender.apply_config({"log_persist_level": "DEBUG", "log_persist_categories": ["app", "rule"]})
    assert sender._accepts("DEBUG", "rule") is True
    assert sender._accepts("DEBUG", "auth") is False


def test_apply_config_ignores_invalid_values() -> None:
    sender = LogSender("http://core")
    sender.apply_config({"log_persist_level": "BOGUS", "log_persist_categories": "x"})
    assert sender._persist_level == "WARN"
    assert sender._persist_categories == {"app"}


def test_enqueue_drops_oldest_when_full() -> None:
    sender = LogSender("http://core", max_buffer_size=2)
    sender.apply_config({"log_persist_level": "DEBUG", "log_persist_categories": ["app", "rule"]})
    for i in range(3):
        sender.enqueue(
            service="pim-worker",
            level="DEBUG",
            category="app",
            message=f"entry {i}",
        )
    messages = [entry["message"] for entry in sender._buffer]
    assert messages == ["entry 1", "entry 2"]


def test_enqueue_respects_persist_level() -> None:
    sender = LogSender("http://core", max_buffer_size=10)
    sender.apply_config({"log_persist_level": "ERROR", "log_persist_categories": ["app"]})
    sender.enqueue(service="pim-worker", level="INFO", category="app", message="nope")
    sender.enqueue(service="pim-worker", level="ERROR", category="app", message="yes")
    assert [entry["message"] for entry in sender._buffer] == ["yes"]
