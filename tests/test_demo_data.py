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

import httpx
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
    """The full `demo` profile: only it exercises every relation type.

    The `tiny` profile (40 products) covers just a slice of the part families,
    so e.g. `alternative_to` never occurs there — an undeclared edge attribute
    slipped through until the profile-level dataset was used.
    """
    return demo.generate_dataset(catalog, "demo", seed=7)


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


def test_catalogue_rejects_a_five_field_refresh_interval(catalog: dict) -> None:
    """The core schedules the refresh as a cron job with **six** fields.

    `tokio-cron-scheduler` wants seconds first; a five-field expression is stored
    happily and then never fires (the scheduler only logs `ParseSchedule` every
    minute). The catalogue check catches it before the import does.
    """
    broken = {**catalog, "collections": [dict(entry) for entry in catalog["collections"]]}
    broken["collections"][0]["refresh_interval"] = "0 3 * * *"
    problems = demo.validate_catalog(broken)
    assert any("6 cron fields" in problem for problem in problems), problems

    # The curated catalogue itself carries the correct form.
    intervals = [entry.get("refresh_interval", "") for entry in catalog["collections"]]
    assert intervals and all(len(interval.split()) == 6 for interval in intervals)


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
    # Every declared demo relation type must actually occur — a type that never
    # occurs hides undeclared edge attributes from the check below. The two
    # exceptions are deliberate: `related`/`collection_member` are core types the
    # demo does not use, and the ETIM class membership is applied through the
    # extras step (standard memberships), not through the MEF references.
    assert used == declared - {"collection_member", "related", "etim_class_member"}


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


def test_model_declares_the_article_number_as_a_business_key(catalog: dict) -> None:
    """`sku` must be a real business key of product/variant.

    The core enforces uniqueness through `object_type.key_attributes` (not through
    the attribute's `is_key` flag), so without these entries two parallel writers
    could create two products with the same article number —
    `plan/detail/demodaten-generator.md` §4.4.
    """
    model = demo.build_model(catalog)
    keys = {
        entry["name"]: entry.get("key_attributes") or []
        for entry in model["sections"]["object_types"]
    }
    assert keys.get("product") == ["sku"]
    assert keys.get("variant") == ["sku"]


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
    assert len(extras["collections"]) == 5
    for collection in extras["collections"]:
        # A collection is the materialised form of a saved-search query: the
        # query selects the members, the importer resolves node keys inside it.
        assert collection["query"]["where"]["conditions"]
        # The core schedules the refresh as a cron job: six fields, seconds first
        # (`tokio-cron-scheduler`) — a five-field expression never fires.
        assert len(collection["refresh_interval"].split()) == 6
    for membership in extras["standard_memberships"]:
        assert membership["object"] in keys
        assert membership["key_attribute"] == "code"
        assert membership["relation"] == "etim_class_member"
    for search in extras["saved_searches"]:
        assert search["query"]["where"]["conditions"]


def test_collection_queries_resolve_to_generated_nodes(catalog: dict, dataset) -> None:
    """Every node key used in a collection query exists in the dataset."""
    keys = {node.key for node in dataset.nodes}
    group_keys = {node.key for node in dataset.nodes if node.type == "part_group"}
    assert group_keys
    referenced = [
        reference["out"]
        for collection in dataset.extras["collections"]
        for reference in collection["query"].get("references", [])
    ]
    assert referenced, "at least one collection filters over a relation"
    assert set(referenced) <= keys
    assert set(referenced) <= group_keys


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
        "masters.mef.json",
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


def test_master_document_pre_creates_the_shared_objects(catalog: dict) -> None:
    """Chunks repeated objects are created once, before the parallel chunks.

    Two chunks creating the same object at the same time either collide (`409`)
    or leave a duplicate row; importing `masters.mef.json` first removes the
    race, so the chunks only update those objects.
    """
    data = demo.generate_dataset(catalog, "demo", seed=5)
    documents = demo.chunk_dataset(data.nodes, data.references, 3)
    masters = demo.build_master_document(data.nodes, data.references, 3)
    assert masters is not None
    assert masters["references"] == []

    occurrences: dict[str, int] = {}
    for document in documents:
        for entry in document["objects"]:
            occurrences[entry["id"]] = occurrences.get(entry["id"], 0) + 1
    shared = {key for key, count in occurrences.items() if count > 1}
    assert shared, "the demo dataset must repeat objects across chunks"
    assert {entry["id"] for entry in masters["objects"]} == shared
    assert len(masters["objects"]) == len(shared)

    # A single chunk never needs the pass, and a master entry must be a
    # complete object so the core can create it.
    assert demo.build_master_document(data.nodes, data.references, 1) is None
    for entry in masters["objects"]:
        assert set(entry) == {"id", "type", "status", "attributes"}


def test_chunks_keep_every_assembly_group_together(catalog: dict) -> None:
    """A part family, its variants and its OEM numbers live in one chunk.

    Only the shared taxonomies (`family_group == ""`) are repeated, which is why
    the master pass stays small (220 of 5 950 objects on the `demo` profile) and
    the chunks resolve their family-local relations on their own.
    """
    data = demo.generate_dataset(catalog, "demo", seed=5)
    documents = demo.chunk_dataset(data.nodes, data.references, 4)
    chunk_of = {
        entry["id"]: index
        for index, document in enumerate(documents)
        for entry in document["objects"]
    }
    groups: dict[str, set[int]] = {}
    for node in data.nodes:
        if node.family_group:
            groups.setdefault(node.family_group, set()).add(chunk_of[node.key])
    assert groups, "the demo dataset must have assembly groups"
    spread = {name: chunks for name, chunks in groups.items() if len(chunks) > 1}
    assert not spread, f"assembly groups split across chunks: {sorted(spread)[:3]}"

    masters = demo.build_master_document(data.nodes, data.references, 4)
    assert masters is not None
    assert len(masters["objects"]) < 0.1 * len(data.nodes)
    shared_groups = {entry["id"] for entry in masters["objects"]}
    assert shared_groups <= {node.key for node in data.nodes if not node.family_group}


def test_retry_document_collects_only_failed_entries(dataset) -> None:
    """The retry pass rebuilds a small MEF document from a run report.

    Parallel chunks share "hot" objects; a concurrent update can fail with a
    write conflict (HTTP `409`, the core maps it), which the worker reports per
    entry. A reference report mixes **record ids** (the endpoint that resolved)
    with **MEF ids** (`demo-cat-…`, the one that did not), so the document is
    only complete when the record ids are mapped back to dataset keys.
    """
    node = dataset.nodes[0]
    reference = dataset.references[0]
    other = dataset.nodes[-1]
    runs = {
        "run-1": {
            "result": {
                "results": [
                    {"id": node.key, "status": "failed"},
                    {"id": "object:01M2JZ", "status": "created"},
                ],
                "references": [
                    {
                        # The `in` side resolved, the `out` side did not.
                        "in": "object:01M2JZ",
                        "out": reference.target,
                        "type": reference.type,
                        "status": "failed",
                    },
                    {"in": "a", "out": "b", "type": "unknown_type", "status": "failed"},
                ],
            }
        }
    }
    record_ids = {reference.source: "object:01M2JZ", other.key: "object:01M2JY"}
    document = demo.build_retry_document(runs, dataset, record_ids)
    assert document is not None
    keys = {entry["id"] for entry in document["objects"]}
    assert node.key in keys
    assert reference.source in keys and reference.target in keys
    assert len(document["references"]) == 1
    assert document["references"][0]["type"] == reference.type
    assert document["source"] == demo.SOURCE

    assert demo.build_retry_document({"run-1": {"result": {"references": []}}}, dataset, {}) is None


def test_cleanup_deletes_every_object_of_the_source() -> None:
    """The cleanup lists the instance per type instead of the dataset keys.

    A duplicate row left behind by an earlier parallel import carries the same
    business key, so a key lookup would only ever find one of the two and the
    other would survive every cleanup. Page two of the list must be requested
    too, objects of other sources must stay untouched, and the second phase must
    pick up a row whose parent cascade never reached it. The deletion itself goes
    through `POST /objects/batch-delete` (`CLEANUP_BATCH` ids per call).
    """
    page = demo.LIST_PAGE
    pages = {
        "product": [
            [{"id": "object:1", "source": demo.SOURCE, "attributes": {"sku": "A"}}] * 2
            + [{"id": "object:foreign", "source": "other", "attributes": {"sku": "X"}}]
        ],
        "vehicle_make": [
            [
                {"id": f"object:make-{index}", "source": demo.SOURCE, "attributes": {}}
                for index in range(page)
            ],
            [{"id": "object:make-last", "source": demo.SOURCE, "attributes": {}}],
        ],
        # Cascade would have taken it; here it is an orphan from a lost race.
        "vehicle_model": [[{"id": "object:orphan", "source": demo.SOURCE, "attributes": {}}]],
    }
    batches: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            payload = json.loads(request.content)
            batches.append(payload)
            ids = payload["ids"]
            # The instance reports what it deleted, what the cascade took below
            # the roots and which ids were gone already.
            return httpx.Response(
                200,
                json={
                    "deleted": len(ids),
                    "cascaded": 0,
                    "missing": ["object:gone"] if "object:1" in ids else [],
                },
            )
        requested = request.url.params.get("type")
        start = int(request.url.params.get("start") or 0)
        rows = pages.get(requested or "", [[]])
        index = start // page
        return httpx.Response(200, json=rows[index] if index < len(rows) else [])

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://core")
    log = demo.ImportLog()
    count = demo.cleanup_objects(client, {}, log)

    ids = [object_id for batch in batches for object_id in batch["ids"]]
    # Two products, the full first make page plus the last one, and the orphan.
    assert count == 1_004
    assert len(batches) >= 2  # batched, not one request per object
    assert all(len(batch["ids"]) <= demo.CLEANUP_BATCH for batch in batches)
    assert "object:1" in ids and "object:make-last" in ids  # the second page too
    assert "object:orphan" in ids  # the orphan phase found it
    assert "object:foreign" not in ids
    assert any(step == "cleanup" for step, _ in log.steps)
    details = " ".join(detail for step, detail in log.steps if step == "cleanup")
    assert "already gone" in details


def test_cleanup_removes_the_generated_collections_and_searches() -> None:
    """Extras get deleted through their `source`, not through their names.

    A saved search of an older catalogue revision may have been renamed (or never
    matched a name lookup at all), so the demo tags every collection and search
    with `source` + `source_id` and cleans up by that filter. Collections go
    first: the core rejects deleting a saved search a collection still references.
    """
    listed: list[str] = []
    deleted: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":
            deleted.append(request.url.path)
            return httpx.Response(204)
        listed.append(request.url.path + "?source=" + (request.url.params.get("source") or ""))
        if request.url.path == "/collections":
            return httpx.Response(200, json=[{"id": "object:c1"}, {"id": "object:c2"}])
        return httpx.Response(
            200,
            json=[{"id": "object:s1"}, {"id": "object:s2"}, {"id": "object:s3"}],
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://core")
    log = demo.ImportLog()
    count = demo.cleanup_extras(client, {}, log)

    assert count == 5
    assert listed == [
        f"/collections?source={demo.SOURCE}",
        f"/saved-searches?source={demo.SOURCE}",
    ]
    # Both collections first, then the searches (the core 409s otherwise).
    assert deleted[:2] == ["/collections/object:c1", "/collections/object:c2"]
    assert deleted[2:] == [
        "/saved-searches/object:s1",
        "/saved-searches/object:s2",
        "/saved-searches/object:s3",
    ]
    assert any(step == "cleanup extras" for step, _ in log.steps)


def test_apply_extras_tags_collections_and_searches_with_the_source() -> None:
    """Every created/updated artefact carries `source` + `source_id`.

    The collection filter search is created from the collection name, so it gets
    its own `source_id`; that is how `--cleanup` recognises it as demo-owned.
    """
    payloads: list[tuple[str, str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            if request.url.path == "/rules":
                return httpx.Response(200, json=[])
            return httpx.Response(200, json=[])
        payloads.append(
            (
                request.method,
                request.url.path,
                json.loads(request.content) if request.content else {},
            )
        )
        if request.url.path == "/collections":
            return httpx.Response(201, json={"id": "object:c1"})
        if request.url.path == "/collections/object:c1/refresh":
            return httpx.Response(200, json={"count": 3})
        return httpx.Response(201, json={"id": "object:s1"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://core")
    extras = {
        "source": demo.SOURCE,
        "releases": [],
        "saved_searches": [{"name": "Offene Gewichte", "query": {"where": {"conditions": []}}}],
        "collections": [
            {
                "name": "Bremsen-Sortiment",
                "query": {"where": {"conditions": []}},
                "refresh_interval": "",
            }
        ],
        "standard_memberships": [],
        "activate_rules": [],
    }
    demo.apply_extras(client, {}, extras, {}, demo.ImportLog())

    searches = {
        body["name"]: body
        for method, path, body in payloads
        if method == "POST" and path == "/saved-searches"
    }
    assert set(searches) == {"Offene Gewichte", "Bremsen-Sortiment (Auswahl)"}
    for name, body in searches.items():
        assert body["source"] == demo.SOURCE
        assert body["source_id"] == name
    collections = {
        body["name"]: body
        for method, path, body in payloads
        if method == "POST" and path == "/collections"
    }
    assert collections["Bremsen-Sortiment"]["source"] == demo.SOURCE
    assert collections["Bremsen-Sortiment"]["source_id"] == "Bremsen-Sortiment"


def test_every_catalogue_rule_runs_in_the_sandbox(catalog: dict) -> None:
    """Every shipped rule must *execute*, not only compile.

    The sandbox raised `NameError` at runtime for helpers it did not expose
    (`enumerate` in the GTIN check, `getattr` in the publish guard) while
    `compile_rule` accepted the scripts — so two of the six demo rules silently
    never ran (found 2026-09-16). This test executes each script against a
    published product and fails on any exception.
    """
    from meadow.rules import Context, execute_rule

    obj = {
        "id": "object:01M2MGP8PAQ2154PMCPE7MWS37",
        "type": "product",
        "status": "published",
        "attributes": {
            "sku": "demo-1",
            "name": "Demo",
            "net_price": 100.0,
            "list_price": 120.0,
            "gtin": "4006381333931",
            "warranty_months": 24,
        },
    }
    rules = catalog["business_rules"]
    assert len(rules) >= 6
    for rule in rules:
        context = Context(
            rule_type=str(rule["type"]),
            object_type="product",
            mode="save",
            actor="user:admin",
            missing_features=("EF000123",),
        )
        result = execute_rule(str(rule["script"]), obj, context)
        assert result.errors == [], f"{rule['name']} reported errors: {result.errors}"

    # The publish guard blocks a user who publishes an incomplete product, but
    # lets the automated import through (the demo data is published deliberately).
    guard = next(rule for rule in rules if rule["name"] == "guard_publish_features")
    user = execute_rule(
        str(guard["script"]),
        obj,
        Context(
            rule_type="workflow_guard",
            object_type="product",
            mode="save",
            actor="user:admin",
            missing_features=("EF000123",),
        ),
    )
    assert user.allowed is False
    assert "EF000123" in (user.deny_reason or "")
    system = execute_rule(
        str(guard["script"]),
        obj,
        Context(
            rule_type="workflow_guard",
            object_type="product",
            mode="save",
            actor="system",
            missing_features=("EF000123",),
        ),
    )
    assert system.allowed is True


def test_cli_check_accepts_the_curated_catalogue() -> None:
    assert demo.main(["--check"]) == 0


def test_cli_writes_into_the_output_directory(tmp_path: Path) -> None:
    exit_code = demo.main(
        ["--profile", "tiny", "--seed", "1", "--chunks", "1", "--out-dir", str(tmp_path)]
    )
    assert exit_code == 0
    assert (tmp_path / "model.mcf.json").exists()
    assert (tmp_path / "data-00.mef.json").exists()
