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
from contextlib import suppress
from pathlib import Path

import httpx

CORE_URL = "http://127.0.0.1:8080"
#: The bundle envelope (`scripts/etim_ixf_to_mef.py`).
BUNDLE_FORMAT = "meadow-standard-bundle"
#: `GET /objects` caps the page size at 1000.
PAGE = 1000
#: Rows per `POST /config/import` request. A full standard's class-feature section
#: has tens of thousands of rows; one request would time out (see `section_chunks`).
SECTION_CHUNK = 5000
#: Byte budget per request. The class-feature section of a standard bundle is
#: **nested** (`{class, features: [...]}`, ~13\.6 features per class for ETIM 10.0),
#: so a row cap alone is not enough — 5000 such rows are ~9 MB.
SECTION_CHUNK_BYTES = 750_000


def configure_console() -> None:
    """Makes symbol lists printable on a Windows console.

    The bundles name units such as `W/(m⋅K)`, `°` or `µm`; a cp1252 console
    raises `UnicodeEncodeError` on those characters (found with the full ETIM
    10.0 bundle). Printing must never abort an install, so the streams fall back
    to UTF-8 with `errors="replace"`.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:  # pragma: no cover - exotic stream
            continue
        with suppress(ValueError, OSError):  # pragma: no cover - exotic stream
            reconfigure(encoding="utf-8", errors="replace")


def _count(value) -> int:
    """Counts one section counter (`len` of the affected rows the core returns)."""
    return len(value) if isinstance(value, list) else int(value or 0)


def section_chunks(
    payload: dict, rows_per_request: int, *, max_bytes: int = SECTION_CHUNK_BYTES
) -> list[dict]:
    """Splits an MCF document so no request carries more than `rows_per_request` rows.

    The class-feature section of a **full** standard is huge (ETIM 10.0: 5640
    class rows that expand to 76 625 rows / 10 MB) — one request outlives any
    sensible timeout (found live: `ReadTimeout` after 15 minutes **and no rows
    written**). Chunking therefore counts **rows and bytes**: a class row carries
    all its features. Each request holds the document envelope plus **one** slice,
    and `mode=merge` merges per row, so the split stays idempotent. `0` disables it.
    """
    if rows_per_request <= 0:
        return [payload]
    envelope = {
        key: value
        for key, value in payload.items()
        if key != "sections" and not isinstance(value, list)
    }
    small: dict[str, object] = {}
    chunks: list[dict] = []
    for name, value in (payload.get("sections") or {}).items():
        if not isinstance(value, list) or len(value) <= rows_per_request:
            # Small sections stay together: one request for the model document.
            small[name] = value
            continue
        bucket: list[object] = []
        size = 0
        for row in value:
            row_size = len(json.dumps(row, ensure_ascii=False))
            if bucket and (len(bucket) >= rows_per_request or size + row_size > max_bytes):
                chunks.append({"sections": {name: bucket}})
                bucket, size = [], 0
            bucket.append(row)
            size += row_size
        if bucket:
            chunks.append({"sections": {name: bucket}})
    if small:
        chunks.insert(0, {"sections": small})
    if not chunks:
        return [payload]
    return [{**envelope, **chunk} for chunk in chunks]


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


def used_by(item: dict) -> int:
    """Sort key for the unit warning: how many class features use the unit."""
    return int(item.get("used_by") or 0)


def missing_units(manifest: dict, available: set[str]) -> list[dict]:
    """The units the bundle needs but the target does not have.

    The converter only *sets* units the target already has, so this is a warning:
    the features concerned stay unit-less instead of pointing at a wrong unit.
    """
    return [
        {
            "code": code,
            "symbol": str(info.get("symbol") or ""),
            "en": str(info.get("en") or ""),
            "used_by": int(info.get("used_by") or 0),
        }
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
    section_chunk: int = SECTION_CHUNK,
) -> dict:
    """Applies the bundle documents in manifest order (idempotent).

    With `bulk` the node document is **queued** as an `inbound_import` job (the
    worker upserts by business key, which is what makes the full standard
    installable at all); the class-feature section is then skipped, because it
    resolves nodes that the job has not created yet — re-running the installer
    afterwards applies it (the installer is idempotent). Large MCF sections are
    uploaded in `section_chunk`-row requests (`section_chunks`).
    """
    result: dict = {
        "steps": [],
        "created_objects": 0,
        "skipped_objects": 0,
        "errors": 0,
        "warnings": 0,
    }
    node_type = str(manifest.get("node_type") or "")
    class_key = str(manifest.get("class_key") or "code")
    known: set[str] | None = None
    queued_nodes = False

    for document in manifest.get("documents") or []:
        path = bundle_dir / str(document.get("file"))
        payload = json.loads(path.read_text(encoding="utf-8"))
        step: dict = {"file": path.name, "kind": document.get("kind")}
        # The manifest and the `classification_standards` entry of the model
        # document both name the standard's version; a divergence would make the
        # installer report something the export does not use.
        warning = standard_mismatch(manifest, payload, document.get("sections") or [])
        if warning:
            step["warning"] = warning
            result["warnings"] += 1
            print(f"  warning: {path.name}: {warning}")
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
                requests = section_chunks(payload, section_chunk)
                if len(requests) > 1:
                    step["chunks"] = len(requests)
                counted: dict[str, dict[str, int]] = {}
                chunk_errors = 0
                for index, chunk in enumerate(requests, start=1):
                    if len(requests) > 1:
                        rows = sum(
                            len(value)
                            for value in (chunk.get("sections") or {}).values()
                            if isinstance(value, list)
                        )
                        size = len(json.dumps(chunk.get("sections"), ensure_ascii=False)) // 1024
                        print(
                            f"    {path.name}: chunk {index}/{len(requests)} "
                            f"({rows} rows, {size} KB)"
                        )
                    response = client.post(
                        "/config/import", params={"mode": "merge"}, headers=headers, json=chunk
                    )
                    response.raise_for_status()
                    body = response.json()
                    for name, section in (body.get("sections") or {}).items():
                        target = counted.setdefault(name, {})
                        for key, value in section.items():
                            target[key] = target.get(key, 0) + _count(value)
                    chunk_errors += len(body.get("errors") or [])
                step["sections"] = counted
                step["errors"] = chunk_errors
                result["errors"] += chunk_errors
        elif document.get("kind") == "mef":
            objects = payload.get("objects") or []
            step["objects"] = len(objects)
            if bulk:
                queued_nodes = True
                if not dry_run:
                    # `match_by_key` makes the job **upsert** by the class key
                    # instead of creating: without it a re-import of an installed
                    # bundle collides with the unique key (`409` per object, found
                    # with the full ETIM bundle).
                    response = client.post(
                        "/imports",
                        params={"match_by_key": class_key},
                        headers=headers,
                        json=payload,
                    )
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

    # Register the standard itself (idempotent, `merge`). The manifest always knew
    # the standard and version; the `classification_standards` section is what the
    # export reads, so the installer writes it even for bundles whose documents
    # predate the section (`standard_mismatch` reports a divergence above).
    document = standard_document(manifest)
    if document is not None:
        step = {"file": "manifest.json", "kind": "standard"}
        started = time.monotonic()
        entries = document["sections"]["classification_standards"]
        if dry_run:
            step["dry_run"] = True
        else:
            response = client.post(
                "/config/import", params={"mode": "merge"}, headers=headers, json=document
            )
            response.raise_for_status()
            body = response.json()
            section = (body.get("sections") or {}).get("classification_standards") or {}
            step["sections"] = {
                "classification_standards": {
                    key: len(value) for key, value in section.items() if isinstance(value, list)
                }
            }
            step["errors"] = len(body.get("errors") or [])
            result["errors"] += step["errors"]
        step["entry"] = entries[0]
        step["seconds"] = round(time.monotonic() - started, 2)
        result["steps"].append(step)
    return result


def standard_document(manifest: dict) -> dict | None:
    """The `classification_standards` document derived from the bundle manifest.

    Returns `None` when the manifest carries no `standard_version` — a version is
    never invented, the export then falls back to its explicit profile field.
    """
    standard = str(manifest.get("standard") or "").strip()
    version = str(manifest.get("standard_version") or "").strip()
    if not standard or not version:
        return None
    entry = {
        "code": standard.lower(),
        "version": version,
        "name": standard,
    }
    if manifest.get("source"):
        entry["source"] = str(manifest["source"])
    if manifest.get("node_type"):
        entry["node_type"] = str(manifest["node_type"])
    return {
        "format": "mcf",
        "version": "1.0",
        "source": "meadow-standard-bundle",
        "sections": {"classification_standards": [entry]},
    }


def standard_mismatch(
    manifest: dict, payload: dict, declared_sections: list[str] | None = None
) -> str | None:
    """Compares the manifest's standard with the document's registry entry.

    The `classification_standards` section is what the export names (the
    reference feature system of a class block), the manifest what the installer
    reports. A bundle whose documents disagree with its manifest is broken, so
    the installer says so instead of installing quietly.

    Only the **model** document is expected to carry the entry — it defines the
    class node type; a feature document legitimately contains only
    `category_attributes`. Returns `None` when there is nothing to compare (a
    bundle without a version, or a document that is not the model).
    """
    version = str(manifest.get("standard_version") or "")
    if not version:
        return None
    sections = payload.get("sections")
    entries = (sections or {}).get("classification_standards") or []
    if isinstance(entries, list) and entries:
        entry = entries[0] if isinstance(entries[0], dict) else {}
        declared = str(entry.get("version") or "")
        if declared != version:
            return (
                f"manifest says {manifest.get('standard')} {version}, "
                f"the document {entry.get('code') or '?'} {declared or '?'}"
            )
        return None
    # An MCF document is recognised by its `sections` object (the envelope has no
    # `kind` field); the model document is the one carrying `object_types`.
    section_names = list(declared_sections or [])
    declares_types = "object_types" in section_names or "object_types" in (sections or {})
    if declares_types:
        return (
            "the model document declares no `classification_standards` entry "
            "(the standard is registered from the manifest)"
        )
    return None


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
        # A wall of 130 symbols helps nobody; the count and the most-used ones do.
        symbols = [str(item["symbol"]) for item in sorted(missing, key=used_by, reverse=True)]
        shown = ", ".join(symbols[:10])
        more = f" (+{len(symbols) - 10} more)" if len(symbols) > 10 else ""
        print(f"  warning: {len(missing)} units missing in the target: {shown}{more}")
        if args.strict_units:
            raise SystemExit("aborting (--strict-units)")
    else:
        print("  all needed units are present")
    return install(
        client,
        headers,
        manifest,
        bundle_dir,
        dry_run=args.dry_run,
        bulk=args.bulk,
        section_chunk=args.section_chunk,
    )


def main(argv: list[str]) -> int:
    configure_console()
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
    parser.add_argument(
        "--section-chunk",
        type=int,
        default=SECTION_CHUNK,
        help="rows per `POST /config/import` request (0 = one request; the full ETIM "
        "standard needs chunks)",
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
