//! Language-tree query endpoint (Phase 7, product form).
//!
//! The `language_tree` table is seeded at startup (see `seed.rs`) and lists
//! every known data language (base languages and regional variants). The
//! product form uses this as the source for which data languages exist, so
//! the user can edit multilingual attribute values across all of them.

use axum::Json;
use axum::extract::State;
use serde_json::Value;
use utoipa::ToSchema;

use crate::app::AppState;
use crate::auth::AuthUser;
use crate::error::ApiError;

/// A single language tree node exposed to clients.
#[derive(Debug, ToSchema)]
pub struct LanguageNode {
    /// Language code, e.g. `en`, `de`, `fr`, `en-GB`.
    pub code: String,
    /// Record key of the parent node, e.g. `lang_en`, or `null` for the root.
    pub parent: Option<String>,
    /// Multilingual label object, e.g. `{"en": "English", "de": "Englisch"}`.
    pub label: Value,
    /// Fallback chain used for resolution, e.g. `["en", "root"]`.
    pub fallback_order: Vec<String>,
}

/// Lists the data languages from the `language_tree` table.
#[utoipa::path(
    get,
    path = "/languages",
    responses(
        (status = 200, description = "List of data languages", body = Vec<LanguageNode>),
        (status = 401, description = "Unauthorized", body = Value)
    )
)]
pub async fn list_languages(
    AuthUser(_claims): AuthUser,
    State(state): State<AppState>,
) -> Result<Json<Vec<Value>>, ApiError> {
    let mut response = state
        .db
        .inner()
        .query("SELECT code, parent, label, fallback_order FROM language_tree ORDER BY code;")
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    Ok(Json(rows))
}
