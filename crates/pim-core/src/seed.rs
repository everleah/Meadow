//! Seed data: system relation types, the universal root object, and the unit
//! catalog (Phase 1).
//!
//! Records are seeded idempotently with `UPSERT`; system records are owned by
//! this seed (they are `readonly`/`system` and must not be edited at runtime).

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
const RELATION_DEFINITIONS: &str = r#"
UPSERT relation_definition:rel_depends_on CONTENT { name: 'depends_on', kind: 'composition', source_types: [], target_types: [], label: { de: 'Abhängig von', en: 'Depends on' }, attributes: [], system: true, created_at: time::now(), updated_at: time::now() };
UPSERT relation_definition:rel_category_member CONTENT { name: 'category_member', kind: 'aggregation', source_types: [], target_types: [], label: { de: 'Kategorie-Zuordnung', en: 'Category membership' }, attributes: [], system: true, created_at: time::now(), updated_at: time::now() };
UPSERT relation_definition:rel_collection_member CONTENT { name: 'collection_member', kind: 'aggregation', source_types: [], target_types: [], label: { de: 'Collection-Zuordnung', en: 'Collection membership' }, attributes: [], system: true, created_at: time::now(), updated_at: time::now() };
UPSERT relation_definition:rel_related CONTENT { name: 'related', kind: 'aggregation', source_types: [], target_types: [], label: { de: 'Verknüpft mit', en: 'Related to' }, attributes: [], system: true, created_at: time::now(), updated_at: time::now() };
"#;

/// The universal root: the composition tree's root, structurally immutable.
const ROOT_OBJECT: &str = r#"
UPSERT object:object_root CONTENT { type: 'root', attributes: {}, status: 'published', version: 1, created_at: time::now(), updated_at: time::now() };
"#;

/// Managed object-type registry (STIBO STEP / Akeneo family model). Seeded
/// idempotently; `key_attributes` start empty (the admin assigns the unique +
/// mandatory keys per type), `label_attribute` defaults to the `name` attribute
/// for business types.
const OBJECT_TYPES: &str = r#"
UPSERT object_type:product CONTENT { name: 'product', label: { de: 'Produkt', en: 'Product' }, description: '', key_attributes: [], label_attribute: 'name', system: true, created_at: time::now(), updated_at: time::now() };
UPSERT object_type:category CONTENT { name: 'category', label: { de: 'Kategorie', en: 'Category' }, description: '', key_attributes: [], label_attribute: 'name', system: true, created_at: time::now(), updated_at: time::now() };
UPSERT object_type:asset CONTENT { name: 'asset', label: { de: 'Asset', en: 'Asset' }, description: '', key_attributes: [], label_attribute: 'name', system: true, created_at: time::now(), updated_at: time::now() };
UPSERT object_type:variant CONTENT { name: 'variant', label: { de: 'Variante', en: 'Variant' }, description: '', key_attributes: [], label_attribute: 'name', system: true, created_at: time::now(), updated_at: time::now() };
UPSERT object_type:root CONTENT { name: 'root', label: { de: 'Universal-Root', en: 'Universal root' }, description: 'Der übergeordnete Universal-Root', key_attributes: [], system: true, created_at: time::now(), updated_at: time::now() };
UPSERT object_type:saved_search CONTENT { name: 'saved_search', label: { de: 'Gespeicherte Suche', en: 'Saved search' }, description: '', key_attributes: [], label_attribute: 'name', system: true, created_at: time::now(), updated_at: time::now() };
UPSERT object_type:collection CONTENT { name: 'collection', label: { de: 'Collection', en: 'Collection' }, description: '', key_attributes: [], label_attribute: 'name', system: true, created_at: time::now(), updated_at: time::now() };
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
        "UPSERT unit:{} CONTENT {{ symbol: '{}', dimension: '{}', canonical_unit_id: {}, factor: {}, offset: {}, label: {{ de: '{}', en: '{}' }}, system: 'default', readonly: true, created_at: time::now(), updated_at: time::now() }};",
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
        "UPSERT type::record('language_tree', 'lang_{}') CONTENT {{ code: '{}', parent: {}, label: {{ de: '{}', en: '{}' }}, fallback_order: [{}], active: true, is_ui: {} }};",
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
