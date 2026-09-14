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
from typing import Any, NamedTuple

#: Namespace of the ETIM BMEcat 5.0 schema.
NS = "https://www.etim-international.com/bmecat/50"
#: `version` attribute of `<BMECAT>` in the ETIM profile.
BMECAT_VERSION = "2005"
#: Default path of the schema (gitignored licensed artefact; override with `BMECAT_XSD`).
DEFAULT_XSD = Path("resources/etim/bmecat_etim_501.xsd")
#: The BMEcat versions this module writes (`plan/detail/bmecat-export.md` §11.12).
VERSIONS = ("2005", "1.2")
#: Namespace per version and schema variant.
#:
#: A **1.2** document satisfies *either* the DTD *or* the XSD variant: the
#: namespace is `#FIXED` in both artefacts (`§11.8`). The default is the classic
#: DTD variant (Rev. A); the XSD variant exists for a receiver that validates
#: with it (it carries identity constraints, which some parsers reject).
NAMESPACES = {
    ("2005", "xsd"): NS,
    ("1.2", "dtd"): "http://www.bmecat.org/bmecat/1.2/bmecat_new_catalog",
    ("1.2", "xsd"): "http://www.bmecat.org/XMLSchema/1.2/bmecat_new_catalog",
}
#: Schema artefact per version and variant (licensed, therefore gitignored).
SCHEMA_FILES = {
    ("2005", "xsd"): DEFAULT_XSD,
    ("1.2", "dtd"): Path("resources/bmecat/bmecat_new_catalog_1_2.dtd"),
    ("1.2", "xsd"): Path("resources/bmecat/bmecat_new_catalog_1_2.xsd"),
}
#: Object types that become a `PRODUCT` (everything else is skipped and reported).
PRODUCT_TYPES = ("product", "variant")
#: BMEcat catalogue language -> the attribute language code of a value map.
ATTRIBUTE_LANGUAGES = {"eng": "en", "ger": "de", "dut": "nl", "fre": "fr", "spa": "es", "ita": "it"}
#: Fallback `SUPPLIER_PID` attributes, in order.
PID_ATTRIBUTES = ("code", "sku")
#: `SUPPLIER_PID` limit of the schema.
MAX_PID = 32
#: `DESCRIPTION_SHORT` limit used for the ETIM profile (the schema allows 150).
MAX_DESCRIPTION = 100
#: `DESCRIPTION_SHORT` limit of the **1.2** schema (80 — much tighter than 2005).
MAX_DESCRIPTION_12 = 80
#: `SUPPLIER_NAME`/`BUYER_NAME` limit of both schemas.
MAX_NAME = 50
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


def _context(profile: Mapping[str, Any]) -> dict[str, Any]:
    """The profile values both document builders share, resolved and defaulted.

    One place decides what a missing or unknown value means (version, schema
    variant, transaction, price source, units, header fields), so the two
    builders cannot drift apart.
    """
    version = str(profile.get("version") or VERSIONS[0]).strip()
    if version not in VERSIONS:
        version = VERSIONS[0]
    variant = str(profile.get("schema_variant") or "dtd").strip().lower()
    if variant not in ("dtd", "xsd"):
        variant = "dtd"
    language = str(profile.get("language") or "eng")
    etim_version = str(profile.get("etim_version") or "10.0")
    transaction = str(profile.get("transaction") or TRANSACTIONS[0]).strip().lower()
    if transaction not in TRANSACTIONS:
        transaction = TRANSACTIONS[0]
    price_type = str(profile.get("price_type") or PRICE_TYPES[0]).strip().lower()
    if price_type not in PRICE_TYPES:
        price_type = PRICE_TYPES[0]
    order_unit = str(profile.get("order_unit") or DEFAULT_ORDER_UNIT).strip()
    return {
        "version": version,
        "variant": variant,
        "namespace": NAMESPACES.get((version, variant), NS),
        "language": language,
        "attribute_language": ATTRIBUTE_LANGUAGES.get(language, language.split("-")[0]),
        "etim_version": etim_version,
        # Fallback of a class without an installed standard: the profile's version.
        "default_system": f"ETIM-{etim_version}",
        "transaction": transaction,
        # BMEcat 1.2 knows no price-less transaction: order details and at least one
        # price block are mandatory there (`ARTICLE_PRICE_DETAILS+`).
        "priced": transaction == "new_catalog" or version == "1.2",
        "price_attribute": str(profile.get("price_attribute") or "").strip(),
        "price_type": price_type,
        "order_unit": order_unit,
        "content_unit": str(profile.get("content_unit") or order_unit).strip(),
        "catalog_id": str(profile.get("catalog_id") or "MEADOW").strip() or "MEADOW",
        "catalog_name": str(profile.get("catalog_name") or "Meadow catalogue").strip(),
        "catalog_version": str(profile.get("catalog_version") or "1.0").strip() or "1.0",
        "currency": str(profile.get("currency") or "EUR").strip().upper() or "EUR",
        "supplier": str(profile.get("supplier") or "Meadow supplier").strip(),
        "buyer": str(profile.get("buyer") or "Meadow buyer").strip(),
        "now": datetime.now(UTC),
    }


def _new_report(context: Mapping[str, Any]) -> dict[str, Any]:
    """The report every document builder fills (same shape for both versions)."""
    priced = bool(context["priced"])
    return {
        "version": context["version"],
        "variant": context["variant"],
        "language": context["language"],
        "etim_version": context["etim_version"],
        "transaction": context["transaction"] if context["version"] != "1.2" else "new_catalog",
        "price_type": context["price_type"] if priced else "",
        "price_attribute": context["price_attribute"] if priced else "",
        "order_unit": context["order_unit"] if priced else "",
        "content_unit": context["content_unit"] if priced else "",
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


def _skip(report: dict[str, Any], reason: str) -> None:
    """Counts one skipped product under a reason."""
    skipped = report["skipped"]
    skipped[reason] = skipped.get(reason, 0) + 1


class _Entry(NamedTuple):
    """A product that passed the checks, ready to be written as one element."""

    pid: str
    name: str
    amount: str
    classes: list[tuple[str, str]]
    attributes: dict[str, Any]
    features: list[dict[str, Any]]
    base_pid: str


def _prepare(
    product: Mapping[str, Any], context: Mapping[str, Any], report: dict[str, Any]
) -> _Entry | None:
    """Validates one product and reports why it cannot be written (`None` = skip).

    Shared by both document builders, so the skip rules cannot drift apart: the
    object type must be a product, the `SUPPLIER_PID` must exist and fit the
    schema (32 characters), at least one class must carry a business key and — in
    a priced delivery — a price must be maintained.
    """
    object_type = str(product.get("type") or "")
    if object_type and object_type not in PRODUCT_TYPES:
        _skip(report, f"type {object_type}")
        return None
    pid = str(product.get("pid") or "")
    if not pid:
        _skip(report, "no supplier pid")
        report["issues"].append(
            f"{product.get('id')}: no supplier PID (a `code`/`sku` attribute is required)"
        )
        return None
    if len(pid) > MAX_PID:
        _skip(report, "supplier pid too long")
        report["issues"].append(f"{pid}: supplier PID longer than {MAX_PID} characters")
        return None
    classes = [
        block
        for block in (class_block(entry) for entry in product.get("classes") or [])
        if block[0]
    ]
    if not classes:
        # The ETIM profile identifies the class; a product without one cannot be
        # transmitted (it stays in the catalogue, just not in this file).
        _skip(report, "not classified")
        report["unclassified"].append(pid)
        return None
    attributes = dict(product.get("attributes") or {})
    price_attribute = str(context["price_attribute"])
    amount = (
        amount_of(attributes.get(price_attribute))
        if context["priced"] and price_attribute
        else None
    )
    if context["priced"] and amount is None:
        # Order details and a price block are mandatory in the priced transactions.
        _skip(report, "no price")
        report["unpriced"].append(pid)
        return None
    return _Entry(
        pid=pid,
        name=display_name(attributes, str(context["attribute_language"]), pid),
        amount=amount or "",
        classes=classes,
        attributes=attributes,
        features=[dict(feature) for feature in product.get("features") or []],
        base_pid=str(product.get("base_pid") or "").strip(),
    )


def _build_12(
    products: Iterable[Mapping[str, Any]],
    definitions: Mapping[str, Mapping[str, Any]],
    context: Mapping[str, Any],
    report: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    """Builds a **BMEcat 1.2** document (`T_NEW_CATALOG`, `ARTICLE`).

    Verified against `bmecat_new_catalog_1_2.dtd` (+ the included
    `bmecat_base_1_2.dtd`, §11.8/§11.12):

    * `HEADER (GENERATOR_INFO?, CATALOG, BUYER?, AGREEMENT*, SUPPLIER, USER_DEFINED_EXTENSIONS?)`
      with `CATALOG (LANGUAGE, CATALOG_ID, CATALOG_VERSION, CATALOG_NAME?, DATETIME?, …)`
      — `LANGUAGE` exactly once, so 1.2 is **one language per document**.
    * `T_NEW_CATALOG (…, ARTICLE*, …)` with
      `ARTICLE (SUPPLIER_AID, ARTICLE_DETAILS, ARTICLE_FEATURES*,
      ARTICLE_ORDER_DETAILS, ARTICLE_PRICE_DETAILS+, …)`.
      `ARTICLE_ORDER_DETAILS` and **at least one** `ARTICLE_PRICE_DETAILS` are
      mandatory (a price-less 1.2 delivery does not exist), so a product without a
      price is skipped and reported like in the priced 2005 transaction.
    * `ARTICLE_FEATURES (REFERENCE_FEATURE_SYSTEM_NAME?, …, FEATURE*)` — the same
      feature content as 2005, and `FEATURE` is optional there (the ETIM profile
      makes the block mandatory); the feature children are identical.

    Two 1.2 limits differ from the ETIM profile and are applied here:
    `DESCRIPTION_SHORT` is **80** characters (2005: 150) and `ARTICLE_REFERENCE`
    knows no `base_product` qualifier (see below).
    """
    now = context["now"]
    lines: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<BMECAT xmlns="{context["namespace"]}" version="1.2">',
        "  <HEADER>",
        "    <GENERATOR_INFO>Meadow PIM</GENERATOR_INFO>",
        "    <CATALOG>",
        element("LANGUAGE", context["language"], "      "),
        element("CATALOG_ID", context["catalog_id"], "      "),
        element("CATALOG_VERSION", context["catalog_version"], "      "),
        element("CATALOG_NAME", context["catalog_name"], "      "),
        '      <DATETIME type="generation_date">',
        element("DATE", f"{now:%Y-%m-%d}", "        "),
        "      </DATETIME>",
        element("CURRENCY", context["currency"], "      "),
        "    </CATALOG>",
        "    <BUYER>",
        element("BUYER_NAME", str(context["buyer"])[:MAX_NAME], "      "),
        "    </BUYER>",
        "    <SUPPLIER>",
        element("SUPPLIER_NAME", str(context["supplier"])[:MAX_NAME], "      "),
        "    </SUPPLIER>",
        "  </HEADER>",
        "  <T_NEW_CATALOG>",
    ]

    for product in sorted(products, key=lambda item: str(item.get("pid") or "")):
        entry = _prepare(product, context, report)
        if entry is None:
            continue
        lines.append('    <ARTICLE mode="new">')
        lines.append(element("SUPPLIER_AID", entry.pid, "      "))
        lines.append("      <ARTICLE_DETAILS>")
        lines.append(element("DESCRIPTION_SHORT", entry.name[:MAX_DESCRIPTION_12], "        "))
        lines.append("      </ARTICLE_DETAILS>")
        codes = [code for code, _ in entry.classes]
        for code, system in entry.classes:
            block, count = _feature_block(
                code,
                codes,
                system or str(context["default_system"]),
                entry.pid,
                entry.features,
                entry.attributes,
                definitions,
                str(context["attribute_language"]),
                report,
                wrapper="ARTICLE_FEATURES",
            )
            lines.extend(block)
            report["classes"] += 1
            report["features"] += count
        lines.extend(
            _order_details(
                str(context["order_unit"]),
                str(context["content_unit"]),
                wrapper="ARTICLE_ORDER_DETAILS",
            )
        )
        lines.extend(
            _price_details(
                entry.amount,
                str(context["currency"]),
                str(context["price_type"]),
                now,
                wrapper="ARTICLE_PRICE_DETAILS",
                price="ARTICLE_PRICE",
            )
        )
        report["prices"] += 1
        if entry.base_pid and entry.base_pid != entry.pid:
            # 1.2 has no `base_product` reference: `REFERENCE_QUALIFIERS` lists
            # `sparepart|similar|followup|mandatory|select|others|accessories|
            # diff_orderunit|consists_of` (DTD and XSD agree), and 1.2 expresses a
            # variant through `FEATURE/VARIANTS` — which this export does not write
            # yet. A wrong qualifier would corrupt the meaning, so the link is
            # dropped **and reported** instead.
            report["issues"].append(
                f"{entry.pid}: BMEcat 1.2 cannot reference a base product "
                f"(`{entry.base_pid}`) — `base_product` is a 2005 qualifier"
            )
        lines.append("    </ARTICLE>")
        report["products"] += 1

    lines += ["  </T_NEW_CATALOG>", "</BMECAT>"]
    return "\n".join(lines) + "\n", report


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
    context = _context(profile)
    report = _new_report(context)
    if context["version"] == "1.2":
        # BMEcat 1.2 has its own element vocabulary (`ARTICLE`, §11.12).
        return _build_12(products, definitions, context, report)

    # --- BMEcat 2005, the ETIM profile ---------------------------------------
    language = context["language"]
    attribute_language = context["attribute_language"]
    default_system = context["default_system"]
    transaction = context["transaction"]
    priced = context["priced"]
    price_type = context["price_type"]
    order_unit = context["order_unit"]
    content_unit = context["content_unit"]
    catalog_id = context["catalog_id"]
    catalog_name = context["catalog_name"]
    catalog_version = context["catalog_version"]
    currency = context["currency"]
    supplier = context["supplier"]
    buyer = context["buyer"]
    now = context["now"]

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
        element("SUPPLIER_NAME", supplier[:MAX_NAME], "    "),
        "    </SUPPLIER>",
        "    <USER_DEFINED_EXTENSIONS>",
        "      <UDX.EDXF.VERSION>5.0</UDX.EDXF.VERSION>",
        "    </USER_DEFINED_EXTENSIONS>",
        "  </HEADER>",
        f"  <{_tag(transaction)}>",
    ]

    for product in sorted(products, key=lambda item: str(item.get("pid") or "")):
        entry = _prepare(product, context, report)
        if entry is None:
            continue
        pid = entry.pid
        attributes = entry.attributes
        lines.append('    <PRODUCT mode="new">')
        lines.append(element("SUPPLIER_PID", pid, "      "))
        lines.append("      <PRODUCT_DETAILS>")
        lines.append(element("DESCRIPTION_SHORT", entry.name[:MAX_DESCRIPTION], "        "))
        lines.append("      </PRODUCT_DETAILS>")
        for code, system in entry.classes:
            block, count = _feature_block(
                code,
                [code for code, _ in entry.classes],
                system or default_system,
                pid,
                entry.features,
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
            lines.extend(_price_details(entry.amount, currency, price_type, now))
            report["prices"] += 1
            # The transaction fixes the position of the UDX block: mandatory after
            # the prices in `T_NEW_CATALOG` (all its children are optional, so an
            # empty block is valid).
            lines.append(_product_udx())
        if entry.base_pid and entry.base_pid != pid:
            lines.append('      <PRODUCT_REFERENCE type="base_product">')
            lines.append(element("PROD_ID_TO", entry.base_pid[:MAX_REFERENCE_ID], "        "))
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


def _order_details(
    order_unit: str, content_unit: str, wrapper: str = "PRODUCT_ORDER_DETAILS"
) -> list[str]:
    """The order details block — mandatory in the priced transactions.

    `ORDER_UNIT` and `CONTENT_UNIT` carry **BMEcat unit codes** (UN/ECE Rec 20,
    e.g. `C62` = piece), not Meadow unit symbols: in the ETIM profile the schema
    enumerates them, and a wrong code fails the XSD validation instead of shipping
    silently. (The 1.2 **DTD** is laxer here — `#PCDATA` — which is why the plan
    warns that a 1.2 XSD validation is stricter than the DTD one.)
    """
    return [
        f"      <{wrapper}>",
        element("ORDER_UNIT", order_unit, "        "),
        element("CONTENT_UNIT", content_unit, "        "),
        f"      </{wrapper}>",
    ]


def _price_details(
    amount: str,
    currency: str,
    price_type: str,
    now: datetime,
    wrapper: str = "PRODUCT_PRICE_DETAILS",
    price: str = "PRODUCT_PRICE",
) -> list[str]:
    """The price block — mandatory in the priced transactions.

    The `DATETIME` becomes the generation date as `valid_start_date` (mandatory in
    2005, optional in 1.2, but written in both); `PRICE_CURRENCY` repeats the
    catalogue currency so a consumer reading only the price block needs no fallback.
    """
    return [
        f"      <{wrapper}>",
        '        <DATETIME type="valid_start_date">',
        element("DATE", f"{now:%Y-%m-%d}", "          "),
        "        </DATETIME>",
        f'        <{price} price_type="{price_type}">',
        element("PRICE_AMOUNT", amount, "          "),
        element("PRICE_CURRENCY", currency, "          "),
        f"        </{price}>",
        f"      </{wrapper}>",
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
    wrapper: str = "PRODUCT_FEATURES",
) -> tuple[list[str], int]:
    """One class block per class (`PRODUCT_FEATURES` in 2005, `ARTICLE_FEATURES` in 1.2).

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
        f"      <{wrapper}>",
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
    lines.append(f"      </{wrapper}>")
    return lines, count


def schema_path(version: str = BMECAT_VERSION, variant: str = "dtd") -> Path | None:
    """The schema to validate against (`BMECAT_XSD`, else the version's artefact).

    `BMECAT_XSD` overrides the resolved default and may point at an **XSD or a
    DTD** — the file name decides how it is used (see `validate`).
    """
    configured = os.environ.get("BMECAT_XSD")
    path = Path(configured) if configured else SCHEMA_FILES.get((version, variant), DEFAULT_XSD)
    return path if path.is_file() else None


def validate(xml: str, path: Path | None = None) -> dict[str, Any]:
    """Validates the document against the BMEcat schema of its version (best effort).

    An XSD is validated with `XMLSchema`, a `.dtd` with `DTD` (BMEcat 1.2 ships
    both variants). `lxml` and the licensed schema are not guaranteed to be
    present, so a missing schema or library is **reported** (`validated: false` +
    `reason`) instead of failing the export — the same honesty rule as the unit
    checks elsewhere.
    """
    schema_file = path or schema_path()
    if schema_file is None:
        return {"validated": False, "reason": "schema not found (set BMECAT_XSD)"}
    try:
        from lxml import etree  # noqa: PLC0415 - optional dependency
    except ImportError as exc:  # pragma: no cover - depends on the environment
        return {"validated": False, "reason": f"lxml not installed ({exc})"}
    try:
        document = etree.fromstring(xml.encode("utf-8"))
        if schema_file.suffix.lower() == ".dtd":
            schema = etree.DTD(str(schema_file))
        else:
            schema = etree.XMLSchema(etree.parse(str(schema_file)))
    except (etree.XMLSyntaxError, etree.DTDParseError) as exc:
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
    "BMECAT_VERSION",
    "DEFAULT_XSD",
    "MAX_PID",
    "NAMESPACES",
    "NS",
    "PRICE_TYPES",
    "PRODUCT_TYPES",
    "SCHEMA_FILES",
    "TRANSACTIONS",
    "VERSIONS",
    "amount_of",
    "build_document",
    "class_block",
    "display_name",
    "element",
    "escape",
    "feature_values",
    "format_number",
    "pid_of",
    "schema_path",
    "validate",
]
