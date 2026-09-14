"""Tests for the ETIM IXF converter (spike phase 1, `scripts/etim_ixf_to_mef.py`).

The fixtures are **synthetic** documents in the IXF shape (the licensed ETIM data
never enters the repository, see `resources/AGENTS.md`); they use the real
namespace so the namespace-agnostic reader is covered too.
"""

# ruff: noqa: E501  # the IXF fixture below is data; its XML lines are legitimately long

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "etim_ixf_to_mef.py"
_spec = importlib.util.spec_from_file_location("etim_ixf_to_mef", SCRIPT)
assert _spec and _spec.loader
converter = importlib.util.module_from_spec(_spec)
# Dataclasses resolve their module through `sys.modules`, so register it first.
sys.modules[_spec.name] = converter
_spec.loader.exec_module(converter)

NS = "https://www.etim-international.com/etimixf/31"

IXF = f"""<?xml version="1.0" encoding="utf-8"?>
<IXF xmlns="{NS}" schemaVersion="3.1">
  <Header>
    <ExportDate>2025-08-05T12:11:25Z</ExportDate>
    <Publication><Name>TEST</Name><Number>10</Number></Publication>
  </Header>
  <Units>
    <Unit><Code>EU570448</Code><Translations>
      <Translation language="EN"><Description>Millimetre</Description><Abbreviation>mm</Abbreviation></Translation>
      <Translation language="nl-BE"><Description>Millimeter</Description><Abbreviation>mm</Abbreviation></Translation>
    </Translations></Unit>
  </Units>
  <Features>
    <Feature><Code>EF000001</Code><Type>Numeric</Type><FeatureGroupCode>EFG00007</FeatureGroupCode>
      <Translations><Translation language="EN"><Description>Nominal current</Description></Translation>
      <Translation language="nl-BE"><Description>Nominale stroom</Description></Translation></Translations></Feature>
    <Feature><Code>EF000002</Code><Type>Logical</Type><FeatureGroupCode>EFG00007</FeatureGroupCode>
      <Translations><Translation language="EN"><Description>With earth contact</Description></Translation></Translations></Feature>
    <Feature><Code>EF000003</Code><Type>Range</Type><FeatureGroupCode>EFG00007</FeatureGroupCode>
      <Translations><Translation language="EN"><Description>Length</Description></Translation></Translations></Feature>
    <Feature><Code>EF000004</Code><Type>Alphanumeric</Type><FeatureGroupCode>EFG00007</FeatureGroupCode>
      <Translations><Translation language="EN"><Description>Colour</Description></Translation></Translations></Feature>
  </Features>
  <FeatureGroups><FeatureGroup><Code>EFG00007</Code>
    <Translations><Translation language="EN"><Description>Electrical</Description></Translation></Translations>
  </FeatureGroup></FeatureGroups>
  <Values><Value><Code>EV000005</Code>
    <Translations><Translation language="EN"><Description>Thread holding</Description></Translation></Translations>
  </Value></Values>
  <Groups><Group><Code>EG000012</Code>
    <Translations><Translation language="EN"><Description>Cables</Description></Translation></Translations>
  </Group></Groups>
  <Classes>
    <Class changeCode="Unchanged"><Code>EC000001</Code><Version>6</Version><Status>Published</Status>
      <GroupCode>EG000012</GroupCode>
      <Translations><Translation language="EN"><Description>Busbar terminal</Description>
        <Synonyms><Synonym>Clamp</Synonym></Synonyms></Translation>
        <Translation language="nl-BE"><Description>Aansluitklem</Description></Translation></Translations>
      <Features>
        <Feature><FeatureCode>EF000001</FeatureCode><UnitCode>EU570448</UnitCode><OrderNumber>1</OrderNumber></Feature>
        <Feature><FeatureCode>EF000003</FeatureCode><UnitCode>EU570448</UnitCode><OrderNumber>2</OrderNumber></Feature>
        <Feature><FeatureCode>EF000004</FeatureCode><OrderNumber>3</OrderNumber></Feature>
      </Features>
    </Class>
    <Class><Code>EC000002</Code><Version>3</Version><Status>Published</Status>
      <GroupCode>EG000099</GroupCode>
      <Translations><Translation language="EN"><Description>Other</Description></Translation></Translations>
      <Features><Feature><FeatureCode>EF000002</FeatureCode><OrderNumber>1</OrderNumber></Feature></Features>
    </Class>
  </Classes>
</IXF>
"""


@pytest.fixture()
def ixf_file(tmp_path: Path) -> Path:
    path = tmp_path / "test-ixf.xml"
    path.write_text(IXF, encoding="utf-8")
    return path


def test_language_and_type_mapping() -> None:
    assert converter.language_code("nl-BE") == "nl"
    assert converter.language_code("EN") == "en"
    assert converter.language_code("de-DE") == "de"
    assert converter.attribute_type("Numeric") == "number"
    assert converter.attribute_type("Range") == "range"
    assert converter.attribute_type("Logical") == "boolean"
    assert converter.attribute_type("Alphanumeric") == "text"
    assert converter.attribute_type("Something-New") is None


def test_read_ixf_collects_vocabulary_and_selection(ixf_file: Path) -> None:
    selection = converter.read_ixf(ixf_file)
    assert selection.seen_classes == 2
    assert [entry.code for entry in selection.classes] == ["EC000001", "EC000002"]
    assert selection.units["EU570448"].symbol == "mm"
    assert selection.features["EF000001"].etim_type == "Numeric"
    assert selection.features["EF000001"].group_code == "EFG00007"
    assert selection.feature_groups == {"EFG00007": {"en": "Electrical"}}
    # The `Groups` section is the *class* grouping (EG…), not a feature group.
    assert selection.class_groups == {"EG000012": {"en": "Cables"}}
    assert selection.features["EF000001"].labels == {
        "en": "Nominal current",
        "nl": "Nominale stroom",
    }
    first = selection.classes[0]
    assert first.labels["en"] == "Busbar terminal"
    assert first.synonyms["en"] == ["Clamp"]
    assert first.status == "Published"
    assert [(f.code, f.unit_code, f.position) for f in first.features] == [
        ("EF000001", "EU570448", 1),
        ("EF000003", "EU570448", 2),
        ("EF000004", "", 3),
    ]


def test_read_ixf_honours_code_group_and_limit_filters(ixf_file: Path) -> None:
    by_code = converter.read_ixf(ixf_file, classes={"EC000002"})
    assert [entry.code for entry in by_code.classes] == ["EC000002"]
    by_group = converter.read_ixf(ixf_file, group="EG000012")
    assert [entry.code for entry in by_group.classes] == ["EC000001"]
    limited = converter.read_ixf(ixf_file, limit=1)
    assert [entry.code for entry in limited.classes] == ["EC000001"]


def test_model_document_maps_types_and_features(ixf_file: Path) -> None:
    selection = converter.read_ixf(ixf_file)
    model, report = converter.build_model_document(
        selection, node_type="etim_class", applies_to="product"
    )
    sections = model["sections"]
    node = sections["object_types"][0]
    assert node["name"] == "etim_class"
    assert node["key_attributes"] == ["code"]
    assert node["label_attribute"] == "description"
    assert node["taxonomy"] is True

    by_name = {item["name"]: item for item in sections["attribute_definitions"]}
    assert by_name["EF000001"]["type"] == "number"
    assert by_name["EF000002"]["type"] == "boolean"
    assert by_name["EF000003"]["type"] == "range"
    assert by_name["EF000004"]["type"] == "text"
    assert by_name["EF000001"]["label"] == {"en": "Nominal current", "nl": "Nominale stroom"}
    assert by_name["EF000001"]["applicable_types"] == ["product"]
    assert by_name["EF000001"]["group_ids"] == ["EFG00007"]
    assert by_name["code"]["is_key"] is True
    assert by_name["description"]["is_key"] is False
    # Units stay unset by default; the report lists what the selection needs.
    assert by_name["EF000001"]["unit"] is None
    assert set(report["units_needed"]) == {"EU570448"}
    assert report["units_needed"]["EU570448"]["symbol"] == "mm"
    assert report["type_histogram"] == {
        "Alphanumeric": 1,
        "Logical": 1,
        "Numeric": 1,
        "Range": 1,
    }


def test_model_document_sets_units_only_when_known(ixf_file: Path) -> None:
    selection = converter.read_ixf(ixf_file)
    with_unit, _ = converter.build_model_document(
        selection,
        node_type="etim_class",
        applies_to="product",
        unit_symbols={"mm"},
        with_units=True,
    )
    by_name = {item["name"]: item for item in with_unit["sections"]["attribute_definitions"]}
    assert by_name["EF000001"]["unit"] == "mm"
    assert by_name["EF000004"]["unit"] is None  # no unit in the class feature

    unknown, _ = converter.build_model_document(
        selection,
        node_type="etim_class",
        applies_to="product",
        unit_symbols={"m"},
        with_units=True,
    )
    by_name = {item["name"]: item for item in unknown["sections"]["attribute_definitions"]}
    assert by_name["EF000001"]["unit"] is None, "a symbol Meadow does not have is not applied"


def test_model_document_groups_features_as_attribute_groups(ixf_file: Path) -> None:
    selection = converter.read_ixf(ixf_file)
    model, report = converter.build_model_document(
        selection, node_type="etim_class", applies_to="product"
    )
    groups = model["sections"]["attribute_groups"]
    assert groups == [
        {
            "name": "EFG00007",
            "label": {"en": "Electrical"},
            "position": 1,
            "applicable_types": ["product"],
        }
    ]
    assert report["feature_groups"] == 1
    assert report["features_without_group"] == 0
    # The class key/description attributes stay ungrouped.
    by_name = {item["name"]: item for item in model["sections"]["attribute_definitions"]}
    assert by_name["code"]["group_ids"] == []
    assert by_name["EF000004"]["group_ids"] == ["EFG00007"]


def test_model_document_tolerates_features_without_a_group() -> None:
    """A feature without `FeatureGroupCode` must not break the model."""
    selection = converter.Selection(
        features={"EF000009": converter.IxfFeature(code="EF000009", etim_type="Numeric")},
        classes=[
            converter.IxfClass(
                code="EC000009",
                features=[converter.ClassFeature(code="EF000009", position=1)],
            )
        ],
    )
    model, report = converter.build_model_document(
        selection, node_type="etim_class", applies_to="product"
    )
    assert model["sections"]["attribute_groups"] == []
    assert report["features_without_group"] == 1
    by_name = {item["name"]: item for item in model["sections"]["attribute_definitions"]}
    assert by_name["EF000009"]["group_ids"] == []


def test_manifest_names_the_group_section(ixf_file: Path) -> None:
    selection = converter.read_ixf(ixf_file)
    _, report = converter.build_model_document(
        selection, node_type="etim_class", applies_to="product"
    )
    manifest = converter.bundle_manifest(
        selection,
        report,
        name="etim-test",
        node_type="etim_class",
        applies_to="product",
    )
    assert manifest["counts"]["feature_groups"] == 1
    model_doc = manifest["documents"][0]
    assert model_doc["sections"] == [
        "object_types",
        "classification_standards",
        "attribute_groups",
        "attribute_definitions",
    ]
    # The model document carries the standard it was built from, so the registry
    # entry travels with the package (`tests/test_standard_bundle.py`).
    assert model_doc["sections"].count("classification_standards") == 1


def test_feature_rows_are_required_and_ordered(ixf_file: Path) -> None:
    selection = converter.read_ixf(ixf_file)
    document = converter.build_features_document(selection)
    entries = document["sections"]["category_attributes"]
    assert [entry["class"] for entry in entries] == ["EC000001", "EC000002"]
    assert entries[0]["class_key"] == "code"
    assert entries[0]["features"] == [
        {"attribute": "EF000001", "required": True, "position": 1},
        {"attribute": "EF000003", "required": True, "position": 2},
        {"attribute": "EF000004", "required": True, "position": 3},
    ]


def test_classes_document_uses_the_english_description(ixf_file: Path) -> None:
    selection = converter.read_ixf(ixf_file)
    document = converter.build_classes_document(selection, node_type="etim_class")
    assert document["objects"] == [
        {
            "type": "etim_class",
            "status": "draft",
            "attributes": {"code": "EC000001", "description": "Busbar terminal"},
        },
        {
            "type": "etim_class",
            "status": "draft",
            "attributes": {"code": "EC000002", "description": "Other"},
        },
    ]
    assert document["references"] == []
