//! Full-text search over objects (plan: `POST /search`).
//!
//! Uses the SurrealDB full-text index on the flexible `attributes` object
//! (`idx_object_attributes_fulltext`, analyzer `meadow_std`). Results are
//! ranked by `search::score`.

use axum::Json;
use axum::extract::State;
use serde::Deserialize;
use serde_json::Value;
use utoipa::ToSchema;

use crate::app::AppState;
use crate::error::ApiError;

/// Payload for a full-text search.
#[derive(Debug, Deserialize, ToSchema)]
pub struct SearchRequest {
    /// Search terms (matched case-insensitively against attribute values).
    pub q: String,
    /// Restrict results to these object types (empty = all).
    #[serde(default)]
    pub types: Option<Vec<String>>,
    /// Maximum number of results (capped at 1000).
    #[serde(default = "default_limit")]
    pub limit: u32,
    /// Number of results to skip.
    #[serde(default)]
    pub start: u32,
}

fn default_limit() -> u32 {
    100
}

/// Searches objects by full-text matching on their attributes.
#[utoipa::path(
    post,
    path = "/search",
    request_body = SearchRequest,
    responses(
        (status = 200, description = "Search results", body = Vec<Value>),
        (status = 400, description = "Invalid query", body = Value)
    )
)]
pub async fn search(
    State(state): State<AppState>,
    Json(body): Json<SearchRequest>,
) -> Result<Json<Vec<Value>>, ApiError> {
    let query = body.q.trim();
    if query.is_empty() {
        return Err(ApiError::bad_request("`q` must not be empty"));
    }
    let limit = body.limit.clamp(1, 1000) as i64;
    let start = body.start as i64;

    let mut sql =
        String::from("SELECT *, search::score(1) AS score FROM object WHERE attributes @@ $q");
    if body.types.is_some() {
        sql.push_str(" AND type IN $types");
    }
    sql.push_str(" ORDER BY score DESC LIMIT $limit START $start");

    let mut request = state.db.inner().query(sql).bind(("q", query.to_owned()));
    if let Some(types) = &body.types {
        request = request.bind(("types", types.clone()));
    }
    let mut response = request
        .bind(("limit", limit))
        .bind(("start", start))
        .await?
        .check()?;
    let results: Vec<Value> = response.take(0)?;
    Ok(Json(results))
}
