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


def install(
    client: httpx.Client,
    headers: dict,
    manifest: dict,
    bundle_dir: Path,
    *,
    dry_run: bool = False,
) -> dict:
    """Applies the bundle documents in manifest order (idempotent)."""
    result: dict = {"steps": [], "created_objects": 0, "skipped_objects": 0, "errors": 0}
    node_type = str(manifest.get("node_type") or "")
    class_key = str(manifest.get("class_key") or "code")
    known: set[str] | None = None

    for document in manifest.get("documents") or []:
        path = bundle_dir / str(document.get("file"))
        payload = json.loads(path.read_text(encoding="utf-8"))
        step: dict = {"file": path.name, "kind": document.get("kind")}
        started = time.monotonic()
        if document.get("kind") == "mcf":
            if dry_run:
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
            if known is None and not dry_run:
                known = existing_keys(client, headers, node_type, class_key)
            step["objects"] = len(objects)
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


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=Path(".tmp/bundle-etim"))
    parser.add_argument("--core", default=CORE_URL)
    parser.add_argument("--user", default="admin")
    parser.add_argument("--password", default="admin")
    parser.add_argument("--dry-run", action="store_true", help="report without writing")
    parser.add_argument(
        "--strict-units", action="store_true", help="abort when a needed unit is missing"
    )
    args = parser.parse_args(argv[1:])

    manifest = read_manifest(args.bundle)
    print(
        f"bundle {manifest.get('name')} {manifest.get('standard_version')} "
        f"from {manifest.get('source')}"
    )
    print(f"counts: {manifest.get('counts')}")

    client = httpx.Client(base_url=args.core, timeout=900)
    login = client.post("/auth/login", json={"username": args.user, "password": args.password})
    login.raise_for_status()
    payload = login.json()
    headers = {"Authorization": f"Bearer {payload.get('access_token') or payload.get('token')}"}

    units = client.get("/units", params={"limit": 1000}, headers=headers).json()
    rows = units.get("items", units) if isinstance(units, dict) else units
    available = {str(row.get("symbol")) for row in rows if isinstance(row, dict)}
    missing = missing_units(manifest, available)
    if missing:
        print(f"warning: units missing in the target: {[item['symbol'] for item in missing]}")
        if args.strict_units:
            print("aborting (--strict-units)", file=sys.stderr)
            return 2
    else:
        print("all needed units are present")

    result = install(client, headers, manifest, args.bundle, dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
