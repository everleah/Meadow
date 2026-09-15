"""Tests for the automotive demo-data generator (`scripts/demo_data.py`).

The generator is a planning artefact of `plan/detail/demodaten-generator.md`
(stage 1+2: deterministic file generation). These tests pin the invariants the
import path relies on: determinism, referential integrity inside every chunk,
declared model entries for every generated value, and the report counts.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
RESOURCES = Path(__file__).resolve().parents[1] / "resources" / "standards" / "etim-10.0"

#: Attribute names that are conventions instead of `attribute_definition` rows
#: (every object type labels itself through `name`).
CONVENTION_ATTRIBUTES = {"name"}

#: Workflow statuses known to the core (`api/search_schema.rs`).
KNOWN_STATUSES = {"draft", "in_review", "approved", "published", "archived"}


def _load_script(name: str):
    """Loads a script module by file name (the scripts are not a package)."""
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # Registered first so dataclasses can resolve the module of the classes.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


demo = _load_script("demo_data")


@pytest.fixture(scope="module")
def catalog() -> dict:
    return demo.load_catalog()


@pytest.fixture(scope="module")
def dataset(catalog: dict):
    return demo.generate_dataset(catalog, "tiny", seed=7)


def test_catalogue_is_consistent(catalog: dict) -> None:
    assert demo.validate_catalog(catalog) == []


def test_catalogue_reports_unknown_references() -> None:
    broken = {
        "channels": [],
        "enum_sets": [
            {"name": "material", "label": {}, "options": [{"code": "steel", "labels": {}}]}
        ],
        "object_types": [{"name": "product", "label": {}, "key_attributes": []}],
        "attributes": [
            {
                "name": "width_mm",
                "label": {},
                "type": "number",
                "applicable_types": ["product"],
            }
        ],
        "attribute_groups": [{"name": "technical", "label": {}, "position": 1}],
        "relation_definitions": [],
        "business_rules": [],
        "export_profiles": [],
        "categories": [],
        "part_groups": [{"code": "PG-X", "name_de": "X", "name_en": "X", "parent": "PG-MISSING"}],
        "part_families": [
            {
                "code": "fam",
                "name_de": "F",
                "name_en": "F",
                "group": "PG-MISSING",
                "positions": [],
                "materials": ["unobtainium"],
                "quality_tiers": ["oe"],
                "weight_kg": [1, 2],
                "price": [1, 2],
                "packaging": "piece",
                "hazardous": False,
                "dims": ["unknown_dimension"],
            }
        ],
        "class_features": [],
        "makes": [],
        "collections": [],
        "saved_searches": [],
    }
    problems = demo.validate_catalog(broken)
    joined = " | ".join(problems)
    assert "unknown parent" in joined
    assert "unknown assembly group" in joined
    assert "unknown material" in joined
    assert "unknown dimension" in joined


def test_generation_is_deterministic(catalog: dict) -> None:
    first = demo.generate_dataset(catalog, "tiny", seed=11)
    second = demo.generate_dataset(catalog, "tiny", seed=11)
    third = demo.generate_dataset(catalog, "tiny", seed=12)
    dump = lambda data: json.dumps(  # noqa: E731 - local helper
        {
            "objects": [node.to_mef() for node in data.nodes],
            "references": [reference.to_mef() for reference in data.references],
        },
        sort_keys=True,
    )
    assert dump(first) == dump(second)
    assert dump(first) != dump(third)


def test_profiles_scale(catalog: dict) -> None:
    counts = {
        name: len(demo.generate_dataset(catalog, name, seed=3).nodes) for name in ("tiny", "demo")
    }
    assert counts["tiny"] < counts["demo"]


def test_object_keys_and_business_keys_are_unique(dataset) -> None:
    keys = [node.key for node in dataset.nodes]
    assert len(keys) == len(set(keys))
    per_type: dict[str, set[str]] = {}
    for node in dataset.nodes:
        if not node.business_key:
            continue
        bucket = per_type.setdefault(node.type, set())
        assert node.business_key not in bucket, f"duplicate key {node.business_key}"
        bucket.add(node.business_key)


def test_references_point_at_generated_objects(dataset) -> None:
    keys = {node.key for node in dataset.nodes}
    for reference in dataset.references:
        assert reference.source in keys
        assert reference.target in keys


def test_chunks_are_closed(catalog: dict) -> None:
    data = demo.generate_dataset(catalog, "tiny", seed=5)
    documents = demo.chunk_dataset(data.nodes, data.references, 3)
    assert len(documents) == 3
    for document in documents:
        keys = {entry["id"] for entry in document["objects"]}
        for reference in document["references"]:
            assert reference["in"] in keys
            assert reference["out"] in keys
        assert document["format_version"] == "1.0"
        assert document["source"] == demo.SOURCE


def test_chunks_cover_everything(catalog: dict) -> None:
    data = demo.generate_dataset(catalog, "tiny", seed=5)
    documents = demo.chunk_dataset(data.nodes, data.references, 3)
    imported = {entry["id"] for document in documents for entry in document["objects"]}
    assert imported == {node.key for node in data.nodes}
    references = {
        (reference["in"], reference["out"], reference["type"])
        for document in documents
        for reference in document["references"]
    }
    assert len(references) == len(data.references)


def test_reference_types_are_declared(catalog: dict, dataset) -> None:
    declared = {relation["name"] for relation in catalog["relation_definitions"]} | {
        "depends_on",
        "category_member",
        "collection_member",
        "related",
    }
    used = {reference.type for reference in dataset.references}
    assert used <= declared, f"undeclared relation types: {sorted(used - declared)}"


def test_edge_attributes_are_declared(catalog: dict, dataset) -> None:
    """Every edge attribute is covered by its definition's template.

    The core validates edge attributes against `relation_definition.attributes`
    only when that template is **non-empty** (`validate_edge_attributes`); the
    built-in `category_member` carries none, so free edge metadata such as
    `position` is allowed there (documented in `plan/detail/kategorisierung.md`).
    """
    declared = {
        relation["name"]: set(relation.get("attributes", []))
        for relation in catalog["relation_definitions"]
    }
    for reference in dataset.references:
        template = declared.get(reference.type)
        if not template:
            continue
        unknown = set(reference.attributes) - template
        assert not unknown, f"{reference.type}: undeclared edge attributes {sorted(unknown)}"


def test_attribute_values_match_the_model(catalog: dict, dataset) -> None:
    definitions = {attribute["name"]: attribute for attribute in catalog["attributes"]}
    enum_options = {
        enum_set["name"]: {option["code"] for option in enum_set["options"]}
        for enum_set in catalog["enum_sets"]
    }
    for node in dataset.nodes:
        for name, value in node.attributes.items():
            if name in CONVENTION_ATTRIBUTES:
                continue
            definition = definitions.get(name)
            assert definition is not None, f"{node.type}: undeclared attribute {name}"
            if definition.get("multilingual"):
                assert isinstance(value, dict), f"{name} on {node.type} must be a language map"
                continue
            if definition["type"] == "enum":
                assert value in enum_options[definition["enum_set"]], f"{name}={value}"
            elif definition["type"] == "range":
                assert set(value) == {"from", "to"}
            elif definition["type"] == "number":
                assert isinstance(value, (int, float)) and not isinstance(value, bool)
            elif definition["type"] == "boolean":
                assert isinstance(value, bool)
            elif definition["type"] in ("text", "url", "date", "reference"):
                assert isinstance(value, str)


def test_multilingual_attributes_are_language_maps(catalog: dict, dataset) -> None:
    definitions = {attribute["name"]: attribute for attribute in catalog["attributes"]}
    for node in dataset.nodes:
        for name, value in node.attributes.items():
            definition = definitions.get(name)
            if definition and definition.get("multilingual"):
                assert set(value) == {"de", "en"}, f"{name} on {node.type}: {value}"


def test_gtin_check_digits_are_valid(dataset) -> None:
    checked = 0
    for node in dataset.nodes:
        gtin = node.attributes.get("gtin")
        if not gtin:
            continue
        assert len(gtin) == 13 and gtin.isdigit()
        assert demo.check_digit_gtin12(gtin[:12]) == gtin[12]
        checked += 1
    assert checked


def test_statuses_are_known(dataset) -> None:
    assert {node.status for node in dataset.nodes} <= KNOWN_STATUSES


def test_model_declares_every_generated_type(catalog: dict, dataset) -> None:
    model = demo.build_model(catalog)
    declared = {entry["name"] for entry in model["sections"]["object_types"]}
    declared |= {"product", "variant", "category", "asset", "root", "collection", "saved_search"}
    assert {node.type for node in dataset.nodes} <= declared
    assert model["format"] == "mcf"
    assert model["source"] == demo.SOURCE
    assert model["created_at"] == demo.CREATED_AT


def test_model_carries_the_category_role_types(catalog: dict) -> None:
    """The additional category dimensions travel with the model.

    The MCF section `relation_definitions` carries `role` since 2026-09-15; the
    generator therefore keeps them in `model.mcf.json` instead of creating them
    through `POST /relation-definitions` (`plan/detail/demodaten-generator.md` §3.2).
    """
    model = demo.build_model(catalog)
    roles = {entry["name"]: entry["role"] for entry in model["sections"]["relation_definitions"]}
    assert roles["part_group_member"] == "category"
    assert roles["etim_class_member"] == "category"
    assert roles["fits_vehicle"] == "link"
    taxonomy_types = {
        entry["name"] for entry in model["sections"]["object_types"] if entry["taxonomy"]
    }
    for entry in model["sections"]["relation_definitions"]:
        if entry["role"] == "category":
            assert set(entry["target_types"]) <= taxonomy_types


def test_class_features_target_own_taxonomy(catalog: dict) -> None:
    document = demo.build_class_features(catalog)
    entries = document["sections"]["category_attributes"]
    group_codes = {group["code"] for group in catalog["part_groups"]}
    attributes = {attribute["name"] for attribute in catalog["attributes"]}
    assert entries
    for entry in entries:
        assert entry["class"] in group_codes
        assert entry["class_key"] == "group_code"
        for feature in entry["features"]:
            assert feature["attribute"] in attributes


def test_standard_bindings_reference_installed_features(catalog: dict) -> None:
    """Every `standard_codes` binding must be a real ETIM feature of a used class."""
    model_path = RESOURCES / "model.mcf.json"
    features_path = RESOURCES / "features.mcf.json"
    if not model_path.exists() or not features_path.exists():
        pytest.skip("ETIM standard bundle not present (resources/ is local only)")
    model = json.loads(model_path.read_text(encoding="utf-8"))["sections"]
    features = json.loads(features_path.read_text(encoding="utf-8"))["sections"]
    known_features = {
        attribute["name"]
        for attribute in model["attribute_definitions"]
        if attribute["name"].startswith("EF")
    }
    class_features = {
        entry["class"]: {feature["attribute"] for feature in entry["features"]}
        for entry in features["category_attributes"]
    }
    used_classes = {
        family["etim_class"] for family in catalog["part_families"] if family.get("etim_class")
    }
    assert used_classes <= set(class_features), "demo uses classes the bundle does not carry"
    for attribute in catalog["attributes"]:
        code = (attribute.get("standard_codes") or {}).get("etim")
        if not code:
            continue
        assert code in known_features, f"{attribute['name']}: unknown ETIM feature {code}"
        assert any(code in class_features[class_code] for class_code in used_classes), (
            f"{attribute['name']}: {code} is not a feature of any used class"
        )


def test_report_matches_dataset(dataset) -> None:
    report = dataset.report
    assert report["objects"] == len(dataset.nodes)
    assert report["references"] == len(dataset.references)
    assert sum(report["by_type"].values()) == report["objects"]
    assert sum(report["by_status"].values()) == report["objects"]
    assert sum(report["by_relation"].values()) == report["references"]


def test_extras_reference_generated_objects(dataset) -> None:
    keys = {node.key for node in dataset.nodes}
    product_keys = {node.key for node in dataset.nodes if node.type == "product"}
    extras = dataset.extras
    assert extras["activate_rules"]
    for release in extras["releases"]:
        assert release["object"] in product_keys
        assert release["status"] == "published"
    assert {collection["name"] for collection in extras["collections"]} == {
        collection["name"] for collection in dataset.extras["collections"]
    }
    for collection in extras["collections"]:
        assert collection["members"]
        assert set(collection["members"]) <= product_keys
        assert collection["refresh_schedule"].count(" ") == 4
    for membership in extras["standard_memberships"]:
        assert membership["object"] in keys
        assert membership["key_attribute"] == "code"
        assert membership["relation"] == "etim_class_member"
    for search in extras["saved_searches"]:
        assert search["query"]["where"]["conditions"]


def test_extras_releases_only_published_products(dataset) -> None:
    status = {node.key: node.status for node in dataset.nodes}
    for release in dataset.extras["releases"]:
        assert status[release["object"]] == "published"


def test_write_outputs_creates_the_expected_files(catalog: dict, tmp_path: Path) -> None:
    data = demo.generate_dataset(catalog, "tiny", seed=2)
    written = demo.write_outputs(data, catalog, tmp_path, chunks=2)
    assert set(written) == {
        "model.mcf.json",
        "class-features.mcf.json",
        "data-00.mef.json",
        "data-01.mef.json",
        "extras.json",
        "report.json",
    }
    for path in written.values():
        assert path.exists() and path.stat().st_size > 0
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["profile"] == "tiny"
    document = json.loads((tmp_path / "data-00.mef.json").read_text(encoding="utf-8"))
    assert document["objects"] and isinstance(document["references"], list)


def test_cli_check_accepts_the_curated_catalogue() -> None:
    assert demo.main(["--check"]) == 0


def test_cli_writes_into_the_output_directory(tmp_path: Path) -> None:
    exit_code = demo.main(
        ["--profile", "tiny", "--seed", "1", "--chunks", "1", "--out-dir", str(tmp_path)]
    )
    assert exit_code == 0
    assert (tmp_path / "model.mcf.json").exists()
    assert (tmp_path / "data-00.mef.json").exists()
