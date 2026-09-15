"""Synthetic automotive demo and load data for Meadow PIM.

The generator reads the curated master-data catalogue
(`scripts/demo_data_catalog.json`) and writes five kinds of artefacts into an
output directory:

``model.mcf.json``
    The data model (MCF): channels, enum sets, attribute groups, object types,
    attribute definitions, relation definitions (including the two additional
    category dimensions), business rules, export profiles. Applies with
    ``POST /config/import?mode=merge`` (admin), ideally after a ``dry_run``.
``class-features.mcf.json``
    The class features of the own taxonomy (section ``category_attributes``).
    Applied **after** the objects exist, because the section addresses class
    nodes by their business key (``POST /config/import?sections=category_attributes``).
``data-<nn>.mef.json``
    Objects and references as MEF documents, split into **closed chunks**: every
    reference of a chunk has both endpoints in that chunk. Objects appear in
    every chunk that references them; the second import updates instead of
    duplicating (matching by business key and source reference). Applies with
    ``POST /imports?emit_events=false&source=meadow-demo``.
``extras.json``
    Everything that needs resolved object ids or post-import steps: channel
    releases, collections with their members, saved searches, the collection
    refresh jobs, the standard class membership and the business rules to
    activate (MCF-imported rules start ``disabled`` by design).
``report.json``
    Counts per object type and relation plus the parameter set — the evidence
    checked by ``tests/test_demo_data.py``.

Values are written as plain numbers in the unit of the attribute
(``unit == display_unit`` for every demo attribute): the import path converts
*quantified* values (``{"value": 87, "unit": "mm"}``) to the canonical SI unit,
which would show up as ``0.087`` in the grid and the handbook.

Deterministic: same seed and profile produce byte-identical files
(``created_at`` is a fixed timestamp). Stage 1+2 of the plan generates files
only; the import runner is stage 3.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Source label written into every MEF document and used for the idempotent
#: source-reference matching on re-import.
SOURCE = "meadow-demo"
#: Fixed document timestamp: keeps the output byte-identical for a given seed.
CREATED_AT = "2026-09-15T00:00:00Z"
#: Curated master data next to this script.
CATALOG_PATH = Path(__file__).with_name("demo_data_catalog.json")
#: Default output directory (gitignored).
DEFAULT_OUT_DIR = Path(".tmp/demo-data")

#: Scale profiles. ``products`` drives everything else (variants, references).
PROFILES: dict[str, dict[str, Any]] = {
    "tiny": {
        "products": 40,
        "variant_ratio": 0.3,
        "fits_per_product": 2,
        "oem_ratio": 0.3,
        "chunks": 1,
        "collection_size": 6,
    },
    "demo": {
        "products": 2000,
        "variant_ratio": 1.3,
        "fits_per_product": 3,
        "oem_ratio": 0.5,
        "chunks": 4,
        "collection_size": 60,
    },
    "perf": {
        "products": 20000,
        "variant_ratio": 2.0,
        "fits_per_product": 4,
        "oem_ratio": 0.5,
        "chunks": 8,
        "collection_size": 400,
    },
}

#: Product status mix: published first, the rest as the realistic long tail.
STATUS_MIX: tuple[tuple[str, int], ...] = (
    ("published", 78),
    ("draft", 12),
    ("in_review", 5),
    ("approved", 3),
    ("archived", 2),
)

COUNTRY_CODES = ("DE", "DE", "PL", "CZ", "IT", "ES", "FR", "TR", "CN", "IN")
CUSTOMS_TARIFFS = {
    "PG-BRK": "87083091",
    "PG-ENG": "84212300",
    "PG-COOL": "84199085",
    "PG-EXH": "87089235",
    "PG-CHS": "87088020",
    "PG-DRV": "87089300",
    "PG-ELC": "85071020",
    "PG-EV": "85044090",
    "PG-CON": "27101981",
    "PG-WKS": "82055980",
    "PG-BDY": "85122000",
    "PG-INT": "63079098",
    "PG-WHL": "40111000",
    "PG-ACC": "87089997",
}
CURRENCIES = ("EUR",)

#: Part brand per assembly group (deterministic manufacturer assignment).
MANUFACTURER_BY_GROUP = {
    "PG-BRK": "bremstech",
    "PG-ENG": "filterpro",
    "PG-COOL": "filterpro",
    "PG-ELC": "elox",
    "PG-EV": "voltline",
    "PG-DRV": "kraftwerk",
    "PG-WKS": "werkstattpartner",
    "PG-ACC": "drivemaster",
}

POSITION_DE = {
    "front": "vorne",
    "rear": "hinten",
    "left": "links",
    "right": "rechts",
    "front_axle": "Vorderachse",
    "rear_axle": "Hinterachse",
    "upper": "oben",
    "lower": "unten",
    "inner": "innen",
    "outer": "außen",
}
POSITION_EN = {
    "front": "front",
    "rear": "rear",
    "left": "left",
    "right": "right",
    "front_axle": "front axle",
    "rear_axle": "rear axle",
    "upper": "upper",
    "lower": "lower",
    "inner": "inner",
    "outer": "outer",
}


def configure_console() -> None:
    """Switches stdout/stderr to UTF-8 (Windows cp1252 breaks on symbols)."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


# --------------------------------------------------------------------------- #
# Catalogue
# --------------------------------------------------------------------------- #


def load_catalog(path: Path | str = CATALOG_PATH) -> dict[str, Any]:
    """Loads and validates the curated master-data catalogue."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    problems = validate_catalog(data)
    if problems:
        raise ValueError("catalog is inconsistent: " + "; ".join(problems))
    return data


def validate_catalog(data: dict[str, Any]) -> list[str]:
    """Returns the inconsistencies of a catalogue (empty list = consistent).

    Checks the cross-references a generator would otherwise fail on later:
    enum options used by the part families, assembly-group parents, category
    parents, assembly-group codes referenced by class features, and generation
    codes within one model.
    """
    problems: list[str] = []
    for key in (
        "channels",
        "enum_sets",
        "object_types",
        "attributes",
        "attribute_groups",
        "relation_definitions",
        "business_rules",
        "export_profiles",
        "categories",
        "part_groups",
        "part_families",
        "class_features",
        "makes",
        "collections",
        "saved_searches",
    ):
        if key not in data:
            problems.append(f"missing section `{key}`")

    attribute_names = {attribute["name"] for attribute in data.get("attributes", [])}
    attribute_names.update({"name", "code", "description"})
    enum_options: dict[str, set[str]] = {
        enum_set["name"]: {option["code"] for option in enum_set["options"]}
        for enum_set in data.get("enum_sets", [])
    }
    group_codes = {group["code"] for group in data.get("part_groups", [])}
    category_codes = {category["code"] for category in data.get("categories", [])}
    type_names = {entry["name"] for entry in data.get("object_types", [])}

    for attribute in data.get("attributes", []):
        if attribute.get("enum_set") and attribute["enum_set"] not in enum_options:
            problems.append(f"attribute {attribute['name']}: unknown enum set")
        for group in attribute.get("group_ids", []):
            if group not in {entry["name"] for entry in data.get("attribute_groups", [])}:
                problems.append(f"attribute {attribute['name']}: unknown group {group}")
        for object_type in attribute.get("applicable_types", []):
            if object_type not in type_names | {"product", "variant", "category", "etim_class"}:
                problems.append(f"attribute {attribute['name']}: unknown type {object_type}")
        if attribute["type"] == "enum" and not attribute.get("enum_set"):
            problems.append(f"attribute {attribute['name']}: enum without enum set")

    for family in data.get("part_families", []):
        if family["group"] not in group_codes:
            problems.append(f"family {family['code']}: unknown assembly group")
        for dimension in family.get("dims", []):
            if dimension not in attribute_names:
                problems.append(f"family {family['code']}: unknown dimension {dimension}")
        for position in family.get("positions", []):
            if position not in enum_options.get("part_position", set()):
                problems.append(f"family {family['code']}: unknown position {position}")
        for material in family.get("materials", []):
            if material not in enum_options.get("material", set()):
                problems.append(f"family {family['code']}: unknown material {material}")
        for tier in family.get("quality_tiers", []):
            if tier not in enum_options.get("quality_tier", set()):
                problems.append(f"family {family['code']}: unknown quality tier {tier}")
        if family.get("packaging") and family["packaging"] not in enum_options.get(
            "packaging_type", set()
        ):
            problems.append(f"family {family['code']}: unknown packaging type")

    for group in data.get("part_groups", []):
        if group.get("parent") and group["parent"] not in group_codes:
            problems.append(f"assembly group {group['code']}: unknown parent")
    for category in data.get("categories", []):
        if category.get("parent") and category["parent"] not in category_codes:
            problems.append(f"category {category['code']}: unknown parent")

    for entry in data.get("class_features", []):
        if entry["group"] not in group_codes:
            problems.append(f"class features: unknown assembly group {entry['group']}")
        for feature in entry["features"]:
            if feature["attribute"] not in attribute_names:
                problems.append(f"class features {entry['group']}: unknown attribute")

    for relation in data.get("relation_definitions", []):
        for attribute in relation.get("attributes", []):
            if attribute not in attribute_names:
                problems.append(f"relation {relation['name']}: unknown edge attribute {attribute}")

    for make in data.get("makes", []):
        for model in make["models"]:
            codes = [generation.split("|")[0] for generation in model["generations"]]
            if len(codes) != len(set(codes)):
                problems.append(f"{make['code']}/{model['code']}: duplicate generation code")
            for fuel in model["fuel_types"]:
                if fuel not in enum_options.get("fuel_type", set()):
                    problems.append(f"{make['code']}/{model['code']}: unknown fuel type {fuel}")
            if model["body_style"] not in enum_options.get("body_style", set()):
                problems.append(f"{make['code']}/{model['code']}: unknown body style")
    return problems


# --------------------------------------------------------------------------- #
# Value helpers
# --------------------------------------------------------------------------- #


def check_digit_gtin12(first_twelve: str) -> str:
    """Computes the GTIN-13 check digit for the first twelve digits."""
    total = sum(int(digit) * (3 if index % 2 else 1) for index, digit in enumerate(first_twelve))
    return str((10 - total % 10) % 10)


def gtin13(serial: int) -> str:
    """Builds a valid 13-digit GTIN from a serial number (prefix `40`)."""
    body = f"40{serial:010d}"
    return body + check_digit_gtin12(body)


def expand_oe_pattern(pattern: str, prefixes: list[str], rng: random.Random) -> str:
    """Fills an OEM number template (`{p} {n3} {n3} {L}`) deterministically."""
    letters = "ABCDEFGHJKLMNPRSTUVWXYZ"
    text = pattern
    if "{p}" in text:
        text = text.replace("{p}", rng.choice(prefixes) if prefixes else f"{rng.randint(1, 9)}K0")
    for width in (1, 2, 3, 4, 5, 6):
        token = f"{{n{width}}}"
        while token in text:
            low = 10 ** (width - 1) if width > 1 else 0
            value = rng.randint(low, 10**width - 1)
            text = text.replace(token, str(value).zfill(width), 1)
    while "{L}" in text:
        text = text.replace("{L}", rng.choice(letters), 1)
    return text


def pick_status(rng: random.Random) -> str:
    """Draws an object status from :data:`STATUS_MIX`."""
    roll = rng.randint(1, 100)
    total = 0
    for status, weight in STATUS_MIX:
        total += weight
        if roll <= total:
            return status
    return "draft"


def family_short_code(family: dict[str, Any]) -> str:
    """Stable three-letter short code of a part family (used in the SKU)."""
    letters = "".join(character for character in family["code"].upper() if character.isalpha())
    return (letters + "XXX")[:3]


# --------------------------------------------------------------------------- #
# Dataset
# --------------------------------------------------------------------------- #


@dataclass
class Node:
    """One generated object."""

    key: str
    type: str
    attributes: dict[str, Any]
    status: str
    #: Business key value (for the report and the tests).
    business_key: str = ""

    def to_mef(self) -> dict[str, Any]:
        return {
            "id": self.key,
            "type": self.type,
            "status": self.status,
            "attributes": self.attributes,
        }


@dataclass
class Reference:
    """One generated reference (relation type name without the `rel_` prefix)."""

    source: str
    target: str
    type: str
    attributes: dict[str, Any] = field(default_factory=dict)

    def to_mef(self) -> dict[str, Any]:
        entry: dict[str, Any] = {"in": self.source, "out": self.target, "type": self.type}
        if self.attributes:
            entry["attributes"] = self.attributes
        return entry


@dataclass
class Dataset:
    """Everything the generator produced."""

    nodes: list[Node]
    references: list[Reference]
    extras: dict[str, Any]
    report: dict[str, Any]

    def node_by_key(self) -> dict[str, Node]:
        return {node.key: node for node in self.nodes}


def dedupe_references(references: list[Reference]) -> list[Reference]:
    """Drops duplicate edges (same source, target, type), keeping the first.

    Without this a family with few products can draw the same alternative twice
    and the import would create a second, identical edge.
    """
    seen: set[tuple[str, str, str]] = set()
    unique: list[Reference] = []
    for reference in references:
        key = (reference.source, reference.target, reference.type)
        if key in seen:
            continue
        seen.add(key)
        unique.append(reference)
    return unique


def generate_dataset(catalog: dict[str, Any], profile: str = "demo", seed: int = 42) -> Dataset:
    """Generates the deterministic dataset for a profile and seed."""
    if profile not in PROFILES:
        raise ValueError(f"unknown profile `{profile}` (expected: {', '.join(PROFILES)})")
    settings = PROFILES[profile]
    rng = random.Random(seed)
    nodes: list[Node] = []
    references: list[Reference] = []

    # --- taxonomies ------------------------------------------------------- #
    for category in catalog["categories"]:
        nodes.append(
            Node(
                key=f"demo-cat-{category['code']}",
                type="category",
                attributes={
                    "name": {"de": category["name_de"], "en": category["name_en"]},
                    "description_short": {
                        "de": f"Kategorie {category['name_de']}",
                        "en": f"Category {category['name_en']}",
                    },
                },
                status="published",
                business_key=category["code"],
            )
        )
    for group in catalog["part_groups"]:
        nodes.append(
            Node(
                key=f"demo-group-{group['code']}",
                type="part_group",
                attributes={
                    "name": {"de": group["name_de"], "en": group["name_en"]},
                    "group_code": group["code"],
                    "description_short": {
                        "de": f"Baugruppe {group['name_de']}",
                        "en": f"Assembly group {group['name_en']}",
                    },
                },
                status="published",
                business_key=group["code"],
            )
        )
    for category in catalog["categories"]:
        if category.get("parent"):
            references.append(
                Reference(
                    f"demo-cat-{category['code']}",
                    f"demo-cat-{category['parent']}",
                    "depends_on",
                )
            )
    for group in catalog["part_groups"]:
        if group.get("parent"):
            references.append(
                Reference(
                    f"demo-group-{group['code']}",
                    f"demo-group-{group['parent']}",
                    "depends_on",
                )
            )

    # --- vehicle master data --------------------------------------------- #
    generation_pool: list[Node] = []
    for make in catalog["makes"]:
        nodes.append(
            Node(
                key=f"demo-make-{make['code']}",
                type="vehicle_make",
                attributes={
                    "name": {"de": make["name"], "en": make["name"]},
                    "make_code": make["code"],
                    "description_short": {
                        "de": f"Fahrzeuge der Marke {make['name']}",
                        "en": f"Vehicles of the make {make['name']}",
                    },
                },
                status="published",
                business_key=make["code"],
            )
        )
        for model in make["models"]:
            model_key = f"demo-model-{make['code']}-{model['code']}"
            nodes.append(
                Node(
                    key=model_key,
                    type="vehicle_model",
                    attributes={
                        "name": {
                            "de": f"{make['name']} {model['name']}",
                            "en": f"{make['name']} {model['name']}",
                        },
                        "model_code": model["code"],
                        "body_style": model["body_style"],
                        "fuel_type": model["fuel_types"][0],
                        "platform_code": model["generations"][0].split("|")[3]
                        if len(model["generations"][0].split("|")) > 3
                        else "",
                        "description_short": {
                            "de": f"Baureihe {model['name']} ({make['name']})",
                            "en": f"Model series {model['name']} ({make['name']})",
                        },
                    },
                    status="published",
                    business_key=model["code"],
                )
            )
            references.append(Reference(model_key, f"demo-make-{make['code']}", "depends_on"))
            for generation in model["generations"]:
                parts = generation.split("|")
                code, year_from, year_to = parts[0], parts[1], parts[2]
                platform = parts[3] if len(parts) > 3 else ""
                displacement = round(rng.uniform(1.0, 3.0), 1)
                generation_node = Node(
                    key=f"demo-gen-{make['code']}-{model['code']}-{code.lower()}",
                    type="vehicle_generation",
                    attributes={
                        "name": {
                            "de": f"{make['name']} {model['name']} {code} ({year_from}–{year_to})",
                            "en": f"{make['name']} {model['name']} {code} ({year_from}–{year_to})",
                        },
                        "generation_code": f"{model['code']}-{code}",
                        "platform_code": platform,
                        "body_style": model["body_style"],
                        "fuel_type": rng.choice(model["fuel_types"]),
                        "drive_type": rng.choice(["fwd", "fwd", "awd", "rwd"]),
                        "engine_code": f"{code[:2].upper()}{rng.randint(100, 999)}",
                        "power_kw": rng.choice([66, 81, 96, 110, 140, 150, 190, 210, 230]),
                        "displacement_l": displacement,
                        "production_from": f"{year_from}-01-01",
                        "production_to": f"{year_to}-12-31",
                        "description_short": {
                            "de": f"Generation {code} der Baureihe {model['name']}",
                            "en": f"Generation {code} of the model series {model['name']}",
                        },
                    },
                    status="published",
                    business_key=f"{model['code']}-{code}",
                )
                nodes.append(generation_node)
                generation_pool.append(generation_node)
                references.append(Reference(generation_node.key, model_key, "depends_on"))

    # --- products and variants ------------------------------------------- #
    families = catalog["part_families"]
    families_by_group: dict[str, list[dict[str, Any]]] = {}
    for family in families:
        families_by_group.setdefault(family["group"], []).append(family)
    product_count = int(settings["products"])
    serial = 0
    products_by_family: dict[str, list[Node]] = {}
    products: list[Node] = []
    for index in range(product_count):
        family = families[index % len(families)]
        serial += 1
        short = family_short_code(family)
        sku = f"MA-{short}-{serial:05d}"
        position = rng.choice(family["positions"]) if family["positions"] else None
        material = rng.choice(family["materials"])
        quality = rng.choice(family["quality_tiers"])
        line = rng.choice(["original", "pro", "pro", "eco", "workshop"])
        position_suffix_de = f", {POSITION_DE[position]}" if position else ""
        position_suffix_en = f", {POSITION_EN[position]}" if position else ""
        variant_suffix_de = "" if position else f", Größe {rng.choice([1, 2, 3])}"
        name_de = f"{family['name_de']}{position_suffix_de}{variant_suffix_de}"
        name_en = f"{family['name_en']}{position_suffix_en}"
        attributes: dict[str, Any] = {
            "sku": sku,
            "gtin": gtin13(serial),
            "manufacturer": MANUFACTURER_BY_GROUP.get(family["group"], "meadow"),
            "product_line": line,
            "quality_tier": quality,
            "name": {"de": name_de, "en": name_en},
            "description_short": {
                "de": f"{name_de} in {quality.upper()}-Qualität",
                "en": f"{name_en} in {quality.upper()} quality",
            },
            "description_long": {
                "de": (
                    f"{name_de} aus dem Sortiment {family['name_de']}. "
                    "Geliefert im Original-Zubehörumfang, geprüft nach geltender Norm."
                ),
                "en": (
                    f"{name_en} from the {family['name_en']} range. "
                    "Delivered with the original accessory scope, "
                    "tested to the applicable standard."
                ),
            },
            "marketing_text": {
                "de": f"Zuverlässige Qualität für Werkstatt und Handel: {name_de}.",
                "en": f"Reliable quality for workshops and trade: {name_en}.",
            },
            "installation_note": {
                "de": "Einbau nach Herstellervorgabe, Anzugsdrehmomente beachten.",
                "en": (
                    "Install according to the manufacturer specification, "
                    "observe the tightening torques."
                ),
            },
            "material": material,
            "net_price": round(rng.uniform(family["price"][0], family["price"][1]), 2),
            "currency": rng.choice(CURRENCIES),
            "packaging_type": family["packaging"],
            "min_order_qty": rng.choice([1, 1, 1, 2, 4]),
            "hazardous": family["hazardous"],
            "country_of_origin": rng.choice(COUNTRY_CODES),
            "customs_tariff_number": CUSTOMS_TARIFFS.get(family["group"], "87089997"),
            "datasheet_url": f"https://datenblatt.meadow-demo.example/{sku}.pdf",
        }
        if position:
            # Families without an installation position leave the attribute
            # unset (a real gap, not an explicit null).
            attributes["part_position"] = position
        if family.get("disposal"):
            attributes["disposal_note"] = {
                "de": "Als Gefahrstoff entsorgen, nicht in den Abwasserkanal geben.",
                "en": "Dispose of as hazardous waste, never via the sewage system.",
            }
        if rng.random() > 0.08:
            attributes["weight_kg"] = round(
                rng.uniform(family["weight_kg"][0], family["weight_kg"][1]), 3
            )
        for dimension in family.get("dims", []):
            bounds = family.get(dimension)
            if dimension == "volume_l":
                attributes["volume_l"] = round(rng.uniform(bounds[0], bounds[1]), 2)
                continue
            numeric = (
                isinstance(bounds, list)
                and len(bounds) == 2
                and all(isinstance(bound, (int, float)) for bound in bounds)
            )
            if not numeric:
                # Non-numeric dimensions (`thread_size`, `cable_length_m`) are
                # written by their dedicated branches below.
                continue
            attributes[dimension] = int(rng.uniform(bounds[0], bounds[1]))
        if family.get("temp_range"):
            attributes["temperature_range"] = {
                "from": family["temp_range"][0],
                "to": family["temp_range"][1],
            }
        if family.get("voltage_v"):
            attributes["operating_voltage_v"] = int(rng.uniform(*family["voltage_v"]))
        if family.get("power_w"):
            attributes["power_w"] = int(rng.uniform(*family["power_w"]))
        if family.get("nominal_current_a"):
            attributes["nominal_current_a"] = int(rng.uniform(*family["nominal_current_a"]))
        if family.get("charging_power_kw"):
            attributes["charging_power_kw"] = round(rng.uniform(*family["charging_power_kw"]), 1)
        if family.get("output_voltage_range"):
            attributes["output_voltage_range"] = {
                "from": family["output_voltage_range"][0],
                "to": family["output_voltage_range"][1],
            }
        if family.get("cable_length_m"):
            attributes["cable_length_m"] = round(rng.uniform(*family["cable_length_m"]), 1)
        if family.get("capacity_ah"):
            attributes["capacity_ah"] = int(rng.uniform(*family["capacity_ah"]))
        if family.get("cold_cranking_a"):
            attributes["cold_cranking_a"] = int(rng.uniform(*family["cold_cranking_a"]))
        if family.get("torque_nm"):
            attributes["tightening_torque_nm"] = int(rng.uniform(*family["torque_nm"]))
        if family["code"] in ("spark_plug", "glow_plug", "tie_rod_end", "ball_joint", "wheel_bolt"):
            attributes["thread_size"] = rng.choice(["M12x1.5", "M14x1.5", "M10x1.25", "M12x1.25"])
        if rng.random() < 0.05:
            attributes.pop("material", None)
        if rng.random() < 0.03:
            attributes.pop("gtin", None)

        product = Node(
            key=f"demo-product-{sku}",
            type="product",
            attributes=attributes,
            status=pick_status(rng),
            business_key=sku,
        )
        nodes.append(product)
        products.append(product)
        products_by_family.setdefault(family["code"], []).append(product)

        # variants share the parent's material/quality/warranty and add their own numbers
        variant_total = int(settings["variant_ratio"])
        variants = variant_total + (1 if rng.random() < (settings["variant_ratio"] % 1) else 0)
        for variant_index in range(max(0, variants)):
            variant_sku = f"{sku}-{variant_index + 1:02d}"
            variant_attributes = {
                "sku": variant_sku,
                "gtin": gtin13(serial * 100 + variant_index + 1),
                "name": {
                    "de": f"{name_de} (Ausführung {variant_index + 1})",
                    "en": f"{name_en} (variant {variant_index + 1})",
                },
                "net_price": round(attributes["net_price"] * rng.uniform(0.85, 1.35), 2),
                "currency": "EUR",
                "packaging_type": family["packaging"],
            }
            if "weight_kg" in attributes:
                variant_attributes["weight_kg"] = round(
                    attributes["weight_kg"] * rng.uniform(0.9, 1.1), 3
                )
            if family["positions"]:
                variant_attributes["part_position"] = rng.choice(family["positions"])
            variant = Node(
                key=f"demo-variant-{variant_sku}",
                type="variant",
                attributes=variant_attributes,
                status="published" if product.status == "published" else product.status,
                business_key=variant_sku,
            )
            nodes.append(variant)
            references.append(Reference(variant.key, product.key, "depends_on"))
            references.append(
                Reference(variant.key, f"demo-group-{family['group']}", "part_group_member")
            )

    # --- OEM numbers ------------------------------------------------------ #
    oem_nodes: list[Node] = []
    oem_target = int(product_count * float(settings["oem_ratio"]))
    for index in range(oem_target):
        product = products[(index * 7) % len(products)]
        family = families[index % len(families)]
        make = catalog["makes"][index % len(catalog["makes"])]
        number = expand_oe_pattern(make["oe_format"], make.get("oe_prefixes", []), rng)
        node = Node(
            key=f"demo-oem-{index:06d}",
            type="oem_number",
            attributes={
                "name": number,
                "oe_number": number,
                "number_kind": rng.choice(["oe", "oe", "interchange", "competitor"]),
                "number_source": make["name"],
            },
            status="published",
            business_key=number,
        )
        nodes.append(node)
        oem_nodes.append(node)
        references.append(
            Reference(
                product.key,
                node.key,
                "oem_reference",
                {"oem_kind": node.attributes["number_kind"]},
            )
        )

    # --- relations between products -------------------------------------- #
    for family_entry in families:
        family_products = products_by_family.get(family_entry["code"], [])
        for index, product in enumerate(family_products):
            if index and rng.random() < 0.35:
                predecessor = family_products[index - 1]
                references.append(
                    Reference(
                        product.key,
                        predecessor.key,
                        "replaces",
                        {
                            "replace_reason": rng.choice(["superseded", "improved"]),
                            "valid_from": f"20{rng.randint(19, 25)}-0{rng.randint(1, 9)}-01",
                        },
                    )
                )
            for _ in range(rng.randint(0, 2)):
                other = rng.choice(family_products)
                if other.key != product.key:
                    references.append(
                        Reference(
                            product.key,
                            other.key,
                            "alternative_to",
                            {
                                "match_type": rng.choice(["oe", "interchange"]),
                                "quality_tier": rng.choice(family_entry["quality_tiers"]),
                            },
                        )
                    )
            if rng.random() < 0.3:
                candidates = [
                    candidate for candidate in family_products if candidate.key != product.key
                ]
                if candidates:
                    references.append(
                        Reference(
                            product.key,
                            rng.choice(candidates).key,
                            "accessory_for",
                            {"mandatory": rng.random() < 0.3},
                        )
                    )
            if rng.random() < 0.15:
                candidates = [c for c in family_products if c.key != product.key]
                if candidates:
                    references.append(
                        Reference(
                            product.key,
                            rng.choice(candidates).key,
                            "part_of_kit",
                            {"quantity": rng.choice([2, 4, 6, 8])},
                        )
                    )

    # --- compatibility with vehicles ------------------------------------- #
    fit_makes = {make["code"]: make for make in catalog["makes"]}
    generations_by_make: dict[str, list[Node]] = {}
    for node in generation_pool:
        parts = node.key.split("-")
        generations_by_make.setdefault(parts[2], []).append(node)
    make_order = sorted(fit_makes)
    for index, product in enumerate(products):
        chosen_makes = {make_order[(index + offset) % len(make_order)] for offset in range(1, 3)}
        for make_code in sorted(chosen_makes):
            candidates = generations_by_make.get(make_code, [])
            if not candidates:
                continue
            for _ in range(max(1, int(settings["fits_per_product"]) // 2)):
                generation = rng.choice(candidates)
                references.append(
                    Reference(
                        product.key,
                        generation.key,
                        "fits_vehicle",
                        {
                            "position": rng.choice(["front_axle", "rear_axle", "front", "rear"]),
                            "qualifier": rng.choice(
                                ["", "ab Baujahr 2015", "nur mit Sportfahrwerk", "ohne Hybrid"]
                            ),
                            "confidence": round(rng.uniform(0.7, 1.0), 2),
                        },
                    )
                )

    # --- category and assembly-group membership -------------------------- #
    shop_leaves = [
        category["code"]
        for category in catalog["categories"]
        if not any(other.get("parent") == category["code"] for other in catalog["categories"])
    ]
    for index, product in enumerate(products):
        references.append(
            Reference(
                product.key,
                f"demo-cat-{shop_leaves[index % len(shop_leaves)]}",
                "category_member",
                {"position": index % 20},
            )
        )
        if index % 5 == 0:
            references.append(Reference(product.key, "demo-cat-cat_new", "category_member", {}))
        family = families[index % len(families)]
        references.append(
            Reference(product.key, f"demo-group-{family['group']}", "part_group_member")
        )

    # --- extras ----------------------------------------------------------- #
    released_products = [product for product in products if product.status == "published"]
    releases: list[dict[str, Any]] = []
    for index, product in enumerate(released_products):
        releases.append({"object": product.key, "channel": "webshop", "status": "published"})
        if index % 3 == 0:
            releases.append({"object": product.key, "channel": "print-2026", "status": "published"})
        if index % 5 == 0:
            releases.append(
                {"object": product.key, "channel": "marketplace-de", "status": "published"}
            )
        if index % 7 == 0:
            releases.append(
                {"object": product.key, "channel": "bmecat-partner", "status": "published"}
            )

    collections = []
    for entry in catalog["collections"]:
        size = min(int(entry["size"]), settings["collection_size"] * 4, len(products))
        members = [products[(index * 3 + 1) % len(products)].key for index in range(size)]
        collections.append(
            {
                "name": entry["name"],
                "note": entry["note"],
                "members": sorted(set(members)),
                "refresh_schedule": "0 3 * * *",
            }
        )

    standard_memberships = []
    for index, family in enumerate(families):
        if not family.get("etim_class"):
            continue
        for product_index in range(index, len(products), len(families)):
            standard_memberships.append(
                {
                    "object": products[product_index].key,
                    "standard_node_type": "etim_class",
                    "key_attribute": "code",
                    "key_value": family["etim_class"],
                    "relation": "etim_class_member",
                }
            )

    extras = {
        "source": SOURCE,
        "releases": releases,
        "collections": collections,
        "saved_searches": [
            {"name": entry["name"], "note": entry.get("note", ""), "query": entry["query"]}
            for entry in catalog["saved_searches"]
        ],
        "standard_memberships": standard_memberships,
        "activate_rules": [rule["name"] for rule in catalog["business_rules"]],
        "profile": profile,
        "seed": seed,
    }

    references = dedupe_references(references)
    report = build_report(nodes, references, profile, seed, catalog)
    return Dataset(nodes=nodes, references=references, extras=extras, report=report)


def build_report(
    nodes: list[Node],
    references: list[Reference],
    profile: str,
    seed: int,
    catalog: dict[str, Any],
) -> dict[str, Any]:
    """Summarises a dataset (object types, statuses, relation types, gaps)."""
    by_type = Counter(node.type for node in nodes)
    by_status = Counter(node.status for node in nodes)
    by_relation = Counter(reference.type for reference in references)
    products = [node for node in nodes if node.type == "product"]
    without_weight = sum(1 for node in products if "weight_kg" not in node.attributes)
    without_material = sum(1 for node in products if "material" not in node.attributes)
    without_gtin = sum(1 for node in products if "gtin" not in node.attributes)
    return {
        "profile": profile,
        "seed": seed,
        "source": SOURCE,
        "objects": len(nodes),
        "references": len(references),
        "by_type": dict(sorted(by_type.items())),
        "by_status": dict(sorted(by_status.items())),
        "by_relation": dict(sorted(by_relation.items())),
        "products_without_weight": without_weight,
        "products_without_material": without_material,
        "products_without_gtin": without_gtin,
        "catalog": {
            "makes": len(catalog["makes"]),
            "models": sum(len(make["models"]) for make in catalog["makes"]),
            "generations": sum(
                len(model["generations"]) for make in catalog["makes"] for model in make["models"]
            ),
            "part_families": len(catalog["part_families"]),
            "part_groups": len(catalog["part_groups"]),
            "categories": len(catalog["categories"]),
        },
    }


# --------------------------------------------------------------------------- #
# Model (MCF)
# --------------------------------------------------------------------------- #


def build_model(catalog: dict[str, Any]) -> dict[str, Any]:
    """Builds the MCF document for the demo data model."""
    sections: dict[str, Any] = {
        "channels": catalog["channels"],
        "attribute_groups": catalog["attribute_groups"],
        "enum_sets": catalog["enum_sets"],
        "object_types": catalog["object_types"],
        "attribute_definitions": catalog["attributes"],
        # Every relation type travels in the model — including the two additional
        # category dimensions (`role: category`); the MCF section carries `role`
        # since 2026-09-15 (`plan/detail/konfiguration-export-import.md` §4.7).
        "relation_definitions": catalog["relation_definitions"],
        "business_rules": [
            {
                "name": rule["name"],
                "type": rule["type"],
                "applies_to": rule["applies_to"],
                "script": rule["script"],
            }
            for rule in catalog["business_rules"]
        ],
        "export_profiles": catalog["export_profiles"],
    }
    return {
        "format": "mcf",
        "version": "1.0",
        "source": SOURCE,
        "created_at": CREATED_AT,
        "sections": sections,
    }


def build_class_features(catalog: dict[str, Any]) -> dict[str, Any]:
    """Builds the MCF document carrying only the class features section."""
    entries = []
    for entry in catalog["class_features"]:
        entries.append(
            {
                "class": entry["group"],
                "class_key": "group_code",
                "features": [
                    {
                        "attribute": feature["attribute"],
                        "required": bool(feature.get("required")),
                        "position": int(feature.get("position", 0)),
                    }
                    for feature in entry["features"]
                ],
            }
        )
    return {
        "format": "mcf",
        "version": "1.0",
        "source": SOURCE,
        "created_at": CREATED_AT,
        "sections": {"category_attributes": entries},
    }


# --------------------------------------------------------------------------- #
# Chunking and output
# --------------------------------------------------------------------------- #


def chunk_dataset(
    nodes: list[Node], references: list[Reference], chunks: int
) -> list[dict[str, Any]]:
    """Splits the dataset into closed MEF chunks.

    A reference is assigned to the chunk of its source object; every chunk also
    carries the target objects of its references, so all references can be
    resolved inside one document (`meadow/worker/imports.py` builds its id map
    from the objects of the document). Objects mentioned by several chunks are
    imported several times — matched by business key/source reference, so the
    second run updates instead of duplicating.
    """
    count = max(1, int(chunks))
    nodes_by_key = {node.key: node for node in nodes}
    home: dict[str, int] = {node.key: index % count for index, node in enumerate(nodes)}
    chunk_nodes: list[dict[str, Node]] = [{} for _ in range(count)]
    chunk_references: list[list[Reference]] = [[] for _ in range(count)]
    for reference in references:
        index = home.get(reference.source, 0)
        chunk_references[index].append(reference)
        for key in (reference.source, reference.target):
            node = nodes_by_key.get(key)
            if node is not None:
                chunk_nodes[index].setdefault(key, node)
    # Objects without any reference still have to be imported.
    for node in nodes:
        chunk_nodes[home[node.key]].setdefault(node.key, node)

    documents = []
    for index in range(count):
        objects = [chunk_nodes[index][key].to_mef() for key in sorted(chunk_nodes[index])]
        references_mef = [
            reference.to_mef()
            for reference in sorted(
                chunk_references[index], key=lambda r: (r.type, r.source, r.target)
            )
        ]
        documents.append(
            {
                "format_version": "1.0",
                "source": SOURCE,
                "created_at": CREATED_AT,
                "objects": objects,
                "references": references_mef,
            }
        )
    return documents


def write_outputs(
    dataset: Dataset,
    catalog: dict[str, Any],
    out_dir: Path,
    chunks: int,
) -> dict[str, Path]:
    """Writes all artefacts into ``out_dir`` and returns the file paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}

    def dump(name: str, payload: Any, *, compact: bool = False) -> None:
        path = out_dir / name
        if compact:
            text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        else:
            text = json.dumps(payload, ensure_ascii=False, indent=2)
        path.write_text(text + "\n", encoding="utf-8")
        written[name] = path

    dump("model.mcf.json", build_model(catalog))
    dump("class-features.mcf.json", build_class_features(catalog))
    documents = chunk_dataset(dataset.nodes, dataset.references, chunks)
    for index, document in enumerate(documents):
        dump(f"data-{index:02d}.mef.json", document, compact=True)
    dump("extras.json", dataset.extras)
    dump("report.json", dataset.report)
    return written


def summarize(report: dict[str, Any], written: dict[str, Path]) -> str:
    """Renders the console summary of a generation run."""
    lines = [
        f"profile {report['profile']} · seed {report['seed']} · source {report['source']}",
        f"objects {report['objects']} · references {report['references']}",
        "types: " + ", ".join(f"{name}={count}" for name, count in report["by_type"].items()),
        "relations: "
        + ", ".join(f"{name}={count}" for name, count in report["by_relation"].items()),
        "gaps: "
        f"weight={report['products_without_weight']} "
        f"material={report['products_without_material']} "
        f"gtin={report['products_without_gtin']}",
    ]
    for name, path in written.items():
        lines.append(f"  {name}  {path.stat().st_size / 1024:.0f} KiB")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate synthetic automotive demo data for Meadow PIM (MCF + MEF).",
    )
    parser.add_argument("--profile", default="demo", choices=sorted(PROFILES))
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--chunks", type=int, default=0, help="0 = profile default")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--catalog", default=str(CATALOG_PATH))
    parser.add_argument(
        "--check",
        action="store_true",
        help="only validate the catalogue and print its counts",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    configure_console()
    args = parse_args(argv)
    if args.check:
        data = json.loads(Path(args.catalog).read_text(encoding="utf-8"))
        problems = validate_catalog(data)
        for problem in problems:
            print(f"  ! {problem}")
        print(f"catalogue ok ({len(problems)} problems)")
        return 1 if problems else 0

    catalog = load_catalog(args.catalog)
    dataset = generate_dataset(catalog, args.profile, args.seed)
    chunks = args.chunks or int(PROFILES[args.profile]["chunks"])
    written = write_outputs(dataset, catalog, Path(args.out_dir), chunks)
    print(summarize(dataset.report, written))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
