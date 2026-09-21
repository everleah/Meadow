"""Tests for the tabular adapter (`meadow/tabular.py`).

The module turns a CSV/XLSX upload into a MEF document and — since 2026-09-21 —
also *describes* its columns and *checks* a column→target mapping. The check is the
component the AI mapping suggestion goes through, so these tests pin the semantics
it enforces: which mistake breaks an import (a column that does not exist, two
columns on one target — `build_mef` keeps the last one silently, an unknown
language, nothing mapped, no object type) and which one only disappoints (a target
without an attribute definition, a multilingual attribute without a language, a
business key no column fills).

`describe_columns` must describe *shapes only*: the last test asserts that no cell
value can reach the prompt (`plan/detail/ki-assistenz.md` §6.8).
"""

from __future__ import annotations

from meadow.tabular import (
    TableData,
    build_mef,
    check_mapping,
    describe_columns,
    parse_csv,
    parse_target,
)

ATTRIBUTES: dict[str, bool] = {
    "sku": False,
    "article_number": False,
    "title": True,
    "weight": False,
}
LANGUAGES = ("de", "en")


def _table() -> TableData:
    return TableData(
        columns=["Kunden-Nr", "Bezeichnung", "Preis", "Leer", "Typ"],
        rows=[
            {"Kunden-Nr": "A-1001", "Bezeichnung": "Schraube", "Preis": "1,50", "Leer": "x"},
            {"Kunden-Nr": "A-1002", "Bezeichnung": "Mutter", "Preis": "2,00", "Leer": ""},
        ],
    )


# --- parsing and the MEF builder (the semantics the check relies on) ------------


def test_parse_csv_handles_bom_semicolons_and_empty_rows() -> None:
    content = "Kunden-Nr;Bezeichnung\nA-1001;Schraube\n\nA-1002;Mutter\n".encode("utf-8-sig")
    table = parse_csv(content)
    assert table.columns == ["Kunden-Nr", "Bezeichnung"]
    # The blank line does not become an object.
    assert [row["Kunden-Nr"] for row in table.rows] == ["A-1001", "A-1002"]


def test_parse_target_splits_only_the_first_separator() -> None:
    assert parse_target("title|de") == ("title", "de")
    assert parse_target("title") == ("title", None)
    assert parse_target("title|") == ("title", None)
    assert parse_target("serie|de|en") == ("serie", "de|en")


def test_the_later_column_wins_for_the_same_target() -> None:
    """The silent overwrite the mapping check exists for."""
    table = TableData(
        columns=["Neu", "Alt"],
        rows=[{"Neu": "fresh", "Alt": "stale"}],
    )
    document = build_mef(table, {"Neu": "name", "Alt": "name"}, default_type="product")
    assert document["objects"][0]["attributes"] == {"name": "stale"}


def test_build_mef_merges_languages_and_takes_the_base_fields_from_columns() -> None:
    table = TableData(
        columns=["id", "Typ", "Stand", "Titel"],
        rows=[{"id": "object:1", "Typ": "variant", "Stand": "approved", "Titel": "Hallo"}],
    )
    document = build_mef(
        table,
        {"id": "id", "Typ": "type", "Stand": "status", "Titel": "title|de"},
        default_type="product",
    )
    obj = document["objects"][0]
    assert obj["id"] == "object:1"
    assert obj["type"] == "variant"
    assert obj["status"] == "approved"
    assert obj["attributes"] == {"title": {"de": "Hallo"}}


# --- describing a column without its values ------------------------------------


def test_describe_columns_names_the_shape_not_the_value() -> None:
    hints = describe_columns(_table())
    assert hints["Kunden-Nr"] == "text, 6 characters, all values different"
    assert hints["Bezeichnung"] == "text, 6-8 characters, all values different"
    # A German decimal comma counts as a number.
    assert hints["Preis"] == "number, 4 characters, all values different"
    # A column that is empty in one of two rows says so.
    assert hints["Leer"] == "text, 1 characters, empty in 1 of 2 rows"
    # A column without a single value is named as such.
    assert describe_columns(TableData(columns=["Nix"], rows=[{"Nix": ""}]))["Nix"] == (
        "empty in every row"
    )


def test_describe_columns_never_contains_a_cell_value() -> None:
    hints = describe_columns(_table())
    blob = " ".join(hints.values()) + " ".join(hints)
    for value in ("A-1001", "Schraube", "1,50", "2,00", "Mutter"):
        assert value not in blob


def test_describe_columns_recognises_dates_and_booleans() -> None:
    table = TableData(
        columns=["Datum", "Zeit", "Aktiv"],
        rows=[
            {"Datum": "2026-09-21", "Zeit": "2026-09-21T10:00:00", "Aktiv": "ja"},
            {"Datum": "2026-09-22", "Zeit": "2026-09-22T11:30:00", "Aktiv": "nein"},
        ],
    )
    hints = describe_columns(table)
    assert hints["Datum"].startswith("date")
    assert hints["Zeit"].startswith("datetime")
    assert hints["Aktiv"].startswith("boolean")


# --- the mapping check ---------------------------------------------------------


def _check(mapping: dict[str, str], **overrides: object):
    values: dict[str, object] = {
        "columns": _table().columns,
        "attributes": ATTRIBUTES,
        "languages": LANGUAGES,
        "conventional": ("name",),
        "key_attributes": (),
        "default_type": "product",
    }
    values.update(overrides)
    return check_mapping(mapping, **values)  # type: ignore[arg-type]


def test_a_workable_mapping_passes_without_a_note() -> None:
    check = _check(
        {
            "Kunden-Nr": "article_number",
            "Bezeichnung": "title|de",
            "Preis": "weight",
            "Leer": "",
            "Typ": "type",
        }
    )
    assert check.ok is True
    assert check.warnings == []
    assert check.message() == "accepted"


def test_a_column_the_file_does_not_have_is_an_error() -> None:
    check = _check({"Bezeichnung": "title|de", "Bild": "image"})
    assert check.ok is False
    assert "`Bild` is not a column of the file" in check.errors


def test_two_columns_on_one_target_are_an_error() -> None:
    check = _check({"Bezeichnung": "title|de", "Kunden-Nr": "title|de"})
    assert check.ok is False
    assert check.errors == [
        "`Bezeichnung`, `Kunden-Nr` write `title|de` — only the last one would survive"
    ]


def test_an_unknown_language_and_a_language_on_a_base_field_are_errors() -> None:
    check = _check({"Bezeichnung": "title|nl", "Typ": "type|de"})
    assert "`title|nl`: `nl` is not a configured data language" in check.errors
    assert "`type|de`: a language applies to an attribute, not to `type`" in check.errors


def test_an_empty_mapping_and_a_missing_object_type_are_errors() -> None:
    assert "no column is mapped" in " ".join(_check({"Bezeichnung": ""}).errors)
    check = _check({"Bezeichnung": "title|de"}, default_type="")
    assert check.ok is False
    assert "no object type" in " ".join(check.errors)
    # A `type` column makes the default type unnecessary.
    assert _check({"Typ": "type", "Bezeichnung": "title|de"}, default_type="").ok is True


def test_an_unknown_attribute_is_only_a_warning() -> None:
    """The core writes an unknown attribute name — the model just does not know it."""
    check = _check({"Kunden-Nr": "kunden_nummer", "Bezeichnung": "title|de"})
    assert check.ok is True
    assert check.warnings == [
        "`kunden_nummer` has no attribute definition — the value is written, "
        "but the attribute stays untyped"
    ]
    assert check.message().startswith("accepted, with notes: ")


def test_a_multilingual_attribute_without_a_language_is_a_warning() -> None:
    check = _check({"Bezeichnung": "title"})
    assert check.ok is True
    assert "`title` is multilingual" in " ".join(check.warnings)
    # A conventional name counts as multilingual too — but its plain form is how the
    # page writes the current language, so it must not be reported.
    assert _check({"Bezeichnung": "name"}).warnings == []


def test_a_business_key_no_column_fills_is_a_warning() -> None:
    check = _check({"Bezeichnung": "title|de"}, key_attributes=("sku",))
    assert check.ok is True
    assert "no column fills the business key `sku` of `product`" in " ".join(check.warnings)
    assert _check({"Kunden-Nr": "sku"}, key_attributes=("sku",)).warnings == []


def test_a_column_without_a_header_and_a_file_without_columns_are_reported() -> None:
    check = _check({"": ""}, columns=["", "Bezeichnung"])
    assert "the file has a column without a header" in " ".join(check.warnings)
    check = _check({}, columns=[])
    assert check.ok is False
    assert check.errors == ["the file has no columns"]
