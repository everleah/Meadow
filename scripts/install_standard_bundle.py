"""Install a **standard bundle** (classification standard) into a Meadow system.

A bundle is what `scripts/etim_ixf_to_mef.py` writes: a manifest plus the model
(MCF: node type + attribute definitions), the node data (MEF: the classes) and
the class-feature rows (MCF). It is the deliberate counterpart to the seeded
**system** data (`plan/detail/bmecat-export.md` §11.9):

* a system runs **without** a standard bundle (nothing is seeded, nothing is
  required — categorisation and the model simply stay empty),
* adding one is a **single command** (this script), not a manual re-modelling,
* the bundle is **not** part of the seed, so it stays licensed-data-clean and
  replaceable (bundle version, another standard, a subset).

Install order is the manifest order — `model.mcf.json` → `classes.mef.json` →
`features.mcf.json` — because the class-feature section resolves classes by their
business key. The installer is **idempotent**: MCF parts are merged
(`mode=merge`), node objects that already exist (matched by the class key) are
skipped.

Usage::

    uv run python scripts/install_standard_bundle.py --bundle .tmp/bundle-etim
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

CORE_URL = "http://127.0.0.1:8080"
#: The bundle envelope (`scripts/etim_ixf_to_mef.py`).
BUNDLE_FORMAT = "meadow-standard-bundle"
#: `GET /objects` caps the page size at 1000.
PAGE = 1000


def read_manifest(bundle_dir: Path) -> dict:
    """Reads and checks the bundle manifest."""
    path = bundle_dir / "manifest.json"
    if not path.exists():
        raise SystemExit(f"no manifest.json in {bundle_dir} (run the converter first)")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("format") != BUNDLE_FORMAT:
        raise SystemExit(f"not a standard bundle: {manifest.get('format')!r}")
    if not manifest.get("documents"):
        raise SystemExit("bundle manifest lists no documents")
    return manifest


def missing_units(manifest: dict, available: set[str]) -> list[dict]:
    """The units the bundle needs but the target does not have.

    The converter only *sets* units the target already has, so this is a warning:
    the features concerned stay unit-less instead of pointing at a wrong unit.
    """
    return [
        {"code": code, "symbol": str(info.get("symbol") or ""), "en": str(info.get("en") or "")}
        for code, info in sorted((manifest.get("units_needed") or {}).items())
        if info.get("symbol") and info["symbol"] not in available
    ]


def existing_keys(client: httpx.Client, headers: dict, node_type: str, class_key: str) -> set[str]:
    """The business keys of the node objects already present in the target."""
    keys: set[str] = set()
    start = 0
    while True:
        response = client.get(
            "/objects",
            params={"type": node_type, "limit": PAGE, "start": start},
            headers=headers,
        )
        response.raise_for_status()
        rows = response.json()
        if not isinstance(rows, list):
            rows = rows.get("items") or []
        for row in rows:
            key = str((row.get("attributes") or {}).get(class_key) or "")
            if key:
                keys.add(key)
        if len(rows) < PAGE:
            return keys
        start += PAGE


def find_bundles(root: Path) -> tuple[list[Path], list[str]]:
    """All bundle directories under `root` (sorted) plus notes about skipped ones.

    A bundle is a directory with a valid `manifest.json`. Anything else in the
    drop-in folder is ignored with a note — a missing bundle is **not** an error,
    the system simply runs without that standard.
    """
    bundles: list[Path] = []
    notes: list[str] = []
    if not root.is_dir():
        return bundles, [f"{root} is not a directory (no bundles installed)"]
    for candidate in sorted(path for path in root.iterdir() if path.is_dir()):
        manifest_file = candidate / "manifest.json"
        if not manifest_file.exists():
            notes.append(f"{candidate.name}: no manifest.json (ignored)")
            continue
        try:
            manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            notes.append(f"{candidate.name}: unreadable manifest ({error})")
            continue
        if manifest.get("format") != BUNDLE_FORMAT:
            notes.append(f"{candidate.name}: not a standard bundle (ignored)")
            continue
        bundles.append(candidate)
    return bundles, notes


def install(
    client: httpx.Client,
    headers: dict,
    manifest: dict,
    bundle_dir: Path,
    *,
    dry_run: bool = False,
    bulk: bool = False,
) -> dict:
    """Applies the bundle documents in manifest order (idempotent).

    With `bulk` the node document is **queued** as an `inbound_import` job (the
    worker upserts by business key, which is what makes the full standard
    installable at all); the class-feature section is then skipped, because it
    resolves nodes that the job has not created yet — re-running the installer
    afterwards applies it (the installer is idempotent).
    """
    result: dict = {"steps": [], "created_objects": 0, "skipped_objects": 0, "errors": 0}
    node_type = str(manifest.get("node_type") or "")
    class_key = str(manifest.get("class_key") or "code")
    known: set[str] | None = None
    queued_nodes = False

    for document in manifest.get("documents") or []:
        path = bundle_dir / str(document.get("file"))
        payload = json.loads(path.read_text(encoding="utf-8"))
        step: dict = {"file": path.name, "kind": document.get("kind")}
        started = time.monotonic()
        if document.get("kind") == "mcf":
            sections = list(document.get("sections") or [])
            if queued_nodes and "category_attributes" in sections:
                step["skipped"] = (
                    "the node import is still queued - run the installer again afterwards"
                )
            elif dry_run:
                step["dry_run"] = True
            else:
                response = client.post(
                    "/config/import", params={"mode": "merge"}, headers=headers, json=payload
                )
                response.raise_for_status()
                body = response.json()
                step["sections"] = {
                    name: {
                        key: len(value) for key, value in section.items() if isinstance(value, list)
                    }
                    for name, section in (body.get("sections") or {}).items()
                }
                step["errors"] = len(body.get("errors") or [])
                result["errors"] += step["errors"]
        elif document.get("kind") == "mef":
            objects = payload.get("objects") or []
            step["objects"] = len(objects)
            if bulk:
                queued_nodes = True
                if not dry_run:
                    response = client.post("/imports", headers=headers, json=payload)
                    if response.status_code >= 300:
                        result["errors"] += 1
                        step["error"] = response.text[:200]
                    else:
                        step["queued_job"] = response.json().get("job_run_id")
            else:
                if known is None and not dry_run:
                    known = existing_keys(client, headers, node_type, class_key)
                for obj in objects:
                    key = str((obj.get("attributes") or {}).get(class_key) or "")
                    if not dry_run and known is not None and key and key in known:
                        result["skipped_objects"] += 1
                        continue
                    if dry_run:
                        result["created_objects"] += 1
                        continue
                    response = client.post("/objects", headers=headers, json=obj)
                    if response.status_code >= 300:
                        result["errors"] += 1
                        continue
                    result["created_objects"] += 1
                    if known is not None and key:
                        known.add(key)
        else:
            step["skipped"] = "unknown document kind"
        step["seconds"] = round(time.monotonic() - started, 2)
        result["steps"].append(step)
    return result


def install_one(
    client: httpx.Client, headers: dict, bundle_dir: Path, args: argparse.Namespace
) -> dict:
    """Checks the units of one bundle and installs it."""
    manifest = read_manifest(bundle_dir)
    print(f"bundle {manifest.get('name')} {manifest.get('standard_version')}")
    print(f"  source: {manifest.get('source')}  counts: {manifest.get('counts')}")
    units = client.get("/units", params={"limit": 1000}, headers=headers).json()
    rows = units.get("items", units) if isinstance(units, dict) else units
    available = {str(row.get("symbol")) for row in rows if isinstance(row, dict)}
    missing = missing_units(manifest, available)
    if missing:
        print(f"  warning: units missing in the target: {[item['symbol'] for item in missing]}")
        if args.strict_units:
            raise SystemExit("aborting (--strict-units)")
    else:
        print("  all needed units are present")
    return install(client, headers, manifest, bundle_dir, dry_run=args.dry_run, bulk=args.bulk)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bundle", type=Path, default=Path(".tmp/bundle-etim"), help="one bundle folder"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="install every bundle found under --standards-dir (default install)",
    )
    parser.add_argument(
        "--standards-dir",
        type=Path,
        default=Path("resources/standards"),
        help="drop-in folder for standard bundles (gitignored)",
    )
    parser.add_argument("--core", default=CORE_URL)
    parser.add_argument("--user", default="admin")
    parser.add_argument("--password", default="admin")
    parser.add_argument("--dry-run", action="store_true", help="report without writing")
    parser.add_argument(
        "--bulk",
        action="store_true",
        help=(
            "queue the node import as an `inbound_import` job "
            "(needs the worker; run the installer again afterwards)"
        ),
    )
    parser.add_argument(
        "--strict-units", action="store_true", help="abort when a needed unit is missing"
    )
    args = parser.parse_args(argv[1:])

    if args.all:
        bundles, notes = find_bundles(args.standards_dir)
        for note in notes:
            print(f"ignored: {note}")
        if not bundles:
            print(f"no standard bundles in {args.standards_dir} - nothing to do")
            return 0
    else:
        bundles = [args.bundle]

    client = httpx.Client(base_url=args.core, timeout=900)
    login = client.post("/auth/login", json={"username": args.user, "password": args.password})
    login.raise_for_status()
    payload = login.json()
    headers = {"Authorization": f"Bearer {payload.get('access_token') or payload.get('token')}"}

    failed = 0
    for bundle_dir in bundles:
        result = install_one(client, headers, bundle_dir, args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        failed += 1 if result["errors"] else 0
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
