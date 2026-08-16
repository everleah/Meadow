"""Smoke test: the core Python dependencies import in the dev environment."""

import importlib


def test_core_dependencies_import() -> None:
    for name in ("nicegui", "pydantic", "nats", "httpx", "structlog", "babel"):
        importlib.import_module(name)
