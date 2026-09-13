"""Spike step 2: export one product as ETIM BMEcat and validate it against the XSD.

Reads a product from the running core (`GET /objects/{id}` plus
`GET /objects/{id}/category-attributes` and the attribute definitions) and writes
an **ETIM BMEcat** document (namespace `…/bmecat/50`, `version="2005"`,
transaction `T_NEW_PRODUCTDATA` — the price-less path, `plan/detail/bmecat-export.md`
§11.3) for one class. The document is validated against
`resources/etim/bmecat_etim_501.xsd` with lxml.

ETIM rules already implemented: every class feature is transmitted (the guideline
requires all of them), a feature without a value is delivered as `-` with
`FVALUE_DETAILS` (`NA` not applicable / `MV` missing value / `UN` unknown), a
range becomes **two** `FVALUE` entries, and `FUNIT` is **not** written (the unit
is fixed inside the ETIM classification).

Known gap (reported, not hidden): alphanumeric features must carry an ETIM value
code (`EV…`); the value-to-feature mapping is missing from both IXF exports, so
the raw text is written and flagged in the report.

Usage (lxml is only needed for validation, hence the ephemeral dependency)::

    uv run --with lxml python scripts/etim_export_spike.py <product-id> \\
        --class-code EC000012 --out .tmp/etim/product.xml
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

CORE_URL = "http://127.0.0.1:8080"
NS = "https://www.etim-international.com/bmecat/50"
XSD = Path("resources/etim/bmecat_etim_501.xsd")
ETIM_VERSION = "10.0"

#: BMEcat catalogue language -> the attribute language code of the value maps.
ATTRIBUTE_LANGUAGES = {"eng": "en", "ger": "de", "dut": "nl", "fre": "fr"}


def escape(text: object) -> str:
    """XML-escapes a text node."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def format_number(value: object) -> str | None:
    """Renders a number without a needless decimal part (`4.0` → `4`)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def element(name: str, text: object, indent: str = "        ") -> str:
    """One XML element — keeps the document assembly lines short."""
    return f"{indent}<{name}>{escape(text)}</{name}>"


def feature_values(value: object, language: str = "en") -> list[str] | None:
    """The `FVALUE` entries for one attribute value (`None` = deliver `-`).

    A multilingual attribute carries a language map: only the **catalogue
    language** counts, so a value that exists in another language only is
    reported as missing (never silently exported in the wrong language).
    """
    if isinstance(value, dict) and "from" in value and "to" in value:
        low, high = format_number(value.get("from")), format_number(value.get("to"))
        if low is None or high is None:
            return None
        return [low, high]
    if isinstance(value, bool):
        return ["true" if value else "false"]
    if isinstance(value, (int, float)):
        rendered = format_number(value)
        return [rendered] if rendered else None
    if isinstance(value, dict):  # a language map -> the catalogue language only
        entry = value.get(language)
        if entry in (None, ""):
            return None
        return feature_values(entry, language)
    text = "" if value is None else str(value).strip()
    return [escape(text)] if text else None


def build_document(
    product: dict,
    features: list[dict],
    definitions: dict[str, dict],
    *,
    class_code: str,
    catalog_id: str,
    supplier: str,
    supplier_pid: str,
    buyer: str = "Meadow buyer",
    language: str = "eng",
) -> tuple[str, dict]:
    """Builds the ETIM BMEcat document plus a report of what was written.

    ``features`` are the resolved class features of the product
    (``GET /objects/{id}/category-attributes``): ``name`` is the ETIM feature
    code (what ``FNAME`` transmits), ``attribute`` the attribute that carries the
    value (a bound **own** attribute, or the code itself when the standard was
    imported as attributes), ``missing`` whether the mandatory value is unset.
    """
    attributes = product.get("attributes") or {}
    name = attributes.get("name") or "unnamed"
    now = datetime.now(UTC)
    lines: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<BMECAT xmlns="{NS}" version="2005">',
        "  <HEADER>",
        "    <GENERATOR_INFO>Meadow PIM (spike)</GENERATOR_INFO>",
        "    <CATALOG>",
        f'      <LANGUAGE default="true">{language}</LANGUAGE>',
        f"      <CATALOG_ID>{escape(catalog_id)}</CATALOG_ID>",
        "      <CATALOG_VERSION>1.0</CATALOG_VERSION>",
        "      <CATALOG_NAME>ETIM export (spike)</CATALOG_NAME>",
        '      <DATETIME type="generation_date">',
        f"        <DATE>{now:%Y-%m-%d}</DATE>",
        "      </DATETIME>",
        "      <CURRENCY>EUR</CURRENCY>",
        "    </CATALOG>",
        "    <BUYER>",
        f"      <BUYER_NAME>{escape(buyer)[:50]}</BUYER_NAME>",
        "    </BUYER>",
        "    <SUPPLIER>",
        f"      <SUPPLIER_NAME>{escape(supplier)}</SUPPLIER_NAME>",
        "    </SUPPLIER>",
        "    <USER_DEFINED_EXTENSIONS>",
        "      <UDX.EDXF.VERSION>5.0</UDX.EDXF.VERSION>",
        "    </USER_DEFINED_EXTENSIONS>",
        "  </HEADER>",
        "  <T_NEW_PRODUCTDATA>",
        '    <PRODUCT mode="new">',
        f"      <SUPPLIER_PID>{escape(supplier_pid)}</SUPPLIER_PID>",
        "      <PRODUCT_DETAILS>",
        f"        <DESCRIPTION_SHORT>{escape(name)[:100]}</DESCRIPTION_SHORT>",
        "      </PRODUCT_DETAILS>",
        "      <PRODUCT_FEATURES>",
        element("REFERENCE_FEATURE_SYSTEM_NAME", f"ETIM-{ETIM_VERSION}"),
        element("REFERENCE_FEATURE_GROUP_ID", class_code),
    ]
    report = {
        "language": language,
        "features": 0,
        "with_value": 0,
        "dash": 0,
        "bound": 0,
        "by_reason": {},
        "text_values": [],
    }
    attribute_language = ATTRIBUTE_LANGUAGES.get(language, "en")
    for feature in features:
        code = str(feature.get("name") or "")
        if not code:
            continue
        attribute = str(feature.get("attribute") or code)
        definition = definitions.get(attribute) or definitions.get(code)
        if definition is None:
            report["by_reason"]["unknown feature"] = (
                report["by_reason"].get("unknown feature", 0) + 1
            )
            continue
        if attribute != code:
            report["bound"] += 1
        value = attributes.get(attribute)
        values = feature_values(value, attribute_language)
        lines.append("        <FEATURE>")
        lines.append(f"          <FNAME>{escape(code)}</FNAME>")
        if values is None:
            lines.append("          <FVALUE>-</FVALUE>")
            reason = "NA" if not feature.get("missing") else "UN"
            lines.append(f"          <FVALUE_DETAILS>{reason}</FVALUE_DETAILS>")
            report["dash"] += 1
            report["by_reason"][reason] = report["by_reason"].get(reason, 0) + 1
        else:
            for entry in values:
                lines.append(f"          <FVALUE>{entry}</FVALUE>")
            report["with_value"] += 1
            if definition.get("type") == "text":
                report["text_values"].append(code)
        lines.append("        </FEATURE>")
        report["features"] += 1
    lines += [
        "      </PRODUCT_FEATURES>",
        "    </PRODUCT>",
        "  </T_NEW_PRODUCTDATA>",
        "</BMECAT>",
    ]
    return "\n".join(lines) + "\n", report


def validate(xml_path: Path) -> tuple[bool, str]:
    """Validates the document against the ETIM BMEcat XSD."""
    from lxml import etree  # noqa: PLC0415 - only needed when validating

    schema = etree.XMLSchema(etree.parse(str(XSD)))
    document = etree.parse(str(xml_path))
    if schema.validate(document):
        return True, "valid"
    return False, "\n".join(
        f"line {error.line}: {error.message}" for error in schema.error_log[:10]
    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("product_id", help="core object id (`object:…`)")
    parser.add_argument("--class-code", required=True, help="ETIM class code (EC…)")
    parser.add_argument("--catalog-id", default="MEADOW-SPIKE")
    parser.add_argument("--supplier", default="Meadow supplier")
    parser.add_argument("--buyer", default="Meadow buyer")
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
    document, report = build_document(
        product,
        list(resolved.get("attributes") or []),
        definitions,
        class_code=args.class_code,
        catalog_id=args.catalog_id,
        supplier=args.supplier,
        supplier_pid=supplier_pid,
        buyer=args.buyer,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(document, encoding="utf-8")
    print(f"written: {args.out} ({len(document)} bytes)")
    print(f"report: {json.dumps(report, ensure_ascii=False)}")
    ok, message = validate(args.out)
    print(f"XSD validation: {'valid' if ok else 'INVALID'}")
    if not ok:
        print(message)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
