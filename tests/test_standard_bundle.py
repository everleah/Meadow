"""Tests for the standard-bundle tooling (bundle manifest + installer).

A standard bundle is the package that carries a classification standard's model
and data (`plan/detail/bmecat-export.md` §11.9). It is deliberately **not** seeded
system data: a system runs without it and installing it is one command. The tests
cover the pure parts (manifest shape, unit comparison, manifest validation).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import httpx

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def load(name: str):
    """Loads a script module by file name (the scripts are not a package)."""
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


generator = load("etim_ixf_to_mef")
installer = load("install_standard_bundle")


def test_manifest_describes_the_documents_in_install_order() -> None:
    selection = generator.Selection(
        classes=[
            generator.IxfClass(
                code="EC000001",
                features=[
                    generator.ClassFeature(code="EF000001", unit_code="EU1", position=1),
                    generator.ClassFeature(code="EF000002", position=2),
                ],
            )
        ],
        features={
            "EF000001": generator.IxfFeature(code="EF000001", etim_type="Numeric"),
            "EF000002": generator.IxfFeature(code="EF000002", etim_type="Logical"),
        },
        publication={"name": "TEST-EXPORT", "number": "10", "date": "2024-12-05"},
    )
    report = {
        "features": 2,
        "feature_groups": 1,
        "units_needed": {"EU1": {"symbol": "mm", "en": "Millimetre"}},
    }
    manifest = generator.bundle_manifest(
        selection, report, name="etim-10.0", node_type="etim_class", applies_to="product"
    )

    assert manifest["format"] == generator.BUNDLE_FORMAT
    assert manifest["name"] == "etim-10.0"
    assert manifest["standard"] == "ETIM"
    assert manifest["standard_version"] == "10.0", "taken from the IXF publication"
    assert manifest["source"] == "TEST-EXPORT"
    assert manifest["counts"] == {
        "classes": 1,
        "features": 2,
        "feature_groups": 1,
        "class_features": 2,
    }
    assert manifest["units_needed"], "the installer checks the target's unit catalog"
    assert [step["file"] for step in manifest["documents"]] == [
        "model.mcf.json",
        "classes.mef.json",
        "features.mcf.json",
    ]
    assert [step["kind"] for step in manifest["documents"]] == ["mcf", "mef", "mcf"]
    assert manifest["documents"][0]["sections"] == [
        "object_types",
        "classification_standards",
        "attribute_groups",
        "attribute_definitions",
    ]
    assert manifest["documents"][2]["sections"] == ["category_attributes"]


def test_the_converter_declares_the_standard_it_converted() -> None:
    """The model document says which standard in which version it carries."""
    selection = generator.Selection(
        classes=[], features={}, publication={"name": "TEST-EXPORT", "number": "10"}
    )

    assert generator.standard_version(selection) == "10.0"
    assert generator.standard_entries(selection, node_type="etim_class") == [
        {
            "code": generator.STANDARD_CODE,
            "version": "10.0",
            "name": generator.STANDARD_NAME,
            "source": "TEST-EXPORT",
            "node_type": "etim_class",
        }
    ]
    # Without a release number in the IXF header no version is invented.
    empty = generator.Selection(classes=[], features={}, publication={})
    assert generator.standard_entries(empty, node_type="etim_class") == []


def test_the_installer_registers_the_manifest_as_a_registry_entry() -> None:
    document = installer.standard_document(
        {
            "standard": "ETIM",
            "standard_version": "10.0",
            "source": "TEST-EXPORT",
            "node_type": "etim_class",
        }
    )

    assert document is not None
    assert document["sections"]["classification_standards"] == [
        {
            "code": "etim",
            "version": "10.0",
            "name": "ETIM",
            "source": "TEST-EXPORT",
            "node_type": "etim_class",
        }
    ]
    # A version is never invented: the export then uses its explicit profile field.
    assert installer.standard_document({"standard": "ETIM", "standard_version": ""}) is None
    assert installer.standard_document({"standard": "", "standard_version": "10.0"}) is None


def test_manifest_and_document_must_agree_on_the_standard() -> None:
    manifest = {"standard": "ETIM", "standard_version": "10.0"}
    matching = {"sections": {"classification_standards": [{"version": "10.0"}]}}
    assert installer.standard_mismatch(manifest, matching) is None

    # The model document (it declares `object_types`) must carry the entry.
    model = {"format": "mcf", "sections": {"object_types": []}}
    assert "no `classification_standards`" in (installer.standard_mismatch(manifest, model) or "")
    # A feature document legitimately carries only `category_attributes`.
    features = {"format": "mcf", "sections": {"category_attributes": []}}
    assert installer.standard_mismatch(manifest, features) is None
    assert installer.standard_mismatch(
        manifest, {"sections": {}}, ["object_types", "attribute_definitions"]
    ), "the manifest's document order counts as the model too"

    other = {"sections": {"classification_standards": [{"version": "11.0"}]}}
    message = installer.standard_mismatch(manifest, other) or ""
    assert "manifest says ETIM 10.0" in message and "11.0" in message

    # Nothing to compare: a bundle without a version, or a document without sections.
    assert installer.standard_mismatch({"standard_version": ""}, model) is None
    assert installer.standard_mismatch(manifest, {"objects": []}) is None


def test_missing_units_compares_the_bundle_with_the_target() -> None:
    manifest = {
        "units_needed": {
            "EU1": {"symbol": "mm", "en": "Millimetre", "used_by": 85},
            "EU2": {"symbol": "VA", "en": "Voltampere", "used_by": 2},
            "EU3": {"symbol": "", "en": "no symbol"},
        }
    }
    assert installer.missing_units(manifest, {"mm"}) == [
        {"code": "EU2", "symbol": "VA", "en": "Voltampere", "used_by": 2}
    ]
    assert installer.missing_units(manifest, {"mm", "VA"}) == []
    assert installer.missing_units({}, {"mm"}) == []


class FakeClient:
    """Records the requests the installer makes (no core, no worker needed)."""

    def __init__(self, existing: list[dict] | None = None) -> None:
        self.posts: list[tuple[str, dict]] = []
        self.existing = existing or []
        self.job_run_id = "job_run:1"

    def _response(self, path: str, payload) -> httpx.Response:
        return httpx.Response(
            200, json=payload, request=httpx.Request("POST", f"http://test{path}")
        )

    def post(self, path: str, *, params=None, headers=None, json=None) -> httpx.Response:
        self.posts.append((path, dict(params or {})))
        self.job_run_id = "job_run:1"
        return self._response(path, {"job_run_id": self.job_run_id, "sections": {}, "errors": []})

    def get(self, path: str, *, params=None, headers=None) -> httpx.Response:
        # `existing_keys` pages through `GET /objects`; one page is enough here.
        rows = self.existing if int((params or {}).get("start", 0)) == 0 else []
        return self._response(path, rows)

    def path_params(self, path: str) -> list[dict]:
        return [params for called, params in self.posts if called == path]


def write_bundle(root: Path) -> Path:
    """A minimal bundle: model, one class, one class-feature section."""
    bundle = root / "etim-10.0"
    bundle.mkdir()
    manifest = {
        "format": installer.BUNDLE_FORMAT,
        "name": "etim-10.0",
        "standard": "ETIM",
        "standard_version": "10.0",
        "source": "TEST",
        "node_type": "etim_class",
        "class_key": "code",
        "counts": {"classes": 1, "features": 1, "feature_groups": 0, "class_features": 1},
        "units_needed": {},
        "documents": [
            {"file": "model.mcf.json", "kind": "mcf", "sections": ["object_types"]},
            {"file": "classes.mef.json", "kind": "mef", "objects": 1},
            {"file": "features.mcf.json", "kind": "mcf", "sections": ["category_attributes"]},
        ],
    }
    (bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    model = {
        "format": "mcf",
        "sections": {
            "object_types": [],
            "classification_standards": [{"code": "etim", "version": "10.0"}],
            "attribute_definitions": [],
        },
    }
    (bundle / "model.mcf.json").write_text(json.dumps(model), encoding="utf-8")
    (bundle / "classes.mef.json").write_text(
        json.dumps({"format": "mef", "objects": [{"type": "etim_class", "attributes": {}}]}),
        encoding="utf-8",
    )
    (bundle / "features.mcf.json").write_text(
        json.dumps({"format": "mcf", "sections": {"category_attributes": []}}), encoding="utf-8"
    )
    return bundle


def test_the_bulk_import_upserts_by_the_class_key(tmp_path: Path) -> None:
    """A re-import of an installed bundle must not collide with the unique key.

    Without `match_by_key` the worker cannot match existing objects and posts new
    ones — the full ETIM bundle failed 25 of 5640 objects with `409 Conflict` on
    the second run (`plan/detail/bmecat-export.md` §11.9).
    """
    bundle = write_bundle(tmp_path)
    client = FakeClient()

    result = installer.install(client, {}, installer.read_manifest(bundle), bundle, bulk=True)

    assert client.path_params("/imports") == [{"match_by_key": "code"}]
    assert result["errors"] == 0
    # The class-feature section needs the nodes the job has not written yet.
    assert "run the installer again" in str(result["steps"][2].get("skipped"))
    assert [path for path, _ in client.posts] == ["/config/import", "/imports", "/config/import"]


def test_a_bundle_without_bulk_posts_the_nodes_one_by_one(tmp_path: Path) -> None:
    bundle = write_bundle(tmp_path)
    client = FakeClient()

    result = installer.install(client, {}, installer.read_manifest(bundle), bundle, bulk=False)

    assert client.path_params("/imports") == [], "no job without `--bulk`"
    assert result["created_objects"] == 1
    assert result["steps"][2].get("skipped") is None, "without a job the section is applied"


def test_large_sections_are_uploaded_in_row_chunks() -> None:
    """A full standard's class-feature section must not go in one request.

    The ETIM 10.0 bundle has 76 625 rows / 10 MB in that section: one
    `POST /config/import` outlived the client timeout and wrote nothing.
    """
    payload = {
        "format": "mcf",
        "source": "TEST",
        "sections": {
            "object_types": [{"name": "product"}],
            "category_attributes": [{"id": index} for index in range(12000)],
        },
    }

    chunks = installer.section_chunks(payload, 5000)

    attribute_chunks = [chunk for chunk in chunks if "category_attributes" in chunk["sections"]]
    assert [len(chunk["sections"]["category_attributes"]) for chunk in attribute_chunks] == [
        5000,
        5000,
        2000,
    ]
    assert all(chunk["format"] == "mcf" and chunk["source"] == "TEST" for chunk in chunks)
    # Small sections travel together — the model document stays one request.
    small = [chunk for chunk in chunks if "object_types" in chunk["sections"]]
    assert len(small) == 1 and small[0]["sections"]["object_types"] == [{"name": "product"}]
    assert installer.section_chunks(payload, 0) == [payload]
    assert len(installer.section_chunks(payload, 20000)) == 1


def test_read_manifest_rejects_foreign_documents(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "manifest.json").write_text(
        json.dumps({"format": "something-else"}), encoding="utf-8"
    )
    try:
        installer.read_manifest(bundle)
    except SystemExit as error:
        assert "not a standard bundle" in str(error)
    else:  # pragma: no cover - the call must fail
        raise AssertionError("a foreign document must be rejected")


def test_read_manifest_needs_a_manifest_file(tmp_path: Path) -> None:
    try:
        installer.read_manifest(tmp_path)
    except SystemExit as error:
        assert "no manifest.json" in str(error)
    else:  # pragma: no cover - the call must fail
        raise AssertionError("a bundle without a manifest must be rejected")


def test_read_manifest_returns_the_document_order(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    manifest = {
        "format": installer.BUNDLE_FORMAT,
        "name": "etim-10.0",
        "documents": [{"file": "model.mcf.json", "kind": "mcf"}],
    }
    (bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert installer.read_manifest(bundle)["name"] == "etim-10.0"


def test_find_bundles_scans_a_drop_in_folder(tmp_path: Path) -> None:
    (tmp_path / "etim-10.0").mkdir()
    (tmp_path / "etim-10.0" / "manifest.json").write_text(
        json.dumps({"format": installer.BUNDLE_FORMAT, "name": "etim-10.0"}), encoding="utf-8"
    )
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "readme.txt").write_text("x", encoding="utf-8")
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "manifest.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "foreign").mkdir()
    (tmp_path / "foreign" / "manifest.json").write_text(
        json.dumps({"format": "mcf"}), encoding="utf-8"
    )

    bundles, notes = installer.find_bundles(tmp_path)
    assert [path.name for path in bundles] == ["etim-10.0"]
    assert len(notes) == 3, "everything else is ignored with a note, never an error"
    assert any("no manifest.json" in note for note in notes)
    assert any("unreadable manifest" in note for note in notes)
    assert any("not a standard bundle" in note for note in notes)


def test_find_bundles_tolerates_a_missing_folder(tmp_path: Path) -> None:
    bundles, notes = installer.find_bundles(tmp_path / "nope")
    assert bundles == []
    assert notes == [f"{tmp_path / 'nope'} is not a directory (no bundles installed)"]
