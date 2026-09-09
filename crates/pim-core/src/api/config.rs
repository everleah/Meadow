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
use serde::Deserialize;
use serde_json::{Map, Value, json};
use utoipa::{IntoParams, ToSchema};

use crate::app::AppState;
use crate::auth::AuthUser;
use crate::error::ApiError;

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
    "units",
    "attribute_groups",
    "enum_sets",
    "object_types",
    "attribute_definitions",
    "relation_definitions",
    "business_rules",
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
}
