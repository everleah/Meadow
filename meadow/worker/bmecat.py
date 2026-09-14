"""BMEcat/ETIM catalogue export (the `bmecat` format of the export job).

Builds one XML document from resolved products: the **ETIM profile** of BMEcat
2005 (namespace `…/bmecat/50`) as verified against the official XSD in
`plan/detail/bmecat-export.md` §11.3/§11.6.

Two transactions of that profile are supported, chosen by the profile's
`transaction`:

* `new_productdata` (default) — the **price-less** path: no prices, no order
  details. It needs no price maintenance and is the V1 behaviour.
* `new_catalog` — the full catalogue delivery. The schema makes
  `PRODUCT_ORDER_DETAILS` (`ORDER_UNIT` + `CONTENT_UNIT`, BMEcat unit codes),
  at least one `PRODUCT_PRICE_DETAILS` and a `USER_DEFINED_EXTENSIONS` block
  **mandatory** per product, so a product without a price is skipped and reported
  (`unpriced`). The price comes from the attribute named in `price_attribute`.

A `variant` may point at its base product (`base_pid` →
`PRODUCT_REFERENCE type="base_product"`), which the schema allows in both
transactions.

The module is **pure**: it maps prepared products to XML and validates the
result; fetching objects, class features and definitions is the caller's job
(`meadow/worker/exports.py`), which keeps the rules here unit-testable.

Rules implemented (all verified against `resources/etim/bmecat_etim_501.xsd`):

* Header order `CATALOG` → `BUYER` → `SUPPLIER` (`BUYER` is mandatory in the ETIM
  profile and must precede `SUPPLIER`); `UDX.EDXF.VERSION` only in the header.
* One `PRODUCT_FEATURES` block per **class** of the product, each with
  `REFERENCE_FEATURE_SYSTEM_NAME` (`ETIM-<version>`) and
  `REFERENCE_FEATURE_GROUP_ID` (the class code). A `class` is the business key of
  an assigned class node — a taxonomy node without one (a shop category) is not a
  class in the sense of this format and never becomes a block.
* Every feature of the class is transmitted (the guideline requires it); a
  feature without a value becomes `-` plus `FVALUE_DETAILS` (`UN` when the
  feature is mandatory, `NA` when it is optional).
* A `range` value (two bounds) becomes **two** `FVALUE` entries.
* No `FUNIT`: the unit is fixed inside the ETIM classification.
* Multilingual attributes carry one value per language; only the catalogue
  language counts (a value in another language is reported as missing, never
  exported in the wrong language).
* `SUPPLIER_PID` is limited to 32 characters by the schema, so it must be a
  business key — an object without one is skipped and reported (or fails the
  export with `strict`).

Known gap, reported rather than hidden: alphanumeric features must carry an ETIM
value code (`EV…`); the mapping is missing from the IXF exports, so the raw text
is written and listed in `report["text_values"]` (§11.4 Punkt 1).
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: Namespace of the ETIM BMEcat 5.0 schema.
NS = "https://www.etim-international.com/bmecat/50"
#: `version` attribute of `<BMECAT>` in the ETIM profile.
BMECAT_VERSION = "2005"
#: Default path of the schema (gitignored licensed artefact; override with `BMECAT_XSD`).
DEFAULT_XSD = Path("resources/etim/bmecat_etim_501.xsd")
#: Object types that become a `PRODUCT` (everything else is skipped and reported).
PRODUCT_TYPES = ("product", "variant")
#: BMEcat catalogue language -> the attribute language code of a value map.
ATTRIBUTE_LANGUAGES = {"eng": "en", "ger": "de", "dut": "nl", "fre": "fr", "spa": "es", "ita": "it"}
#: Fallback `SUPPLIER_PID` attributes, in order.
PID_ATTRIBUTES = ("code", "sku")
#: `SUPPLIER_PID` limit of the schema.
MAX_PID = 32
#: `DESCRIPTION_SHORT` limit of the schema.
MAX_DESCRIPTION = 100
#: `PROD_ID_TO` limit of the schema (a `PRODUCT_REFERENCE` target).
MAX_REFERENCE_ID = 80
#: Transactions of the ETIM profile: `new_productdata` is the price-less path
#: (V1), `new_catalog` the full catalogue delivery — it **requires**
#: `PRODUCT_ORDER_DETAILS`, at least one `PRODUCT_PRICE_DETAILS` and a
#: `USER_DEFINED_EXTENSIONS` block per product (verified against the XSD).
TRANSACTIONS = ("new_productdata", "new_catalog")
#: `price_type` of a `PRODUCT_PRICE` (`typePRICE_TYPE` of the schema).
PRICE_TYPES = ("net_list", "net_customer", "nrp")
#: BMEcat unit code (UN/ECE Rec 20) when the profile names none: `C62` = piece.
DEFAULT_ORDER_UNIT = "C62"


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
    reported as missing (never silently exported in the wrong language). A
    `range` value (`{"from", "to"}`) becomes two entries.
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
    # Raw text: the element writer escapes it exactly once (double escaping
    # would turn `&` into `&amp;amp;` in the document).
    return [text] if text else None


def pid_of(attributes: Mapping[str, Any], profile: Mapping[str, Any]) -> str:
    """The `SUPPLIER_PID` of a product (configured attribute, else `code`/`sku`)."""
    configured = str(profile.get("pid_attribute") or "").strip()
    candidates = (configured, *PID_ATTRIBUTES) if configured else PID_ATTRIBUTES
    for name in candidates:
        value = attributes.get(name)
        if isinstance(value, Mapping):  # a multilingual key
            value = next((entry for entry in value.values() if entry), None)
        text = "" if value is None else str(value).strip()
        if text:
            return text
    return ""


def class_block(entry: object) -> tuple[str, str]:
    """One product class as `(code, reference feature system)`.

    A product's `classes` carries either a **mapping** (`{"code": "EC000012",
    "system": "ETIM-10.0"}` — the shape the export job builds from the installed
    classification standard) or a bare class code, which then falls back to the
    profile's version (`ETIM-<etim_version>`).
    """
    if isinstance(entry, Mapping):
        code = str(entry.get("code") or "").strip()
        return code, str(entry.get("system") or "").strip()
    return str(entry or "").strip(), ""


def amount_of(value: object) -> str | None:
    """The `PRICE_AMOUNT` of an attribute value (`None` = no price maintained).

    A quantified value (`{"value": 12.5, "unit": "EUR"}`) contributes its number;
    a multilingual map counts in the catalogue language like every other value; a
    numeric **string** (`"12.50"`, e.g. from an import) is passed through, because
    the schema validates the value anyway. Anything else is no price.
    """
    if isinstance(value, Mapping):
        if "value" in value:
            return amount_of(value.get("value"))
        for key in ("from", "to"):
            if key in value:
                return amount_of(value.get(key))
        value = value.get("en", next(iter(value.values()), None))
    if isinstance(value, str):
        text = value.strip()
        try:
            float(text)
        except ValueError:
            return None
        return text or None
    return format_number(value)


def display_name(attributes: Mapping[str, Any], language: str, fallback: str) -> str:
    """`DESCRIPTION_SHORT`: the object's label in the catalogue language."""
    raw = attributes.get("name")
    text = ""
    if isinstance(raw, Mapping):
        text = str(raw.get(language) or "").strip()
        if not text:
            text = str(next((entry for entry in raw.values() if entry), "") or "").strip()
    elif raw is not None:
        text = str(raw).strip()
    return text or fallback


def build_document(
    products: Iterable[Mapping[str, Any]],
    definitions: Mapping[str, Mapping[str, Any]],
    profile: Mapping[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Builds the BMEcat document plus a report of what was written.

    Each product is `{"pid", "type", "attributes", "classes", "features"}`:
    `classes` are the classification blocks of the object — either
    `{"code": "EC000012", "system": "ETIM-10.0"}` (the shape the export job builds
    from the installed classification standard, see
    `plan/detail/kategorien.md`) or a bare class code, which then uses the
    profile's version — and `features` the resolved class features
    (`GET /objects/{id}/category-attributes`), each with `name` (the feature code
    an export transmits), `attribute` (the attribute carrying the value), the
    contributing `nodes` (also **codes**, see `_feature_block`) and `required`.
    `base_pid` is optional and only used for a variant.
    """
    profile = dict(profile or {})
    language = str(profile.get("language") or "eng")
    attribute_language = ATTRIBUTE_LANGUAGES.get(language, language.split("-")[0])
    etim_version = str(profile.get("etim_version") or "10.0")
    # Fallback of a class without an installed standard: the profile's version.
    default_system = f"ETIM-{etim_version}"
    transaction = str(profile.get("transaction") or TRANSACTIONS[0]).strip().lower()
    if transaction not in TRANSACTIONS:
        transaction = TRANSACTIONS[0]
    priced = transaction == "new_catalog"
    #: Attribute carrying the price; `new_catalog` transmits no product without one.
    price_attribute = str(profile.get("price_attribute") or "").strip()
    price_type = str(profile.get("price_type") or PRICE_TYPES[0]).strip().lower()
    if price_type not in PRICE_TYPES:
        price_type = PRICE_TYPES[0]
    order_unit = str(profile.get("order_unit") or DEFAULT_ORDER_UNIT).strip()
    content_unit = str(profile.get("content_unit") or order_unit).strip()
    catalog_id = str(profile.get("catalog_id") or "MEADOW").strip() or "MEADOW"
    catalog_name = str(profile.get("catalog_name") or "Meadow catalogue").strip()
    catalog_version = str(profile.get("catalog_version") or "1.0").strip() or "1.0"
    currency = str(profile.get("currency") or "EUR").strip().upper() or "EUR"
    supplier = str(profile.get("supplier") or "Meadow supplier").strip()
    buyer = str(profile.get("buyer") or "Meadow buyer").strip()
    now = datetime.now(UTC)

    report: dict[str, Any] = {
        "language": language,
        "etim_version": etim_version,
        "transaction": transaction,
        "price_type": price_type if priced else "",
        "price_attribute": price_attribute if priced else "",
        "order_unit": order_unit if priced else "",
        "content_unit": content_unit if priced else "",
        "products": 0,
        "skipped": {},
        "classes": 0,
        "features": 0,
        "with_value": 0,
        "dash": 0,
        "bound": 0,
        "prices": 0,
        "references": 0,
        "by_reason": {},
        "text_values": [],
        "missing_required": [],
        "unclassified": [],
        # The `REFERENCE_FEATURE_SYSTEM_NAME` values actually written — one per
        # class block, so a catalogue with two standards shows both.
        "feature_systems": [],
        # Filled by the caller (`meadow/worker/exports.py`): assigned taxonomy
        # nodes without a business key — they cannot be a reference feature group.
        "nodes_without_code": [],
        # Filled by the caller: `node_type` -> system name of the **installed**
        # standards (empty when none is registered — the profile's version is used).
        "standards": {},
        # Products a priced catalogue had to skip (a price is mandatory there).
        "unpriced": [],
        "issues": [],
    }

    lines: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<BMECAT xmlns="{NS}" version="{BMECAT_VERSION}">',
        "  <HEADER>",
        "    <GENERATOR_INFO>Meadow PIM</GENERATOR_INFO>",
        "    <CATALOG>",
        f'      <LANGUAGE default="true">{escape(language)}</LANGUAGE>',
        element("CATALOG_ID", catalog_id, "      "),
        element("CATALOG_VERSION", catalog_version, "      "),
        element("CATALOG_NAME", catalog_name, "      "),
        '      <DATETIME type="generation_date">',
        element("DATE", f"{now:%Y-%m-%d}", "        "),
        "      </DATETIME>",
        element("CURRENCY", currency, "      "),
        "    </CATALOG>",
        "    <BUYER>",
        element("BUYER_NAME", buyer[:50], "      "),
        "    </BUYER>",
        "    <SUPPLIER>",
        element("SUPPLIER_NAME", supplier, "      "),
        "    </SUPPLIER>",
        "    <USER_DEFINED_EXTENSIONS>",
        "      <UDX.EDXF.VERSION>5.0</UDX.EDXF.VERSION>",
        "    </USER_DEFINED_EXTENSIONS>",
        "  </HEADER>",
        f"  <{_tag(transaction)}>",
    ]

    def skip(reason: str) -> None:
        skipped = report["skipped"]
        skipped[reason] = skipped.get(reason, 0) + 1

    for product in sorted(products, key=lambda item: str(item.get("pid") or "")):
        object_type = str(product.get("type") or "")
        if object_type and object_type not in PRODUCT_TYPES:
            skip(f"type {object_type}")
            continue
        pid = str(product.get("pid") or "")
        if not pid:
            skip("no supplier pid")
            report["issues"].append(
                f"{product.get('id')}: no supplier PID (a `code`/`sku` attribute is required)"
            )
            continue
        if len(pid) > MAX_PID:
            skip("supplier pid too long")
            report["issues"].append(f"{pid}: supplier PID longer than {MAX_PID} characters")
            continue
        classes = [
            block
            for block in (class_block(entry) for entry in product.get("classes") or [])
            if block[0]
        ]
        if not classes:
            # The ETIM profile identifies the class; a product without one cannot
            # be transmitted (it stays in the catalogue, just not in this file).
            skip("not classified")
            report["unclassified"].append(pid)
            continue

        attributes = dict(product.get("attributes") or {})
        features = [dict(feature) for feature in product.get("features") or []]
        name = display_name(attributes, attribute_language, pid)
        # `new_catalog` needs a price on every product: `PRODUCT_PRICE_DETAILS` is
        # mandatory in that transaction, so a product without one is reported.
        amount = amount_of(attributes.get(price_attribute)) if priced and price_attribute else None
        if priced and amount is None:
            skip("no price")
            report["unpriced"].append(pid)
            continue
        lines.append('    <PRODUCT mode="new">')
        lines.append(element("SUPPLIER_PID", pid, "      "))
        lines.append("      <PRODUCT_DETAILS>")
        lines.append(element("DESCRIPTION_SHORT", name[:MAX_DESCRIPTION], "        "))
        lines.append("      </PRODUCT_DETAILS>")
        for code, system in classes:
            block, count = _feature_block(
                code,
                [code for code, _ in classes],
                system or default_system,
                pid,
                features,
                attributes,
                definitions,
                attribute_language,
                report,
            )
            lines.extend(block)
            report["classes"] += 1
            report["features"] += count
        if priced:
            lines.extend(_order_details(order_unit, content_unit))
            lines.extend(_price_details(amount, currency, price_type, now))
            report["prices"] += 1
            # The transaction fixes the position of the UDX block: mandatory after
            # the prices in `T_NEW_CATALOG` (all its children are optional, so an
            # empty block is valid).
            lines.append(_product_udx())
        base = str(product.get("base_pid") or "").strip()
        if base and base != pid:
            lines.append('      <PRODUCT_REFERENCE type="base_product">')
            lines.append(element("PROD_ID_TO", base[:MAX_REFERENCE_ID], "        "))
            lines.append("      </PRODUCT_REFERENCE>")
            report["references"] += 1
        lines.append("    </PRODUCT>")
        report["products"] += 1

    lines += [
        f"  </{_tag(transaction)}>",
        "</BMECAT>",
    ]
    return "\n".join(lines) + "\n", report


def _tag(transaction: str) -> str:
    """The XML tag of a transaction (`new_catalog` → `T_NEW_CATALOG`)."""
    return f"T_{transaction.upper()}"


def _order_details(order_unit: str, content_unit: str) -> list[str]:
    """`PRODUCT_ORDER_DETAILS` — mandatory in `T_NEW_CATALOG`.

    `ORDER_UNIT` and `CONTENT_UNIT` carry **BMEcat unit codes** (UN/ECE Rec 20,
    e.g. `C62` = piece), not Meadow unit symbols: the schema enumerates them, so a
    wrong code fails the XSD validation instead of shipping silently.
    """
    return [
        "      <PRODUCT_ORDER_DETAILS>",
        element("ORDER_UNIT", order_unit, "        "),
        element("CONTENT_UNIT", content_unit, "        "),
        "      </PRODUCT_ORDER_DETAILS>",
    ]


def _price_details(amount: str, currency: str, price_type: str, now: datetime) -> list[str]:
    """`PRODUCT_PRICE_DETAILS` — mandatory in `T_NEW_CATALOG`.

    The `DATETIME` is mandatory in the schema and becomes the generation date as
    `valid_start_date`; `PRICE_CURRENCY` repeats the catalogue currency so a
    consumer reading only the price block needs no fallback.
    """
    return [
        "      <PRODUCT_PRICE_DETAILS>",
        '        <DATETIME type="valid_start_date">',
        element("DATE", f"{now:%Y-%m-%d}", "          "),
        "        </DATETIME>",
        f'        <PRODUCT_PRICE price_type="{price_type}">',
        element("PRICE_AMOUNT", amount, "          "),
        element("PRICE_CURRENCY", currency, "          "),
        "        </PRODUCT_PRICE>",
        "      </PRODUCT_PRICE_DETAILS>",
    ]


def _product_udx() -> str:
    """The `USER_DEFINED_EXTENSIONS` block mandatory in `T_NEW_CATALOG`.

    Its children are all optional, so an empty block satisfies the sequence — and
    the element must **not** carry `UDX.EDXF.VERSION` (that belongs to the header).
    """
    return "      <USER_DEFINED_EXTENSIONS/>"


def _feature_block(
    class_code: str,
    class_codes: Sequence[str],
    system: str,
    pid: str,
    features: Sequence[Mapping[str, Any]],
    attributes: Mapping[str, Any],
    definitions: Mapping[str, Mapping[str, Any]],
    language: str,
    report: dict[str, Any],
) -> tuple[list[str], int]:
    """One `PRODUCT_FEATURES` block per class.

    A feature belongs to the class that defines it (`nodes`, **class codes**); a
    feature whose defining classes are all **outside** the product's own classes
    was inherited from an ancestor and belongs to every block (it is part of each
    class's effective feature set). A feature of *another* assigned class is not
    repeated here.

    `system` is the `REFERENCE_FEATURE_SYSTEM_NAME` of the block — the installed
    classification standard of the class (`ETIM-10.0`), not a free-text guess.
    """
    assigned = {str(code) for code in class_codes}
    if system not in report["feature_systems"]:
        report["feature_systems"].append(system)
    lines = [
        "      <PRODUCT_FEATURES>",
        element("REFERENCE_FEATURE_SYSTEM_NAME", system, "        "),
        element("REFERENCE_FEATURE_GROUP_ID", class_code, "        "),
    ]
    count = 0
    for feature in sorted(features, key=lambda item: str(item.get("name") or "")):
        nodes = {str(node) for node in feature.get("nodes") or []}
        if nodes and class_code not in nodes and nodes & assigned:
            continue
        code = str(feature.get("name") or "")
        if not code:
            continue
        carrier = str(feature.get("attribute") or code)
        value = attributes.get(carrier)
        values = feature_values(value, language)
        lines.append("        <FEATURE>")
        lines.append(element("FNAME", code, "          "))
        if values is None:
            # `UN` = a mandatory feature without a value, `NA` = an optional one
            # the supplier does not maintain.
            reason = "UN" if feature.get("required") else "NA"
            lines.append("          <FVALUE>-</FVALUE>")
            lines.append(element("FVALUE_DETAILS", reason, "          "))
            report["dash"] += 1
            report["by_reason"][reason] = report["by_reason"].get(reason, 0) + 1
            if feature.get("required"):
                report["missing_required"].append(
                    {"pid": pid, "feature": code, "attribute": carrier}
                )
        else:
            for entry in values:
                lines.append(element("FVALUE", entry, "          "))
            report["with_value"] += 1
            if carrier != code:
                report["bound"] += 1
            definition = definitions.get(carrier) or definitions.get(code) or {}
            if str(definition.get("type") or "") in ("text", "url"):
                report["text_values"].append(code)
        lines.append("        </FEATURE>")
        count += 1
    lines.append("      </PRODUCT_FEATURES>")
    return lines, count


def schema_path() -> Path | None:
    """The XSD to validate against (`BMECAT_XSD`, else the resolved default)."""
    configured = os.environ.get("BMECAT_XSD")
    path = Path(configured) if configured else DEFAULT_XSD
    return path if path.is_file() else None


def validate(xml: str, path: Path | None = None) -> dict[str, Any]:
    """Validates the document against the ETIM XSD (best effort).

    `lxml` and the licensed schema are not guaranteed to be present, so a missing
    schema or library is **reported** (`validated: false` + `reason`) instead of
    failing the export — the same honesty rule as the unit checks elsewhere.
    """
    schema_file = path or schema_path()
    if schema_file is None:
        return {"validated": False, "reason": "schema not found (set BMECAT_XSD)"}
    try:
        from lxml import etree  # noqa: PLC0415 - optional dependency
    except ImportError as exc:  # pragma: no cover - depends on the environment
        return {"validated": False, "reason": f"lxml not installed ({exc})"}
    try:
        schema = etree.XMLSchema(etree.parse(str(schema_file)))
        document = etree.fromstring(xml.encode("utf-8"))
    except etree.XMLSyntaxError as exc:
        return {"validated": False, "valid": False, "errors": [str(exc)]}
    if schema.validate(document):
        return {"validated": True, "valid": True, "schema": str(schema_file)}
    return {
        "validated": True,
        "valid": False,
        "schema": str(schema_file),
        "errors": [f"line {error.line}: {error.message}" for error in schema.error_log[:10]],
    }


__all__ = [
    "ATTRIBUTE_LANGUAGES",
    "DEFAULT_XSD",
    "MAX_PID",
    "NS",
    "PRODUCT_TYPES",
    "build_document",
    "display_name",
    "element",
    "escape",
    "feature_values",
    "format_number",
    "pid_of",
    "schema_path",
    "validate",
]
