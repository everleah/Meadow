//! Unit catalog and conversion endpoints (Phase 6).
//!
//! The `unit` table is seeded at startup (Phase 1); these read-only endpoints
//! expose it as the single source of truth for quantified-value conversion.
//! Values are stored in the canonical SI unit of their dimension; conversion
//! is the linear-affine function `canonical = value * factor + offset`.

use axum::Json;
use axum::extract::{Query, State};
use serde::Deserialize;
use serde_json::{Value, json};
use utoipa::{IntoParams, ToSchema};

use crate::app::AppState;
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
