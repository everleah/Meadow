//! `GET /permissions`: the permission catalog as a read-only contract.
//!
//! The catalog itself lives in [`crate::permissions`] (it is domain data, used by the
//! login path and the write paths as well); this module only serves it. The UI renders
//! its user and group editors from it, and the AI area reads it to suggest permissions,
//! so it follows the same pattern as `GET /jobs/schema` and `GET /connector-types`:
//! one document, declared where the values are declared.
//!
//! Visible to every authenticated caller (`viewer` and up, and the worker's API key):
//! the document lists no data, only which permissions exist and which role holds them.

use axum::Json;
use axum::extract::State;
use axum::http::HeaderMap;
use serde_json::Value;

use crate::app::AppState;
use crate::auth::{ROLE_VIEWER, api_key_or_role};
use crate::error::ApiError;
use crate::permissions;

/// The permission catalog: every permission with its area, its role defaults, a label
/// key for the UI and a one-sentence description, plus the areas and their order.
#[utoipa::path(
    get,
    path = "/permissions",
    responses(
        (status = 200, description = "Permission catalog", body = Value),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Forbidden", body = Value)
    )
)]
pub async fn permission_catalog(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    api_key_or_role(&headers, &state.config, ROLE_VIEWER)?;
    Ok(Json(permissions::document()))
}
