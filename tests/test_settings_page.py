"""Page-level tests for `/system-settings` — the AI commissioning aid.

The AI settings are the one group a fresh installation cannot fill from the
handbook alone: without an endpoint and a model name the assistant stays
"not configured" (`plan/detail/ki-assistenz.md` §12). The preset button therefore
fills the chosen provider's example values — but only into **empty** fields, since
a wrong example is worse than none. These tests drive the real page and read the
editors, so a button that overwrites a configured endpoint would fail here.

Replaced, like in `test_users_page.py`: the core client (`FakeCore`) and the auth
middleware; `auth.is_admin()` answers `True`, so the page builds.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncGenerator, Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from nicegui import ui
from nicegui.testing import User, user_simulation

from meadow.frontend.settings import PROVIDER_EXAMPLES

MAIN_FILE = Path(__file__).resolve().parents[1] / "meadow" / "frontend" / "__main__.py"


class FakeCore:
    """Stands in for `CoreClient`: records every call and answers with fixed data."""

    calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
    data: dict[str, Any] = {}

    def __init__(self, base_url: str, **kwargs: Any) -> None:
        self.base_url = base_url
        self.timeout = kwargs.get("timeout", 10.0)

    def __getattr__(self, name: str) -> Callable[..., Awaitable[Any]]:
        async def call(*args: Any, **kwargs: Any) -> Any:
            FakeCore.calls.append((name, args, kwargs))
            if name in FakeCore.data:
                answer = FakeCore.data[name]
                if isinstance(answer, Exception):
                    raise answer
                return answer
            return [] if name.startswith("list_") else {}

        return call

    @classmethod
    def reset(cls, data: dict[str, Any] | None = None) -> None:
        cls.calls = []
        cls.data = data or {}


def _settings(endpoint: str, model: str, provider: str = "generic") -> dict[str, Any]:
    """The AI group as `GET /system/settings` answers it (`value` + `source`)."""
    return {
        "ai_enabled": {"value": False, "source": "default"},
        "ai_provider": {"value": provider, "source": "default"},
        "ai_endpoint": {"value": endpoint, "source": "default"},
        "ai_model": {"value": model, "source": "default"},
        "ai_api_key": {"value": None, "source": "default", "configured": False, "secret": True},
    }


def _drop_frontend_modules() -> None:
    """Forgets the frontend modules so the simulation imports them fresh."""
    for name in [name for name in sys.modules if name.startswith("meadow.frontend")]:
        del sys.modules[name]


@pytest.fixture
async def settings_user(monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[User]:
    """Boots the real app for one test, with a stub core and an admin session."""
    _drop_frontend_modules()
    async with user_simulation(main_file=MAIN_FILE) as user:
        from meadow.frontend import auth, layout, settings

        FakeCore.reset({"list_system_settings": _settings("", "")})
        monkeypatch.setattr(settings, "CoreClient", FakeCore)
        monkeypatch.setattr(layout, "CoreClient", FakeCore)
        monkeypatch.setattr(auth, "is_admin", lambda: True)

        async def allow(  # the middleware calls `self.dispatch(request, call_next)`
            self: Any, request: Any, call_next: Callable[[Any], Awaitable[Any]]
        ) -> Any:
            return await call_next(request)

        monkeypatch.setattr(auth.AuthMiddleware, "dispatch", allow)
        yield user


def _value(user: User, key: str) -> Any:
    """The current value of the editor card of `key`."""
    found = user.find(marker=f"setting-{key}").elements
    assert found, f"the `{key}` editor is missing"
    element = next(iter(found))
    assert isinstance(element, ui.input), f"`{key}` is not a text editor"
    return element.value


async def _wait_until(condition: Callable[[], bool], tries: int = 60) -> None:
    """Waits for recorded state (a click on a handler only schedules it)."""
    for _ in range(tries):
        if condition():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("condition was not reached")


async def test_the_preset_button_fills_empty_ai_fields(settings_user: User) -> None:
    """A fresh installation gets the provider's example endpoint and model."""
    user = settings_user
    await user.open("/system-settings")
    user.find(marker="ai-preset-apply").click()
    await _wait_until(lambda: bool(_value(user, "ai_endpoint")))
    example = PROVIDER_EXAMPLES["generic"]
    assert _value(user, "ai_endpoint") == example["ai_endpoint"]
    assert _value(user, "ai_model") == example["ai_model"]


async def test_the_preset_button_keeps_a_configured_endpoint(
    settings_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An administrator's own endpoint stays; only the empty model is filled."""
    user = settings_user
    from meadow.frontend import settings

    FakeCore.reset({"list_system_settings": _settings("https://internal.example.com/v1", "")})
    monkeypatch.setattr(settings, "CoreClient", FakeCore)
    await user.open("/system-settings")
    user.find(marker="ai-preset-apply").click()
    await _wait_until(lambda: bool(_value(user, "ai_model")))
    assert _value(user, "ai_endpoint") == "https://internal.example.com/v1"
    assert _value(user, "ai_model") == PROVIDER_EXAMPLES["generic"]["ai_model"]
