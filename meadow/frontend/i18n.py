"""Minimal i18n for the frontend: JSON translation strings and a `t()` helper.

The translation files live in `meadow/frontend/locales/<locale>.json`. The active
locale is stored per user in the NiceGUI session (`app.storage.user`) and defaults
to the browser's `Accept-Language` on first use.
"""

from __future__ import annotations

import json
from pathlib import Path

from nicegui import app, ui

#: Directory with the translation JSON files.
LOCALES_DIR = Path(__file__).parent / "locales"
#: Default locale when no preference or browser match is found.
DEFAULT_LOCALE = "en"
#: Supported locales (each has a matching JSON file).
SUPPORTED_LOCALES = ("en", "de", "fr")

_STRINGS: dict[str, dict[str, str]] = {}


def _load(locale: str) -> dict[str, str]:
    """Loads (and caches) the translation strings for a locale."""
    if locale not in _STRINGS:
        path = LOCALES_DIR / f"{locale}.json"
        _STRINGS[locale] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return _STRINGS[locale]


def supported_locales() -> list[str]:
    """Returns the supported locale codes."""
    return list(SUPPORTED_LOCALES)


def best_locale(header: str | None) -> str:
    """Returns the best supported locale for an `Accept-Language` header."""
    if header:
        for part in header.split(","):
            base = part.split(";")[0].strip().lower().split("-")[0]
            if base in SUPPORTED_LOCALES:
                return base
    return DEFAULT_LOCALE


def current_locale() -> str:
    """The active locale for the current user."""
    locale = app.storage.user.get("locale")
    return locale if locale in SUPPORTED_LOCALES else DEFAULT_LOCALE


def set_locale(locale: str) -> None:
    """Sets the user's locale (session) and reloads the current page."""
    if locale in SUPPORTED_LOCALES:
        app.storage.user["locale"] = locale
    ui.navigate.reload()


def t(key: str, *, locale: str | None = None, **kwargs: object) -> str:
    """Returns the translated string for the active (or given) locale.

    Falls back to the key itself when no translation exists, so missing keys are
    immediately visible during development.
    """
    target = locale or current_locale()
    text = _load(target).get(key) or _load(DEFAULT_LOCALE).get(key) or key
    if kwargs:
        return text.format(**kwargs)
    return text
