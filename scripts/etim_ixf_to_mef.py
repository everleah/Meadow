"""Convert an ETIM IXF export into Meadow MCF/MEF documents (spike phase 1).

The converter reads an IXF document
(`ETIM-10.0-ALL-SECTORS-IXF-NO-CHANGE-CODES-METRIC-*.xml`, see
`plan/detail/bmecat-export.md` §11.4) and writes three importable files for a
**selection** of classes:

* ``model.mcf.json`` — the model: the taxonomy node type for the classes, one
  attribute group per referenced ETIM *feature group* (``EFG…``, the ETIM
  "Feature Grouping") and one attribute definition per referenced ETIM feature
  (name = the stable feature code ``EF…``, label = the IXF translations, type
  from the IXF feature type, ``group_ids`` = its feature group).
* ``classes.mef.json`` — the class nodes themselves (data), keyed by ``EC…``.
* ``features.mcf.json`` — the class-feature rows (``category_attributes``), i.e.
  which feature belongs to which class, in which order (``OrderNumber``).

Import order (the feature section resolves classes by their business key):
``model.mcf.json`` → ``classes.mef.json`` → ``features.mcf.json``.

Units are **not** applied by default: the IXF carries ETIM unit codes
(``EU…``) whose abbreviation has to be mapped to a Meadow unit deliberately, so
the report lists every unit the selection needs (``--with-units`` writes the
abbreviation as the symbol for the units Meadow actually has, taken from a
``--units-file`` with the ``GET /units`` payload).

Usage::

    uv run python scripts/etim_ixf_to_mef.py resources/etim/<export>.xml \\
        --limit 10 --out-dir .tmp/etim
"""

from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

#: IXF language tag -> Meadow language code (base language; the IXF has e.g.
#: `nl-BE`, Meadow labels are keyed by `nl`).
LANGUAGE_CODES = {"EN": "en", "DE": "de", "NL-BE": "nl", "FR-BE": "fr", "FR": "fr", "NL": "nl"}

#: IXF feature type -> Meadow data type (`plan/detail/datenmodell.md`).
TYPE_MAP = {"Logical": "boolean", "Numeric": "number", "Alphanumeric": "text", "Range": "range"}

#: Standard-bundle envelope (`plan/detail/bmecat-export.md` §11.9): a *package*
#: carrying a classification standard's model **and** data, installable in the
#: order of `documents` — deliberately **not** seeded system data (a system runs
#: without it, adding it is one command).
BUNDLE_FORMAT = "meadow-standard-bundle"
BUNDLE_VERSION = "1.0"


def local(tag: str) -> str:
    """Strips the `{namespace}` prefix from an ElementTree tag."""
    return tag.rsplit("}", 1)[-1]


def language_code(raw: str | None) -> str:
    """Maps an IXF language tag to a Meadow language code (lowercase fallback)."""
    tag = (raw or "").strip()
    return LANGUAGE_CODES.get(tag, tag.split("-")[0].lower())


def attribute_type(etim_type: str) -> str | None:
    """Meadow data type of an IXF feature type (`None` = unknown)."""
    return TYPE_MAP.get(etim_type.strip())


@dataclass
class IxfFeature:
    """One entry of the `Features` section."""

    code: str
    etim_type: str
    group_code: str = ""
    labels: dict[str, str] = field(default_factory=dict)


@dataclass
class IxfUnit:
    """One entry of the `Units` section."""

    code: str
    symbol: str = ""
    labels: dict[str, str] = field(default_factory=dict)


@dataclass
class ClassFeature:
    """One `Feature` of a class: the feature plus its default unit and order."""

    code: str
    unit_code: str = ""
    position: int = 0


@dataclass
class IxfClass:
    """One selected entry of the `Classes` section."""

    code: str
    version: str = ""
    status: str = ""
    group_code: str = ""
    labels: dict[str, str] = field(default_factory=dict)
    synonyms: dict[str, list[str]] = field(default_factory=dict)
    features: list[ClassFeature] = field(default_factory=list)


@dataclass
class Selection:
    """The parsed subset plus the vocabulary it references."""

    classes: list[IxfClass] = field(default_factory=list)
    features: dict[str, IxfFeature] = field(default_factory=dict)
    units: dict[str, IxfUnit] = field(default_factory=dict)
    #: `FeatureGroups` (`EFG…`) — the ETIM *feature* grouping, which maps onto
    #: Meadow's `attribute_group` (one section per group in the product form).
    feature_groups: dict[str, dict[str, str]] = field(default_factory=dict)
    #: `Groups` (`EG…`) — the ETIM *class* grouping (organisational, 159 items).
    class_groups: dict[str, dict[str, str]] = field(default_factory=dict)
    #: `Header/Publication`: name, release number and date of the IXF export.
    publication: dict[str, str] = field(default_factory=dict)
    seen_classes: int = 0
    unknown_types: Counter[str] = field(default_factory=Counter)


def _child(node: ET.Element, path: str) -> ET.Element | None:
    """Finds the first descendant along a `/`-separated **local-name** path.

    ElementTree paths need the namespace, the IXF namespace is fixed but this
    keeps the reader independent of it (and of the IXF version).
    """
    current: ET.Element | None = node
    for name in path.split("/"):
        if current is None:
            return None
        current = next((item for item in current if local(item.tag) == name), None)
    return current


def _children(node: ET.Element, path: str) -> list[ET.Element]:
    """All children matching the last segment of a local-name path.

    `_child` returns the *first* match (a single element), so the list variant
    resolves the parent path first and then filters its children.
    """
    head, _, last = path.rpartition("/")
    parent = _child(node, head) if head else node
    if parent is None:
        return []
    return [item for item in parent if local(item.tag) == last]


def _text(node: ET.Element, path: str) -> str:
    """Trimmed text of the first match of a local-name path (empty when absent)."""
    found = _child(node, path)
    return (found.text or "").strip() if found is not None else ""


def _translations(node: ET.Element) -> tuple[dict, dict]:
    """Reads `Translations` -> (descriptions, synonyms) keyed by language code."""
    labels: dict[str, str] = {}
    synonyms_by_language: dict[str, list[str]] = {}
    for translation in _children(node, "Translations/Translation"):
        language = language_code(translation.get("language"))
        for description in _children(translation, "Description"):
            if description.text:
                labels[language] = description.text.strip()
        names = [
            item.text.strip()
            for item in _children(translation, "Synonyms/Synonym")
            if item.text and item.text.strip()
        ]
        if names:
            synonyms_by_language[language] = names
    return labels, synonyms_by_language


def read_ixf(
    path: Path,
    *,
    classes: set[str] | None = None,
    group: str | None = None,
    limit: int | None = None,
) -> Selection:
    """Streams the IXF document and collects the selected classes + vocabulary.

    The document order (`Units`, `Features`, `…`, `Classes`) means one pass is
    enough: features are known by the time the classes are read.
    """
    selection = Selection()
    stack: list[str] = []
    wanted = classes or set()

    for event, elem in ET.iterparse(path, events=("start", "end")):
        tag = local(elem.tag)
        if event == "start":
            stack.append(tag)
            continue

        depth = len(stack) - 1
        section = stack[1] if len(stack) > 1 else ""
        if depth == 2:  # the item element itself, e.g. `Unit` inside `Units`
            if section == "Header":
                selection.publication = {
                    "name": _text(elem, "Name"),
                    "number": _text(elem, "Number"),
                    "date": _text(elem, "Date"),
                }
            elif section == "Units":
                labels, _ = _translations(elem)
                code = _text(elem, "Code")
                if code:
                    selection.units[code] = IxfUnit(
                        code=code,
                        symbol=_text(elem, "Translations/Translation/Abbreviation"),
                        labels=labels,
                    )
            elif section == "Features":
                labels, _ = _translations(elem)
                code = _text(elem, "Code")
                if code:
                    selection.features[code] = IxfFeature(
                        code=code,
                        etim_type=_text(elem, "Type"),
                        group_code=_text(elem, "FeatureGroupCode"),
                        labels=labels,
                    )
            elif section == "FeatureGroups":
                labels, _ = _translations(elem)
                code = _text(elem, "Code")
                if code:
                    selection.feature_groups[code] = labels
            elif section == "Groups":
                labels, _ = _translations(elem)
                code = _text(elem, "Code")
                if code:
                    selection.class_groups[code] = labels
            elif section == "Classes":
                selection.seen_classes += 1
                code = _text(elem, "Code")
                group_code = _text(elem, "GroupCode")
                take = bool(code) and (
                    (code in wanted) if wanted else (group is None or group_code == group)
                )
                if take and (limit is None or len(selection.classes) < limit):
                    labels, synonyms = _translations(elem)
                    entry = IxfClass(
                        code=code,
                        version=_text(elem, "Version"),
                        status=_text(elem, "Status"),
                        group_code=group_code,
                        labels=labels,
                        synonyms=synonyms,
                    )
                    for feature in _children(elem, "Features/Feature"):
                        feature_code = _text(feature, "FeatureCode")
                        if not feature_code:
                            continue
                        entry.features.append(
                            ClassFeature(
                                code=feature_code,
                                unit_code=_text(feature, "UnitCode"),
                                position=int(_text(feature, "OrderNumber") or 0),
                            )
                        )
                    selection.classes.append(entry)
        stack.pop()
        if len(stack) <= 1 or (depth == 2 and len(stack) == 2):
            elem.clear()

    for entry in selection.classes:
        for class_feature in entry.features:
            known = selection.features.get(class_feature.code)
            if known and attribute_type(known.etim_type) is None:
                selection.unknown_types[known.etim_type] += 1
    return selection


def build_model_document(
    selection: Selection,
    *,
    node_type: str,
    applies_to: str,
    unit_symbols: set[str] | None = None,
    with_units: bool = False,
) -> tuple[dict, dict]:
    """Builds the model MCF document and the report of what it needed."""
    used = {
        class_feature.code
        for entry in selection.classes
        for class_feature in entry.features
        if class_feature.code in selection.features
    }
    definitions = []
    unit_conflicts: dict[str, set[str]] = {}
    # `FeatureGroups` in document order — one `attribute_group` per used group, so
    # the features stay browsable in the UI (product form sections, export
    # selection). Attributes reference their group by name; the MCF import
    # resolves that to the record id (`attribute_groups` is applied before
    # `attribute_definitions`).
    used_group_codes = {
        selection.features[code].group_code for code in used if selection.features[code].group_code
    }
    group_entries = [
        {
            "name": code,
            "label": selection.feature_groups.get(code) or {},
            "position": position,
            "applicable_types": [applies_to],
        }
        for position, code in enumerate(
            (code for code in selection.feature_groups if code in used_group_codes), start=1
        )
    ]
    for code in sorted(used):
        feature = selection.features[code]
        mapped = attribute_type(feature.etim_type)
        if mapped is None:
            continue
        units = {
            class_feature.unit_code
            for entry in selection.classes
            for class_feature in entry.features
            if class_feature.code == code and class_feature.unit_code
        }
        if len(units) > 1:
            unit_conflicts[code] = units
        definition: dict = {
            "name": code,
            "label": feature.labels,
            "type": mapped,
            "group_ids": [feature.group_code] if feature.group_code else [],
            "inheritable": False,
            "is_key": False,
            "multilingual": False,
            "unit": None,
            "display_unit": None,
            "enum_set": None,
            "applicable_types": [applies_to],
            "required_types": [],
            "computed": False,
            "computed_rule": None,
            "dependencies": [],
            "computed_reads_inherited": False,
            "constraints": None,
            "default_value": None,
            "description": f"ETIM feature {code} ({feature.etim_type})",
        }
        if with_units and units:
            symbol = selection.units.get(next(iter(units)), IxfUnit(code="")).symbol
            if symbol and (unit_symbols is None or symbol in unit_symbols):
                definition["unit"] = symbol
        definitions.append(definition)

    node_definition = {
        "name": node_type,
        "label": {"en": "ETIM class", "de": "ETIM-Klasse"},
        "key_attributes": ["code"],
        "label_attribute": "description",
        "taxonomy": True,
        "system": False,
    }
    code_definition = {
        "name": "code",
        "label": {"en": "Code", "de": "Code"},
        "type": "text",
        "group_ids": [],
        "inheritable": False,
        "is_key": True,
        "multilingual": False,
        "unit": None,
        "display_unit": None,
        "enum_set": None,
        "applicable_types": [node_type],
        "required_types": [],
        "computed": False,
        "computed_rule": None,
        "dependencies": [],
        "computed_reads_inherited": False,
        "constraints": None,
        "default_value": None,
        "description": "ETIM class code (EC…)",
    }
    description_definition = {
        **code_definition,
        "name": "description",
        "label": {"en": "Description", "de": "Beschreibung"},
        "is_key": False,
        "description": "ETIM class description (single language; multilingual labels follow later)",
    }

    report = {
        "classes": len(selection.classes),
        "features": len(definitions),
        "feature_groups": len(group_entries),
        "features_without_group": sum(
            1 for code in sorted(used) if not selection.features[code].group_code
        ),
        "type_histogram": Counter(selection.features[code].etim_type for code in sorted(used)),
        "units_needed": {
            code: {
                "symbol": selection.units.get(code).symbol if code in selection.units else "",
                "en": (
                    selection.units.get(code).labels.get("en", "")
                    if code in selection.units
                    else ""
                ),
                "used_by": sum(
                    1
                    for entry in selection.classes
                    for class_feature in entry.features
                    if class_feature.unit_code == code
                ),
            }
            for code in sorted(
                {
                    class_feature.unit_code
                    for entry in selection.classes
                    for class_feature in entry.features
                    if class_feature.unit_code
                }
            )
        },
        "unit_conflicts": {code: sorted(units) for code, units in unit_conflicts.items()},
        "unknown_types": dict(selection.unknown_types),
    }
    document = {
        "format": "mcf",
        "version": "1.0",
        "source": "etim-ixf",
        "created_at": datetime.now(UTC).isoformat(),
        "sections": {
            "object_types": [node_definition],
            "attribute_groups": group_entries,
            "attribute_definitions": [code_definition, description_definition, *definitions],
        },
    }
    return document, report


def build_classes_document(selection: Selection, *, node_type: str) -> dict:
    """Builds the MEF document with one object per class node."""
    objects = []
    for entry in selection.classes:
        description = entry.labels.get("en") or next(iter(entry.labels.values()), "")
        objects.append(
            {
                "type": node_type,
                "status": "draft",
                "attributes": {"code": entry.code, "description": description},
            }
        )
    return {
        "format_version": "1.0",
        "source": "etim-ixf",
        "created_at": datetime.now(UTC).isoformat(),
        "objects": objects,
        "references": [],
    }


def build_features_document(selection: Selection, *, class_key: str = "code") -> dict:
    """Builds the MCF document with the `category_attributes` section."""
    entries = []
    for entry in selection.classes:
        features = [
            {
                "attribute": class_feature.code,
                "required": True,
                "position": class_feature.position,
            }
            for class_feature in entry.features
            if class_feature.code in selection.features
        ]
        if features:
            entries.append({"class": entry.code, "class_key": class_key, "features": features})
    return {
        "format": "mcf",
        "version": "1.0",
        "source": "etim-ixf",
        "created_at": datetime.now(UTC).isoformat(),
        "sections": {"category_attributes": entries},
    }


def bundle_manifest(
    selection: Selection,
    report: dict,
    *,
    name: str,
    node_type: str,
    applies_to: str,
    class_key: str = "code",
) -> dict:
    """The manifest describing the bundle's documents and what they need."""
    number = selection.publication.get("number", "")
    class_features = sum(len(entry.features) for entry in selection.classes)
    return {
        "format": BUNDLE_FORMAT,
        "format_version": BUNDLE_VERSION,
        "name": name,
        "standard": "ETIM",
        "standard_version": f"{number}.0" if number else "",
        "source": selection.publication.get("name", ""),
        "created_at": datetime.now(UTC).isoformat(),
        "node_type": node_type,
        "applies_to": applies_to,
        "class_key": class_key,
        "counts": {
            "classes": len(selection.classes),
            "features": report["features"],
            "feature_groups": report["feature_groups"],
            "class_features": class_features,
        },
        # The installer compares this with the target's unit catalog.
        "units_needed": report["units_needed"],
        "documents": [
            {
                "file": "model.mcf.json",
                "kind": "mcf",
                "sections": ["object_types", "attribute_groups", "attribute_definitions"],
            },
            {"file": "classes.mef.json", "kind": "mef", "objects": len(selection.classes)},
            {"file": "features.mcf.json", "kind": "mcf", "sections": ["category_attributes"]},
        ],
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ixf", type=Path, help="IXF export to read")
    parser.add_argument("--out-dir", type=Path, default=Path(".tmp/etim"), help="output folder")
    parser.add_argument("--classes", default="", help="comma-separated ETIM class codes (EC…)")
    parser.add_argument("--group", default=None, help="only classes of this ETIM group (EG…)")
    parser.add_argument("--limit", type=int, default=None, help="cap the number of classes")
    parser.add_argument("--node-type", default="etim_class", help="Meadow object type of the nodes")
    parser.add_argument("--applies-to", default="product", help="object type the features apply to")
    parser.add_argument("--units-file", type=Path, default=None, help="`GET /units` payload (JSON)")
    parser.add_argument(
        "--with-units", action="store_true", help="set units where the symbol matches"
    )
    parser.add_argument("--bundle-name", default="etim-10.0", help="name of the standard bundle")
    args = parser.parse_args(argv[1:])

    wanted = {code.strip() for code in args.classes.split(",") if code.strip()}
    selection = read_ixf(args.ixf, classes=wanted or None, group=args.group, limit=args.limit)
    if not selection.classes:
        print("no classes matched the selection", file=sys.stderr)
        return 1
    unit_symbols = None
    if args.units_file and args.units_file.exists():
        payload = json.loads(args.units_file.read_text(encoding="utf-8"))
        rows = payload.get("items", payload) if isinstance(payload, dict) else payload
        unit_symbols = {str(row.get("symbol")) for row in rows if isinstance(row, dict)}

    model, report = build_model_document(
        selection,
        node_type=args.node_type,
        applies_to=args.applies_to,
        unit_symbols=unit_symbols,
        with_units=args.with_units,
    )
    classes = build_classes_document(selection, node_type=args.node_type)
    features = build_features_document(selection)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    for name, document in (
        ("model.mcf.json", model),
        ("classes.mef.json", classes),
        ("features.mcf.json", features),
    ):
        (args.out_dir / name).write_text(
            json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    (args.out_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    manifest = bundle_manifest(
        selection,
        report,
        name=args.bundle_name,
        node_type=args.node_type,
        applies_to=args.applies_to,
    )
    (args.out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"bundle: {manifest['name']} {manifest['standard_version']} -> {args.out_dir}")
    print(f"classes={report['classes']} features={report['features']} out={args.out_dir}")
    print(f"feature groups: {report['feature_groups']}")
    print(f"types={dict(report['type_histogram'])}")
    print(
        f"units needed: {len(report['units_needed'])}, conflicts: {len(report['unit_conflicts'])}"
    )
    for code, info in list(report["units_needed"].items())[:10]:
        print(
            f"  {code} {info['symbol']!r} {info['en']} (used by {info['used_by']} class features)"
        )
    if report["unknown_types"]:
        print(f"unknown ETIM types: {report['unknown_types']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
