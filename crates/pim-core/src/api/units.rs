//! Unit catalog and conversion endpoints (Phase 6).
//!
//! The `unit` table is seeded at startup (Phase 1); these read-only endpoints
//! expose it as the single source of truth for quantified-value conversion.
//! Values are stored in the canonical SI unit of their dimension; conversion
//! is the linear-affine function `canonical = value * factor + offset`.

use axum::Json;
use axum::extract::{Path, Query, State};
use axum::http::StatusCode;
use serde::Deserialize;
use serde_json::{Map, Value, json};
use utoipa::{IntoParams, ToSchema};

use crate::app::AppState;
use crate::audit::{self, AuditEntry};
use crate::auth::AuthUser;
use crate::db::Db;
use crate::error::ApiError;

/// Query parameters for `GET /units`.
#[derive(Debug, Deserialize, ToSchema, IntoParams)]
#[into_params(parameter_in = Query)]
pub struct ListUnits {
    /// Filter by unit symbol (e.g. `mm`).
    pub symbol: Option<String>,
    /// Filter by dimension (e.g. `length`).
    pub dimension: Option<String>,
    #[serde(default = "default_limit")]
    pub limit: u32,
    #[serde(default)]
    pub start: u32,
}

/// Query parameters for `GET /units/convert`.
#[derive(Debug, Deserialize, ToSchema, IntoParams)]
#[into_params(parameter_in = Query)]
pub struct ConvertQuery {
    /// Numeric value to convert (in the `from` unit).
    pub value: f64,
    /// Source unit (symbol or record key, e.g. `mm` or `unit_mm`).
    pub from: String,
    /// Target unit (symbol or record key). Defaults to the dimension's
    /// canonical SI unit.
    pub to: Option<String>,
}

fn default_limit() -> u32 {
    1000
}

/// Looks up a unit by symbol first, then by record key.
async fn find_unit(db: &Db, symbol_or_key: &str) -> Result<Option<Value>, ApiError> {
    let mut response = db
        .inner()
        .query("SELECT * FROM unit WHERE symbol = $q LIMIT 1;")
        .bind(("q", symbol_or_key.to_owned()))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    if let Some(unit) = rows.into_iter().next() {
        return Ok(Some(unit));
    }
    let mut response = db
        .inner()
        .query("LET $u = type::record('unit', $q); SELECT * FROM $u;")
        .bind(("q", symbol_or_key.to_owned()))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(1)?;
    Ok(rows.into_iter().next())
}

fn factor_of(unit: &Value) -> f64 {
    unit.get("factor").and_then(Value::as_f64).unwrap_or(1.0)
}

fn offset_of(unit: &Value) -> f64 {
    unit.get("offset").and_then(Value::as_f64).unwrap_or(0.0)
}

fn symbol_of(unit: &Value) -> String {
    unit.get("symbol")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned()
}

fn dimension_of(unit: &Value) -> String {
    unit.get("dimension")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned()
}

fn canonical_unit_id_of(unit: &Value) -> String {
    unit.get("canonical_unit_id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned()
}

/// Pure linear-affine conversion: `(value * factor + offset)` to canonical,
/// then `(canonical - to_offset) / to_factor` to the target unit.
fn convert_value(
    value: f64,
    from_factor: f64,
    from_offset: f64,
    to_factor: f64,
    to_offset: f64,
) -> f64 {
    let canonical = value * from_factor + from_offset;
    if to_factor == 0.0 {
        canonical
    } else {
        (canonical - to_offset) / to_factor
    }
}

/// Lists the unit catalog, optionally filtered by symbol or dimension.
#[utoipa::path(
    get,
    path = "/units",
    params(ListUnits),
    responses((status = 200, description = "Unit catalog", body = Vec<Value>))
)]
pub async fn list_units(
    State(state): State<AppState>,
    Query(params): Query<ListUnits>,
) -> Result<Json<Vec<Value>>, ApiError> {
    let limit = params.limit.clamp(1, 10_000) as i64;
    let start = params.start as i64;

    let mut sql = String::from("SELECT * FROM unit");
    let mut conditions: Vec<&str> = Vec::new();
    if params.symbol.is_some() {
        conditions.push("symbol = $symbol");
    }
    if params.dimension.is_some() {
        conditions.push("dimension = $dimension");
    }
    if !conditions.is_empty() {
        sql.push_str(" WHERE ");
        sql.push_str(&conditions.join(" AND "));
    }
    sql.push_str(" ORDER BY dimension, symbol LIMIT $limit START $start");

    let mut query = state.db.inner().query(sql);
    if let Some(symbol) = &params.symbol {
        query = query.bind(("symbol", symbol.clone()));
    }
    if let Some(dimension) = &params.dimension {
        query = query.bind(("dimension", dimension.clone()));
    }
    let mut response = query
        .bind(("limit", limit))
        .bind(("start", start))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    Ok(Json(rows))
}

/// Converts a value between two units of the same dimension.
#[utoipa::path(
    get,
    path = "/units/convert",
    params(ConvertQuery),
    responses(
        (status = 200, description = "Converted value", body = Value),
        (status = 400, description = "Dimension mismatch", body = Value),
        (status = 404, description = "Unknown unit", body = Value)
    )
)]
pub async fn convert(
    State(state): State<AppState>,
    Query(params): Query<ConvertQuery>,
) -> Result<Json<Value>, ApiError> {
    let from = find_unit(&state.db, params.from.trim())
        .await?
        .ok_or_else(|| ApiError::not_found(format!("unknown unit `{}`", params.from)))?;
    let to = match params
        .to
        .as_deref()
        .map(str::trim)
        .filter(|t| !t.is_empty())
    {
        Some(target) => find_unit(&state.db, target)
            .await?
            .ok_or_else(|| ApiError::not_found(format!("unknown unit `{target}`")))?,
        None => {
            let canonical_id = canonical_unit_id_of(&from);
            if canonical_id.is_empty() {
                from.clone()
            } else {
                find_unit(&state.db, &canonical_id)
                    .await?
                    .ok_or_else(|| ApiError::internal("canonical unit not found"))?
            }
        }
    };

    let from_dimension = dimension_of(&from);
    let to_dimension = dimension_of(&to);
    if from_dimension != to_dimension {
        return Err(ApiError::bad_request(format!(
            "cannot convert between dimensions `{from_dimension}` and `{to_dimension}`"
        )));
    }

    let target = convert_value(
        params.value,
        factor_of(&from),
        offset_of(&from),
        factor_of(&to),
        offset_of(&to),
    );
    Ok(Json(json!({ "value": target, "unit": symbol_of(&to) })))
}

/// Payload for creating a custom unit.
#[derive(Debug, Deserialize, ToSchema)]
pub struct CreateUnit {
    /// Display symbol (e.g. `PAL`).
    pub symbol: String,
    /// Multilingual label (optional).
    #[serde(default)]
    pub label: Option<Value>,
    /// Canonical SI unit this custom unit maps to (symbol or record key).
    pub canonical_unit_id: String,
    /// Linear factor to canonical: canonical = value * factor + offset.
    pub factor: f64,
    /// Offset to canonical.
    #[serde(default)]
    pub offset: f64,
}

/// Payload for a partial custom-unit update.
#[derive(Debug, Deserialize, ToSchema)]
pub struct UpdateUnit {
    #[serde(default)]
    pub symbol: Option<String>,
    #[serde(default)]
    pub label: Option<Value>,
    #[serde(default)]
    pub canonical_unit_id: Option<String>,
    #[serde(default)]
    pub factor: Option<f64>,
    #[serde(default)]
    pub offset: Option<f64>,
}

/// Splits a `table:key` path segment and verifies the expected table.
fn parse_key<'a>(id: &'a str, table: &str) -> Result<&'a str, ApiError> {
    let (actual_table, key) = id
        .split_once(':')
        .ok_or_else(|| ApiError::bad_request(format!("invalid id `{id}`")))?;
    if actual_table != table {
        return Err(ApiError::bad_request(format!(
            "id `{id}` does not reference table `{table}`"
        )));
    }
    Ok(key)
}

/// Loads a unit by its record key.
async fn fetch_unit_by_key(db: &Db, key: &str) -> Result<Option<Value>, ApiError> {
    let sql = "LET $u = type::record('unit', $key); SELECT * FROM $u;";
    let mut response = db.inner().query(sql).bind(("key", key)).await?.check()?;
    let rows: Vec<Value> = response.take(1)?;
    Ok(rows.into_iter().next())
}

/// Whether a unit is a seeded/default entry (read-only).
fn is_readonly(unit: &Value) -> bool {
    unit.get("system")
        .and_then(Value::as_str)
        .map(|system| system == "default")
        .unwrap_or(false)
        || unit
            .get("readonly")
            .and_then(Value::as_bool)
            .unwrap_or(false)
}

/// Ensures a unit symbol is unique (ignores the record `except`).
async fn ensure_unique_symbol(db: &Db, symbol: &str, except: Option<&str>) -> Result<(), ApiError> {
    let rows: Vec<Value> = db
        .inner()
        .query("SELECT id FROM unit WHERE symbol = $symbol")
        .bind(("symbol", symbol.to_owned()))
        .await?
        .check()?
        .take(0)?;
    let conflict = rows
        .iter()
        .any(|row| row.get("id").and_then(Value::as_str) != except);
    if conflict {
        return Err(ApiError::conflict(format!(
            "unit symbol `{symbol}` already exists"
        )));
    }
    Ok(())
}

/// Resolves a unit reference (symbol or record key) to the canonical (base)
/// unit of its dimension, following the chain if a display unit is given.
async fn resolve_canonical(db: &Db, reference: &str) -> Result<(Value, String), ApiError> {
    let mut current = find_unit(db, reference)
        .await?
        .ok_or_else(|| ApiError::bad_request(format!("`{reference}` does not reference a unit")))?;
    let mut guard = 0;
    while guard < 4 {
        let canonical_id = canonical_unit_id_of(&current);
        if canonical_id.is_empty() {
            break;
        }
        current = find_unit(db, &canonical_id)
            .await?
            .ok_or_else(|| ApiError::internal("canonical unit not found"))?;
        guard += 1;
    }
    let canonical_key = current
        .get("id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .split(':')
        .next_back()
        .unwrap_or_default()
        .to_owned();
    Ok((current, canonical_key))
}

/// Creates a custom unit (admin/superadmin).
#[utoipa::path(
    post,
    path = "/units",
    request_body = CreateUnit,
    responses(
        (status = 201, description = "Created unit", body = Value),
        (status = 400, description = "Invalid payload", body = Value),
        (status = 409, description = "Symbol already exists", body = Value)
    )
)]
pub async fn create_unit(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Json(body): Json<CreateUnit>,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    claims.require("attribute_definition.manage")?;
    let symbol = body.symbol.trim().to_owned();
    if symbol.is_empty() {
        return Err(ApiError::bad_request("`symbol` must not be empty"));
    }
    let factor = body.factor;
    if !factor.is_finite() || factor == 0.0 {
        return Err(ApiError::bad_request("`factor` must be a non-zero number"));
    }
    ensure_unique_symbol(&state.db, &symbol, None).await?;

    let (canonical, canonical_key) =
        resolve_canonical(&state.db, body.canonical_unit_id.trim()).await?;
    let dimension = dimension_of(&canonical);

    let content = json!({
        "symbol": symbol,
        "dimension": dimension,
        "canonical_unit_id": canonical_key,
        "factor": factor,
        "offset": body.offset,
        "label": body.label.unwrap_or_else(|| json!({})),
        "system": "custom",
        "readonly": false,
    });
    let sql = "
        LET $u = type::record('unit', rand::ulid());
        CREATE $u CONTENT $content;
        SELECT * FROM $u;
    ";
    let mut response = state
        .db
        .inner()
        .query(sql)
        .bind(("content", content))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(2)?;
    let unit = rows
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::internal("created unit was not returned"))?;
    let unit_id = unit
        .get("id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();

    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &claims.sub,
            action: "unit_created",
            object_id: Some(&unit_id),
            from: None,
            to: None,
            metadata: Some(json!({ "symbol": symbol, "dimension": dimension })),
        },
    )
    .await;

    Ok((StatusCode::CREATED, Json(unit)))
}

/// Partially updates a custom unit (admin/superadmin).
#[utoipa::path(
    patch,
    path = "/units/{id}",
    request_body = UpdateUnit,
    responses(
        (status = 200, description = "Updated unit", body = Value),
        (status = 400, description = "Invalid id/payload or default unit", body = Value),
        (status = 404, description = "Unit not found", body = Value),
        (status = 409, description = "Symbol already exists", body = Value)
    )
)]
pub async fn update_unit(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
    Json(body): Json<UpdateUnit>,
) -> Result<Json<Value>, ApiError> {
    claims.require("attribute_definition.manage")?;
    let key = parse_key(&id, "unit")?;
    let current = fetch_unit_by_key(&state.db, key)
        .await?
        .ok_or_else(|| ApiError::not_found(format!("unit `{id}` not found")))?;
    if is_readonly(&current) {
        return Err(ApiError::bad_request("default unit is read-only"));
    }

    let mut changes = Map::new();
    if let Some(symbol) = body.symbol {
        let symbol = symbol.trim().to_owned();
        if symbol.is_empty() {
            return Err(ApiError::bad_request("`symbol` must not be empty"));
        }
        ensure_unique_symbol(&state.db, &symbol, Some(id.as_str())).await?;
        changes.insert("symbol".to_owned(), Value::String(symbol));
    }
    if let Some(factor) = body.factor {
        if !factor.is_finite() || factor == 0.0 {
            return Err(ApiError::bad_request("`factor` must be a non-zero number"));
        }
        changes.insert("factor".to_owned(), json!(factor));
    }
    if let Some(offset) = body.offset {
        changes.insert("offset".to_owned(), json!(offset));
    }
    if let Some(label) = body.label {
        changes.insert("label".to_owned(), label);
    }
    if let Some(reference) = body.canonical_unit_id {
        let (canonical, canonical_key) = resolve_canonical(&state.db, reference.trim()).await?;
        changes.insert("canonical_unit_id".to_owned(), Value::String(canonical_key));
        changes.insert(
            "dimension".to_owned(),
            Value::String(dimension_of(&canonical)),
        );
    }

    let sql = "
        LET $u = type::record('unit', $key);
        UPDATE $u MERGE $changes;
        UPDATE $u SET updated_at = time::now();
        SELECT * FROM $u;
    ";
    let mut response = state
        .db
        .inner()
        .query(sql)
        .bind(("key", key))
        .bind(("changes", Value::Object(changes)))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(3)?;
    let unit = rows
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::not_found(format!("unit `{id}` not found")))?;

    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &claims.sub,
            action: "unit_updated",
            object_id: Some(&id),
            from: None,
            to: None,
            metadata: None,
        },
    )
    .await;

    Ok(Json(unit))
}

/// Deletes a custom unit (admin/superadmin). Default units are read-only and a
/// unit referenced by an attribute definition cannot be deleted.
#[utoipa::path(
    delete,
    path = "/units/{id}",
    responses(
        (status = 204, description = "Deleted unit"),
        (status = 400, description = "Default unit", body = Value),
        (status = 404, description = "Unit not found", body = Value),
        (status = 409, description = "Unit in use", body = Value)
    )
)]
pub async fn delete_unit(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<StatusCode, ApiError> {
    claims.require("attribute_definition.manage")?;
    let key = parse_key(&id, "unit")?;
    let current = fetch_unit_by_key(&state.db, key)
        .await?
        .ok_or_else(|| ApiError::not_found(format!("unit `{id}` not found")))?;
    if is_readonly(&current) {
        return Err(ApiError::bad_request("default unit is read-only"));
    }
    let symbol = symbol_of(&current);
    let in_use: Vec<Value> = state
        .db
        .inner()
        .query(
            "SELECT id FROM attribute_definition WHERE unit_id IN [$id, $key, $symbol] OR display_unit_id IN [$id, $key, $symbol];",
        )
        .bind(("id", id.clone()))
        .bind(("key", key))
        .bind(("symbol", symbol))
        .await?
        .check()?
        .take(0)?;
    if !in_use.is_empty() {
        return Err(ApiError::conflict(
            "unit is in use by one or more attribute definitions",
        ));
    }

    let sql = "LET $u = type::record('unit', $key); DELETE $u;";
    state
        .db
        .inner()
        .query(sql)
        .bind(("key", key))
        .await?
        .check()?;

    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &claims.sub,
            action: "unit_deleted",
            object_id: Some(&id),
            from: None,
            to: None,
            metadata: None,
        },
    )
    .await;

    Ok(StatusCode::NO_CONTENT)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn linear_conversion_without_offset() {
        // 12.5 mm -> m: 12.5 * 0.001 = 0.0125
        let value = convert_value(12.5, 0.001, 0.0, 1.0, 0.0);
        assert!((value - 0.0125).abs() < 1e-12);
    }

    #[test]
    fn affine_conversion_celsius_to_kelvin() {
        // 20 °C -> K: 20 * 1 + 273.15 = 293.15
        let value = convert_value(20.0, 1.0, 273.15, 1.0, 0.0);
        assert!((value - 293.15).abs() < 1e-9);
    }

    #[test]
    fn affine_conversion_kelvin_to_fahrenheit() {
        // 293.15 K -> °F: (293.15 - 255.372222...) / 0.555555... ≈ 68.0
        let value = convert_value(
            293.15,
            1.0,
            0.0,
            0.555_555_555_555_555_6,
            255.372_222_222_222_23,
        );
        assert!((value - 68.0).abs() < 1e-6);
    }
}
