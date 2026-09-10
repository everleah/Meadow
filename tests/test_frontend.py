"""Smoke tests for the frontend package (Phase 7)."""

from __future__ import annotations

import importlib

from meadow.frontend.api import CoreError, LoginResponse, MeResponse, is_session_expired
from meadow.frontend.edit_queue import next_object_id, position_of
from meadow.frontend.grid import (
    MAX_CONTENT_CHARS,
    attribute_column_specs,
    attribute_columns,
    build_patch,
    collect_subtree,
    completeness_badge,
    content_widths,
    editable_cell_value,
    editable_cells,
    enrich_tree_nodes,
    export_fields,
    filter_tree_nodes,
    is_editable_definition,
    language_cells,
    language_field,
    name_language_columns,
    resolve_value,
    restore_columns,
    row_data,
    status_badge,
    storable_cell_value,
    with_header_tooltips,
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


def test_completeness_badge() -> None:
    assert "bg-grey" in completeness_badge(0)
    assert "bg-orange" in completeness_badge(50)
    assert "bg-positive" in completeness_badge(100)
    assert "0%" in completeness_badge(0)
    assert "50%" in completeness_badge(50)
    assert "100%" in completeness_badge(100)


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
    assert row["_completeness"] == ""  # no completeness value present


def test_row_data_with_completeness() -> None:
    row = row_data(
        {
            "id": "object:1",
            "type": "product",
            "status": "draft",
            "attributes": {"name": {"en": "Widget"}},
            "completeness": {"en": 50},
        },
        "en",
    )
    assert "50%" in row["_completeness"]
    assert "bg-orange" in row["_completeness"]


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
        {"name": "price", "label": {"en": "Price", "de": "Preis"}, "type": "number"},
        {"name": "sku", "label": {}, "type": "text"},
    ]
    columns = attribute_columns(definitions, "de")
    assert columns[0] == {
        "headerName": "Preis",
        "field": "attr_price",
        "width": 140,
        "hide": True,
        "editable": True,
        "cellDataType": "number",
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


def test_attribute_columns_adds_language_columns_for_multilingual_attributes() -> None:
    definitions = [
        {"name": "name", "label": {"de": "Name"}, "multilingual": True},
        {"name": "sku", "label": {}, "multilingual": False},
    ]
    columns, language_fields = attribute_column_specs(definitions, "de", ["en", "fr"])
    assert [c["field"] for c in columns] == [
        "attr_name",
        "attr_name__en",
        "attr_name__fr",
        "attr_sku",
    ]
    assert columns[1]["headerName"] == "Name · EN"
    assert all(c["hide"] for c in columns)
    assert language_fields == {"attr_name__en": "name", "attr_name__fr": "name"}
    # A non-multilingual attribute never gets language columns.
    assert attribute_columns(definitions, "de")[1]["field"] == "attr_sku"


def test_language_field() -> None:
    assert language_field("attr_name", "de-AT") == "attr_name__de-AT"
    assert language_field("_name", "de") == "_name__de"


def test_name_language_columns() -> None:
    columns, language_fields = name_language_columns("Name", ["de"])
    assert columns == [
        {"headerName": "Name · DE", "field": "_name__de", "width": 140, "hide": True}
    ]
    assert language_fields == {"_name__de": "name"}
    assert name_language_columns("Name", []) == ([], {})


def test_language_cells_use_the_stored_value_without_fallback() -> None:
    cells = language_cells(
        {"attributes": {"name": {"de": "Maus", "en": "Mouse"}}},
        ["en", "fr"],
        {"name": "_name"},
    )
    assert cells == {"_name__en": "Mouse", "_name__fr": ""}


def test_language_cells_resolve_enum_labels_per_language() -> None:
    cells = language_cells(
        {"attributes": {"color": {"de": "red", "en": "red"}}},
        ["en"],
        {"color": "attr_color"},
        {"en": {"color": {"red": "Red"}}},
    )
    assert cells["attr_color__en"] == "Red"


def test_language_cells_ignore_missing_attributes() -> None:
    cells = language_cells({"attributes": {}}, ["en"], {"name": "_name"})
    assert cells == {"_name__en": ""}


def test_is_editable_definition() -> None:
    assert is_editable_definition({"name": "title", "type": "text"})
    assert is_editable_definition({"name": "price", "type": "number"})
    assert is_editable_definition({"name": "flag", "type": "boolean"})
    assert is_editable_definition({"name": "color", "type": "enum"})
    assert is_editable_definition({"name": "due", "type": "date"})
    # Computed values belong to a rule, quantified values need a unit editor,
    # reference/media attributes need dedicated pickers.
    assert not is_editable_definition({"name": "gross", "type": "number", "computed": True})
    assert not is_editable_definition({"name": "height", "type": "number", "unit_id": "unit_m"})
    assert not is_editable_definition({"name": "ref", "type": "reference"})


def test_editor_props_for_a_date_column() -> None:
    columns, _ = attribute_column_specs(
        [
            {"name": "due", "type": "date"},
            {"name": "shipped", "type": "date", "computed": True},
        ],
        "de",
    )
    assert columns[0]["editable"] is True
    assert columns[0]["cellEditor"] == "agDateStringCellEditor"
    assert "editable" not in columns[1]  # computed -> read-only


def test_with_header_tooltips_adds_the_header_text() -> None:
    columns = with_header_tooltips(
        [{"headerName": "Name", "field": "_name"}, {"headerName": "", "field": "_refs"}]
    )
    assert columns[0]["headerTooltip"] == "Name"
    assert "headerTooltip" not in columns[1]


def test_content_widths_grows_with_the_content_and_is_capped() -> None:
    columns = [{"headerName": "ID", "field": "_id"}, {"headerName": "Long", "field": "_name"}]
    rows = [{"_id": "object:1", "_name": "x" * (MAX_CONTENT_CHARS + 20)}]
    widths = content_widths(columns, rows)
    assert [w["key"] for w in widths] == ["_id", "_name"]
    assert widths[0]["newWidth"] < widths[1]["newWidth"]
    # The long value is capped at MAX_CONTENT_CHARS characters.
    assert widths[1]["newWidth"] == MAX_CONTENT_CHARS * 8 + 26
    # Empty content keeps a readable minimum width.
    empty = content_widths([{"headerName": "", "field": "_x"}], [{}])
    assert empty[0]["newWidth"] == 70


def test_restore_columns_filters_unknown_fields() -> None:
    known = {"_id", "_name", "attr_title"}
    assert restore_columns(["_id", "attr_title"], known) == {"_id", "attr_title"}
    # Removed attributes are dropped; nothing usable left -> keep the default.
    assert restore_columns(["attr_gone"], known) is None
    assert restore_columns(None, known) is None
    assert restore_columns(["_name", 42], known) == {"_name"}


def test_position_of_a_sequential_edit_queue() -> None:
    ids = ["object:1", "object:2", "object:3"]
    assert position_of(ids, "object:1") == 0
    assert position_of(ids, "object:3") == 2
    assert position_of(ids, "object:9") is None
    assert position_of([], "object:1") is None


def test_next_object_id_advances_and_stops_at_the_end() -> None:
    ids = ["object:1", "object:2", "object:3"]
    assert next_object_id(ids, "object:1") == "object:2"
    assert next_object_id(ids, "object:2") == "object:3"
    # The last entry (and an id outside the queue) ends the sequence.
    assert next_object_id(ids, "object:3") is None
    assert next_object_id(ids, "object:9") is None
    assert next_object_id(["object:1"], "object:1") is None


def test_attribute_columns_add_editor_props_for_editable_types() -> None:
    definitions = [
        {"name": "title", "type": "text"},
        {"name": "price", "type": "number"},
        {"name": "flag", "type": "boolean"},
        {"name": "color", "type": "enum"},
        {"name": "gross", "type": "number", "computed": True},
    ]
    columns, _ = attribute_column_specs(
        definitions, "de", [], {"color": {"red": "Rot", "blue": "Blau"}}
    )
    assert columns[0]["editable"] is True
    assert columns[0].get("cellDataType") is None
    assert columns[1]["cellDataType"] == "number" and columns[1]["editable"] is True
    assert columns[2]["cellDataType"] == "boolean" and columns[2]["editable"] is True
    assert columns[3]["cellEditor"] == "agSelectCellEditor"
    assert columns[3]["cellEditorParams"] == {"values": ["Rot", "Blau"]}
    assert "editable" not in columns[4]  # computed -> read-only
    # An enum column without resolved options stays read-only (no empty select).
    assert "editable" not in attribute_column_specs([definitions[3]], "de")[0][0]
    # Per-language columns never become editable.
    with_languages, _ = attribute_column_specs(
        [{"name": "title", "type": "text", "multilingual": True}], "de", ["en"]
    )
    assert with_languages[0]["editable"] is True
    assert "editable" not in with_languages[1]


def test_editable_cell_value_uses_the_native_type() -> None:
    number = {"name": "price", "type": "number"}
    boolean = {"name": "flag", "type": "boolean"}
    enum = {"name": "color", "type": "enum"}
    text = {"name": "title", "type": "text"}
    assert editable_cell_value(number, 12.5, "de") == 12.5
    assert editable_cell_value(number, "12,5", "de") == 12.5
    assert editable_cell_value(number, None, "de") is None
    assert editable_cell_value(boolean, True, "de") is True
    assert editable_cell_value(boolean, "false", "de") is False
    assert editable_cell_value(enum, "red", "de", {"red": "Rot"}) == "Rot"
    assert editable_cell_value(enum, "gone", "de", {"red": "Rot"}) == "gone"
    assert editable_cell_value(text, {"en": "Mouse", "de": "Maus"}, "en") == "Mouse"


def test_editable_cells_skips_unset_working_language_values() -> None:
    editable = {"title": {"name": "title", "type": "text"}, "price": {"name": "price"}}
    assert editable_cells({"attributes": {}}, "en", editable) == {
        "attr_title": None,
        "attr_price": None,
    }
    # A value maintained in another language keeps the resolved display value.
    assert editable_cells({"attributes": {"title": {"de": "Maus"}}}, "en", editable) == {
        "attr_price": None
    }
    assert editable_cells({"attributes": {"title": {"de": "Maus"}}}, "de", editable) == {
        "attr_title": "Maus",
        "attr_price": None,
    }


def test_storable_cell_value_restores_codes_and_merges_languages() -> None:
    enum = {"name": "color", "type": "enum"}
    text = {"name": "title", "type": "text"}
    number = {"name": "price", "type": "number"}
    boolean = {"name": "flag", "type": "boolean"}
    assert storable_cell_value(enum, "Rot", "red", "de", {"red": "Rot"}) == "red"
    assert storable_cell_value(enum, "Neu", "red", "de", {"red": "Rot"}) == "Neu"
    assert storable_cell_value(number, "12,5", "1", "de") == 12.5
    assert storable_cell_value(number, "12", "1", "de") == 12
    assert storable_cell_value(boolean, "true", None, "de") is True
    assert storable_cell_value(text, "", "x", "de") is None
    assert storable_cell_value(text, "Mouse", "Maus", "en") == "Mouse"
    # Multilingual: only the working language changes, the others are preserved.
    assert storable_cell_value(text, "Mouse", {"de": "Maus"}, "en") == {
        "de": "Maus",
        "en": "Mouse",
    }
    assert storable_cell_value(text, "Neu", {"de": "Maus"}, "de") == {"de": "Neu"}
    assert storable_cell_value(text, "", {"de": "Maus"}, "de") == {}


def test_export_fields_maps_and_deduplicates_language_columns() -> None:
    columns = [
        {"field": "_id"},
        {"field": "_name"},
        {"field": "_completeness"},
        {"field": "attr_name"},
        {"field": "attr_name__en"},
        {"field": "attr_price"},
        {"field": "_refs"},
    ]
    fields = export_fields(
        columns,
        {"_id", "_name", "attr_name", "attr_name__en", "attr_price"},
        {"attr_name__en": "name"},
    )
    # `_name`, `attr_name` and `attr_name__en` all denote the `name` attribute
    # -> exported once.
    assert fields == ["id", "name", "price"]


def test_export_fields_falls_back_to_base_fields() -> None:
    assert export_fields([{"field": "attr_price"}], set()) == ["id", "type", "status"]


def test_collect_subtree() -> None:
    children = {"a": ["b", "c"], "b": ["d"], "c": [], "d": [], "e": []}
    assert set(collect_subtree("a", children)) == {"a", "b", "c", "d"}
    assert collect_subtree("e", children) == ["e"]


def test_enrich_tree_nodes() -> None:
    objects = {
        "object:root": {"id": "object:root", "attributes": {}, "type": "root"},
        "object:a": {"id": "object:a", "attributes": {"name": {"en": "Alpha"}}, "type": "product"},
        "object:b": {"id": "object:b", "attributes": {"name": {"de": "Beta"}}, "type": "variant"},
        "object:c": {"id": "object:c", "attributes": {}, "type": "category"},
    }
    raw = [
        {
            "id": "object:root",
            "children": [
                {"id": "object:a", "children": []},
                {"id": "object:b", "children": []},
                {"id": "object:c", "children": []},
            ],
        }
    ]
    nodes, children_by_id = enrich_tree_nodes(raw, objects, "de", "object:root", "Root")
    assert nodes[0]["id"] == "object:root"
    assert nodes[0]["label"] == "Root"
    assert nodes[0]["type"] == "root"
    children = nodes[0]["children"]
    assert [c["label"] for c in children] == ["Alpha", "Beta", "category"]
    assert children[0]["type"] == "product"
    assert children_by_id["object:root"] == ["object:a", "object:b", "object:c"]


def test_filter_tree_nodes() -> None:
    nodes = [
        {
            "id": "root",
            "label": "Structure",
            "type": "root",
            "children": [
                {"id": "a", "label": "Alpha", "type": "product", "children": []},
                {
                    "id": "b",
                    "label": "Beta",
                    "type": "variant",
                    "children": [{"id": "b1", "label": "Betty", "type": "variant", "children": []}],
                },
            ],
        }
    ]
    assert filter_tree_nodes(nodes, "") == nodes
    # "beta" matches the "Beta" node; its "Betty" child is dropped (no match).
    filtered = filter_tree_nodes(nodes, "beta")
    assert filtered[0]["id"] == "root"
    assert [c["id"] for c in filtered[0]["children"]] == ["b"]
    assert filtered[0]["children"][0]["children"] == []
    # "betty" keeps ancestors: root -> b -> b1.
    filtered = filter_tree_nodes(nodes, "betty")
    assert filtered[0]["id"] == "root"
    assert [c["id"] for c in filtered[0]["children"]] == ["b"]
    assert filtered[0]["children"][0]["children"][0]["id"] == "b1"
    assert filter_tree_nodes(nodes, "zzz") == []


def test_form_i18n_keys_translate() -> None:
    assert t("form.summary", locale="de") == "Stammdaten"
    assert t("form.summary", locale="en") == "Basic data"
    assert t("form.summary", locale="fr") == "Informations principales"
    assert t("form.ungrouped", locale="de") == "Attribute ohne Gruppe"
    assert t("form.languages", locale="de").format(count=9) == "Sprachen (9)"
    assert (
        t("form.save_failed_reason", locale="en").format(
            reason="validation failed: sku: SKU is required (required)"
        )
        == "Could not save: validation failed: sku: SKU is required (required)"
    )


def test_core_error_carries_message_and_status() -> None:
    error = CoreError("validation failed: sku: SKU is required (required)", status=400)
    assert str(error) == "validation failed: sku: SKU is required (required)"
    assert error.status == 400
