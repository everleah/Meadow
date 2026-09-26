//! Language-tree query + admin endpoints.
//!
//! The `language_tree` table is seeded at startup (see `seed.rs`) and lists
//! every known data language (base languages and regional variants). `GET
//! /languages` is read-only (the product form lists the data languages);
//! `POST`/`PATCH`/`DELETE /languages/{code}` maintain the tree (admin only)
//! and recompute each node's `fallback_order` from its new parent chain.

use axum::Json;
use axum::extract::{Path, State};
use axum::http::{HeaderMap, StatusCode};
use serde::Deserialize;
use serde_json::{Value, json};
use utoipa::ToSchema;

use crate::app::AppState;
use crate::auth::AuthUser;
use crate::db::Db;
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
    /// Whether the language is active for data maintenance (product form).
    pub active: bool,
    /// Whether the language is offered as a UI language (drawer switcher).
    pub is_ui: bool,
}

/// Payload for creating a language tree node.
#[derive(Debug, Deserialize, ToSchema)]
pub struct CreateLanguage {
    /// Language code (unique), e.g. `es`, `it`, `de-CH`.
    pub code: String,
    /// Parent record key (e.g. `lang_de`) or empty/null for the universal root.
    pub parent: Option<String>,
    /// Multilingual label object `{"en": ..., "de": ...}`.
    pub label: Value,
    /// Whether the language is active for data maintenance (default true).
    #[serde(default = "default_true")]
    pub active: bool,
    /// Whether the language is offered as a UI language (default false).
    #[serde(default)]
    pub is_ui: bool,
}

/// Payload for partially updating a language tree node.
#[derive(Debug, Deserialize, ToSchema)]
pub struct UpdateLanguage {
    /// New parent record key; an empty string resets to the universal root;
    /// a missing field leaves the parent unchanged.
    pub parent: Option<String>,
    /// New multilingual label object.
    pub label: Option<Value>,
    /// Activate/deactivate for data maintenance.
    pub active: Option<bool>,
    /// Offer/hide as a UI language.
    pub is_ui: Option<bool>,
}

fn default_true() -> bool {
    true
}

fn record_key(code: &str) -> String {
    format!("lang_{code}")
}

/// Returns the `fallback_order` for a node whose parent is `parent_key`
/// (empty for the universal root). The chain is `[parent.code] + parent.fallback_order`.
async fn parent_chain(db: &Db, parent_key: Option<&str>) -> Result<Vec<String>, ApiError> {
    let Some(parent_key) = parent_key else {
        return Ok(vec![]);
    };
    let mut response = db
        .inner()
        .query("SELECT code, fallback_order FROM type::record('language_tree', $pk);")
        .bind(("pk", parent_key.to_owned()))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    let row = rows
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::bad_request("parent language not found"))?;
    let code = row
        .get("code")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_owned();
    let parent_fb: Vec<String> = row
        .get("fallback_order")
        .and_then(Value::as_array)
        .map(|a| {
            a.iter()
                .filter_map(Value::as_str)
                .map(str::to_owned)
                .collect()
        })
        .unwrap_or_default();
    let mut chain = vec![code];
    chain.extend(parent_fb);
    Ok(chain)
}

/// Recomputes `fallback_order` for a node and all its descendants (BFS),
/// used after a parent change so the whole subtree stays consistent.
async fn recompute_subtree(db: &Db, code: &str) -> Result<(), ApiError> {
    let mut queue = vec![code.to_owned()];
    while let Some(current) = queue.pop() {
        let current_key = record_key(&current);
        let mut loaded = db
            .inner()
            .query("SELECT parent FROM type::record('language_tree', $key);")
            .bind(("key", current_key.clone()))
            .await?
            .check()?;
        let rows: Vec<Value> = loaded.take(0)?;
        let Some(row) = rows.into_iter().next() else {
            continue;
        };
        let parent = row.get("parent").and_then(Value::as_str).map(str::to_owned);
        let fallback = parent_chain(db, parent.as_deref()).await?;
        db.inner()
            .query("UPDATE type::record('language_tree', $key) SET fallback_order = $fb;")
            .bind(("key", current_key))
            .bind(("fb", fallback))
            .await?
            .check()?;
        let mut children = db
            .inner()
            .query("SELECT VALUE code FROM language_tree WHERE parent = $key;")
            .bind(("key", current))
            .await?
            .check()?;
        let next: Vec<String> = children.take(0)?;
        queue.extend(next);
    }
    Ok(())
}

/// Returns a node's code and every descendant code (BFS), for cycle protection.
async fn subtree_codes(db: &Db, code: &str) -> Result<Vec<String>, ApiError> {
    let mut result = vec![code.to_owned()];
    let mut queue = vec![code.to_owned()];
    while let Some(current) = queue.pop() {
        let mut children = db
            .inner()
            .query("SELECT VALUE code FROM language_tree WHERE parent = $key;")
            .bind(("key", current))
            .await?
            .check()?;
        let next: Vec<String> = children.take(0)?;
        for child in next {
            if !result.contains(&child) {
                result.push(child.clone());
                queue.push(child);
            }
        }
    }
    Ok(result)
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
    State(state): State<AppState>,
    headers: HeaderMap,
) -> Result<Json<Vec<Value>>, ApiError> {
    crate::auth::require_permission(&state, &headers, "language_tree.read")?;
    let mut response = state
        .db
        .inner()
        .query("SELECT code, parent, label, fallback_order, active, is_ui FROM language_tree ORDER BY code;")
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    Ok(Json(rows))
}

/// Creates a language tree node (admin/superadmin only).
#[utoipa::path(
    post,
    path = "/languages",
    request_body = CreateLanguage,
    responses(
        (status = 201, description = "Created language", body = Value),
        (status = 400, description = "Invalid payload or parent", body = Value),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Forbidden", body = Value),
        (status = 409, description = "Code already exists", body = Value)
    )
)]
pub async fn create_language(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Json(body): Json<CreateLanguage>,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    claims.require("language_tree.manage")?;
    let code = body.code.trim();
    if code.is_empty() {
        return Err(ApiError::bad_request("`code` must not be empty"));
    }
    let key = record_key(code);
    let mut exists = state
        .db
        .inner()
        .query("SELECT code FROM type::record('language_tree', $key);")
        .bind(("key", key.clone()))
        .await?
        .check()?;
    let rows: Vec<Value> = exists.take(0)?;
    if !rows.is_empty() {
        return Err(ApiError::conflict(format!(
            "language `{code}` already exists"
        )));
    }
    let parent = body
        .parent
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_owned);
    let fallback = parent_chain(&state.db, parent.as_deref()).await?;
    // SurrealDB `option<T>` fields reject JSON `null`; omit `parent` for the root.
    let mut content = serde_json::Map::new();
    content.insert("code".to_owned(), json!(code));
    content.insert("label".to_owned(), body.label);
    content.insert("fallback_order".to_owned(), json!(fallback));
    content.insert("active".to_owned(), json!(body.active));
    content.insert("is_ui".to_owned(), json!(body.is_ui));
    if let Some(pk) = &parent {
        content.insert("parent".to_owned(), json!(pk));
    }
    let mut response = state
        .db
        .inner()
        .query("CREATE type::record('language_tree', $key) CONTENT $content;")
        .bind(("key", key))
        .bind(("content", Value::Object(content)))
        .await?
        .check()?;
    let created: Vec<Value> = response.take(0)?;
    let node = created
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::internal("created language was not returned"))?;
    state.cache.invalidate_language_tree().await;
    Ok((StatusCode::CREATED, Json(node)))
}

/// Partially updates a language tree node (admin/superadmin only).
#[utoipa::path(
    patch,
    path = "/languages/{code}",
    request_body = UpdateLanguage,
    responses(
        (status = 200, description = "Updated language", body = Value),
        (status = 400, description = "Invalid payload, parent or cycle", body = Value),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Forbidden", body = Value),
        (status = 404, description = "Language not found", body = Value)
    )
)]
pub async fn update_language(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(code): Path<String>,
    Json(body): Json<UpdateLanguage>,
) -> Result<Json<Value>, ApiError> {
    claims.require("language_tree.manage")?;
    let key = record_key(&code);
    let mut current = state
        .db
        .inner()
        .query("SELECT * FROM type::record('language_tree', $key);")
        .bind(("key", key.clone()))
        .await?
        .check()?;
    let rows: Vec<Value> = current.take(0)?;
    if rows.is_empty() {
        return Err(ApiError::not_found(format!("language `{code}` not found")));
    }

    let mut bindings: Vec<(String, Value)> = Vec::new();
    let mut sets: Vec<String> = Vec::new();
    let mut parent_changed = false;

    if let Some(label) = body.label {
        sets.push("label = $label".to_owned());
        bindings.push(("label".to_owned(), label));
    }
    if let Some(active) = body.active {
        sets.push("active = $active".to_owned());
        bindings.push(("active".to_owned(), json!(active)));
    }
    if let Some(is_ui) = body.is_ui {
        sets.push("is_ui = $is_ui".to_owned());
        bindings.push(("is_ui".to_owned(), json!(is_ui)));
    }
    // Parent change is signalled by the field being present (empty => root).
    if let Some(raw_parent) = body.parent {
        let new_parent = if raw_parent.trim().is_empty() {
            None
        } else {
            Some(raw_parent.trim().to_owned())
        };
        if new_parent.as_deref() == Some(key.as_str()) {
            return Err(ApiError::bad_request("a language cannot be its own parent"));
        }
        // Cycle protection: the new parent may not be a descendant.
        let subtree = subtree_codes(&state.db, &code).await?;
        if let Some(parent_key) = &new_parent {
            let parent_code = parent_key.strip_prefix("lang_").unwrap_or(parent_key);
            if parent_code != code && subtree.iter().any(|c| c == parent_code) {
                return Err(ApiError::bad_request(
                    "would create a cycle in the language tree",
                ));
            }
        }
        match &new_parent {
            Some(pk) => {
                sets.push("parent = $parent".to_owned());
                bindings.push(("parent".to_owned(), json!(pk)));
            }
            None => {
                // SurrealDB `option<T>` fields reject JSON `null`; use NONE.
                sets.push("parent = NONE".to_owned());
            }
        }
        parent_changed = true;
    }

    if sets.is_empty() {
        return Ok(Json(Value::Null));
    }
    let set_sql = sets.join(", ");
    let mut query = state.db.inner().query(format!(
        "UPDATE type::record('language_tree', $key) SET {set_sql};"
    ));
    for (name, value) in bindings.iter() {
        query = query.bind((name.as_str(), value.clone()));
    }
    query = query.bind(("key", key.clone()));
    query.await?.check()?;
    // Recompute fallback_order for the node and its descendants after the
    // parent field has been written (reads the new parent).
    if parent_changed {
        recompute_subtree(&state.db, &code).await?;
    }
    let mut response = state
        .db
        .inner()
        .query("SELECT * FROM type::record('language_tree', $key);")
        .bind(("key", key.clone()))
        .await?
        .check()?;
    let updated: Vec<Value> = response.take(0)?;
    state.cache.invalidate_language_tree().await;
    let node = updated
        .into_iter()
        .next()
        .unwrap_or_else(|| json!({ "id": format!("language_tree:{key}") }));
    Ok(Json(node))
}

/// Deletes a language tree node (admin/superadmin only). A node that still has
/// child languages cannot be deleted (children would be orphaned).
#[utoipa::path(
    delete,
    path = "/languages/{code}",
    responses(
        (status = 204, description = "Language deleted"),
        (status = 400, description = "Node has children or is the root", body = Value),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Forbidden", body = Value),
        (status = 404, description = "Language not found", body = Value)
    )
)]
pub async fn delete_language(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(code): Path<String>,
) -> Result<StatusCode, ApiError> {
    claims.require("language_tree.manage")?;
    if code == "root" {
        return Err(ApiError::bad_request(
            "the universal root cannot be deleted",
        ));
    }
    let key = record_key(&code);
    let mut children = state
        .db
        .inner()
        .query("SELECT VALUE code FROM language_tree WHERE parent = $key;")
        .bind(("key", key.clone()))
        .await?
        .check()?;
    let child_codes: Vec<String> = children.take(0)?;
    if !child_codes.is_empty() {
        return Err(ApiError::bad_request(
            "language has child languages; remove them first",
        ));
    }
    let mut exists = state
        .db
        .inner()
        .query("SELECT code FROM type::record('language_tree', $key);")
        .bind(("key", key.clone()))
        .await?
        .check()?;
    let rows: Vec<Value> = exists.take(0)?;
    if rows.is_empty() {
        return Err(ApiError::not_found(format!("language `{code}` not found")));
    }
    state
        .db
        .inner()
        .query("DELETE type::record('language_tree', $key);")
        .bind(("key", key))
        .await?
        .check()?;
    state.cache.invalidate_language_tree().await;
    Ok(StatusCode::NO_CONTENT)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn record_key_prefixes_code() {
        assert_eq!(record_key("en-GB"), "lang_en-GB");
        assert_eq!(record_key("root"), "lang_root");
    }
}
