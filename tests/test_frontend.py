"""Smoke tests for the frontend package (Phase 7)."""

from __future__ import annotations

import importlib

from meadow.frontend.api import LoginResponse, MeResponse
from meadow.frontend.i18n import best_locale, t


def test_frontend_modules_import() -> None:
    """Importing the frontend package registers NiceGUI pages/middleware.

    This is a side-effect smoke test: it must not start the server.
    """
    for module in (
        "meadow.frontend",
        "meadow.frontend.config",
        "meadow.frontend.theme",
        "meadow.frontend.i18n",
        "meadow.frontend.api",
        "meadow.frontend.auth",
        "meadow.frontend.layout",
        "meadow.frontend.views",
    ):
        importlib.import_module(module)


def test_login_response_parses() -> None:
    response = LoginResponse.model_validate({"token": "abc", "token_type": "bearer"})
    assert response.token == "abc"
    assert response.token_type == "bearer"


def test_me_response_parses() -> None:
    response = MeResponse.model_validate(
        {"sub": "user:admin", "username": "admin", "roles": ["superadmin"]}
    )
    assert response.username == "admin"
    assert response.roles == ["superadmin"]


def test_best_locale() -> None:
    assert best_locale("de-DE,de;q=0.9,en;q=0.8") == "de"
    assert best_locale("fr-CH, fr;q=0.9, en;q=0.8") == "fr"
    assert best_locale("en-US,en;q=0.9") == "en"
    assert best_locale("ja-JP;q=0.9, ru;q=0.8") == "en"
    assert best_locale(None) == "en"


def test_t_translates_with_explicit_locale() -> None:
    assert t("nav.dashboard", locale="en") == "Dashboard"
    assert t("nav.dashboard", locale="de") == "Dashboard"
    assert t("nav.products", locale="de") == "Produkte"
    assert t("nav.products", locale="fr") == "Produits"
    assert t("nav.connectors", locale="fr") == "Connecteurs"


def test_t_falls_back_to_key() -> None:
    assert t("does.not.exist", locale="en") == "does.not.exist"
    assert t("login.username", locale="de") == "Benutzername"
