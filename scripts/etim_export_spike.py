"""Spike step 2: export one product as ETIM BMEcat and validate it against the XSD.

Thin CLI around `meadow.worker.bmecat` — the **same** module the `outbound_export`
job uses, so the spike can never drift from the production rules. It reads a
product from the running core (`GET /objects/{id}`,
`GET /objects/{id}/category-attributes` and the attribute definitions), writes an
**ETIM BMEcat** document (namespace `…/bmecat/50`, `version="2005"`, transaction
`T_NEW_PRODUCTDATA` — the price-less path) for one class and validates it against
`resources/etim/bmecat_etim_501.xsd`.

The rules themselves (every class feature transmitted, `-` + `FVALUE_DETAILS` for a
missing value, a range as **two** `FVALUE`, no `FUNIT`, catalogue language only,
`REFERENCE_FEATURE_GROUP_ID` = the class code) are documented in
`plan/detail/bmecat-export.md` §11.3/§11.6 and implemented in
`meadow/worker/bmecat.py`; `tests/test_bmecat.py` covers them. The export wizard
uses the same code (§11.10).

Usage::

    uv run python scripts/etim_export_spike.py <product-id> \\
        --class-code EC000012 --out .tmp/etim/product.xml
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

# The script lives in `scripts/`, the `meadow` package in the repository root
# (the project is not installed as a package, so a script has to point at it).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meadow.worker import bmecat  # noqa: E402

CORE_URL = "http://127.0.0.1:8080"
#: Version part of `REFERENCE_FEATURE_SYSTEM_NAME` (`ETIM-10.0`).
ETIM_VERSION = "10.0"


def build(
    product: dict,
    features: list[dict],
    definitions: dict[str, dict],
    *,
    class_code: str,
    catalog_id: str,
    supplier: str,
    supplier_pid: str = "",
    buyer: str = "Meadow buyer",
    language: str = "eng",
) -> tuple[str, dict]:
    """Builds the document for one product/class via the worker module.

    `supplier_pid` overrides the business-key attribute (the production path puts
    the **attribute name** into the profile and lets the module read the value;
    the spike lets the caller pass the value itself). `features` are the resolved
    class features of the product: `name` is the ETIM feature code (what `FNAME`
    transmits), `attribute` the attribute carrying the value (a bound **own**
    attribute, or the code itself when the standard was imported as attributes)
    and `required` whether the class demands it.
    """
    attributes = dict(product.get("attributes") or {})
    profile = {
        "catalog_id": catalog_id,
        "catalog_name": "ETIM export (spike)",
        "catalog_version": "1.0",
        "language": language,
        "currency": "EUR",
        "supplier": supplier,
        "buyer": buyer,
        "pid_attribute": "code",
        "etim_version": ETIM_VERSION,
    }
    entry = {
        "id": product.get("id"),
        "type": product.get("type") or "product",
        "attributes": attributes,
        "pid": supplier_pid or bmecat.pid_of(attributes, profile),
        "classes": [class_code] if class_code else [],
        "features": [dict(feature) for feature in features],
    }
    return bmecat.build_document([entry], definitions, profile)


def validate(document: str) -> tuple[bool, str]:
    """Validates the document against the ETIM BMEcat XSD (`bmecat.validate`)."""
    result = bmecat.validate(document)
    if not result.get("validated"):
        return False, f"not validated: {result.get('reason') or 'schema unavailable'}"
    if result.get("valid"):
        return True, "valid"
    return False, "\n".join(str(error) for error in result.get("errors") or [])


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("product_id", help="core object id (`object:…`)")
    parser.add_argument("--class-code", required=True, help="ETIM class code (EC…)")
    parser.add_argument("--catalog-id", default="MEADOW-SPIKE")
    parser.add_argument("--supplier", default="Meadow supplier")
    parser.add_argument("--buyer", default="Meadow buyer")
    parser.add_argument("--language", default="eng", help="BMEcat catalogue language code")
    parser.add_argument(
        "--pid",
        default="",
        help="supplier PID (business key; falls back to the `code`/`sku` attribute)",
    )
    parser.add_argument("--out", type=Path, default=Path(".tmp/etim/product.xml"))
    args = parser.parse_args(argv[1:])

    client = httpx.Client(base_url=CORE_URL, timeout=60)
    login = client.post("/auth/login", json={"username": "admin", "password": "admin"}).json()
    headers = {"Authorization": f"Bearer {login.get('access_token') or login.get('token')}"}
    product = client.get(f"/objects/{args.product_id}", headers=headers).json()
    resolved = client.get(f"/objects/{args.product_id}/category-attributes", headers=headers).json()
    definitions = {
        str(item["name"]): item
        for item in client.get(
            "/attribute-definitions", params={"limit": 1000}, headers=headers
        ).json()
    }
    # `SUPPLIER_PID` is limited to 32 characters, so it must be a business key —
    # the internal object id (`object:…`, 33 characters) is not a valid PID.
    attributes = product.get("attributes") or {}
    supplier_pid = str(args.pid or attributes.get("code") or attributes.get("sku") or "")
    if not supplier_pid:
        print("error: no supplier PID (use --pid or set a `code`/`sku` attribute)", file=sys.stderr)
        return 2
    if len(supplier_pid) > 32:
        print(
            f"error: supplier PID longer than 32 characters: {supplier_pid}",
            file=sys.stderr,
        )
        return 2
    document, report = build(
        product,
        list(resolved.get("attributes") or []),
        definitions,
        class_code=args.class_code,
        catalog_id=args.catalog_id,
        supplier=args.supplier,
        supplier_pid=supplier_pid,
        buyer=args.buyer,
        language=args.language,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(document, encoding="utf-8")
    print(f"written: {args.out} ({len(document)} bytes)")
    print(f"report: {json.dumps(report, ensure_ascii=False)}")
    ok, message = validate(document)
    print(f"XSD validation: {'valid' if ok else 'INVALID'}")
    if not ok:
        print(message)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
