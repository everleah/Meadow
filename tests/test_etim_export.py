"""Tests for the ETIM BMEcat export spike (`scripts/etim_export_spike.py`).

The spike is a thin CLI around `meadow/worker/bmecat.py` — the *same* module the
export job uses — so these tests only pin the adapter (which product/class/profile
shape the CLI hands to the module, and how it folds the schema verdict into a
result). The rules themselves are covered by `tests/test_bmecat.py`; the XSD
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

PRODUCT = {
    "id": "object:X",
    "type": "product",
    "attributes": {"sku": "411061", "name": {"en": "Widget", "de": "Dings"}, "EF1": True},
}
DEFINITIONS = {"EF1": {"type": "boolean"}, "EF2": {"type": "number"}}
FEATURES = [
    {"name": "EF1", "attribute": "EF1", "required": True},
    {"name": "EF2", "attribute": "EF2", "required": True},
]


def _build(**overrides: object) -> tuple[str, dict]:
    kwargs: dict = {
        "class_code": "EC000012",
        "catalog_id": "CAT",
        "supplier": "Supplier",
        "supplier_pid": "411061",
    }
    kwargs.update(overrides)
    return export.build(PRODUCT, FEATURES, DEFINITIONS, **kwargs)


def test_the_adapter_hands_one_product_with_the_given_class_to_the_module() -> None:
    document, report = _build()

    assert report["products"] == 1, "the spike exports exactly the given product"
    assert report["classes"] == 1, "…and exactly the given class"
    assert "<REFERENCE_FEATURE_GROUP_ID>EC000012</REFERENCE_FEATURE_GROUP_ID>" in document
    assert "<SUPPLIER_PID>411061</SUPPLIER_PID>" in document
    assert "<DESCRIPTION_SHORT>Widget</DESCRIPTION_SHORT>" in document
    # The rules are the module's: a missing mandatory value is `-` + `UN`, not omitted.
    assert "<FVALUE>-</FVALUE>" in document
    assert "<FVALUE_DETAILS>UN</FVALUE_DETAILS>" in document
    assert report["by_reason"] == {"UN": 1}


def test_a_missing_class_is_reported_instead_of_written() -> None:
    document, report = _build(class_code="")

    assert report["skipped"] == {"not classified": 1}, "a class is mandatory"
    assert "<REFERENCE_FEATURE_GROUP_ID>" not in document


def test_validation_reports_a_missing_schema_instead_of_raising() -> None:
    ok, message = export.validate("<BMECAT/>")

    # The licensed schema and `lxml` are optional: the verdict is `False` **with a
    # reason** if a prerequisite is missing, and never an exception.
    assert isinstance(ok, bool)
    if not ok:
        assert message


def test_the_spike_uses_the_module_and_not_a_copy_of_the_rules() -> None:
    """Regression guard for the rule duplication that used to live in the script."""
    source = SCRIPT.read_text(encoding="utf-8")

    assert "from meadow.worker import bmecat" in source
    # No value rendering, no XML assembly — those belong to the worker module. The
    # docstring may *name* an element; writing one is what must not come back.
    for rule in (
        "def feature_values",
        "def format_number",
        "def escape",
        "def element",
        "<FVALUE>",
        "<PRODUCT_FEATURES>",
        "<REFERENCE_FEATURE_GROUP_ID>",
    ):
        assert rule not in source, f"`{rule}` belongs to meadow/worker/bmecat.py"
