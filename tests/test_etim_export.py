"""Tests for the ETIM BMEcat export spike (`scripts/etim_export_spike.py`).

The pure parts (value rendering, document assembly) are covered here; the XSD
validation runs against `resources/etim/bmecat_etim_501.xsd`, which is licensed
material and therefore not part of the repository (`resources/AGENTS.md`).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "etim_export_spike.py"
_spec = importlib.util.spec_from_file_location("etim_export_spike", SCRIPT)
assert _spec and _spec.loader
export = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = export
_spec.loader.exec_module(export)


def test_feature_values_covers_every_data_type() -> None:
    assert export.feature_values(42.5) == ["42.5"]
    assert export.feature_values(4.0) == ["4"], "an integral float loses its decimal part"
    assert export.feature_values(True) == ["true"]
    assert export.feature_values(False) == ["false"]
    # A range is transmitted as **two** FVALUE entries (ETIM guideline).
    assert export.feature_values({"from": 10, "to": 20}) == ["10", "20"]
    assert export.feature_values({"en": "text", "de": "Text"}) == ["text"]
    assert export.feature_values({"de": "nur deutsch"}, "de") == ["nur deutsch"]


def test_feature_values_reports_missing_values_as_none() -> None:
    assert export.feature_values(None) is None
    assert export.feature_values("") is None
    assert export.feature_values("   ") is None
    # Only the catalogue language counts, never a value from another language.
    assert export.feature_values({"de": "nur deutsch"}) is None
    assert export.feature_values({"from": 1, "to": None}) is None


def test_text_values_are_escaped() -> None:
    assert export.feature_values(" a & b ") == ["a &amp; b"]


def test_document_follows_the_etim_profile() -> None:
    product = {"id": "object:X", "attributes": {"name": "Widget", "EF1": True}}
    definitions = {"EF1": {"type": "boolean"}, "EF2": {"type": "number"}}
    features = [
        {"name": "EF1", "attribute": "EF1", "required": True, "missing": False},
        {"name": "EF2", "attribute": "EF2", "required": True, "missing": True},
    ]
    document, report = export.build_document(
        product,
        features,
        definitions,
        class_code="EC000012",
        catalog_id="CAT",
        supplier="Supplier",
        supplier_pid="411061",
    )
    assert report == {
        "language": "eng",
        "features": 2,
        "with_value": 1,
        "dash": 1,
        "bound": 0,
        "by_reason": {"UN": 1},
        "text_values": [],
    }
    # HEADER order is fixed by the XSD: BUYER is mandatory and comes before SUPPLIER.
    assert document.index("<BUYER>") < document.index("<SUPPLIER>")
    assert (
        '<BMECAT xmlns="https://www.etim-international.com/bmecat/50" version="2005">' in document
    )
    assert "<REFERENCE_FEATURE_SYSTEM_NAME>ETIM-10.0</REFERENCE_FEATURE_SYSTEM_NAME>" in document
    assert "<REFERENCE_FEATURE_GROUP_ID>EC000012</REFERENCE_FEATURE_GROUP_ID>" in document
    # A feature without a value is delivered as `-` with a reason, never omitted.
    assert "<FVALUE>-</FVALUE>" in document
    assert "<FVALUE_DETAILS>UN</FVALUE_DETAILS>" in document
    # The ETIM profile has no UDX.EDXF.VERSION inside the product block.
    assert document.count("<UDX.EDXF.VERSION>") == 1
    assert "<T_NEW_PRODUCTDATA>" in document, "the price-less transaction is used"


def test_a_bound_own_attribute_supplies_the_feature_value() -> None:
    """The binding (`standard_codes`) reads the value from the own attribute."""
    product = {"id": "object:X", "attributes": {"name": "Widget", "nominal_current": 16}}
    definitions = {"nominal_current": {"type": "number"}}
    features = [
        {"name": "EF000123", "attribute": "nominal_current", "required": True, "missing": False},
        {"name": "EF000124", "attribute": "EF000124", "required": False, "missing": False},
    ]
    document, report = export.build_document(
        product,
        features,
        definitions,
        class_code="EC000012",
        catalog_id="CAT",
        supplier="Supplier",
        supplier_pid="411061",
    )
    # `FNAME` keeps the **standard's** code, the value comes from the own attribute.
    assert "<FNAME>EF000123</FNAME>" in document
    assert "<FVALUE>16</FVALUE>" in document
    assert report["bound"] == 1
    assert report["with_value"] == 1
    # A feature without a definition is reported, not silently written.
    assert report["by_reason"]["unknown feature"] == 1
