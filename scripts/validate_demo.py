"""Acceptance validation of an imported demo dataset (read-only, plus one export).

Runs the checklist of `plan/detail/demodaten-generator.md` §7 against a running
instance: it compares `GET /objects/stats` with the generated `report.json`,
looks for duplicate business keys, checks channel releases, business rules,
collections, saved searches, the composition path, the class features and — unless
`--skip-export` — exports one collection as BMEcat and validates it against the
ETIM XSD if `resources/etim/bmecat_etim_501.xsd` is present.

The script only reads; the single write is the export job (which stores a file in
the object storage). It exits with `1` as soon as one check fails, so it can be
used as a gate after `scripts/demo_data.py --cleanup --import`.

Usage:
    uv run python scripts/validate_demo.py
    uv run python scripts/validate_demo.py --report .tmp/demo-data/report.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:8080"
DEFAULT_REPORT = Path(".tmp/demo-data/report.json")
DEFAULT_EXTRAS = Path(".tmp/demo-data/extras.json")
DEFAULT_OUT_DIR = Path(".tmp/demo-validate")
DEFAULT_COLLECTION = "E-Mobilitaet Ladepaket"
DEFAULT_SOURCE = "meadow-demo"
DEFAULT_CHANNELS = ("webshop", "print-2026", "marketplace-de", "bmecat-partner")
LIST_PAGE = 1000
#: Safety net for the paged listings (`--max-pages`); the demo dataset needs 3.
MAX_PAGES = 12
#: BMEcat export parameters (same shape as the saved demo export profile).
BMECAT_PARAMETERS: dict[str, Any] = {
    "catalog_id": "MEADOW-AUTO-2026",
    "catalog_name": "Meadow Automotive Katalog 2026",
    "catalog_version": "1.0",
    "language": "deu",
    "currency": "EUR",
    "supplier": "Meadow Automotive GmbH",
    "buyer": "Werkstattverbund Nord",
    "pid_attribute": "sku",
    "etim_version": "10.0",
    "transaction": "new_catalog",
    "price_attribute": "net_price",
    "price_type": "net_customer",
    "order_unit": "C62",
    "content_unit": "C62",
    "version": "2005",
}


class Checks:
    """Collects the results and prints them as they are checked."""

    def __init__(self) -> None:
        self.failed = 0

    def ok(self, name: str, detail: str = "") -> None:
        print(f"  ok   {name}{': ' + detail if detail else ''}")

    def fail(self, name: str, detail: str = "") -> None:
        self.failed += 1
        print(f"  FAIL {name}{': ' + detail if detail else ''}")


def configure_console() -> None:
    """Switches stdout/stderr to UTF-8 (Windows cp1252 breaks on non-ASCII names)."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def login(client: httpx.Client, user: str, password: str) -> dict[str, str]:
    response = client.post("/auth/login", json={"username": user, "password": password})
    response.raise_for_status()
    return {"Authorization": f"Bearer {response.json()['token']}"}


def _load_extras(path: Path) -> dict[str, list[str]]:
    """Reads the generated extras: which collections and searches to check.

    Without the file the checks fall back to the empty list so that the script
    stays usable against any instance (`--extras` points at the artefacts).
    """
    if not path.exists():
        print(f"note: {path} not found, skipping the collection/search checks")
        return {"collections": [], "searches": []}
    extras = json.loads(path.read_text(encoding="utf-8"))
    collections = [str(entry["name"]) for entry in extras.get("collections") or []]
    searches = [str(entry["name"]) for entry in extras.get("saved_searches") or []]
    # The importer creates a filter search per collection ("<name> (Auswahl)").
    searches += [f"{name} (Auswahl)" for name in collections]
    return {"collections": collections, "searches": searches}


def pages(
    client: httpx.Client,
    headers: dict[str, str],
    params: dict[str, Any],
    *,
    max_pages: int = MAX_PAGES,
) -> list[dict[str, Any]]:
    """Reads a paged `GET /objects` listing completely (bounded by `max_pages`)."""
    rows: list[dict[str, Any]] = []
    for page in range(max_pages):
        page_params = {**params, "limit": LIST_PAGE, "start": page * LIST_PAGE}
        response = client.get("/objects", params=page_params, headers=headers)
        response.raise_for_status()
        chunk = response.json()
        rows.extend(chunk)
        if len(chunk) < LIST_PAGE:
            break
    return rows


def check_expected_counts(
    checks: Checks,
    stats: dict[str, Any],
    expected: dict[str, Any],
) -> None:
    """Every generated type must be present at least as often as in the report."""
    by_type = stats.get("by_type") or {}
    short = {
        name: (int(by_type.get(name, 0)), int(count))
        for name, count in (expected.get("by_type") or {}).items()
        if int(by_type.get(name, 0)) < int(count)
    }
    if short:
        checks.fail("object counts", f"below the report: {short}")
    else:
        checks.ok("object counts", f"{stats.get('total')} objects, all types covered")
    checks.ok(
        "status mix",
        ", ".join(
            f"{name}={count}" for name, count in sorted((stats.get("by_status") or {}).items())
        ),
    )
    checks.ok(
        "incomplete",
        ", ".join(
            f"{name}={count}"
            for name, count in sorted((stats.get("incomplete_by_type") or {}).items())
        ),
    )


def check_duplicate_keys(
    checks: Checks,
    client: httpx.Client,
    headers: dict[str, str],
    source: str,
) -> None:
    """No two live objects of one type may share a business key.

    Pages through the API (no direct database access) and groups by the key
    attributes the object types declare, so it also covers types this script does
    not know.
    """
    types = client.get("/object-types", headers=headers).json()
    duplicates: dict[str, dict[str, int]] = {}
    checked = 0
    for entry in types:
        keys = entry.get("key_attributes") or []
        name = entry.get("name")
        if not keys or not name:
            continue
        for key in keys:
            seen: dict[str, int] = {}
            for row in pages(
                client,
                headers,
                {"type": name, "source": source},
            ):
                value = (row.get("attributes") or {}).get(key)
                if isinstance(value, str) and value:
                    seen[value] = seen.get(value, 0) + 1
            checked += 1
            hits = {value: count for value, count in seen.items() if count > 1}
            if hits:
                duplicates[f"{name}.{key}"] = hits
    if duplicates:
        checks.fail("business keys", json.dumps(duplicates, ensure_ascii=False)[:300])
    else:
        checks.ok("business keys", f"{checked} key attributes without duplicates")


def check_releases(
    checks: Checks,
    client: httpx.Client,
    headers: dict[str, str],
    channels: tuple[str, ...],
) -> None:
    """Every demo channel must have released products."""
    counts: dict[str, int] = {}
    for channel in channels:
        rows = pages(
            client,
            headers,
            {"type": "product", "released_for": channel},
            max_pages=6,
        )
        counts[channel] = len(rows)
    empty = [channel for channel, count in counts.items() if not count]
    detail = ", ".join(f"{channel}={count}" for channel, count in counts.items())
    if empty:
        checks.fail("channel releases", f"channels without releases: {empty} ({detail})")
    else:
        checks.ok("channel releases", detail)


def check_rules(checks: Checks, client: httpx.Client, headers: dict[str, str]) -> None:
    rules = client.get("/rules", headers=headers).json()
    active = sorted(rule["name"] for rule in rules if rule.get("state") == "active")
    if active:
        checks.ok("business rules", f"{len(active)} active: {', '.join(active)}")
    else:
        checks.fail("business rules", "no active rule")


def check_collections(
    checks: Checks,
    client: httpx.Client,
    headers: dict[str, str],
    expected: list[str],
) -> dict[str, dict[str, Any]]:
    """The generated collections must exist and have members.

    `expected` comes from the generated `extras.json`: collections the generator
    does not own (leftovers of older catalogue revisions, manual test data) are
    reported but not judged.
    """
    collections = client.get("/collections", headers=headers).json()
    by_name: dict[str, dict[str, Any]] = {}
    details: list[str] = []
    missing: list[str] = []
    empty: list[str] = []
    for collection in collections:
        name = str((collection.get("attributes") or {}).get("name"))
        members = client.get(f"/collections/{collection['id']}/members", headers=headers)
        count = len(members.json()) if members.status_code == 200 else -1
        by_name[name] = {**collection, "members": count}
        if name in expected:
            details.append(f"{name}={count}")
            if count == 0:
                empty.append(name)
    missing = [name for name in expected if name not in by_name]
    if missing:
        checks.fail("collections", f"missing: {missing}")
    elif empty:
        checks.fail("collections", f"without members: {empty}")
    else:
        checks.ok("collections", ", ".join(details))
    return by_name


def check_searches(
    checks: Checks,
    client: httpx.Client,
    headers: dict[str, str],
    expected: list[str],
) -> None:
    """The generated saved searches must exist and find something."""
    searches = client.get("/saved-searches", headers=headers).json()
    by_name = {str((search.get("attributes") or {}).get("name")): search for search in searches}
    details: list[str] = []
    empty: list[str] = []
    for name in expected:
        search = by_name.get(name)
        if search is None:
            empty.append(f"{name} (missing)")
            continue
        result = client.post(f"/saved-searches/{search['id']}/execute", headers=headers)
        count = result.json().get("count") if result.status_code == 200 else -1
        details.append(f"{name}={count}")
        if count == 0:
            empty.append(name)
    if empty:
        checks.fail("saved searches", f"without hits: {empty}")
    else:
        checks.ok("saved searches", ", ".join(details))


def check_composition_path(
    checks: Checks,
    client: httpx.Client,
    headers: dict[str, str],
) -> None:
    """A vehicle generation must sit below model and make (composition path)."""
    generations = client.get(
        "/objects", params={"type": "vehicle_generation", "limit": 5}, headers=headers
    ).json()
    if not generations:
        checks.fail("composition path", "no vehicle generation found")
        return
    generation = generations[0]
    path = client.get(f"/objects/{generation['id']}/ancestors", headers=headers).json()
    types = [str(entry.get("type")) for entry in path]
    code = (generation.get("attributes") or {}).get("generation_code")
    if types == ["vehicle_make", "vehicle_model", "vehicle_generation"]:
        checks.ok("composition path", f"{code}: {' -> '.join(types)}")
    else:
        checks.fail("composition path", f"{code}: {types}")


def check_class_features(
    checks: Checks,
    client: httpx.Client,
    headers: dict[str, str],
) -> None:
    """A classified product must resolve at least one ETIM class."""
    query = json.dumps(
        {
            "where": {
                "op": "and",
                "conditions": [{"field": "attributes.charging_power_kw", "operator": "not_empty"}],
            },
            "limit": 5,
        }
    )
    products = client.get(
        "/objects", params={"type": "product", "where": query}, headers=headers
    ).json()
    if not products:
        checks.fail("class features", "no charging product found")
        return
    product = products[0]
    features = client.get(f"/objects/{product['id']}/category-attributes", headers=headers).json()
    classes = features.get("classes") or []
    codes = [entry.get("code") if isinstance(entry, dict) else entry for entry in classes]
    missing = len(features.get("missing") or [])
    sku = (product.get("attributes") or {}).get("sku")
    if codes:
        checks.ok("class features", f"{sku}: {len(codes)} classes, {missing} missing features")
    else:
        checks.fail("class features", f"{sku}: no class resolved ({missing} missing)")


def check_bmecat_export(
    checks: Checks,
    client: httpx.Client,
    headers: dict[str, str],
    collections: dict[str, dict[str, Any]],
    out_dir: Path,
    timeout: float,
) -> None:
    """Exports one collection as BMEcat and validates it against the ETIM XSD."""
    collection = collections.get(DEFAULT_COLLECTION)
    if collection is None:
        checks.fail("bmecat export", f"collection `{DEFAULT_COLLECTION}` not found")
        return
    response = client.post(
        "/exports",
        json={
            "source": {"kind": "collection", "id": collection["id"]},
            "format": "bmecat",
            "parameters": {"fields": [], "locales": ["de"], "bmecat": BMECAT_PARAMETERS},
        },
        headers=headers,
    )
    if response.status_code not in (200, 201, 202):
        checks.fail("bmecat export", f"queueing failed: HTTP {response.status_code}")
        return
    run_id = str(response.json().get("job_run_id"))
    deadline = time.time() + timeout
    result: dict[str, Any] | None = None
    while time.time() < deadline:
        for run in client.get("/jobs/runs", params={"limit": 20}, headers=headers).json():
            if str(run.get("id")) == run_id and run.get("status") in ("success", "failed"):
                result = run
        if result is not None:
            break
        time.sleep(2)
    if result is None:
        checks.fail("bmecat export", "job did not finish in time")
        return
    if result.get("status") != "success":
        checks.fail("bmecat export", str(result.get("error") or result.get("status")))
        return
    export_result = result.get("result") or {}
    file = client.get(f"/exports/{run_id}/file", headers=headers, follow_redirects=True)
    if file.status_code != 200:
        checks.fail("bmecat export", f"download failed: HTTP {file.status_code}")
        return
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "meadow-export-bmecat.xml"
    path.write_bytes(file.content)
    issues = export_result.get("issues") or []
    if issues:
        checks.fail("bmecat export", f"{len(issues)} report issues: {issues[:2]}")
        return
    checks.ok(
        "bmecat export",
        f"{export_result.get('exported')} products, {len(file.content)} bytes → {path}",
    )
    xsd = Path("resources/etim/bmecat_etim_501.xsd")
    if not xsd.exists():
        checks.ok("bmecat schema", "skipped (no resources/etim/bmecat_etim_501.xsd)")
        return
    try:
        from lxml import etree
    except ImportError:
        checks.ok("bmecat schema", "skipped (lxml not installed)")
        return
    schema = etree.XMLSchema(etree.parse(str(xsd)))
    document = etree.fromstring(file.content)
    if schema.validate(document):
        checks.ok("bmecat schema", f"valid against {xsd}")
    else:
        checks.fail("bmecat schema", str(schema.error_log.filter_from_errors()[:2]))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate an imported Meadow demo dataset against its report.",
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--user", default="admin")
    parser.add_argument("--password", default="admin")
    parser.add_argument(
        "--report",
        default=str(DEFAULT_REPORT),
        help="generated report.json to compare the counts with (skipped if missing)",
    )
    parser.add_argument(
        "--source",
        default=DEFAULT_SOURCE,
        help="source value of the generated objects (for the duplicate check)",
    )
    parser.add_argument("--channels", nargs="*", default=list(DEFAULT_CHANNELS))
    parser.add_argument(
        "--extras",
        default=str(DEFAULT_EXTRAS),
        help="generated extras.json: the collections and searches it names are the checked ones",
    )
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--skip-export", action="store_true")
    parser.add_argument("--timeout", type=float, default=300.0, help="export wait in seconds")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    configure_console()
    args = parse_args(argv)
    checks = Checks()
    report_path = Path(args.report)
    extras = _load_extras(Path(args.extras))
    with httpx.Client(base_url=args.base_url, timeout=180.0) as client:
        headers = login(client, args.user, args.password)
        print(f"validating {args.base_url} (source {args.source})")

        stats = client.get("/objects/stats", headers=headers).json()
        if report_path.exists():
            expected = json.loads(report_path.read_text(encoding="utf-8"))
            check_expected_counts(checks, stats, expected)
        else:
            checks.ok("object counts", f"{stats.get('total')} objects (no report to compare)")

        check_duplicate_keys(checks, client, headers, args.source)
        check_releases(checks, client, headers, tuple(args.channels))
        check_rules(checks, client, headers)
        collections = check_collections(checks, client, headers, extras["collections"])
        check_searches(checks, client, headers, extras["searches"])
        check_composition_path(checks, client, headers)
        check_class_features(checks, client, headers)
        if args.skip_export:
            checks.ok("bmecat export", "skipped (--skip-export)")
        else:
            check_bmecat_export(
                checks, client, headers, collections, Path(args.out_dir), args.timeout
            )

    if checks.failed:
        print(f"\n{checks.failed} check(s) failed")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
