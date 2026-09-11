//! Seed data: system relation types, the universal root object, and the unit
//! catalog (Phase 1).
//!
//! Records are seeded **create-if-missing** (`INSERT … ON DUPLICATE KEY UPDATE
//! id = id`): a record that already exists is left untouched, so edits made in
//! the admin pages (labels, type restrictions, edge attributes, unit or
//! language data) survive a restart. The seed only *owns* the structural
//! identity of the built-in relation types (`kind`, `system`, `role`), which is
//! enforced with an explicit `UPDATE`.
//!
//! Background: the seed used `UPSERT … CONTENT` before, which rewrote every
//! system record on every start and silently discarded runtime edits.

use crate::auth;
use crate::db::Db;
use crate::error::ApiError;

/// A unit-catalog entry. `factor`/`offset` are decimal strings that map the
/// unit to its dimension's canonical SI unit: `canonical = value * factor + offset`.
struct UnitSeed {
    key: &'static str,
    symbol: &'static str,
    dimension: &'static str,
    canonical: Option<&'static str>,
    factor: &'static str,
    offset: &'static str,
    de: &'static str,
    en: &'static str,
}

/// A language-tree node. `parent` holds the parent's record key (e.g.
/// `lang_en`); `fallback_order` lists the ancestor codes (excluding itself).
struct LangSeed {
    code: &'static str,
    parent: Option<&'static str>,
    fallback_order: &'static [&'static str],
    de: &'static str,
    en: &'static str,
}

/// System relation types: exactly one composition, the rest aggregations.
///
/// Seeded with `INSERT … ON DUPLICATE KEY UPDATE id = id`, so an existing
/// record is **never overwritten**: edits made in the relation-type admin
/// (label, source types, edge attributes) survive a restart. Only the fields the
/// seed *owns* (`kind`, `system`, `role`) are enforced afterwards; runtime
/// records without a role (created before the classification) fall back to
/// `link`.
const RELATION_DEFINITIONS: &str = r#"
INSERT INTO relation_definition (id, name, kind, role, source_types, target_types, label, attributes, system, created_at, updated_at) VALUES (relation_definition:rel_depends_on, 'depends_on', 'composition', 'composition', [], [], { de: 'Abhängig von', en: 'Depends on' }, [], true, time::now(), time::now()) ON DUPLICATE KEY UPDATE id = id;
INSERT INTO relation_definition (id, name, kind, role, source_types, target_types, label, attributes, system, created_at, updated_at) VALUES (relation_definition:rel_category_member, 'category_member', 'aggregation', 'category', [], [], { de: 'Kategorie-Zuordnung', en: 'Category membership' }, [], true, time::now(), time::now()) ON DUPLICATE KEY UPDATE id = id;
INSERT INTO relation_definition (id, name, kind, role, source_types, target_types, label, attributes, system, created_at, updated_at) VALUES (relation_definition:rel_collection_member, 'collection_member', 'aggregation', 'collection', [], [], { de: 'Collection-Zuordnung', en: 'Collection membership' }, [], true, time::now(), time::now()) ON DUPLICATE KEY UPDATE id = id;
INSERT INTO relation_definition (id, name, kind, role, source_types, target_types, label, attributes, system, created_at, updated_at) VALUES (relation_definition:rel_related, 'related', 'aggregation', 'link', [], [], { de: 'Verknüpft mit', en: 'Related to' }, [], true, time::now(), time::now()) ON DUPLICATE KEY UPDATE id = id;
UPDATE relation_definition:rel_depends_on SET kind = 'composition', role = 'composition', system = true;
UPDATE relation_definition:rel_category_member SET kind = 'aggregation', role = 'category', system = true;
UPDATE relation_definition:rel_collection_member SET kind = 'aggregation', role = 'collection', system = true;
UPDATE relation_definition:rel_related SET kind = 'aggregation', role = 'link', system = true;
UPDATE relation_definition SET role = 'link' WHERE role IS NONE;
"#;

/// The universal root: the composition tree's root, structurally immutable.
const ROOT_OBJECT: &str = r#"
INSERT INTO object (id, type, attributes, status, version, created_at, updated_at) VALUES (object:object_root, 'root', {}, 'published', 1, time::now(), time::now()) ON DUPLICATE KEY UPDATE id = id;
"#;

/// Managed object-type registry (STIBO STEP / Akeneo family model). Seeded
/// idempotently; `key_attributes` start empty (the admin assigns the unique +
/// mandatory keys per type), `label_attribute` defaults to the `name` attribute
/// for business types.
const OBJECT_TYPES: &str = r#"
INSERT INTO object_type (id, name, label, description, key_attributes, label_attribute, system, created_at, updated_at) VALUES (object_type:product, 'product', { de: 'Produkt', en: 'Product' }, '', [], 'name', true, time::now(), time::now()) ON DUPLICATE KEY UPDATE id = id;
INSERT INTO object_type (id, name, label, description, key_attributes, label_attribute, system, created_at, updated_at) VALUES (object_type:category, 'category', { de: 'Kategorie', en: 'Category' }, '', [], 'name', true, time::now(), time::now()) ON DUPLICATE KEY UPDATE id = id;
INSERT INTO object_type (id, name, label, description, key_attributes, label_attribute, system, created_at, updated_at) VALUES (object_type:asset, 'asset', { de: 'Asset', en: 'Asset' }, '', [], 'name', true, time::now(), time::now()) ON DUPLICATE KEY UPDATE id = id;
INSERT INTO object_type (id, name, label, description, key_attributes, label_attribute, system, created_at, updated_at) VALUES (object_type:variant, 'variant', { de: 'Variante', en: 'Variant' }, '', [], 'name', true, time::now(), time::now()) ON DUPLICATE KEY UPDATE id = id;
INSERT INTO object_type (id, name, label, description, key_attributes, system, created_at, updated_at) VALUES (object_type:root, 'root', { de: 'Universal-Root', en: 'Universal root' }, 'Der übergeordnete Universal-Root', [], true, time::now(), time::now()) ON DUPLICATE KEY UPDATE id = id;
INSERT INTO object_type (id, name, label, description, key_attributes, label_attribute, system, created_at, updated_at) VALUES (object_type:saved_search, 'saved_search', { de: 'Gespeicherte Suche', en: 'Saved search' }, '', [], 'name', true, time::now(), time::now()) ON DUPLICATE KEY UPDATE id = id;
INSERT INTO object_type (id, name, label, description, key_attributes, label_attribute, system, created_at, updated_at) VALUES (object_type:collection, 'collection', { de: 'Collection', en: 'Collection' }, '', [], 'name', true, time::now(), time::now()) ON DUPLICATE KEY UPDATE id = id;
"#;

/// Unit catalog covering the 13 dimensions documented in
/// `plan/detail/einheiten.md` (SI base/derived units, prefixes, and
/// imperial/US customary units).
const UNITS: &[UnitSeed] = &[
    // length (canonical: m)
    UnitSeed {
        key: "unit_m",
        symbol: "m",
        dimension: "length",
        canonical: None,
        factor: "1.0",
        offset: "0.0",
        de: "Meter",
        en: "metre",
    },
    UnitSeed {
        key: "unit_mm",
        symbol: "mm",
        dimension: "length",
        canonical: Some("unit_m"),
        factor: "0.001",
        offset: "0.0",
        de: "Millimeter",
        en: "millimetre",
    },
    UnitSeed {
        key: "unit_cm",
        symbol: "cm",
        dimension: "length",
        canonical: Some("unit_m"),
        factor: "0.01",
        offset: "0.0",
        de: "Zentimeter",
        en: "centimetre",
    },
    UnitSeed {
        key: "unit_km",
        symbol: "km",
        dimension: "length",
        canonical: Some("unit_m"),
        factor: "1000.0",
        offset: "0.0",
        de: "Kilometer",
        en: "kilometre",
    },
    UnitSeed {
        key: "unit_um",
        symbol: "µm",
        dimension: "length",
        canonical: Some("unit_m"),
        factor: "0.000001",
        offset: "0.0",
        de: "Mikrometer",
        en: "micrometre",
    },
    UnitSeed {
        key: "unit_in",
        symbol: "in",
        dimension: "length",
        canonical: Some("unit_m"),
        factor: "0.0254",
        offset: "0.0",
        de: "Zoll",
        en: "inch",
    },
    UnitSeed {
        key: "unit_ft",
        symbol: "ft",
        dimension: "length",
        canonical: Some("unit_m"),
        factor: "0.3048",
        offset: "0.0",
        de: "Fuß",
        en: "foot",
    },
    UnitSeed {
        key: "unit_yd",
        symbol: "yd",
        dimension: "length",
        canonical: Some("unit_m"),
        factor: "0.9144",
        offset: "0.0",
        de: "Yard",
        en: "yard",
    },
    UnitSeed {
        key: "unit_mi",
        symbol: "mi",
        dimension: "length",
        canonical: Some("unit_m"),
        factor: "1609.344",
        offset: "0.0",
        de: "Meile",
        en: "mile",
    },
    // area (canonical: m²)
    UnitSeed {
        key: "unit_m2",
        symbol: "m²",
        dimension: "area",
        canonical: None,
        factor: "1.0",
        offset: "0.0",
        de: "Quadratmeter",
        en: "square metre",
    },
    UnitSeed {
        key: "unit_cm2",
        symbol: "cm²",
        dimension: "area",
        canonical: Some("unit_m2"),
        factor: "0.0001",
        offset: "0.0",
        de: "Quadratzentimeter",
        en: "square centimetre",
    },
    UnitSeed {
        key: "unit_mm2",
        symbol: "mm²",
        dimension: "area",
        canonical: Some("unit_m2"),
        factor: "0.000001",
        offset: "0.0",
        de: "Quadratmillimeter",
        en: "square millimetre",
    },
    UnitSeed {
        key: "unit_sq_in",
        symbol: "sq in",
        dimension: "area",
        canonical: Some("unit_m2"),
        factor: "0.00064516",
        offset: "0.0",
        de: "Quadratzoll",
        en: "square inch",
    },
    UnitSeed {
        key: "unit_sq_ft",
        symbol: "sq ft",
        dimension: "area",
        canonical: Some("unit_m2"),
        factor: "0.09290304",
        offset: "0.0",
        de: "Quadratfuß",
        en: "square foot",
    },
    // volume (canonical: m³)
    UnitSeed {
        key: "unit_m3",
        symbol: "m³",
        dimension: "volume",
        canonical: None,
        factor: "1.0",
        offset: "0.0",
        de: "Kubikmeter",
        en: "cubic metre",
    },
    UnitSeed {
        key: "unit_l",
        symbol: "l",
        dimension: "volume",
        canonical: Some("unit_m3"),
        factor: "0.001",
        offset: "0.0",
        de: "Liter",
        en: "litre",
    },
    UnitSeed {
        key: "unit_ml",
        symbol: "ml",
        dimension: "volume",
        canonical: Some("unit_m3"),
        factor: "0.000001",
        offset: "0.0",
        de: "Milliliter",
        en: "millilitre",
    },
    UnitSeed {
        key: "unit_cm3",
        symbol: "cm³",
        dimension: "volume",
        canonical: Some("unit_m3"),
        factor: "0.000001",
        offset: "0.0",
        de: "Kubikzentimeter",
        en: "cubic centimetre",
    },
    UnitSeed {
        key: "unit_cu_in",
        symbol: "cu in",
        dimension: "volume",
        canonical: Some("unit_m3"),
        factor: "0.000016387064",
        offset: "0.0",
        de: "Kubikzoll",
        en: "cubic inch",
    },
    UnitSeed {
        key: "unit_cu_ft",
        symbol: "cu ft",
        dimension: "volume",
        canonical: Some("unit_m3"),
        factor: "0.028316846592",
        offset: "0.0",
        de: "Kubikfuß",
        en: "cubic foot",
    },
    UnitSeed {
        key: "unit_gal_us",
        symbol: "gal_us",
        dimension: "volume",
        canonical: Some("unit_m3"),
        factor: "0.003785411784",
        offset: "0.0",
        de: "Gallone (US)",
        en: "gallon (US)",
    },
    UnitSeed {
        key: "unit_gal_imp",
        symbol: "gal_imp",
        dimension: "volume",
        canonical: Some("unit_m3"),
        factor: "0.00454609",
        offset: "0.0",
        de: "Gallone (imp.)",
        en: "gallon (imp.)",
    },
    UnitSeed {
        key: "unit_fl_oz_us",
        symbol: "fl_oz_us",
        dimension: "volume",
        canonical: Some("unit_m3"),
        factor: "0.0000295735295625",
        offset: "0.0",
        de: "Fluid Ounce (US)",
        en: "fluid ounce (US)",
    },
    UnitSeed {
        key: "unit_fl_oz_imp",
        symbol: "fl_oz_imp",
        dimension: "volume",
        canonical: Some("unit_m3"),
        factor: "0.0000284130625",
        offset: "0.0",
        de: "Fluid Ounce (imp.)",
        en: "fluid ounce (imp.)",
    },
    // mass (canonical: kg)
    UnitSeed {
        key: "unit_kg",
        symbol: "kg",
        dimension: "mass",
        canonical: None,
        factor: "1.0",
        offset: "0.0",
        de: "Kilogramm",
        en: "kilogram",
    },
    UnitSeed {
        key: "unit_g",
        symbol: "g",
        dimension: "mass",
        canonical: Some("unit_kg"),
        factor: "0.001",
        offset: "0.0",
        de: "Gramm",
        en: "gram",
    },
    UnitSeed {
        key: "unit_mg",
        symbol: "mg",
        dimension: "mass",
        canonical: Some("unit_kg"),
        factor: "0.000001",
        offset: "0.0",
        de: "Milligramm",
        en: "milligram",
    },
    UnitSeed {
        key: "unit_t",
        symbol: "t",
        dimension: "mass",
        canonical: Some("unit_kg"),
        factor: "1000.0",
        offset: "0.0",
        de: "Tonne",
        en: "tonne",
    },
    UnitSeed {
        key: "unit_lb",
        symbol: "lb",
        dimension: "mass",
        canonical: Some("unit_kg"),
        factor: "0.45359237",
        offset: "0.0",
        de: "Pfund",
        en: "pound",
    },
    UnitSeed {
        key: "unit_oz",
        symbol: "oz",
        dimension: "mass",
        canonical: Some("unit_kg"),
        factor: "0.028349523125",
        offset: "0.0",
        de: "Unze",
        en: "ounce",
    },
    // time (canonical: s)
    UnitSeed {
        key: "unit_s",
        symbol: "s",
        dimension: "time",
        canonical: None,
        factor: "1.0",
        offset: "0.0",
        de: "Sekunde",
        en: "second",
    },
    UnitSeed {
        key: "unit_ms",
        symbol: "ms",
        dimension: "time",
        canonical: Some("unit_s"),
        factor: "0.001",
        offset: "0.0",
        de: "Millisekunde",
        en: "millisecond",
    },
    UnitSeed {
        key: "unit_min",
        symbol: "min",
        dimension: "time",
        canonical: Some("unit_s"),
        factor: "60.0",
        offset: "0.0",
        de: "Minute",
        en: "minute",
    },
    UnitSeed {
        key: "unit_h",
        symbol: "h",
        dimension: "time",
        canonical: Some("unit_s"),
        factor: "3600.0",
        offset: "0.0",
        de: "Stunde",
        en: "hour",
    },
    // voltage (canonical: V)
    UnitSeed {
        key: "unit_V",
        symbol: "V",
        dimension: "voltage",
        canonical: None,
        factor: "1.0",
        offset: "0.0",
        de: "Volt",
        en: "volt",
    },
    UnitSeed {
        key: "unit_mV",
        symbol: "mV",
        dimension: "voltage",
        canonical: Some("unit_V"),
        factor: "0.001",
        offset: "0.0",
        de: "Millivolt",
        en: "millivolt",
    },
    UnitSeed {
        key: "unit_kV",
        symbol: "kV",
        dimension: "voltage",
        canonical: Some("unit_V"),
        factor: "1000.0",
        offset: "0.0",
        de: "Kilovolt",
        en: "kilovolt",
    },
    UnitSeed {
        key: "unit_MV",
        symbol: "MV",
        dimension: "voltage",
        canonical: Some("unit_V"),
        factor: "1000000.0",
        offset: "0.0",
        de: "Megavolt",
        en: "megavolt",
    },
    // current (canonical: A)
    UnitSeed {
        key: "unit_A",
        symbol: "A",
        dimension: "current",
        canonical: None,
        factor: "1.0",
        offset: "0.0",
        de: "Ampere",
        en: "ampere",
    },
    UnitSeed {
        key: "unit_mA",
        symbol: "mA",
        dimension: "current",
        canonical: Some("unit_A"),
        factor: "0.001",
        offset: "0.0",
        de: "Milliampere",
        en: "milliampere",
    },
    UnitSeed {
        key: "unit_kA",
        symbol: "kA",
        dimension: "current",
        canonical: Some("unit_A"),
        factor: "1000.0",
        offset: "0.0",
        de: "Kiloampere",
        en: "kiloampere",
    },
    // power (canonical: W)
    UnitSeed {
        key: "unit_W",
        symbol: "W",
        dimension: "power",
        canonical: None,
        factor: "1.0",
        offset: "0.0",
        de: "Watt",
        en: "watt",
    },
    UnitSeed {
        key: "unit_mW",
        symbol: "mW",
        dimension: "power",
        canonical: Some("unit_W"),
        factor: "0.001",
        offset: "0.0",
        de: "Milliwatt",
        en: "milliwatt",
    },
    UnitSeed {
        key: "unit_kW",
        symbol: "kW",
        dimension: "power",
        canonical: Some("unit_W"),
        factor: "1000.0",
        offset: "0.0",
        de: "Kilowatt",
        en: "kilowatt",
    },
    UnitSeed {
        key: "unit_MW",
        symbol: "MW",
        dimension: "power",
        canonical: Some("unit_W"),
        factor: "1000000.0",
        offset: "0.0",
        de: "Megawatt",
        en: "megawatt",
    },
    UnitSeed {
        key: "unit_hp",
        symbol: "hp",
        dimension: "power",
        canonical: Some("unit_W"),
        factor: "745.6999",
        offset: "0.0",
        de: "Pferdestärke (mech.)",
        en: "horsepower (mech.)",
    },
    // energy (canonical: J)
    UnitSeed {
        key: "unit_J",
        symbol: "J",
        dimension: "energy",
        canonical: None,
        factor: "1.0",
        offset: "0.0",
        de: "Joule",
        en: "joule",
    },
    UnitSeed {
        key: "unit_kJ",
        symbol: "kJ",
        dimension: "energy",
        canonical: Some("unit_J"),
        factor: "1000.0",
        offset: "0.0",
        de: "Kilojoule",
        en: "kilojoule",
    },
    UnitSeed {
        key: "unit_MJ",
        symbol: "MJ",
        dimension: "energy",
        canonical: Some("unit_J"),
        factor: "1000000.0",
        offset: "0.0",
        de: "Megajoule",
        en: "megajoule",
    },
    UnitSeed {
        key: "unit_Wh",
        symbol: "Wh",
        dimension: "energy",
        canonical: Some("unit_J"),
        factor: "3600.0",
        offset: "0.0",
        de: "Wattstunde",
        en: "watt-hour",
    },
    UnitSeed {
        key: "unit_kWh",
        symbol: "kWh",
        dimension: "energy",
        canonical: Some("unit_J"),
        factor: "3600000.0",
        offset: "0.0",
        de: "Kilowattstunde",
        en: "kilowatt-hour",
    },
    // frequency (canonical: Hz)
    UnitSeed {
        key: "unit_Hz",
        symbol: "Hz",
        dimension: "frequency",
        canonical: None,
        factor: "1.0",
        offset: "0.0",
        de: "Hertz",
        en: "hertz",
    },
    UnitSeed {
        key: "unit_kHz",
        symbol: "kHz",
        dimension: "frequency",
        canonical: Some("unit_Hz"),
        factor: "1000.0",
        offset: "0.0",
        de: "Kilohertz",
        en: "kilohertz",
    },
    UnitSeed {
        key: "unit_MHz",
        symbol: "MHz",
        dimension: "frequency",
        canonical: Some("unit_Hz"),
        factor: "1000000.0",
        offset: "0.0",
        de: "Megahertz",
        en: "megahertz",
    },
    UnitSeed {
        key: "unit_GHz",
        symbol: "GHz",
        dimension: "frequency",
        canonical: Some("unit_Hz"),
        factor: "1000000000.0",
        offset: "0.0",
        de: "Gigahertz",
        en: "gigahertz",
    },
    // force (canonical: N)
    UnitSeed {
        key: "unit_N",
        symbol: "N",
        dimension: "force",
        canonical: None,
        factor: "1.0",
        offset: "0.0",
        de: "Newton",
        en: "newton",
    },
    UnitSeed {
        key: "unit_kN",
        symbol: "kN",
        dimension: "force",
        canonical: Some("unit_N"),
        factor: "1000.0",
        offset: "0.0",
        de: "Kilonewton",
        en: "kilonewton",
    },
    UnitSeed {
        key: "unit_MN",
        symbol: "MN",
        dimension: "force",
        canonical: Some("unit_N"),
        factor: "1000000.0",
        offset: "0.0",
        de: "Meganewton",
        en: "meganewton",
    },
    // pressure (canonical: Pa)
    UnitSeed {
        key: "unit_Pa",
        symbol: "Pa",
        dimension: "pressure",
        canonical: None,
        factor: "1.0",
        offset: "0.0",
        de: "Pascal",
        en: "pascal",
    },
    UnitSeed {
        key: "unit_kPa",
        symbol: "kPa",
        dimension: "pressure",
        canonical: Some("unit_Pa"),
        factor: "1000.0",
        offset: "0.0",
        de: "Kilopascal",
        en: "kilopascal",
    },
    UnitSeed {
        key: "unit_MPa",
        symbol: "MPa",
        dimension: "pressure",
        canonical: Some("unit_Pa"),
        factor: "1000000.0",
        offset: "0.0",
        de: "Megapascal",
        en: "megapascal",
    },
    UnitSeed {
        key: "unit_bar",
        symbol: "bar",
        dimension: "pressure",
        canonical: Some("unit_Pa"),
        factor: "100000.0",
        offset: "0.0",
        de: "Bar",
        en: "bar",
    },
    UnitSeed {
        key: "unit_mbar",
        symbol: "mbar",
        dimension: "pressure",
        canonical: Some("unit_Pa"),
        factor: "100.0",
        offset: "0.0",
        de: "Millibar",
        en: "millibar",
    },
    UnitSeed {
        key: "unit_psi",
        symbol: "psi",
        dimension: "pressure",
        canonical: Some("unit_Pa"),
        factor: "6894.757",
        offset: "0.0",
        de: "Pfund pro Quadratzoll",
        en: "pounds per square inch",
    },
    // temperature (canonical: K)
    UnitSeed {
        key: "unit_K",
        symbol: "K",
        dimension: "temperature",
        canonical: None,
        factor: "1.0",
        offset: "0.0",
        de: "Kelvin",
        en: "kelvin",
    },
    UnitSeed {
        key: "unit_degC",
        symbol: "°C",
        dimension: "temperature",
        canonical: Some("unit_K"),
        factor: "1.0",
        offset: "273.15",
        de: "Grad Celsius",
        en: "degree Celsius",
    },
    UnitSeed {
        key: "unit_degF",
        symbol: "°F",
        dimension: "temperature",
        canonical: Some("unit_K"),
        factor: "0.5555555555555556",
        offset: "255.37222222222223",
        de: "Grad Fahrenheit",
        en: "degree Fahrenheit",
    },
];

fn unit_upsert(u: &UnitSeed) -> String {
    let canonical = match u.canonical {
        Some(key) => format!("'{key}'"),
        None => "NONE".to_owned(),
    };
    format!(
        "INSERT INTO unit (id, symbol, dimension, canonical_unit_id, factor, offset, label, system, readonly, created_at, updated_at) VALUES (unit:{}, '{}', '{}', {}, {}, {}, {{ de: '{}', en: '{}' }}, 'default', true, time::now(), time::now()) ON DUPLICATE KEY UPDATE id = id;",
        u.key, u.symbol, u.dimension, canonical, u.factor, u.offset, u.de, u.en,
    )
}

/// Default language tree: `root` plus the main ISO 639-1 languages and their
/// regional variants (see `plan/detail/sprachbaum-mehrsprachigkeit.md`).
const LANG_TREE: &[LangSeed] = &[
    LangSeed {
        code: "root",
        parent: None,
        fallback_order: &[],
        de: "Wurzel",
        en: "Root",
    },
    LangSeed {
        code: "en",
        parent: Some("lang_root"),
        fallback_order: &["root"],
        de: "Englisch",
        en: "English",
    },
    LangSeed {
        code: "de",
        parent: Some("lang_root"),
        fallback_order: &["root"],
        de: "Deutsch",
        en: "German",
    },
    LangSeed {
        code: "fr",
        parent: Some("lang_root"),
        fallback_order: &["root"],
        de: "Französisch",
        en: "French",
    },
    LangSeed {
        code: "en-GB",
        parent: Some("lang_en"),
        fallback_order: &["en", "root"],
        de: "Englisch (UK)",
        en: "English (United Kingdom)",
    },
    LangSeed {
        code: "en-US",
        parent: Some("lang_en"),
        fallback_order: &["en", "root"],
        de: "Englisch (US)",
        en: "English (United States)",
    },
    LangSeed {
        code: "de-DE",
        parent: Some("lang_de"),
        fallback_order: &["de", "root"],
        de: "Deutsch (Deutschland)",
        en: "German (Germany)",
    },
    LangSeed {
        code: "de-AT",
        parent: Some("lang_de"),
        fallback_order: &["de", "root"],
        de: "Deutsch (Österreich)",
        en: "German (Austria)",
    },
    LangSeed {
        code: "fr-FR",
        parent: Some("lang_fr"),
        fallback_order: &["fr", "root"],
        de: "Französisch (Frankreich)",
        en: "French (France)",
    },
    LangSeed {
        code: "fr-CA",
        parent: Some("lang_fr"),
        fallback_order: &["fr", "root"],
        de: "Französisch (Kanada)",
        en: "French (Canada)",
    },
];

fn lang_upsert(lang: &LangSeed) -> String {
    let parent = match lang.parent {
        Some(parent) => format!("'{parent}'"),
        None => "NONE".to_owned(),
    };
    let fallback_order = lang
        .fallback_order
        .iter()
        .map(|code| format!("'{code}'"))
        .collect::<Vec<_>>()
        .join(", ");
    // Base languages (root's children) are offered as UI languages.
    let is_ui = lang.parent == Some("lang_root") && lang.code != "root";
    format!(
        "INSERT INTO language_tree (id, code, parent, label, fallback_order, active, is_ui) VALUES (type::record('language_tree', 'lang_{}'), '{}', {}, {{ de: '{}', en: '{}' }}, [{}], true, {}) ON DUPLICATE KEY UPDATE id = id;",
        lang.code, lang.code, parent, lang.de, lang.en, fallback_order, is_ui,
    )
}

/// Seeds system data idempotently.
pub async fn apply(db: &Db) -> Result<(), surrealdb::Error> {
    let mut sql = String::from(RELATION_DEFINITIONS);
    sql.push_str(ROOT_OBJECT);
    sql.push_str(OBJECT_TYPES);
    for unit in UNITS {
        sql.push_str(&unit_upsert(unit));
    }
    for lang in LANG_TREE {
        sql.push_str(&lang_upsert(lang));
    }
    db.inner().query(sql).await?.check().map(|_| ())
}

/// Ensures a default `superadmin` user exists (development bootstrap only).
pub async fn ensure_default_admin(db: &Db) -> Result<(), ApiError> {
    let mut response = db
        .inner()
        .query("SELECT count() FROM user WHERE username = 'admin' GROUP ALL")
        .await?
        .check()?;
    let counts: Vec<serde_json::Value> = response.take(0)?;
    let count = counts
        .first()
        .and_then(|entry| entry.get("count"))
        .and_then(serde_json::Value::as_u64)
        .unwrap_or(0);
    if count > 0 {
        return Ok(());
    }

    let password_hash =
        auth::hash_password("admin").map_err(|error| ApiError::internal(error.to_string()))?;

    let sql = "
        UPSERT user:admin CONTENT {
            username: 'admin',
            email: 'admin@example.com',
            password_hash: $password_hash,
            auth_provider: 'local',
            roles: ['superadmin'],
            status: 'active',
            token_version: 1,
            must_change_password: true,
            locale: 'en'
        };
    ";
    db.inner()
        .query(sql)
        .bind(("password_hash", password_hash))
        .await?
        .check()
        .map(|_| ())
        .map_err(ApiError::from)
}
