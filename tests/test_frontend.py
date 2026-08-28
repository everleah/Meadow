"""Smoke tests for the frontend package (Phase 7)."""

from __future__ import annotations

import importlib

from meadow.frontend.api import LoginResponse, MeResponse, is_session_expired
from meadow.frontend.grid import (
    attribute_columns,
    build_patch,
    collect_subtree,
    enrich_tree_nodes,
    resolve_value,
    row_data,
    status_badge,
)
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
        "meadow.frontend.grid",
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


def test_grid_view_keys_translate() -> None:
    assert t("grid.views", locale="de") == "Ansichten"
    assert t("grid.save_view", locale="en") == "Save view"
    assert t("grid.close", locale="fr") == "Fermer"
    assert t("grid.view_name_required", locale="de") == "Bitte einen Ansichtsnamen eingeben."


def test_resolve_value() -> None:
    assert resolve_value({"en": "Product", "de": "Produkt"}, "de") == "Produkt"
    assert resolve_value({"en": "Product", "de": "Produkt"}, "fr") == "Product"
    assert resolve_value("Plain", "en") == "Plain"
    assert resolve_value(None, "en") == ""
    assert resolve_value({}, "en") == ""


def test_status_badge() -> None:
    assert "bg-primary" in status_badge("published")
    assert "bg-neutral" in status_badge("draft")
    assert "bg-secondary" in status_badge("unknown")


def test_row_data() -> None:
    row = row_data(
        {
            "id": "object:1",
            "type": "product",
            "status": "published",
            "attributes": {"name": {"en": "Widget"}},
        },
        "en",
    )
    assert row["_id"] == "object:1"
    assert row["_name"] == "Widget"
    assert row["_type"] == "product"
    assert "published" in row["_status"]


def test_build_patch_multilingual() -> None:
    original = {"attributes": {"name": {"en": "Widget", "de": "Bauteil"}}}
    patch = build_patch("object:1", "Neues Teil", original, "de")
    assert patch == {
        "id": "object:1",
        "attributes": {"name": {"en": "Widget", "de": "Neues Teil"}},
    }


def test_build_patch_scalar() -> None:
    original = {"attributes": {"name": "Widget"}}
    patch = build_patch("object:1", "Gadget", original, "de")
    assert patch == {"id": "object:1", "attributes": {"name": "Gadget"}}


def test_is_session_expired_detects_expired_token() -> None:
    results = [{"id": "object:1", "success": False, "error": "invalid or expired token"}]
    assert is_session_expired(results)


def test_is_session_expired_ignores_other_failures() -> None:
    assert not is_session_expired([{"id": "object:1", "success": True, "status": "draft"}])
    assert not is_session_expired(
        [{"id": "object:1", "success": False, "error": "some other error"}]
    )
    assert not is_session_expired([])


def test_attribute_columns_resolves_label_and_hides() -> None:
    definitions = [
        {"name": "price", "label": {"en": "Price", "de": "Preis"}},
        {"name": "sku", "label": {}},
    ]
    columns = attribute_columns(definitions, "de")
    assert columns[0] == {
        "headerName": "Preis",
        "field": "attr_price",
        "width": 140,
        "hide": True,
    }
    assert columns[1]["field"] == "attr_sku"
    assert columns[1]["headerName"] == "sku"  # falls back to name when label missing


def test_row_data_with_attribute_names() -> None:
    row = row_data(
        {
            "id": "object:1",
            "type": "product",
            "status": "draft",
            "attributes": {"name": {"en": "Widget"}, "price": 9.5},
        },
        "en",
        ["price"],
    )
    assert row["attr_price"] == "9.5"


def test_collect_subtree() -> None:
    children = {"a": ["b", "c"], "b": ["d"], "c": [], "d": [], "e": []}
    assert set(collect_subtree("a", children)) == {"a", "b", "c", "d"}
    assert collect_subtree("e", children) == ["e"]


def test_enrich_tree_nodes() -> None:
    objects = {
        "object:root": {"id": "object:root", "attributes": {}},
        "object:a": {"id": "object:a", "attributes": {"name": {"en": "Alpha"}}},
        "object:b": {"id": "object:b", "attributes": {"name": {"de": "Beta"}}},
    }
    raw = [
        {
            "id": "object:root",
            "children": [
                {"id": "object:a", "children": []},
                {"id": "object:b", "children": []},
            ],
        }
    ]
    nodes, children_by_id = enrich_tree_nodes(raw, objects, "de", "object:root", "Root")
    assert nodes[0]["id"] == "object:root"
    assert nodes[0]["label"] == "Root"
    assert [c["label"] for c in nodes[0]["children"]] == ["Alpha", "Beta"]
    assert children_by_id["object:root"] == ["object:a", "object:b"]
