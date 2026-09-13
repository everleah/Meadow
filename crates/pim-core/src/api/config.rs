//! Configuration export/import (Meadow Configuration Format, MCF) — Phase 9.
//!
//! `GET /config/export` serialises the data model & reference data (object
//! types, attribute definitions, attribute groups, enum sets, units, relation
//! definitions, language tree, business rules) as an **MCF** document, using
//! **stable keys** (name / code / symbol) instead of local ULIDs so the
//! document can be imported into another environment. The companion import
//! (`POST /config/import`) is a later step of Phase 9.

use std::collections::HashMap;

use axum::Json;
use axum::extract::{Query, State};
use axum::http::{HeaderMap, StatusCode};
use serde::Deserialize;
use serde_json::{Map, Value, json};
use utoipa::{IntoParams, ToSchema};

use crate::api::jobs::queue_run;
use crate::app::AppState;
use crate::auth::{AuthUser, ROLE_ADMIN, api_key_or_role};
use crate::error::ApiError;

// The class-feature section lives in its own file (ConfigReport/Mode and the
// section helpers stay private to this module, so the file sits inside `config/`).
mod category_attributes;
use category_attributes::{apply_category_attributes, category_attributes_section};

/// Query parameters for `GET /config/export`.
#[derive(Debug, Deserialize, ToSchema, IntoParams)]
#[into_params(parameter_in = Query)]
pub struct ConfigExportQuery {
    /// Comma-separated section names (`languages,units,object_types,...`);
    /// empty or `all` = all supported sections.
    #[serde(default)]
    pub sections: Option<String>,
    /// Include seeded/system entries (units with `system=default`, object types
    /// and relation definitions with `system=true`). Default `false`.
    #[serde(default)]
    pub include_system: bool,
}

/// The supported MCF sections, in dependency order.
const SECTIONS: &[&str] = &[
    "languages",
    "channels",
    "units",
    "attribute_groups",
    "enum_sets",
    "object_types",
    "attribute_definitions",
    "relation_definitions",
    "business_rules",
    "category_attributes",
];

fn parse_sections(raw: Option<&str>) -> Result<Vec<String>, ApiError> {
    let raw = raw.unwrap_or("all").trim();
    if raw.is_empty() || raw.eq_ignore_ascii_case("all") {
        return Ok(SECTIONS.iter().map(|s| s.to_string()).collect());
    }
    let mut result = Vec::new();
    for name in raw.split(',') {
        let name = name.trim();
        if name.is_empty() {
            continue;
        }
        if !SECTIONS.contains(&name) {
            return Err(ApiError::bad_request(format!("unknown section `{name}`")));
        }
        result.push(name.to_string());
    }
    Ok(result)
}

/// Runs a simple (bind-free) SELECT and returns all rows.
async fn query_rows(db: &crate::db::Db, sql: &str) -> Result<Vec<Value>, ApiError> {
    let mut response = db.inner().query(sql).await?.check()?;
    Ok(response.take(0)?)
}

/// Strips a `<table>:` prefix (e.g. `unit:unit_m` → `unit_m`). If the prefix is
/// present but does not match `table`, returns `None`.
fn bare<'a>(id: &'a str, table: &str) -> Option<&'a str> {
    let prefix = format!("{table}:");
    id.strip_prefix(&prefix)
}

fn as_str<'a>(value: &'a Value, field: &str) -> Option<&'a str> {
    value.get(field).and_then(Value::as_str)
}

fn as_bool(value: &Value, field: &str) -> bool {
    value.get(field).and_then(Value::as_bool).unwrap_or(false)
}

fn as_string_array(value: &Value, field: &str) -> Vec<String> {
    value
        .get(field)
        .and_then(Value::as_array)
        .map(|arr| {
            arr.iter()
                .filter_map(Value::as_str)
                .map(str::to_owned)
                .collect()
        })
        .unwrap_or_default()
}

/// Builds `symbol/record-key/full-id → symbol` maps from the unit table so any
/// unit reference (symbol, bare key like `unit_mm`, or full `unit:unit_mm`) can
/// be resolved to its symbol.
async fn unit_symbol_maps(db: &crate::db::Db) -> Result<HashMap<String, String>, ApiError> {
    let units = query_rows(db, "SELECT id, symbol FROM unit;").await?;
    let mut map = HashMap::new();
    for unit in units {
        let symbol = as_str(&unit, "symbol").unwrap_or_default().to_owned();
        if symbol.is_empty() {
            continue;
        }
        map.insert(symbol.clone(), symbol.clone());
        if let Some(id) = as_str(&unit, "id") {
            map.insert(id.to_owned(), symbol.clone());
            if let Some(key) = bare(id, "unit") {
                map.insert(key.to_owned(), symbol.clone());
            }
        }
    }
    Ok(map)
}

/// Builds `full-id → name` (and `name → name`) maps from the attribute-group and
/// enum-set tables so their record-id references resolve to stable names.
async fn group_name_map(db: &crate::db::Db) -> Result<HashMap<String, String>, ApiError> {
    let groups = query_rows(db, "SELECT id, name FROM attribute_group;").await?;
    let mut map = HashMap::new();
    for group in groups {
        let name = as_str(&group, "name").unwrap_or_default().to_owned();
        map.insert(name.clone(), name.clone());
        if let Some(id) = as_str(&group, "id") {
            map.insert(id.to_owned(), name.clone());
        }
    }
    Ok(map)
}

async fn enum_set_name_map(db: &crate::db::Db) -> Result<HashMap<String, String>, ApiError> {
    let sets = query_rows(db, "SELECT id, name FROM enum_set;").await?;
    let mut map = HashMap::new();
    for set in sets {
        let name = as_str(&set, "name").unwrap_or_default().to_owned();
        map.insert(name.clone(), name.clone());
        if let Some(id) = as_str(&set, "id") {
            map.insert(id.to_owned(), name.clone());
        }
    }
    Ok(map)
}

/// Resolves a reference to a symbol via the unit map, falling back to the
/// reference itself (so an already-symbol value is preserved).
fn resolve_symbol(map: &HashMap<String, String>, reference: &str) -> String {
    map.get(reference)
        .cloned()
        .unwrap_or_else(|| reference.to_owned())
}

/// Strips the `lang_` record-key prefix to get a language code (`lang_root` → `root`).
fn lang_code(reference: &str) -> String {
    reference
        .strip_prefix("lang_")
        .unwrap_or(reference)
        .to_owned()
}

async fn languages_section(db: &crate::db::Db) -> Result<Vec<Value>, ApiError> {
    let rows = query_rows(
        db,
        "SELECT code, parent, label, active, is_ui FROM language_tree ORDER BY code;",
    )
    .await?;
    Ok(rows
        .into_iter()
        .map(|row| {
            let parent = row.get("parent").and_then(Value::as_str).map(lang_code);
            json!({
                "code": as_str(&row, "code").unwrap_or_default(),
                "parent": parent,
                "label": row.get("label").cloned().unwrap_or(Value::Null),
                "active": as_bool(&row, "active"),
                "is_ui": as_bool(&row, "is_ui"),
            })
        })
        .collect())
}

async fn units_section(
    db: &crate::db::Db,
    include_system: bool,
    symbol_map: &HashMap<String, String>,
) -> Result<Vec<Value>, ApiError> {
    let rows = query_rows(db, "SELECT * FROM unit ORDER BY dimension, symbol;").await?;
    let mut result = Vec::new();
    for unit in rows {
        let system = as_str(&unit, "system").unwrap_or_default().to_owned();
        if !include_system && system == "default" {
            continue;
        }
        let canonical = as_str(&unit, "canonical_unit_id");
        let canonical_unit = canonical.map(|c| resolve_symbol(symbol_map, c));
        result.push(json!({
            "symbol": as_str(&unit, "symbol").unwrap_or_default(),
            "dimension": as_str(&unit, "dimension").unwrap_or_default(),
            "canonical_unit": canonical_unit,
            "factor": unit.get("factor").cloned().unwrap_or(Value::Null),
            "offset": unit.get("offset").cloned().unwrap_or(Value::Null),
            "label": unit.get("label").cloned().unwrap_or(Value::Null),
            "system": system,
        }));
    }
    Ok(result)
}

/// Serialises the channel catalog (stable key = `code`).
async fn channels_section(db: &crate::db::Db) -> Result<Vec<Value>, ApiError> {
    let rows = query_rows(db, "SELECT * FROM channel ORDER BY position, code;").await?;
    Ok(rows
        .into_iter()
        .map(|channel| {
            json!({
                "code": as_str(&channel, "code").unwrap_or_default(),
                "label": channel.get("label").cloned().unwrap_or_else(|| json!({})),
                "description": channel.get("description").cloned().unwrap_or(Value::Null),
                "active": channel.get("active").cloned().unwrap_or(json!(true)),
                "position": channel.get("position").cloned().unwrap_or(json!(0)),
            })
        })
        .collect())
}

async fn attribute_groups_section(db: &crate::db::Db) -> Result<Vec<Value>, ApiError> {
    let rows = query_rows(db, "SELECT * FROM attribute_group ORDER BY position;").await?;
    Ok(rows
        .into_iter()
        .map(|group| {
            json!({
                "name": as_str(&group, "name").unwrap_or_default(),
                "label": group.get("label").cloned().unwrap_or(Value::Null),
                "position": group.get("position").cloned().unwrap_or(json!(0)),
                "applicable_types": Value::Array(
                    as_string_array(&group, "applicable_types").into_iter().map(Value::from).collect()
                ),
            })
        })
        .collect())
}

async fn enum_sets_section(db: &crate::db::Db) -> Result<Vec<Value>, ApiError> {
    let sets = query_rows(db, "SELECT * FROM enum_set ORDER BY position, name;").await?;
    let mut result = Vec::new();
    for set in sets {
        let options = resolve_options(db, &set).await?;
        result.push(json!({
            "name": as_str(&set, "name").unwrap_or_default(),
            "label": set.get("label").cloned().unwrap_or(Value::Null),
            "position": set.get("position").cloned().unwrap_or(json!(0)),
            "options": Value::Array(options),
        }));
    }
    Ok(result)
}

/// Resolves a set's `options` (record keys) to their option records, emitting
/// `{code, labels, position, active}` (the stable-key form).
async fn resolve_options(db: &crate::db::Db, set: &Value) -> Result<Vec<Value>, ApiError> {
    let option_keys: Vec<String> = set
        .get("options")
        .and_then(Value::as_array)
        .map(|a| {
            a.iter()
                .filter_map(Value::as_str)
                .map(|s| {
                    bare(s, "enum_option")
                        .map(str::to_owned)
                        .unwrap_or_else(|| s.to_owned())
                })
                .collect()
        })
        .unwrap_or_default();
    if option_keys.is_empty() {
        return Ok(vec![]);
    }
    let sql = "LET $recs = array::map($keys, |$k| type::record('enum_option', $k)); \
               SELECT * FROM enum_option WHERE id IN $recs ORDER BY position;";
    let mut response = db
        .inner()
        .query(sql)
        .bind(("keys", option_keys))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(1)?;
    Ok(rows
        .into_iter()
        .map(|option| {
            json!({
                "code": as_str(&option, "code").unwrap_or_default(),
                "labels": option.get("labels").cloned().unwrap_or(Value::Null),
                "position": option.get("position").cloned().unwrap_or(json!(0)),
                "active": as_bool(&option, "active"),
            })
        })
        .collect())
}

async fn object_types_section(
    db: &crate::db::Db,
    include_system: bool,
) -> Result<Vec<Value>, ApiError> {
    let rows = query_rows(db, "SELECT * FROM object_type ORDER BY name;").await?;
    let mut result = Vec::new();
    for object_type in rows {
        if !include_system && as_bool(&object_type, "system") {
            continue;
        }
        result.push(json!({
            "name": as_str(&object_type, "name").unwrap_or_default(),
            "label": object_type.get("label").cloned().unwrap_or(Value::Null),
            "description": object_type.get("description").cloned().unwrap_or(Value::Null),
            "key_attributes": Value::Array(
                as_string_array(&object_type, "key_attributes").into_iter().map(Value::from).collect()
            ),
            "label_attribute": object_type.get("label_attribute").cloned().unwrap_or(Value::Null),
            // Taxonomy node types (`plan/detail/kategorisierung.md`, Stufe A) must
            // travel with the model: without the flag a categorisation into the
            // imported type would be rejected in the target environment.
            "taxonomy": as_bool(&object_type, "taxonomy"),
            "system": as_bool(&object_type, "system"),
        }));
    }
    Ok(result)
}

async fn attribute_definitions_section(
    db: &crate::db::Db,
    group_map: &HashMap<String, String>,
    unit_map: &HashMap<String, String>,
    enum_map: &HashMap<String, String>,
) -> Result<Vec<Value>, ApiError> {
    let rows = query_rows(db, "SELECT * FROM attribute_definition ORDER BY name;").await?;
    let mut result = Vec::new();
    for definition in rows {
        let group_ids: Vec<String> = definition
            .get("group_ids")
            .and_then(Value::as_array)
            .map(|arr| {
                arr.iter()
                    .filter_map(Value::as_str)
                    .map(|g| group_map.get(g).cloned().unwrap_or_else(|| g.to_owned()))
                    .collect()
            })
            .unwrap_or_default();
        let unit = as_str(&definition, "unit_id").map(|u| resolve_symbol(unit_map, u));
        let display_unit =
            as_str(&definition, "display_unit_id").map(|u| resolve_symbol(unit_map, u));
        let enum_set = as_str(&definition, "enum_set_id")
            .map(|e| enum_map.get(e).cloned().unwrap_or_else(|| e.to_owned()));
        result.push(json!({
            "name": as_str(&definition, "name").unwrap_or_default(),
            "label": definition.get("label").cloned().unwrap_or(Value::Null),
            "type": as_str(&definition, "type").unwrap_or_default(),
            "group_ids": Value::Array(group_ids.into_iter().map(Value::from).collect()),
            "inheritable": as_bool(&definition, "inheritable"),
            "is_key": as_bool(&definition, "is_key"),
            "multilingual": as_bool(&definition, "multilingual"),
            "unit": unit,
            "display_unit": display_unit,
            "enum_set": enum_set,
            "standard_codes": definition.get("standard_codes").cloned().unwrap_or(Value::Null),
            "applicable_types": Value::Array(
                as_string_array(&definition, "applicable_types").into_iter().map(Value::from).collect()
            ),
            "required_types": Value::Array(
                as_string_array(&definition, "required_types").into_iter().map(Value::from).collect()
            ),
            "computed": as_bool(&definition, "computed"),
            "computed_rule": definition.get("computed_rule").cloned().unwrap_or(Value::Null),
            "dependencies": Value::Array(
                as_string_array(&definition, "dependencies").into_iter().map(Value::from).collect()
            ),
            "computed_reads_inherited": as_bool(&definition, "computed_reads_inherited"),
            "constraints": definition.get("constraints").cloned().unwrap_or(Value::Null),
            "default_value": definition.get("default_value").cloned().unwrap_or(Value::Null),
            "description": definition.get("description").cloned().unwrap_or(Value::Null),
        }));
    }
    Ok(result)
}

async fn relation_definitions_section(
    db: &crate::db::Db,
    include_system: bool,
) -> Result<Vec<Value>, ApiError> {
    let rows = query_rows(db, "SELECT * FROM relation_definition ORDER BY name;").await?;
    let mut result = Vec::new();
    for definition in rows {
        if !include_system && as_bool(&definition, "system") {
            continue;
        }
        result.push(json!({
            "name": as_str(&definition, "name").unwrap_or_default(),
            "kind": as_str(&definition, "kind").unwrap_or_default(),
            "source_types": Value::Array(
                as_string_array(&definition, "source_types").into_iter().map(Value::from).collect()
            ),
            "target_types": Value::Array(
                as_string_array(&definition, "target_types").into_iter().map(Value::from).collect()
            ),
            "label": definition.get("label").cloned().unwrap_or(Value::Null),
            "attributes": Value::Array(
                as_string_array(&definition, "attributes").into_iter().map(Value::from).collect()
            ),
            "system": as_bool(&definition, "system"),
        }));
    }
    Ok(result)
}

async fn business_rules_section(db: &crate::db::Db) -> Result<Vec<Value>, ApiError> {
    let rows = query_rows(db, "SELECT * FROM business_rule ORDER BY name;").await?;
    Ok(rows
        .into_iter()
        .map(|rule| {
            json!({
                "name": as_str(&rule, "name").unwrap_or_default(),
                "type": as_str(&rule, "type").unwrap_or_default(),
                "applies_to": Value::Array(
                    as_string_array(&rule, "applies_to").into_iter().map(Value::from).collect()
                ),
                "script": rule.get("script").cloned().unwrap_or(Value::Null),
                "state": as_str(&rule, "state").unwrap_or("draft"),
            })
        })
        .collect())
}

/// Exports the configuration as an MCF document (admin/superadmin).
#[utoipa::path(
    get,
    path = "/config/export",
    params(ConfigExportQuery),
    responses(
        (status = 200, description = "Configuration (MCF) document", body = Value),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Forbidden", body = Value)
    )
)]
pub async fn export_config(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Query(params): Query<ConfigExportQuery>,
) -> Result<Json<Value>, ApiError> {
    if !claims.is_admin() {
        return Err(ApiError::forbidden("requires admin role"));
    }
    let sections = parse_sections(params.sections.as_deref())?;
    let include_system = params.include_system;

    // Preload the reference maps needed by the attribute-definition section.
    let unit_map = unit_symbol_maps(&state.db).await?;
    let group_map = group_name_map(&state.db).await?;
    let enum_map = enum_set_name_map(&state.db).await?;

    let mut out = Map::new();
    for section in &sections {
        let value = match section.as_str() {
            "languages" => languages_section(&state.db).await?,
            "channels" => channels_section(&state.db).await?,
            "units" => units_section(&state.db, include_system, &unit_map).await?,
            "attribute_groups" => attribute_groups_section(&state.db).await?,
            "enum_sets" => enum_sets_section(&state.db).await?,
            "object_types" => object_types_section(&state.db, include_system).await?,
            "attribute_definitions" => {
                attribute_definitions_section(&state.db, &group_map, &unit_map, &enum_map).await?
            }
            "relation_definitions" => {
                relation_definitions_section(&state.db, include_system).await?
            }
            "business_rules" => business_rules_section(&state.db).await?,
            "category_attributes" => category_attributes_section(&state.db).await?,
            _ => unreachable!("sections were validated by parse_sections"),
        };
        out.insert(section.clone(), Value::Array(value));
    }

    // Best-effort UTC RFC3339 timestamp (no chrono dependency in the core; the
    // datetime JSON codec elsewhere uses ISO-8601 strings, so we mirror that).
    let created_at = json!(utc_now_iso());

    Ok(Json(json!({
        "format_version": "1.0",
        "kind": "configuration",
        "created_at": created_at,
        "sections": Value::Object(out),
    })))
}

/// Returns the current UTC time as an RFC3339/ISO-8601 string (`…Z`).
fn utc_now_iso() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let duration = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default();
    let secs = duration.as_secs();
    let millis = duration.subsec_millis();
    let day = secs / 86400;
    let rem = secs % 86400;
    let (hour, minute, second) = (rem / 3600, (rem % 3600) / 60, rem % 60);
    let (year, month, day) = civil_from_days(day as i64);
    format!("{year:04}-{month:02}-{day:02}T{hour:02}:{minute:02}:{second:02}.{millis:03}Z")
}

/// Converts days since the Unix epoch to a proleptic-Gregorian `(year, month, day)`.
fn civil_from_days(days_since_epoch: i64) -> (i64, u32, u32) {
    // Howard Hinnant's `civil_from_days` algorithm.
    let z = days_since_epoch + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let year = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = if month <= 2 { year + 1 } else { year };
    (year, month as u32, day as u32)
}

// ---------------------------------------------------------------------------
// Import (`POST /config/import`)
// ---------------------------------------------------------------------------

/// Query parameters for `POST /config/import`.
#[derive(Debug, Deserialize, ToSchema, IntoParams)]
#[into_params(parameter_in = Query)]
pub struct ConfigImportQuery {
    /// `dry_run` (preview, default when using `?dry_run=true`), `merge` (default) or `replace`.
    #[serde(default)]
    pub mode: Option<String>,
    /// Alias for `mode=dry_run`.
    #[serde(default)]
    pub dry_run: bool,
    /// When `true`, store the document and queue a `config_import` job instead
    /// of applying synchronously (202 + `job_run_id`).
    #[serde(default, rename = "async")]
    pub async_: bool,
}

/// The configured import mode.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum Mode {
    DryRun,
    Merge,
    Replace,
}

impl Mode {
    fn label(&self) -> &'static str {
        match self {
            Mode::DryRun => "dry_run",
            Mode::Merge => "merge",
            Mode::Replace => "replace",
        }
    }
}

fn parse_mode(query: &ConfigImportQuery) -> Result<Mode, ApiError> {
    if query.dry_run {
        return Ok(Mode::DryRun);
    }
    let mode = query.mode.as_deref().unwrap_or("merge");
    match mode {
        "dry_run" => Ok(Mode::DryRun),
        "merge" => Ok(Mode::Merge),
        "replace" => Ok(Mode::Replace),
        _ => Err(ApiError::bad_request(format!(
            "unsupported mode `{mode}` (expected dry_run | merge | replace)"
        ))),
    }
}

/// Summary counters for the import report.
#[derive(Debug, Default, serde::Serialize)]
struct Summary {
    created: usize,
    updated: usize,
    skipped: usize,
    deleted: usize,
    errors: usize,
    warnings: usize,
}

/// Per-section detail for the import report.
#[derive(Debug, Default, serde::Serialize)]
struct SectionReport {
    created: Vec<String>,
    updated: Vec<String>,
    skipped: Vec<String>,
    deleted: Vec<String>,
    errors: Vec<String>,
}

#[derive(Debug, serde::Serialize)]
struct ReportItem {
    section: String,
    key: String,
    message: String,
}

/// The import report structure.
#[derive(Debug, serde::Serialize)]
struct ConfigReport {
    mode: String,
    summary: Summary,
    sections: std::collections::BTreeMap<String, SectionReport>,
    errors: Vec<ReportItem>,
    warnings: Vec<ReportItem>,
}

impl ConfigReport {
    fn new(mode: Mode) -> Self {
        ConfigReport {
            mode: mode.label().to_owned(),
            summary: Summary::default(),
            sections: std::collections::BTreeMap::new(),
            errors: Vec::new(),
            warnings: Vec::new(),
        }
    }

    fn section(&mut self, name: &str) -> &mut SectionReport {
        self.sections.entry(name.to_owned()).or_default()
    }

    fn created(&mut self, section: &str, key: impl Into<String>) {
        self.section(section).created.push(key.into());
        self.summary.created += 1;
    }

    fn updated(&mut self, section: &str, key: impl Into<String>) {
        self.section(section).updated.push(key.into());
        self.summary.updated += 1;
    }

    fn skipped(&mut self, section: &str, key: impl Into<String>) {
        self.section(section).skipped.push(key.into());
        self.summary.skipped += 1;
    }

    fn deleted(&mut self, section: &str, key: impl Into<String>) {
        self.section(section).deleted.push(key.into());
        self.summary.deleted += 1;
    }

    fn error(&mut self, section: &str, key: impl Into<String>, message: impl Into<String>) {
        let key = key.into();
        self.section(section).errors.push(key.clone());
        self.errors.push(ReportItem {
            section: section.to_owned(),
            key,
            message: message.into(),
        });
        self.summary.errors += 1;
    }

    fn warn(&mut self, section: &str, key: impl Into<String>, message: impl Into<String>) {
        self.warnings.push(ReportItem {
            section: section.to_owned(),
            key: key.into(),
            message: message.into(),
        });
        self.summary.warnings += 1;
    }
}

/// Tracks stable-key → target record-key resolution as entities are applied.
#[derive(Default)]
struct Resolve {
    /// language code -> `lang_<code>`
    language: HashMap<String, String>,
    /// unit symbol -> bare unit record key
    unit: HashMap<String, String>,
    /// group name -> bare attribute_group record key
    group: HashMap<String, String>,
    /// enum set name -> bare enum_set record key
    enum_set: HashMap<String, String>,
}

/// Extracts the bare record key (`table:key` → `key`) from a record `id`.
fn id_of(record: &Value, table: &str) -> String {
    record
        .get("id")
        .and_then(Value::as_str)
        .map(|id| bare(id, table).unwrap_or(id).to_owned())
        .unwrap_or_default()
}

/// Runs a `WHERE <field> = $key` lookup and returns the matching record.
async fn fetch_by_field(
    db: &crate::db::Db,
    table: &str,
    field: &str,
    key: &str,
) -> Result<Option<Value>, ApiError> {
    let sql = format!("SELECT * FROM {table} WHERE {field} = $key LIMIT 1;");
    let mut response = db
        .inner()
        .query(&sql)
        .bind(("key", key.to_owned()))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    Ok(rows.into_iter().next())
}

/// Creates a record keyed by `key` (`table:<key>`) and returns the created record.
async fn create_keyed(
    db: &crate::db::Db,
    table: &str,
    key: &str,
    content: Value,
) -> Result<Value, ApiError> {
    let sql = format!(
        "LET $r = type::record('{table}', $key); CREATE $r CONTENT $content; SELECT * FROM $r;"
    );
    let mut response = db
        .inner()
        .query(&sql)
        .bind(("key", key.to_owned()))
        .bind(("content", content))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(2)?;
    rows.into_iter()
        .next()
        .ok_or_else(|| ApiError::internal(format!("created `{table}` record was not returned")))
}

/// Creates a record with a generated ULID key and returns the created record.
async fn create_ulid(db: &crate::db::Db, table: &str, content: Value) -> Result<Value, ApiError> {
    let sql = format!(
        "LET $r = type::record('{table}', rand::ulid()); CREATE $r CONTENT $content; SELECT * FROM $r;"
    );
    let mut response = db
        .inner()
        .query(&sql)
        .bind(("content", content))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(2)?;
    rows.into_iter()
        .next()
        .ok_or_else(|| ApiError::internal(format!("created `{table}` record was not returned")))
}

/// Merges a partial update into a record keyed by `key` (`table:<key>`).
///
/// Note: SurrealDB returns no rows for a trailing `SELECT * FROM $r` in the same
/// statement as an `UPDATE`, so we only run the UPDATE here (callers already
/// hold the record key).
async fn update_keyed(
    db: &crate::db::Db,
    table: &str,
    key: &str,
    content: Value,
) -> Result<(), ApiError> {
    let sql = format!("LET $r = type::record('{table}', $key); UPDATE $r MERGE $content;");
    let _ = db
        .inner()
        .query(&sql)
        .bind(("key", key.to_owned()))
        .bind(("content", content))
        .await?
        .check()?;
    Ok(())
}

/// Loads the stable-key → record-key maps from the target environment so
/// references resolve to existing records (pre-populated before new entities
/// are created during the import).
async fn preload_resolve(db: &crate::db::Db) -> Result<Resolve, ApiError> {
    let mut resolve = Resolve::default();

    let languages = query_rows(db, "SELECT id, code FROM language_tree;").await?;
    for lang in languages {
        if let Some(code) = as_str(&lang, "code") {
            resolve
                .language
                .insert(code.to_owned(), id_of(&lang, "language_tree"));
        }
    }

    let units = query_rows(db, "SELECT id, symbol FROM unit;").await?;
    for unit in units {
        if let Some(symbol) = as_str(&unit, "symbol") {
            resolve.unit.insert(symbol.to_owned(), id_of(&unit, "unit"));
        }
    }

    let groups = query_rows(db, "SELECT id, name FROM attribute_group;").await?;
    for group in groups {
        if let Some(name) = as_str(&group, "name") {
            resolve
                .group
                .insert(name.to_owned(), id_of(&group, "attribute_group"));
        }
    }

    let sets = query_rows(db, "SELECT id, name FROM enum_set;").await?;
    for set in sets {
        if let Some(name) = as_str(&set, "name") {
            resolve
                .enum_set
                .insert(name.to_owned(), id_of(&set, "enum_set"));
        }
    }

    Ok(resolve)
}

/// Returns the full record id for a group name.
fn group_ref(resolve: &Resolve, name: &str) -> String {
    resolve
        .group
        .get(name)
        .map(|key| format!("attribute_group:{key}"))
        .unwrap_or_else(|| name.to_owned())
}

/// Returns the full record id for an enum set name.
fn enum_set_ref(resolve: &Resolve, name: &str) -> String {
    resolve
        .enum_set
        .get(name)
        .map(|key| format!("enum_set:{key}"))
        .unwrap_or_else(|| name.to_owned())
}

/// Returns the bare unit record key for a symbol (falling back to the input).
fn unit_ref(resolve: &Resolve, symbol: &str) -> String {
    resolve
        .unit
        .get(symbol)
        .cloned()
        .unwrap_or_else(|| symbol.to_owned())
}

fn string_array(values: Option<&Value>) -> Vec<String> {
    values
        .and_then(Value::as_array)
        .map(|arr| {
            arr.iter()
                .filter_map(Value::as_str)
                .map(str::to_owned)
                .collect()
        })
        .unwrap_or_default()
}

fn json_string_array(values: &[String]) -> Value {
    Value::Array(values.iter().map(|s| Value::String(s.clone())).collect())
}

/// Returns `true` when writing `content` (a partial `MERGE` map) to `existing`
/// would change any non-volatile field, so a re-import of an unchanged document
/// can be reported as `skipped` instead of `updated` (idempotent report).
fn would_change(existing: &Value, content: &Value, ignore: &[&str]) -> bool {
    let Some(obj) = content.as_object() else {
        return true;
    };
    for (key, value) in obj {
        if ignore.contains(&key.as_str()) {
            continue;
        }
        if value.is_null() {
            continue;
        }
        match existing.get(key) {
            Some(existing_value) if existing_value == value => {}
            _ => return true,
        }
    }
    false
}

/// Volatile fields that are not compared for change detection.
const VOLATILE: &[&str] = &[
    "version",
    "updated_by",
    "updated_at",
    "created_at",
    "failure_count",
];

// -- replace (target-only cleanup) ------------------------------------------

/// The stable-key field per section. `languages`/`channels` use `code`, `units`
/// uses `symbol`, `category_attributes` its class key, everything else `name`.
fn key_field(section: &str) -> &'static str {
    match section {
        "languages" => "code",
        "channels" => "code",
        "units" => "symbol",
        "category_attributes" => "class",
        _ => "name",
    }
}

/// Runs a `SELECT count() ...` and returns the count.
async fn count_rows(
    db: &crate::db::Db,
    sql: &str,
    binds: &[(&str, String)],
) -> Result<usize, ApiError> {
    let mut query = db.inner().query(sql);
    for (name, value) in binds {
        query = query.bind((name.to_owned(), value.clone()));
    }
    let mut response = query.await?.check()?;
    let rows: Vec<Value> = response.take(0)?;
    // `SELECT count()` yields `[{ "count": N }]`, so read the field, not the row.
    Ok(rows
        .first()
        .and_then(|r| r.get("count"))
        .and_then(Value::as_i64)
        .map(|n| n.max(0) as usize)
        .unwrap_or(0))
}

/// Binds a single named string and runs the count query.
macro_rules! count_where {
    ($db:expr, $sql:expr, $name:expr, $value:expr) => {
        count_rows($db, $sql, &[($name, ($value).to_owned())]).await
    };
}

/// Returns `true` when a target-only record is referenced by other records
/// (or by objects) so it must not be deleted by a `replace`.
async fn record_in_use(db: &crate::db::Db, section: &str, row: &Value) -> Result<bool, ApiError> {
    match section {
        "languages" => {
            // Block `root` (handled by the caller) and any node with children or
            // referenced as a parent.
            let code = as_str(row, "code").unwrap_or_default();
            let key = format!("lang_{code}");
            let children = count_where!(
                db,
                "SELECT count() FROM language_tree WHERE parent = $key;",
                "key",
                key
            )?;
            Ok(children > 0)
        }
        "units" => {
            // `unit_id`/`display_unit_id` store the bare record key (e.g. `unit_m`).
            let key = id_of(row, "unit");
            let n = count_rows(
                db,
                "SELECT count() FROM attribute_definition WHERE \
                 unit_id = $key OR display_unit_id = $key;",
                &[("key", key)],
            )
            .await?;
            Ok(n > 0)
        }
        "channels" => {
            // Releases reference the channel by its code.
            let code = as_str(row, "code").unwrap_or_default();
            let n = count_where!(
                db,
                "SELECT count() FROM release WHERE channel = $code;",
                "code",
                code
            )?;
            Ok(n > 0)
        }
        "attribute_groups" => {
            // `group_ids` stores full record ids (e.g. `attribute_group:<ulid>`).
            let full = format!("attribute_group:{}", id_of(row, "attribute_group"));
            let n = count_rows(
                db,
                "SELECT count() FROM attribute_definition WHERE $full IN group_ids;",
                &[("full", full)],
            )
            .await?;
            Ok(n > 0)
        }
        "enum_sets" => {
            // `enum_set_id` stores the full record id (e.g. `enum_set:<ulid>`).
            let full = format!("enum_set:{}", id_of(row, "enum_set"));
            let n = count_rows(
                db,
                "SELECT count() FROM attribute_definition WHERE enum_set_id = $full;",
                &[("full", full)],
            )
            .await?;
            Ok(n > 0)
        }
        "object_types" => {
            let name = as_str(row, "name").unwrap_or_default();
            let obj = count_where!(
                db,
                "SELECT count() FROM object WHERE type = $name;",
                "name",
                name
            )?;
            let referenced = count_where!(
                db,
                "SELECT count() FROM attribute_definition WHERE \
                 $name IN applicable_types OR $name IN required_types;",
                "name",
                name
            )?;
            let group_ref = count_where!(
                db,
                "SELECT count() FROM attribute_group WHERE $name IN applicable_types;",
                "name",
                name
            )?;
            let rel_ref = count_where!(
                db,
                "SELECT count() FROM relation_definition WHERE \
                 $name IN source_types OR $name IN target_types;",
                "name",
                name
            )?;
            let rule_ref = count_where!(
                db,
                "SELECT count() FROM business_rule WHERE $name IN applies_to;",
                "name",
                name
            )?;
            Ok(obj + referenced + group_ref + rel_ref + rule_ref > 0)
        }
        "attribute_definitions" => {
            let name = as_str(row, "name").unwrap_or_default();
            let ot_ref = count_where!(
                db,
                "SELECT count() FROM object_type WHERE \
                 $name IN key_attributes OR label_attribute = $name;",
                "name",
                name
            )?;
            let value_use = count_where!(
                db,
                "SELECT count() FROM object WHERE $name IN object::keys(attributes ?? {});",
                "name",
                name
            )?;
            Ok(ot_ref + value_use > 0)
        }
        "relation_definitions" => {
            let key = format!("rel_{}", as_str(row, "name").unwrap_or_default());
            let n = count_where!(
                db,
                "SELECT count() FROM reference WHERE definition_id = $key;",
                "key",
                key
            )?;
            Ok(n > 0)
        }
        "business_rules" => {
            // Business rules are not referenced in a blocking way by objects.
            Ok(false)
        }
        _ => Ok(true),
    }
}

/// Deletes target-only (non-system) records of a section whose stable key is
/// not present in the imported document. Records that are still in use are
/// reported as errors and left in place (never deleted). Only invoked for
/// `Mode::Replace`.
async fn replace_target_only(
    db: &crate::db::Db,
    section: &str,
    entries: &[Value],
    report: &mut ConfigReport,
) -> Result<(), ApiError> {
    let (table, system_filter) = match section {
        "languages" => ("language_tree", None),
        "channels" => ("channel", None),
        "units" => ("unit", Some("system != 'default'")),
        "attribute_groups" => ("attribute_group", None),
        "enum_sets" => ("enum_set", None),
        "object_types" => ("object_type", Some("system != true")),
        "attribute_definitions" => ("attribute_definition", None),
        "relation_definitions" => ("relation_definition", Some("system != true")),
        "business_rules" => ("business_rule", None),
        _ => return Ok(()),
    };
    let field = key_field(section);
    let keep: std::collections::HashSet<String> = entries
        .iter()
        .filter_map(|e| as_str(e, field).map(str::to_owned))
        .collect();
    let keep_json = Value::Array(keep.iter().map(|s| Value::String(s.clone())).collect());

    let mut sql = format!("SELECT id, {field} FROM {table} WHERE {field} NOT IN $keep");
    if let Some(filter) = system_filter {
        sql.push_str(&format!(" AND {filter}"));
    }
    if section == "languages" {
        sql.push_str(" AND code != 'root'");
    }
    let mut response = db
        .inner()
        .query(&sql)
        .bind(("keep", keep_json))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;

    for row in rows {
        let key = as_str(&row, field).unwrap_or_default().to_owned();
        if key.is_empty() {
            continue;
        }
        if record_in_use(db, section, &row).await? {
            report.error(section, &key, "in use; replace left it in place");
            continue;
        }
        let rec_key = id_of(&row, table);
        if rec_key.is_empty() {
            continue;
        }
        let del = format!("DELETE FROM {table} WHERE id = type::record('{table}', $key);");
        db.inner()
            .query(&del)
            .bind(("key", rec_key))
            .await?
            .check()?;
        report.deleted(section, &key);
    }
    Ok(())
}

// -- language ----------------------------------------------------------------

async fn apply_languages(
    db: &crate::db::Db,
    mode: Mode,
    entries: &[Value],
    resolve: &mut Resolve,
    report: &mut ConfigReport,
) -> Result<(), ApiError> {
    // Build a parent map from the document so `fallback_order` can be derived
    // as `[parent] + parent.fallback_order` (parents are exported before children).
    let parent_of: HashMap<&str, Option<&str>> = entries
        .iter()
        .map(|e| (as_str(e, "code").unwrap_or(""), as_str(e, "parent")))
        .collect();

    fn chain<'a>(
        code: &'a str,
        parent_of: &HashMap<&'a str, Option<&'a str>>,
        seen: &mut std::collections::HashSet<&'a str>,
    ) -> Vec<String> {
        if !seen.insert(code) {
            return Vec::new();
        }
        match parent_of.get(code).copied().flatten() {
            Some(parent) => {
                let mut result = vec![parent.to_owned()];
                result.extend(chain(parent, parent_of, seen));
                result
            }
            None => Vec::new(),
        }
    }

    for entry in entries {
        let Some(code) = as_str(entry, "code") else {
            report.error("languages", "", "entry without `code`");
            continue;
        };
        let code = code.to_owned();
        let parent_code = as_str(entry, "parent");
        let parent_ref = parent_code.map(|p| format!("lang_{p}"));
        let fallback = chain(&code, &parent_of, &mut std::collections::HashSet::new());
        // SurrealDB `option<string>` fields reject JSON `null`, so `parent` is
        // only included when set (omitting it → `NONE`).
        let mut content = Map::new();
        content.insert("code".to_owned(), Value::String(code.clone()));
        content.insert("fallback_order".to_owned(), json_string_array(&fallback));
        content.insert(
            "label".to_owned(),
            entry.get("label").cloned().unwrap_or_else(|| json!({})),
        );
        content.insert("active".to_owned(), Value::Bool(as_bool(entry, "active")));
        content.insert("is_ui".to_owned(), Value::Bool(as_bool(entry, "is_ui")));
        if let Some(parent_ref) = parent_ref {
            content.insert("parent".to_owned(), Value::String(parent_ref));
        }
        let content = Value::Object(content);

        match fetch_by_field(db, "language_tree", "code", &code).await? {
            Some(existing) => {
                let key = id_of(&existing, "language_tree");
                if would_change(&existing, &content, VOLATILE) {
                    if mode == Mode::DryRun {
                        report.updated("languages", &code);
                    } else {
                        update_keyed(db, "language_tree", &key, content).await?;
                        report.updated("languages", &code);
                    }
                } else {
                    report.skipped("languages", &code);
                }
                resolve.language.insert(code.clone(), key);
            }
            None => {
                if mode == Mode::DryRun {
                    report.created("languages", &code);
                } else {
                    let rec =
                        create_keyed(db, "language_tree", &format!("lang_{code}"), content).await?;
                    let key = id_of(&rec, "language_tree");
                    resolve.language.insert(code.clone(), key);
                    report.created("languages", &code);
                }
            }
        }
    }
    Ok(())
}

// -- units -------------------------------------------------------------------

async fn apply_units(
    db: &crate::db::Db,
    mode: Mode,
    entries: &[Value],
    resolve: &mut Resolve,
    report: &mut ConfigReport,
) -> Result<(), ApiError> {
    for entry in entries {
        let Some(symbol) = as_str(entry, "symbol") else {
            report.error("units", "", "entry without `symbol`");
            continue;
        };
        let symbol = symbol.to_owned();
        let system = as_str(entry, "system").unwrap_or("custom");
        let canonical_ref = as_str(entry, "canonical_unit").map(|c| unit_ref(resolve, c));
        // Omit `canonical_unit_id` for the canonical unit itself (NULL is
        // rejected by the `option<string>` field).
        let mut content = Map::new();
        content.insert("symbol".to_owned(), Value::String(symbol.clone()));
        content.insert(
            "dimension".to_owned(),
            Value::String(as_str(entry, "dimension").unwrap_or_default().to_owned()),
        );
        content.insert(
            "factor".to_owned(),
            entry.get("factor").cloned().unwrap_or(json!(1.0)),
        );
        content.insert(
            "offset".to_owned(),
            entry.get("offset").cloned().unwrap_or(json!(0.0)),
        );
        content.insert(
            "label".to_owned(),
            entry.get("label").cloned().unwrap_or_else(|| json!({})),
        );
        content.insert("system".to_owned(), Value::String(system.to_owned()));
        content.insert("readonly".to_owned(), Value::Bool(system == "default"));
        if let Some(canonical_ref) = canonical_ref {
            content.insert("canonical_unit_id".to_owned(), Value::String(canonical_ref));
        }
        let content = Value::Object(content);

        if system == "default" {
            // Seeded/system units must already exist with the same conversion.
            match fetch_by_field(db, "unit", "symbol", &symbol).await? {
                Some(_) => {
                    report.skipped("units", &symbol);
                }
                None => {
                    report.warn(
                        "units",
                        &symbol,
                        "`system=default` unit missing in target; skipped",
                    );
                    report.skipped("units", &symbol);
                }
            }
            continue;
        }

        match fetch_by_field(db, "unit", "symbol", &symbol).await? {
            Some(existing) => {
                let key = id_of(&existing, "unit");
                if would_change(&existing, &content, VOLATILE) {
                    if mode == Mode::DryRun {
                        report.updated("units", &symbol);
                    } else {
                        update_keyed(db, "unit", &key, content).await?;
                        report.updated("units", &symbol);
                    }
                } else {
                    report.skipped("units", &symbol);
                }
                resolve.unit.insert(symbol.clone(), key);
            }
            None => {
                if mode == Mode::DryRun {
                    report.created("units", &symbol);
                } else {
                    let rec = create_ulid(db, "unit", content).await?;
                    let key = id_of(&rec, "unit");
                    resolve.unit.insert(symbol.clone(), key);
                    report.created("units", &symbol);
                }
            }
        }
    }
    Ok(())
}

// -- channels ----------------------------------------------------------------

/// Applies the channel catalog; the `code` is the record key (like object types).
async fn apply_channels(
    db: &crate::db::Db,
    mode: Mode,
    entries: &[Value],
    report: &mut ConfigReport,
) -> Result<(), ApiError> {
    for entry in entries {
        let Some(code) = as_str(entry, "code") else {
            report.error("channels", "", "entry without `code`");
            continue;
        };
        let code = code.to_owned();
        let mut content = Map::new();
        content.insert("code".to_owned(), Value::String(code.clone()));
        content.insert(
            "label".to_owned(),
            entry
                .get("label")
                .cloned()
                .unwrap_or_else(|| json!({ "en": code.clone() })),
        );
        // `option<string>`: an absent/`null` description is omitted instead of
        // written as `null`.
        if let Some(description) = as_str(entry, "description").map(str::trim)
            && !description.is_empty()
        {
            content.insert(
                "description".to_owned(),
                Value::String(description.to_owned()),
            );
        }
        content.insert(
            "active".to_owned(),
            entry.get("active").cloned().unwrap_or(json!(true)),
        );
        content.insert(
            "position".to_owned(),
            entry.get("position").cloned().unwrap_or(json!(0)),
        );
        let content = Value::Object(content);

        match fetch_by_field(db, "channel", "code", &code).await? {
            Some(existing) => {
                let key = id_of(&existing, "channel");
                if would_change(&existing, &content, VOLATILE) {
                    if mode == Mode::DryRun {
                        report.updated("channels", &code);
                    } else {
                        update_keyed(db, "channel", &key, content).await?;
                        report.updated("channels", &code);
                    }
                } else {
                    report.skipped("channels", &code);
                }
            }
            None => {
                if mode == Mode::DryRun {
                    report.created("channels", &code);
                } else {
                    create_keyed(db, "channel", &code, content).await?;
                    report.created("channels", &code);
                }
            }
        }
    }
    Ok(())
}

// -- attribute groups --------------------------------------------------------

async fn apply_attribute_groups(
    db: &crate::db::Db,
    mode: Mode,
    entries: &[Value],
    resolve: &mut Resolve,
    report: &mut ConfigReport,
) -> Result<(), ApiError> {
    for entry in entries {
        let Some(name) = as_str(entry, "name") else {
            report.error("attribute_groups", "", "entry without `name`");
            continue;
        };
        let name = name.to_owned();
        let content = json!({
            "name": name,
            "label": entry.get("label").cloned().unwrap_or_else(|| json!({})),
            "position": entry.get("position").cloned().unwrap_or(json!(0)),
            "applicable_types": entry.get("applicable_types").cloned().unwrap_or_else(|| json!([])),
        });

        match fetch_by_field(db, "attribute_group", "name", &name).await? {
            Some(existing) => {
                let key = id_of(&existing, "attribute_group");
                if would_change(&existing, &content, VOLATILE) {
                    if mode == Mode::DryRun {
                        report.updated("attribute_groups", &name);
                    } else {
                        update_keyed(db, "attribute_group", &key, content).await?;
                        report.updated("attribute_groups", &name);
                    }
                } else {
                    report.skipped("attribute_groups", &name);
                }
                resolve.group.insert(name.clone(), key);
            }
            None => {
                if mode == Mode::DryRun {
                    report.created("attribute_groups", &name);
                } else {
                    let rec = create_ulid(db, "attribute_group", content).await?;
                    let key = id_of(&rec, "attribute_group");
                    resolve.group.insert(name.clone(), key);
                    report.created("attribute_groups", &name);
                }
            }
        }
    }
    Ok(())
}

// -- enum sets ---------------------------------------------------------------

async fn apply_enum_sets(
    db: &crate::db::Db,
    mode: Mode,
    entries: &[Value],
    resolve: &mut Resolve,
    report: &mut ConfigReport,
) -> Result<(), ApiError> {
    for entry in entries {
        let Some(name) = as_str(entry, "name") else {
            report.error("enum_sets", "", "entry without `name`");
            continue;
        };
        let name = name.to_owned();
        let options = entry
            .get("options")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();

        // Create/update the set itself first, then reconcile options by code.
        let set_content = json!({
            "name": name,
            "label": entry.get("label").cloned().unwrap_or_else(|| json!({})),
            "position": entry.get("position").cloned().unwrap_or(json!(0)),
            "options": json!([]),
        });

        let existing = fetch_by_field(db, "enum_set", "name", &name).await?;
        let set_key = match &existing {
            Some(rec) => id_of(rec, "enum_set"),
            None => {
                if mode == Mode::DryRun {
                    // For dry-run we need a key to reference the set; use the name.
                    name.clone()
                } else {
                    let rec = create_ulid(db, "enum_set", set_content).await?;
                    let key = id_of(&rec, "enum_set");
                    report.created("enum_sets", &name);
                    key
                }
            }
        };
        if let Some(existing) = &existing {
            let set_update = json!({
                "label": entry.get("label").cloned().unwrap_or_else(|| json!({})),
                "position": entry.get("position").cloned().unwrap_or(json!(0)),
            });
            if would_change(existing, &set_update, VOLATILE) {
                if mode == Mode::DryRun {
                    report.updated("enum_sets", &name);
                } else {
                    update_keyed(db, "enum_set", &set_key, set_update).await?;
                    report.updated("enum_sets", &name);
                }
            } else {
                report.skipped("enum_sets", &name);
            }
        }
        resolve.enum_set.insert(name.clone(), set_key.clone());

        for option in &options {
            let Some(code) = as_str(option, "code") else {
                report.error("enum_sets", &name, "option without `code`");
                continue;
            };
            let option_content = json!({
                "code": code,
                "labels": option.get("labels").cloned().unwrap_or_else(|| json!({})),
                "position": option.get("position").cloned().unwrap_or(json!(0)),
                "active": as_bool(option, "active"),
            });
            let option_key = find_enum_option(db, mode, code).await?;
            match option_key {
                Some(option) => {
                    let key = id_of(&option, "enum_option");
                    if would_change(&option, &option_content, VOLATILE) {
                        if mode == Mode::DryRun {
                            report.updated("enum_sets", format!("{name}/{code}"));
                        } else {
                            update_keyed(db, "enum_option", &key, option_content).await?;
                            // ensure the option is linked into this set (options can be shared)
                            link_enum_option(db, &set_key, &key).await?;
                            report.updated("enum_sets", format!("{name}/{code}"));
                        }
                    } else {
                        report.skipped("enum_sets", format!("{name}/{code}"));
                    }
                }
                None => {
                    if mode == Mode::DryRun {
                        report.created("enum_sets", format!("{name}/{code}"));
                    } else {
                        let rec = create_ulid(db, "enum_option", option_content).await?;
                        let key = id_of(&rec, "enum_option");
                        link_enum_option(db, &set_key, &key).await?;
                        report.created("enum_sets", format!("{name}/{code}"));
                    }
                }
            }
        }
    }
    Ok(())
}

/// Finds an enum option by its (globally unique) code; returns its record.
async fn find_enum_option(
    db: &crate::db::Db,
    mode: Mode,
    code: &str,
) -> Result<Option<Value>, ApiError> {
    if mode == Mode::DryRun {
        return Ok(None);
    }
    let sql = "SELECT * FROM enum_option WHERE code = $code LIMIT 1;";
    let mut response = db
        .inner()
        .query(sql)
        .bind(("code", code.to_owned()))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    Ok(rows.into_iter().next())
}

/// Links an option (by bare key) into a set's `options` array (if not present).
/// Enum options may be shared across sets, so an existing option found by code
/// is re-linked to the importing set too.
async fn link_enum_option(
    db: &crate::db::Db,
    set_key: &str,
    option_key: &str,
) -> Result<(), ApiError> {
    let option_id = format!("enum_option:{option_key}");
    // `array::contains` is not a valid function in this SurrealDB build, so
    // deduplicate after appending with `array::distinct`.
    let sql = "UPDATE type::record('enum_set', $key) SET options = array::distinct(array::append(options ?? [], $o));";
    let _ = db
        .inner()
        .query(sql)
        .bind(("key", set_key.to_owned()))
        .bind(("o", option_id))
        .await?
        .check()?;
    Ok(())
}

// -- object types ------------------------------------------------------------

async fn apply_object_types(
    db: &crate::db::Db,
    mode: Mode,
    entries: &[Value],
    report: &mut ConfigReport,
) -> Result<(), ApiError> {
    for entry in entries {
        let Some(name) = as_str(entry, "name") else {
            report.error("object_types", "", "entry without `name`");
            continue;
        };
        let name = name.to_owned();
        let system = as_bool(entry, "system");
        // Omit nullable option fields (`description`, `label_attribute`) when
        // absent, so `option<string>` fields stay `NONE` instead of `NULL`.
        let mut content = Map::new();
        content.insert("name".to_owned(), Value::String(name.clone()));
        content.insert(
            "label".to_owned(),
            entry.get("label").cloned().unwrap_or_else(|| json!({})),
        );
        content.insert(
            "key_attributes".to_owned(),
            entry
                .get("key_attributes")
                .cloned()
                .unwrap_or_else(|| json!([])),
        );
        content.insert("system".to_owned(), Value::Bool(system));
        // Only when the document carries the key: an older MCF file without
        // `taxonomy` must not silently clear the flag of an existing type.
        if let Some(taxonomy) = entry.get("taxonomy")
            && !taxonomy.is_null()
        {
            content.insert(
                "taxonomy".to_owned(),
                Value::Bool(as_bool(entry, "taxonomy")),
            );
        }
        if let Some(description) = entry.get("description")
            && !description.is_null()
        {
            content.insert("description".to_owned(), description.clone());
        }
        if let Some(label_attribute) = entry.get("label_attribute")
            && !label_attribute.is_null()
        {
            content.insert("label_attribute".to_owned(), label_attribute.clone());
        }
        let content = Value::Object(content);

        match fetch_by_field(db, "object_type", "name", &name).await? {
            Some(existing) => {
                if system {
                    report.skipped("object_types", &name);
                    continue;
                }
                if would_change(&existing, &content, VOLATILE) {
                    if mode == Mode::DryRun {
                        report.updated("object_types", &name);
                    } else {
                        update_keyed(db, "object_type", &name, content).await?;
                        report.updated("object_types", &name);
                    }
                } else {
                    report.skipped("object_types", &name);
                }
            }
            None => {
                if mode == Mode::DryRun {
                    report.created("object_types", &name);
                } else {
                    create_keyed(db, "object_type", &name, content).await?;
                    report.created("object_types", &name);
                }
            }
        }
    }
    Ok(())
}

// -- attribute definitions ---------------------------------------------------

async fn apply_attribute_definitions(
    db: &crate::db::Db,
    mode: Mode,
    entries: &[Value],
    resolve: &Resolve,
    report: &mut ConfigReport,
) -> Result<(), ApiError> {
    for entry in entries {
        let Some(name) = as_str(entry, "name") else {
            report.error("attribute_definitions", "", "entry without `name`");
            continue;
        };
        let name = name.to_owned();
        let type_value = as_str(entry, "type").unwrap_or("text");

        let group_ids = string_array(entry.get("group_ids"))
            .iter()
            .map(|g| group_ref(resolve, g))
            .collect::<Vec<_>>();
        let unit = as_str(entry, "unit").map(|u| unit_ref(resolve, u));
        let display_unit = as_str(entry, "display_unit").map(|u| unit_ref(resolve, u));
        let enum_set = as_str(entry, "enum_set").map(|s| enum_set_ref(resolve, s));

        let mut content = Map::new();
        content.insert("name".to_owned(), Value::String(name.clone()));
        content.insert(
            "label".to_owned(),
            entry.get("label").cloned().unwrap_or_else(|| json!({})),
        );
        content.insert("type".to_owned(), Value::String(type_value.to_owned()));
        content.insert("group_ids".to_owned(), json_string_array(&group_ids));
        content.insert(
            "inheritable".to_owned(),
            Value::Bool(as_bool(entry, "inheritable")),
        );
        content.insert("is_key".to_owned(), Value::Bool(as_bool(entry, "is_key")));
        content.insert(
            "multilingual".to_owned(),
            Value::Bool(as_bool(entry, "multilingual")),
        );
        content.insert(
            "applicable_types".to_owned(),
            entry
                .get("applicable_types")
                .cloned()
                .unwrap_or_else(|| json!([])),
        );
        content.insert(
            "required_types".to_owned(),
            entry
                .get("required_types")
                .cloned()
                .unwrap_or_else(|| json!([])),
        );
        content.insert(
            "computed".to_owned(),
            Value::Bool(as_bool(entry, "computed")),
        );
        if let Some(rule) = as_str(entry, "computed_rule") {
            content.insert("computed_rule".to_owned(), Value::String(rule.to_owned()));
        }
        content.insert(
            "dependencies".to_owned(),
            entry
                .get("dependencies")
                .cloned()
                .unwrap_or_else(|| json!([])),
        );
        content.insert(
            "computed_reads_inherited".to_owned(),
            Value::Bool(as_bool(entry, "computed_reads_inherited")),
        );
        if let Some(u) = unit {
            content.insert("unit_id".to_owned(), Value::String(u));
        }
        if let Some(u) = display_unit {
            content.insert("display_unit_id".to_owned(), Value::String(u));
        }
        if let Some(s) = enum_set {
            content.insert("enum_set_id".to_owned(), Value::String(s));
        }
        // The standard binding travels with the model: without it an imported
        // own attribute would no longer feed its class feature
        // (`plan/detail/bmecat-export.md` §11.7). A missing/null map means
        // "not bound" — the `option<object>` field rejects JSON `null`.
        content.insert(
            "standard_codes".to_owned(),
            entry
                .get("standard_codes")
                .filter(|value| value.is_object())
                .cloned()
                .unwrap_or_else(|| json!({})),
        );
        if let Some(constraints) = entry.get("constraints")
            && !constraints.is_null()
        {
            content.insert("constraints".to_owned(), constraints.clone());
        }
        if let Some(default) = entry.get("default_value")
            && !default.is_null()
        {
            content.insert("default_value".to_owned(), default.clone());
        }
        if let Some(description) = as_str(entry, "description") {
            content.insert(
                "description".to_owned(),
                Value::String(description.to_owned()),
            );
        }
        content.insert("version".to_owned(), json!(1));
        let content = Value::Object(content);

        match fetch_by_field(db, "attribute_definition", "name", &name).await? {
            Some(existing) => {
                // Block incompatible type changes (per the datentypwechsel matrix).
                let existing_type = as_str(&existing, "type").unwrap_or_default();
                if !existing_type.is_empty() && existing_type != type_value {
                    report.error(
                        "attribute_definitions",
                        &name,
                        format!(
                            "type mismatch: target is `{existing_type}`, source `{type_value}` (blocked per datentypwechsel)"
                        ),
                    );
                    continue;
                }
                if would_change(&existing, &content, VOLATILE) {
                    if mode == Mode::DryRun {
                        report.updated("attribute_definitions", &name);
                    } else {
                        let key = id_of(&existing, "attribute_definition");
                        update_keyed(db, "attribute_definition", &key, content).await?;
                        report.updated("attribute_definitions", &name);
                    }
                } else {
                    report.skipped("attribute_definitions", &name);
                }
            }
            None => {
                if mode == Mode::DryRun {
                    report.created("attribute_definitions", &name);
                } else {
                    create_ulid(db, "attribute_definition", content).await?;
                    report.created("attribute_definitions", &name);
                }
            }
        }
    }
    Ok(())
}

// -- relation definitions ----------------------------------------------------

async fn apply_relation_definitions(
    db: &crate::db::Db,
    mode: Mode,
    entries: &[Value],
    report: &mut ConfigReport,
) -> Result<(), ApiError> {
    for entry in entries {
        let Some(name) = as_str(entry, "name") else {
            report.error("relation_definitions", "", "entry without `name`");
            continue;
        };
        let name = name.to_owned();
        let system = as_bool(entry, "system");
        let content = json!({
            "name": name,
            "kind": as_str(entry, "kind").unwrap_or("aggregation"),
            "source_types": entry.get("source_types").cloned().unwrap_or_else(|| json!([])),
            "target_types": entry.get("target_types").cloned().unwrap_or_else(|| json!([])),
            "label": entry.get("label").cloned().unwrap_or_else(|| json!({})),
            "attributes": entry.get("attributes").cloned().unwrap_or_else(|| json!([])),
            "system": system,
        });

        let key = format!("rel_{name}");
        match fetch_by_field(db, "relation_definition", "name", &name).await? {
            Some(existing) => {
                if system {
                    report.skipped("relation_definitions", &name);
                    continue;
                }
                if would_change(&existing, &content, VOLATILE) {
                    if mode == Mode::DryRun {
                        report.updated("relation_definitions", &name);
                    } else {
                        let key = id_of(&existing, "relation_definition");
                        update_keyed(db, "relation_definition", &key, content).await?;
                        report.updated("relation_definitions", &name);
                    }
                } else {
                    report.skipped("relation_definitions", &name);
                }
            }
            None => {
                if mode == Mode::DryRun {
                    report.created("relation_definitions", &name);
                } else {
                    create_keyed(db, "relation_definition", &key, content).await?;
                    report.created("relation_definitions", &name);
                }
            }
        }
    }
    Ok(())
}

// -- business rules ----------------------------------------------------------

async fn apply_business_rules(
    db: &crate::db::Db,
    mode: Mode,
    entries: &[Value],
    report: &mut ConfigReport,
) -> Result<(), ApiError> {
    for entry in entries {
        let Some(name) = as_str(entry, "name") else {
            report.error("business_rules", "", "entry without `name`");
            continue;
        };
        let name = name.to_owned();
        let content = json!({
            "name": name,
            "type": as_str(entry, "type").unwrap_or("validation"),
            "applies_to": entry.get("applies_to").cloned().unwrap_or_else(|| json!([])),
            "script": entry.get("script").cloned().unwrap_or_else(|| json!("")),
            "state": "disabled", // imported rules start disabled (review before activation)
            "failure_count": 0,
            "version": 1,
            "updated_by": "system:config-import",
        });

        match fetch_by_field(db, "business_rule", "name", &name).await? {
            Some(existing) => {
                if would_change(&existing, &content, VOLATILE) {
                    if mode == Mode::DryRun {
                        report.updated("business_rules", &name);
                    } else {
                        let key = id_of(&existing, "business_rule");
                        update_keyed(db, "business_rule", &key, content).await?;
                        report.updated("business_rules", &name);
                    }
                } else {
                    report.skipped("business_rules", &name);
                }
            }
            None => {
                if mode == Mode::DryRun {
                    report.created("business_rules", &name);
                } else {
                    create_ulid(db, "business_rule", content).await?;
                    report.created("business_rules", &name);
                }
            }
        }
    }
    Ok(())
}

/// Applies a parsed MCF document against the target environment.
/// MCF entries may carry `null` for an empty label map: the **export** writes
/// `null` when a record has no label, while the core's object-typed fields
/// (`label`, `labels`) reject `NULL`. Normalising the entries once keeps every
/// section (and every future one) tolerant instead of patching each apply
/// function. Found in the ETIM spike (`plan/detail/bmecat-export.md` §11.4).
fn normalise_entry_maps(entries: &mut [Value]) {
    for entry in entries.iter_mut() {
        let Some(fields) = entry.as_object_mut() else {
            continue;
        };
        for name in ["label", "labels"] {
            if fields.get(name).is_some_and(Value::is_null) {
                fields.insert(name.to_owned(), json!({}));
            }
        }
    }
}

async fn apply_document(
    db: &crate::db::Db,
    doc: &Value,
    mode: Mode,
) -> Result<ConfigReport, ApiError> {
    let mut report = ConfigReport::new(mode);
    let mut resolve = preload_resolve(db).await?;
    let sections = doc
        .get("sections")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();

    if mode == Mode::Replace {
        // Delete target-only (non-system) records first, in reverse dependency
        // order (dependents before their dependencies), so the applied section
        // matches the document exactly. In-use records are kept (reported as
        // errors) — never deleted. Only sections the document includes are
        // replaced; absent sections are left untouched.
        for section in SECTIONS.iter().rev() {
            let Some(entries) = sections.get(*section).and_then(Value::as_array).cloned() else {
                continue;
            };
            replace_target_only(db, section, &entries, &mut report).await?;
        }
    }

    for section in SECTIONS {
        let mut entries = sections
            .get(*section)
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        normalise_entry_maps(&mut entries);
        match *section {
            "languages" => apply_languages(db, mode, &entries, &mut resolve, &mut report).await?,
            "channels" => apply_channels(db, mode, &entries, &mut report).await?,
            "units" => apply_units(db, mode, &entries, &mut resolve, &mut report).await?,
            "attribute_groups" => {
                apply_attribute_groups(db, mode, &entries, &mut resolve, &mut report).await?
            }
            "enum_sets" => apply_enum_sets(db, mode, &entries, &mut resolve, &mut report).await?,
            "object_types" => apply_object_types(db, mode, &entries, &mut report).await?,
            "attribute_definitions" => {
                apply_attribute_definitions(db, mode, &entries, &resolve, &mut report).await?
            }
            "relation_definitions" => {
                apply_relation_definitions(db, mode, &entries, &mut report).await?
            }
            "business_rules" => apply_business_rules(db, mode, &entries, &mut report).await?,
            "category_attributes" => {
                apply_category_attributes(db, mode, &entries, &mut report).await?
            }
            _ => {}
        }
    }

    Ok(report)
}

/// Persists an MCF document in `import_document` (`kind = "mcf"`) and returns
/// its record id (used by the `config_import` job).
async fn store_mcf_document(
    state: &AppState,
    doc: &Value,
    format_version: &str,
    user_id: &str,
) -> Result<String, ApiError> {
    let mut content = serde_json::Map::new();
    content.insert("document".to_owned(), doc.clone());
    if !format_version.is_empty() {
        content.insert(
            "format_version".to_owned(),
            Value::String(format_version.to_owned()),
        );
    }
    content.insert("kind".to_owned(), Value::String("mcf".to_owned()));
    content.insert("created_by".to_owned(), Value::String(user_id.to_owned()));
    let sql = "
        LET $d = type::record('import_document', rand::ulid());
        CREATE $d CONTENT $content;
        SELECT * FROM $d;
    ";
    let mut response = state
        .db
        .inner()
        .query(sql)
        .bind(("content", Value::Object(content)))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(2)?;
    let record = rows
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::internal("created import document was not returned"))?;
    Ok(record
        .get("id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned())
}

/// Imports an MCF configuration document (admin/superadmin, or the worker via
/// `X-Api-Key`). With `?async=true` the document is stored and a `config_import`
/// job is queued (202 + `job_run_id`); otherwise it is applied synchronously.
#[utoipa::path(
    post,
    path = "/config/import",
    params(ConfigImportQuery),
    request_body = Value,
    responses(
        (status = 200, description = "Import report (sync)", body = Value),
        (status = 202, description = "Import job queued (async)", body = Value),
        (status = 400, description = "Invalid document", body = Value),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Forbidden", body = Value)
    )
)]
pub async fn import_config(
    State(state): State<AppState>,
    headers: HeaderMap,
    Query(params): Query<ConfigImportQuery>,
    Json(doc): Json<Value>,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    let user_id = api_key_or_role(&headers, &state.config, ROLE_ADMIN)?;
    let mode = parse_mode(&params)?;

    // Validate the document envelope.
    let format_version = doc
        .get("format_version")
        .and_then(Value::as_str)
        .unwrap_or("");
    if !format_version.is_empty() && format_version != "1.0" {
        return Err(ApiError::bad_request(format!(
            "unsupported `format_version` `{format_version}` (expected 1.0)"
        )));
    }
    if doc.get("sections").and_then(Value::as_object).is_none() {
        return Err(ApiError::bad_request(
            "document is missing a `sections` object",
        ));
    }

    // Async path: persist the document and queue a `config_import` job.
    if params.async_ {
        let document_id = store_mcf_document(&state, &doc, format_version, &user_id).await?;
        let parameters = json!({ "document_id": document_id, "mode": mode.label() });
        let (_, run) = queue_run(
            &state.db,
            &state.config,
            None,
            "config_import",
            parameters,
            "manual",
            &user_id,
        )
        .await?;
        let run_id = run
            .get("id")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_owned();
        return Ok((StatusCode::ACCEPTED, Json(json!({ "job_run_id": run_id }))));
    }

    let report = apply_document(&state.db, &doc, mode).await?;
    Ok((
        StatusCode::OK,
        Json(serde_json::to_value(report).unwrap_or(Value::Null)),
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_sections_defaults_to_all() {
        let all = parse_sections(None).expect("all sections");
        assert_eq!(
            all,
            SECTIONS.iter().map(|s| s.to_string()).collect::<Vec<_>>()
        );
        assert_eq!(parse_sections(Some("")).expect("empty"), all);
        assert_eq!(
            parse_sections(Some("all")).expect("'all'"),
            all,
            "'all' should be case-insensitive"
        );
    }

    #[test]
    fn parse_sections_subset_and_unknown() {
        let subset = parse_sections(Some("object_types,units")).expect("subset");
        assert_eq!(
            subset,
            vec!["object_types".to_string(), "units".to_string()]
        );
        assert!(
            parse_sections(Some("nope")).is_err(),
            "unknown section must fail"
        );
    }

    #[test]
    fn bare_strips_table_prefix() {
        assert_eq!(bare("unit:unit_mm", "unit"), Some("unit_mm"));
        assert_eq!(bare("unit_mm", "unit"), None);
        assert_eq!(bare("object:01M0", "unit"), None);
    }

    #[test]
    fn lang_code_strips_record_key_prefix() {
        assert_eq!(lang_code("lang_root"), "root");
        assert_eq!(lang_code("lang_de"), "de");
        assert_eq!(lang_code("de"), "de");
    }

    #[test]
    fn resolve_symbol_prefers_map_and_falls_back() {
        let mut map = HashMap::new();
        map.insert("unit_m".to_string(), "m".to_string());
        map.insert("m".to_string(), "m".to_string());
        assert_eq!(resolve_symbol(&map, "unit_m"), "m");
        assert_eq!(
            resolve_symbol(&map, "unit_cm"),
            "unit_cm",
            "unknown ref kept as-is"
        );
    }

    #[test]
    fn utc_now_iso_is_rfc3339() {
        let now = utc_now_iso();
        assert!(now.ends_with('Z'), "must be UTC with Z suffix");
        assert!(now.len() >= 20);
        assert!(now.contains('T'));
    }

    #[test]
    fn parse_mode_supports_replace() {
        let replace = ConfigImportQuery {
            mode: Some("replace".to_owned()),
            dry_run: false,
            async_: false,
        };
        assert_eq!(parse_mode(&replace).expect("replace"), Mode::Replace);
        assert_eq!(parse_mode(&replace).expect("replace").label(), "replace");

        let merge = ConfigImportQuery {
            mode: Some("merge".to_owned()),
            dry_run: false,
            async_: false,
        };
        assert_eq!(parse_mode(&merge).expect("merge"), Mode::Merge);

        let dry = ConfigImportQuery {
            mode: None,
            dry_run: true,
            async_: false,
        };
        assert_eq!(parse_mode(&dry).expect("dry_run"), Mode::DryRun);

        let bad = ConfigImportQuery {
            mode: Some("nope".to_owned()),
            dry_run: false,
            async_: false,
        };
        assert!(parse_mode(&bad).is_err(), "unknown mode must fail");

        let async_q = ConfigImportQuery {
            mode: Some("merge".to_owned()),
            dry_run: false,
            async_: true,
        };
        assert!(
            async_q.async_,
            "async_ must round-trip the `async` query flag"
        );
    }

    #[test]
    fn key_field_per_section() {
        assert_eq!(key_field("languages"), "code");
        assert_eq!(key_field("channels"), "code");
        assert_eq!(key_field("units"), "symbol");
        assert_eq!(key_field("object_types"), "name");
        assert_eq!(key_field("attribute_definitions"), "name");
        assert_eq!(key_field("business_rules"), "name");
        assert_eq!(
            key_field("category_attributes"),
            "class",
            "class features are keyed by the class, not by a local id"
        );
    }

    #[test]
    fn category_attributes_comes_last_and_after_its_dependencies() {
        assert_eq!(
            SECTIONS.last().copied(),
            Some("category_attributes"),
            "the class-feature section must be applied last"
        );
        let position = |name: &str| SECTIONS.iter().position(|section| *section == name);
        let features = position("category_attributes");
        // The section resolves classes (taxonomy objects) and features
        // (attribute definitions), so it depends on both sections.
        assert!(position("object_types") < features);
        assert!(position("attribute_definitions") < features);
    }

    #[test]
    fn null_label_maps_are_normalised() {
        let mut entries = vec![
            json!({ "name": "a", "label": null }),
            json!({ "name": "b", "labels": null, "label": { "en": "B" } }),
            json!({ "name": "c" }),
            json!("not an object"),
        ];
        normalise_entry_maps(&mut entries);
        // The export writes `null` for a missing label map; the core rejects NULL.
        assert_eq!(entries[0]["label"], json!({}));
        assert_eq!(entries[1]["labels"], json!({}));
        assert_eq!(
            entries[1]["label"],
            json!({ "en": "B" }),
            "labels stay untouched"
        );
        assert_eq!(
            entries[2].get("label"),
            None,
            "an absent field stays absent"
        );
        assert_eq!(entries[3], json!("not an object"));
    }
}
