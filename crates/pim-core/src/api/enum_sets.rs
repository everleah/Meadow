//! Enum set & option handlers (Phase 9, Reference-Data style).
//!
//! Enum options are decoupled from the attribute definition: an `enum_option`
//! is a stable `code` plus multilingual `labels`, and an `enum_set` bundles a
//! reusable list of options. An `enum` attribute references an `enum_set`
//! (`attribute_definition.enum_set_id`) instead of a string list, while objects
//! still store the option's `code` (language-independent). Display resolves the
//! label per data language via the `language_tree` fallback chain.

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

const OPTION_TABLE: &str = "enum_option";

/// Payload for creating an enum set.
#[derive(Debug, Deserialize, ToSchema)]
pub struct CreateEnumSet {
    /// Set name (unique).
    pub name: String,
    /// Multilingual label.
    #[serde(default)]
    pub label: Option<Value>,
    /// Option ids (full `enum_option:<key>` or bare key).
    #[serde(default)]
    pub options: Vec<String>,
    /// Sort position.
    #[serde(default)]
    pub position: Option<i64>,
}

/// Payload for partially updating an enum set.
#[derive(Debug, Deserialize, ToSchema)]
pub struct UpdateEnumSet {
    #[serde(default)]
    pub label: Option<Value>,
    #[serde(default)]
    pub options: Option<Vec<String>>,
    #[serde(default)]
    pub position: Option<i64>,
    /// When removing options that are still stored on objects, a map
    /// `{ old_code: new_code }` re-assigns those values first.
    #[serde(default)]
    pub option_remap: Option<serde_json::Map<String, Value>>,
}

/// Payload for creating an enum option.
#[derive(Debug, Deserialize, ToSchema)]
pub struct CreateEnumOption {
    /// Stable, language-independent code (e.g. `red`).
    pub code: String,
    /// Multilingual labels.
    #[serde(default)]
    pub labels: Option<Value>,
    #[serde(default)]
    pub position: Option<i64>,
    #[serde(default)]
    pub active: Option<bool>,
}

/// Payload for partially updating an enum option.
#[derive(Debug, Deserialize, ToSchema)]
pub struct UpdateEnumOption {
    #[serde(default)]
    pub labels: Option<Value>,
    #[serde(default)]
    pub position: Option<i64>,
    #[serde(default)]
    pub active: Option<bool>,
}

/// Query parameters for listing enum options.
#[derive(Debug, Deserialize, ToSchema, IntoParams)]
#[into_params(parameter_in = Query)]
pub struct ListEnumOptions {
    /// Optionally filter by the enum set they belong to.
    #[serde(default)]
    pub set_id: Option<String>,
}

fn key_of(record_id: &str) -> String {
    record_id
        .split_once(':')
        .map(|(_, key)| key.to_owned())
        .unwrap_or_else(|| record_id.to_owned())
}

fn record_id(table: &str, value: &str) -> String {
    if value.contains(':') {
        value.to_owned()
    } else {
        format!("{table}:{value}")
    }
}

/// Normalizes an option list to full `enum_option:<key>` record-id strings.
fn option_records(options: &[String]) -> Vec<String> {
    options
        .iter()
        .map(|o| record_id(OPTION_TABLE, o.trim()))
        .filter(|o| !o.ends_with(':'))
        .collect()
}

async fn fetch_enum_set(db: &Db, key: &str) -> Result<Option<Value>, ApiError> {
    let sql = "LET $r = type::record('enum_set', $key); SELECT * FROM $r;";
    let mut response = db.inner().query(sql).bind(("key", key)).await?.check()?;
    let rows: Vec<Value> = response.take(1)?;
    Ok(rows.into_iter().next())
}

async fn fetch_enum_option(db: &Db, key: &str) -> Result<Option<Value>, ApiError> {
    let sql = "LET $r = type::record('enum_option', $key); SELECT * FROM $r;";
    let mut response = db.inner().query(sql).bind(("key", key)).await?.check()?;
    let rows: Vec<Value> = response.take(1)?;
    Ok(rows.into_iter().next())
}

/// Loads the option records referenced by a set's `options` array.
async fn resolve_options(db: &Db, set: &Value) -> Result<Vec<Value>, ApiError> {
    let option_keys: Vec<String> = set
        .get("options")
        .and_then(Value::as_array)
        .map(|a| a.iter().filter_map(Value::as_str).map(key_of).collect())
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
    Ok(rows)
}

/// Lists enum sets, each with its options resolved.
#[utoipa::path(
    get,
    path = "/enum-sets",
    responses(
        (status = 200, description = "Enum sets", body = Vec<Value>),
        (status = 403, description = "Forbidden", body = Value)
    )
)]
pub async fn list_enum_sets(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
) -> Result<Json<Vec<Value>>, ApiError> {
    if !claims.is_editor() {
        return Err(ApiError::forbidden("requires editor role"));
    }
    let mut response = state
        .db
        .inner()
        .query("SELECT * FROM enum_set ORDER BY position, name;")
        .await?
        .check()?;
    let sets: Vec<Value> = response.take(0)?;
    let mut result = Vec::with_capacity(sets.len());
    for mut set in sets {
        let options = resolve_options(&state.db, &set).await?;
        set["options"] = Value::Array(options);
        result.push(set);
    }
    Ok(Json(result))
}

/// Creates an enum set (admin).
#[utoipa::path(
    post,
    path = "/enum-sets",
    request_body = CreateEnumSet,
    responses(
        (status = 201, description = "Created", body = Value),
        (status = 400, description = "Invalid payload", body = Value),
        (status = 403, description = "Forbidden", body = Value)
    )
)]
pub async fn create_enum_set(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Json(body): Json<CreateEnumSet>,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    if !claims.is_admin() {
        return Err(ApiError::forbidden("requires admin role"));
    }
    let name = body.name.trim().to_owned();
    if name.is_empty() {
        return Err(ApiError::bad_request("`name` must not be empty"));
    }
    if !name
        .chars()
        .all(|c| c.is_alphanumeric() || c == '_' || c == '-')
    {
        return Err(ApiError::bad_request(
            "`name` may only contain letters, digits, `_` and `-`",
        ));
    }
    let existing: Vec<Value> = state
        .db
        .inner()
        .query("SELECT id FROM enum_set WHERE name = $name LIMIT 1")
        .bind(("name", name.clone()))
        .await?
        .check()?
        .take(0)?;
    if !existing.is_empty() {
        return Err(ApiError::conflict(format!(
            "enum set `{name}` already exists"
        )));
    }
    let mut content = Map::new();
    content.insert("name".to_owned(), Value::String(name.clone()));
    if let Some(label) = body.label.clone() {
        content.insert("label".to_owned(), label);
    } else {
        content.insert("label".to_owned(), json!({ "en": name }));
    }
    content.insert(
        "options".to_owned(),
        Value::Array(
            option_records(&body.options)
                .into_iter()
                .map(Value::String)
                .collect(),
        ),
    );
    content.insert(
        "position".to_owned(),
        Value::from(body.position.unwrap_or(0)),
    );

    let key = format!("enum_set_{name}");
    state
        .db
        .inner()
        .query("LET $r = type::record('enum_set', $key); CREATE $r CONTENT $content;")
        .bind(("key", key.clone()))
        .bind(("content", Value::Object(content)))
        .await?
        .check()?;
    let set = fetch_enum_set(&state.db, &key)
        .await?
        .ok_or_else(|| ApiError::internal("created enum set was not returned"))?;
    let record_id = set.get("id").and_then(Value::as_str).unwrap_or_default();
    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &claims.sub,
            action: "enum_set_created",
            object_id: Some(record_id),
            from: None,
            to: None,
            metadata: Some(json!({ "name": name })),
        },
    )
    .await;
    Ok((StatusCode::CREATED, Json(set)))
}

/// Gets a single enum set with its options resolved.
#[utoipa::path(
    get,
    path = "/enum-sets/{id}",
    responses(
        (status = 200, description = "Enum set", body = Value),
        (status = 404, description = "Not found", body = Value)
    )
)]
pub async fn get_enum_set(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<Json<Value>, ApiError> {
    if !claims.is_editor() {
        return Err(ApiError::forbidden("requires editor role"));
    }
    let key = key_of(&id);
    let mut set = fetch_enum_set(&state.db, &key)
        .await?
        .ok_or_else(|| ApiError::not_found(format!("enum set `{id}` not found")))?;
    let options = resolve_options(&state.db, &set).await?;
    set["options"] = Value::Array(options);
    Ok(Json(set))
}

/// Partially updates an enum set (admin).
#[utoipa::path(
    patch,
    path = "/enum-sets/{id}",
    request_body = UpdateEnumSet,
    responses(
        (status = 200, description = "Updated", body = Value),
        (status = 400, description = "Invalid payload", body = Value),
        (status = 403, description = "Forbidden", body = Value),
        (status = 404, description = "Not found", body = Value)
    )
)]
pub async fn update_enum_set(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
    Json(body): Json<UpdateEnumSet>,
) -> Result<Json<Value>, ApiError> {
    if !claims.is_admin() {
        return Err(ApiError::forbidden("requires admin role"));
    }
    let key = key_of(&id);
    let current = fetch_enum_set(&state.db, &key)
        .await?
        .ok_or_else(|| ApiError::not_found(format!("enum set `{id}` not found")))?;

    let mut content = Map::new();
    if let Some(label) = body.label {
        content.insert("label".to_owned(), label);
    }
    if let Some(position) = body.position {
        content.insert("position".to_owned(), Value::from(position));
    }
    if let Some(options) = body.options {
        // Enforce that referenced option records exist.
        let recs = option_records(&options);
        for rec in &recs {
            if fetch_enum_option(&state.db, &key_of(rec)).await?.is_none() {
                return Err(ApiError::bad_request(format!(
                    "enum option `{rec}` does not exist"
                )));
            }
        }
        removed_usage_check(&state.db, &current, &options, body.option_remap.as_ref()).await?;
        content.insert(
            "options".to_owned(),
            Value::Array(recs.into_iter().map(Value::String).collect()),
        );
    }

    let mut response = state
        .db
        .inner()
        .query(
            "LET $r = type::record('enum_set', $key); UPDATE $r MERGE $content; UPDATE $r SET updated_at = time::now(), updated_by = $user; SELECT * FROM $r;",
        )
        .bind(("key", key.clone()))
        .bind(("content", Value::Object(content)))
        .bind(("user", claims.sub.clone()))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(3)?;
    let set = rows.into_iter().next().unwrap_or_else(|| current.clone());
    let record_id = set.get("id").and_then(Value::as_str).unwrap_or_default();
    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &claims.sub,
            action: "enum_set_updated",
            object_id: Some(record_id),
            from: None,
            to: None,
            metadata: Some(json!({ "name": key })),
        },
    )
    .await;
    Ok(Json(set))
}

/// Checks objects that carry an option code being removed from a set; needs a
/// remap target if used, else blocks with 409 (set-level, across the
/// attributes that reference this set).
async fn removed_usage_check(
    db: &Db,
    current: &Value,
    new_options: &[String],
    remap: Option<&serde_json::Map<String, Value>>,
) -> Result<(), ApiError> {
    let old_codes: Vec<String> = current
        .get("options")
        .and_then(Value::as_array)
        .map(|a| a.iter().filter_map(Value::as_str).map(key_of).collect())
        .unwrap_or_default();
    let new_keys: std::collections::HashSet<String> = option_records(new_options)
        .into_iter()
        .map(|s| key_of(&s))
        .collect();
    // Collect the attribute names referencing this set (the set key).
    let set_key = current
        .get("id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    let attr_names: Vec<String> = find_attributes_for_set(db, &set_key).await?;
    if attr_names.is_empty() {
        return Ok(());
    }
    for old_key in old_codes {
        if new_keys.contains(&old_key) {
            continue;
        }
        // Resolve the option code (from the option record by key).
        let Some(option) = fetch_enum_option(db, &old_key).await? else {
            continue;
        };
        let Some(code) = option.get("code").and_then(Value::as_str) else {
            continue;
        };
        let used = count_object_usage(db, &attr_names, code).await?;
        if used <= 0 {
            continue;
        }
        let target = remap
            .and_then(|m| m.get(code))
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|s| !s.is_empty());
        match target {
            Some(target) => {
                migrate_object_codes(db, &attr_names, code, target).await?;
            }
            None => {
                return Err(ApiError::conflict(format!(
                    "enum value `{code}` is still used by {used} object(s); re-map it or keep it"
                )));
            }
        }
    }
    Ok(())
}

/// Attribute names whose definition references the given enum set.
async fn find_attributes_for_set(db: &Db, set_id: &str) -> Result<Vec<String>, ApiError> {
    let mut response = db
        .inner()
        .query("SELECT name FROM attribute_definition WHERE enum_set_id = $set AND type = 'enum' LIMIT 1000;")
        .bind(("set", set_id))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    Ok(rows
        .iter()
        .filter_map(|r| r.get("name").and_then(Value::as_str).map(str::to_owned))
        .collect())
}

/// Counts objects carrying the given code in any of the attribute names.
async fn count_object_usage(db: &Db, attr_names: &[String], code: &str) -> Result<i64, ApiError> {
    let mut total = 0i64;
    for name in attr_names {
        let sql = "SELECT count() AS count FROM object WHERE deleted_at IS NONE AND attributes[$name] = $value GROUP ALL;";
        let mut response = db
            .inner()
            .query(sql)
            .bind(("name", name.clone()))
            .bind(("value", code.to_owned()))
            .await?
            .check()?;
        let rows: Vec<Value> = response.take(0)?;
        total += rows
            .first()
            .and_then(|r| r.get("count"))
            .and_then(Value::as_i64)
            .unwrap_or(0);
    }
    Ok(total)
}

/// Migrates objects carrying `old_code` to `new_code` for the attribute names.
async fn migrate_object_codes(
    db: &Db,
    attr_names: &[String],
    old_code: &str,
    new_code: &str,
) -> Result<(), ApiError> {
    for name in attr_names {
        db.inner()
            .query(
                "UPDATE object SET attributes[$name] = $target WHERE deleted_at IS NONE AND attributes[$name] = $old;",
            )
            .bind(("name", name.clone()))
            .bind(("old", old_code.to_owned()))
            .bind(("target", new_code.to_owned()))
            .await?
            .check()?;
    }
    Ok(())
}

/// Deletes an enum set (admin). Blocked while an attribute references it.
#[utoipa::path(
    delete,
    path = "/enum-sets/{id}",
    responses(
        (status = 204, description = "Deleted"),
        (status = 400, description = "Invalid id", body = Value),
        (status = 403, description = "Forbidden", body = Value),
        (status = 404, description = "Not found", body = Value),
        (status = 409, description = "In use", body = Value)
    )
)]
pub async fn delete_enum_set(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<StatusCode, ApiError> {
    if !claims.is_admin() {
        return Err(ApiError::forbidden("requires admin role"));
    }
    let key = key_of(&id);
    let set = fetch_enum_set(&state.db, &key)
        .await?
        .ok_or_else(|| ApiError::not_found(format!("enum set `{id}` not found")))?;
    let record_id = set.get("id").and_then(Value::as_str).unwrap_or_default();
    let used: Vec<Value> = state
        .db
        .inner()
        .query("SELECT id FROM attribute_definition WHERE enum_set_id = $set LIMIT 1;")
        .bind(("set", record_id.to_owned()))
        .await?
        .check()?
        .take(0)?;
    if !used.is_empty() {
        return Err(ApiError::conflict(format!(
            "enum set `{id}` is referenced by an attribute definition"
        )));
    }
    state
        .db
        .inner()
        .query("DELETE type::record('enum_set', $key);")
        .bind(("key", key.clone()))
        .await?
        .check()?;
    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &claims.sub,
            action: "enum_set_deleted",
            object_id: Some(record_id),
            from: None,
            to: None,
            metadata: Some(json!({ "name": key })),
        },
    )
    .await;
    Ok(StatusCode::NO_CONTENT)
}

/// Lists enum options (optionally filtered by set id).
#[utoipa::path(
    get,
    path = "/enum-options",
    params(ListEnumOptions),
    responses(
        (status = 200, description = "Enum options", body = Vec<Value>),
        (status = 403, description = "Forbidden", body = Value)
    )
)]
pub async fn list_enum_options(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Query(params): Query<ListEnumOptions>,
) -> Result<Json<Vec<Value>>, ApiError> {
    if !claims.is_editor() {
        return Err(ApiError::forbidden("requires editor role"));
    }
    if let Some(set_id) = params.set_id {
        let key = key_of(&set_id);
        let set = fetch_enum_set(&state.db, &key)
            .await?
            .ok_or_else(|| ApiError::not_found(format!("enum set `{set_id}` not found")))?;
        let options = resolve_options(&state.db, &set).await?;
        return Ok(Json(options));
    }
    let mut response = state
        .db
        .inner()
        .query("SELECT * FROM enum_option ORDER BY position, code;")
        .await?
        .check()?;
    let options: Vec<Value> = response.take(0)?;
    Ok(Json(options))
}

/// Creates an enum option (admin).
#[utoipa::path(
    post,
    path = "/enum-options",
    request_body = CreateEnumOption,
    responses(
        (status = 201, description = "Created", body = Value),
        (status = 400, description = "Invalid payload", body = Value),
        (status = 403, description = "Forbidden", body = Value)
    )
)]
pub async fn create_enum_option(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Json(body): Json<CreateEnumOption>,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    if !claims.is_admin() {
        return Err(ApiError::forbidden("requires admin role"));
    }
    let code = body.code.trim().to_owned();
    if code.is_empty() {
        return Err(ApiError::bad_request("`code` must not be empty"));
    }
    let existing: Vec<Value> = state
        .db
        .inner()
        .query("SELECT id FROM enum_option WHERE code = $code LIMIT 1")
        .bind(("code", code.clone()))
        .await?
        .check()?
        .take(0)?;
    if !existing.is_empty() {
        return Err(ApiError::conflict(format!(
            "enum option `{code}` already exists"
        )));
    }
    let mut content = Map::new();
    content.insert("code".to_owned(), Value::String(code.clone()));
    if let Some(labels) = body.labels.clone() {
        content.insert("labels".to_owned(), labels);
    } else {
        content.insert("labels".to_owned(), json!({ "en": code }));
    }
    content.insert(
        "position".to_owned(),
        Value::from(body.position.unwrap_or(0)),
    );
    content.insert(
        "active".to_owned(),
        Value::Bool(body.active.unwrap_or(true)),
    );

    let key = format!("enum_option_{code}");
    state
        .db
        .inner()
        .query("LET $r = type::record('enum_option', $key); CREATE $r CONTENT $content;")
        .bind(("key", key.clone()))
        .bind(("content", Value::Object(content)))
        .await?
        .check()?;
    let option = fetch_enum_option(&state.db, &key)
        .await?
        .ok_or_else(|| ApiError::internal("created enum option was not returned"))?;
    let record_id = option.get("id").and_then(Value::as_str).unwrap_or_default();
    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &claims.sub,
            action: "enum_option_created",
            object_id: Some(record_id),
            from: None,
            to: None,
            metadata: Some(json!({ "code": code })),
        },
    )
    .await;
    Ok((StatusCode::CREATED, Json(option)))
}

/// Partially updates an enum option (admin).
#[utoipa::path(
    patch,
    path = "/enum-options/{id}",
    request_body = UpdateEnumOption,
    responses(
        (status = 200, description = "Updated", body = Value),
        (status = 403, description = "Forbidden", body = Value),
        (status = 404, description = "Not found", body = Value)
    )
)]
pub async fn update_enum_option(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
    Json(body): Json<UpdateEnumOption>,
) -> Result<Json<Value>, ApiError> {
    if !claims.is_admin() {
        return Err(ApiError::forbidden("requires admin role"));
    }
    let key = key_of(&id);
    let current = fetch_enum_option(&state.db, &key)
        .await?
        .ok_or_else(|| ApiError::not_found(format!("enum option `{id}` not found")))?;
    let mut content = Map::new();
    if let Some(labels) = body.labels {
        content.insert("labels".to_owned(), labels);
    }
    if let Some(position) = body.position {
        content.insert("position".to_owned(), Value::from(position));
    }
    if let Some(active) = body.active {
        content.insert("active".to_owned(), Value::Bool(active));
    }
    let mut response = state
        .db
        .inner()
        .query(
            "LET $r = type::record('enum_option', $key); UPDATE $r MERGE $content; UPDATE $r SET updated_at = time::now(), updated_by = $user; SELECT * FROM $r;",
        )
        .bind(("key", key.clone()))
        .bind(("content", Value::Object(content)))
        .bind(("user", claims.sub.clone()))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(3)?;
    let option = rows.into_iter().next().unwrap_or_else(|| current.clone());
    let record_id = option.get("id").and_then(Value::as_str).unwrap_or_default();
    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &claims.sub,
            action: "enum_option_updated",
            object_id: Some(record_id),
            from: None,
            to: None,
            metadata: Some(json!({ "code": key })),
        },
    )
    .await;
    Ok(Json(option))
}

/// Deletes an enum option (admin). Blocked while an enum set references it.
#[utoipa::path(
    delete,
    path = "/enum-options/{id}",
    responses(
        (status = 204, description = "Deleted"),
        (status = 400, description = "Invalid id", body = Value),
        (status = 403, description = "Forbidden", body = Value),
        (status = 404, description = "Not found", body = Value),
        (status = 409, description = "In use", body = Value)
    )
)]
pub async fn delete_enum_option(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<StatusCode, ApiError> {
    if !claims.is_admin() {
        return Err(ApiError::forbidden("requires admin role"));
    }
    let key = key_of(&id);
    let option = fetch_enum_option(&state.db, &key)
        .await?
        .ok_or_else(|| ApiError::not_found(format!("enum option `{id}` not found")))?;
    let record_id = option.get("id").and_then(Value::as_str).unwrap_or_default();
    let used: Vec<Value> = state
        .db
        .inner()
        .query("SELECT id FROM enum_set WHERE options CONTAINS $rid LIMIT 1;")
        .bind(("rid", record_id.to_owned()))
        .await?
        .check()?
        .take(0)?;
    if !used.is_empty() {
        return Err(ApiError::conflict(format!(
            "enum option `{id}` is referenced by an enum set"
        )));
    }
    state
        .db
        .inner()
        .query("DELETE type::record('enum_option', $key);")
        .bind(("key", key.clone()))
        .await?
        .check()?;
    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &claims.sub,
            action: "enum_option_deleted",
            object_id: Some(record_id),
            from: None,
            to: None,
            metadata: Some(json!({ "code": key })),
        },
    )
    .await;
    Ok(StatusCode::NO_CONTENT)
}
