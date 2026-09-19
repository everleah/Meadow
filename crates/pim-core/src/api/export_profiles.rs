//! Saved export profiles (Phase 9, `plan/detail/bmecat-export.md` §5).
//!
//! An **export profile** is the reusable configuration of the export wizard: the
//! format plus everything a user would otherwise retype on every run — the field
//! list, the locales, the options and the `bmecat` block (catalogue metadata,
//! transaction, prices, dialect). It stores exactly the parts of an
//! `ExportRequest` **minus `source`**: the data to export is picked per run, the
//! *how* is what is worth keeping.
//!
//! The wizard offers the stored profiles in a select and can save the current
//! settings as a new one; `POST /exports` is unchanged, so a profile is a
//! convenience for the caller, not a second configuration path. The MCF section
//! `export_profiles` makes them portable (`konfiguration-export-import.md`).

use axum::Json;
use axum::extract::{Path, Query, State};
use axum::http::{HeaderMap, StatusCode};
use serde::Deserialize;
use serde_json::{Map, Value, json};
use utoipa::{IntoParams, ToSchema};

use crate::app::AppState;
use crate::audit::{self, AuditEntry};
use crate::auth::{ROLE_EDITOR, api_key_or_role};
use crate::db::Db;
use crate::error::ApiError;

/// Table holding the export profiles.
pub const TABLE: &str = "export_profile";
/// Formats `POST /exports` accepts (kept in sync with `api::exports`).
pub const FORMATS: &[&str] = &["csv", "xlsx", "json", "bmecat"];
/// Keys a profile may carry (`ExportRequest` without `source`).
pub const PARAMETER_KEYS: &[&str] = &["fields", "locales", "options", "bmecat"];
/// `name` limit (it is the record key and the label in the wizard).
pub const MAX_NAME: usize = 80;

/// What a parameter key holds, for the schema document (and the AI prompt).
///
/// The **inner** keys of `options`/`bmecat` are read by the export worker
/// (`meadow/worker/exports.py`), so they are named in the description instead of
/// being declared here — the core rejects unknown *top-level* keys only.
fn parameter_shape(key: &str) -> (&'static str, &'static str) {
    match key {
        "fields" => (
            "string_list",
            "Attribute names to export; empty means the object's own attributes",
        ),
        "locales" => (
            "string_list",
            "Language codes to resolve values for; empty means the working language",
        ),
        "options" => (
            "object",
            "Format switches: `header` (csv/xlsx, default true), `separator` (default \",\"), \
             `resolve_inheritance` (fill values maintained on an ancestor), \
             `include_ancestors` (json/MEF: keep the composition parents)",
        ),
        "bmecat" => (
            "object",
            "BMEcat dialect: `version` (e.g. \"2005\"), `schema_variant` and the \
             catalog header fields for the BMEcat export",
        ),
        _ => ("json", ""),
    }
}

/// Payload for `POST /export-profiles/validate`: a profile to check without saving.
#[derive(Debug, Deserialize, ToSchema)]
pub struct ValidateExportProfile {
    /// Export format (`csv`, `xlsx`, `json`, `bmecat`).
    pub format: String,
    /// `{fields?, locales?, options?, bmecat?}` — the request without `source`.
    #[serde(default)]
    pub parameters: Option<Value>,
}

/// The parameter contract of an export profile — read-only metadata.
///
/// Formats and parameter shapes are the server's contract (`FORMATS`,
/// `PARAMETER_KEYS`), so a caller (the profile editor, the AI assistant) can build
/// a valid profile without guessing. Read-only, so `editor` may read it.
#[utoipa::path(
    get,
    path = "/export-profiles/schema",
    responses(
        (status = 200, description = "Formats and parameter contract", body = Value),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Forbidden", body = Value)
    )
)]
pub async fn export_profile_schema(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    api_key_or_role(&headers, &state.config, ROLE_EDITOR)?;
    let parameters: Vec<Value> = PARAMETER_KEYS
        .iter()
        .map(|key| {
            let (shape, description) = parameter_shape(key);
            json!({ "key": key, "shape": shape, "description": description })
        })
        .collect();
    Ok(Json(
        json!({ "formats": FORMATS, "parameters": parameters }),
    ))
}

/// Structural check of an export profile — **without saving** (editor or API key).
///
/// Runs the write path's validation (`validate_format` + `normalize_parameters`),
/// so a caller gets exactly the verdict a save would produce.
#[utoipa::path(
    post,
    path = "/export-profiles/validate",
    request_body = ValidateExportProfile,
    responses(
        (status = 200, description = "Validation result", body = Value),
        (status = 400, description = "Invalid payload", body = Value),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Forbidden", body = Value)
    )
)]
pub async fn validate_export_profile(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<ValidateExportProfile>,
) -> Result<Json<Value>, ApiError> {
    api_key_or_role(&headers, &state.config, ROLE_EDITOR)?;
    let mut errors: Vec<String> = Vec::new();
    if let Err(error) = validate_format(&body.format) {
        errors.push(error.message().to_owned());
    }
    if let Err(error) = normalize_parameters(body.parameters.unwrap_or(Value::Null)) {
        errors.push(error.message().to_owned());
    }
    Ok(Json(json!({ "ok": errors.is_empty(), "errors": errors })))
}

fn default_limit() -> u32 {
    1000
}

/// Query parameters for `GET /export-profiles`.
#[derive(Debug, Deserialize, ToSchema, IntoParams)]
#[into_params(parameter_in = Query)]
pub struct ListExportProfiles {
    /// Only return profiles of this format (`csv`/`xlsx`/`json`/`bmecat`).
    #[serde(default)]
    pub format: Option<String>,
    #[serde(default = "default_limit")]
    pub limit: u32,
    #[serde(default)]
    pub start: u32,
}

/// Payload for creating an export profile.
#[derive(Debug, Deserialize, ToSchema)]
pub struct CreateExportProfile {
    /// Display name, used as the record key (immutable after creation).
    pub name: String,
    /// Export format (`csv`, `xlsx`, `json`, `bmecat`).
    pub format: String,
    #[serde(default)]
    pub notes: Option<String>,
    /// `{fields?, locales?, options?, bmecat?}` — the request without `source`.
    #[serde(default)]
    pub parameters: Option<Value>,
}

/// Payload for updating an export profile (the `name` is immutable).
#[derive(Debug, Deserialize, ToSchema)]
pub struct UpdateExportProfile {
    #[serde(default)]
    pub format: Option<String>,
    #[serde(default)]
    pub notes: Option<String>,
    #[serde(default)]
    pub parameters: Option<Value>,
}

/// The record key of a profile name (lower case, `-` for everything else).
///
/// Deterministic, so an MCF round trip into an empty catalogue produces the same
/// keys, and a profile can be addressed by name (`meadow-bmecat`) or record key.
pub fn record_key(name: &str) -> String {
    let mut key = String::new();
    let mut previous_dash = false;
    for character in name.trim().chars() {
        if character.is_ascii_alphanumeric() {
            key.push(character.to_ascii_lowercase());
            previous_dash = false;
        } else if !previous_dash && !key.is_empty() {
            key.push('-');
            previous_dash = true;
        }
    }
    while key.ends_with('-') {
        key.pop();
    }
    key
}

fn validate_name(name: &str) -> Result<String, ApiError> {
    let trimmed = name.trim();
    if trimmed.is_empty() {
        return Err(ApiError::bad_request("`name` must not be empty"));
    }
    if trimmed.chars().count() > MAX_NAME {
        return Err(ApiError::bad_request(format!(
            "`name` must not be longer than {MAX_NAME} characters"
        )));
    }
    if record_key(trimmed).is_empty() {
        return Err(ApiError::bad_request(
            "`name` must contain at least one letter or digit",
        ));
    }
    Ok(trimmed.to_owned())
}

/// Normalises and validates a format name.
fn validate_format(format: &str) -> Result<String, ApiError> {
    let normalized = format.trim().to_lowercase();
    if !FORMATS.contains(&normalized.as_str()) {
        return Err(ApiError::bad_request(format!(
            "unsupported format `{format}` (expected {})",
            FORMATS.join(" | ")
        )));
    }
    Ok(normalized)
}

/// Keeps only the known parameter keys and checks their shape.
///
/// Rejecting unknown keys instead of dropping them is deliberate: a typo in a
/// hand-edited MCF document or an API call would otherwise be accepted silently
/// and the profile would be missing exactly the setting the caller meant.
pub fn normalize_parameters(parameters: Value) -> Result<Value, ApiError> {
    let map = match parameters {
        Value::Null => return Ok(json!({})),
        Value::Object(map) => map,
        other => {
            return Err(ApiError::bad_request(format!(
                "`parameters` must be an object, got {}",
                match other {
                    Value::Array(_) => "an array",
                    Value::String(_) => "a string",
                    Value::Number(_) => "a number",
                    Value::Bool(_) => "a boolean",
                    _ => "null",
                }
            )));
        }
    };
    let mut clean = Map::new();
    for (key, value) in map {
        if !PARAMETER_KEYS.contains(&key.as_str()) {
            return Err(ApiError::bad_request(format!(
                "unknown parameter `{key}` (allowed: {})",
                PARAMETER_KEYS.join(", ")
            )));
        }
        match key.as_str() {
            "fields" | "locales" => {
                let entries = value.as_array().ok_or_else(|| {
                    ApiError::bad_request(format!("`{key}` must be an array of strings"))
                })?;
                let mut names: Vec<Value> = Vec::new();
                for entry in entries {
                    let text = entry.as_str().ok_or_else(|| {
                        ApiError::bad_request(format!("`{key}` must be an array of strings"))
                    })?;
                    let text = text.trim();
                    if !text.is_empty() && !names.iter().any(|name| name == text) {
                        names.push(Value::String(text.to_owned()));
                    }
                }
                clean.insert(key, Value::Array(names));
            }
            _ => {
                if !value.is_object() {
                    return Err(ApiError::bad_request(format!(
                        "`{key}` must be an object (got `{}`)",
                        value
                    )));
                }
                clean.insert(key, value);
            }
        }
    }
    Ok(Value::Object(clean))
}

/// The record key of a profile row (used by the MCF section too).
pub fn key_of(row: &Value) -> String {
    row.get("name")
        .and_then(Value::as_str)
        .map(record_key)
        .unwrap_or_default()
}

/// The record key of a reference: accepts a bare key (`etim-katalog-de`), a
/// `table:key` reference and the `` table:`key` `` form SurrealDB returns for
/// ids that need quoting (a dash is one such case) — a caller must be able to
/// pass back the `id` it just received.
pub fn bare_key(id: &str) -> String {
    id.strip_prefix("export_profile:")
        .unwrap_or(id)
        .trim()
        .trim_matches('`')
        .to_owned()
}

/// Loads one profile by record key or by name.
pub(crate) async fn find_profile(db: &Db, id: &str) -> Result<Option<Value>, ApiError> {
    let mut response = db
        .inner()
        .query(
            "SELECT * FROM export_profile \
             WHERE id = type::record('export_profile', $key) OR name = $name LIMIT 1;",
        )
        .bind(("key", bare_key(id)))
        .bind(("name", id.trim().to_owned()))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    Ok(rows.into_iter().next())
}

/// Lists the export profiles (editor+ or machine-to-machine API key).
#[utoipa::path(
    get,
    path = "/export-profiles",
    params(ListExportProfiles),
    responses((status = 200, description = "Export profiles", body = Vec<Value>))
)]
pub async fn list_export_profiles(
    headers: HeaderMap,
    State(state): State<AppState>,
    Query(params): Query<ListExportProfiles>,
) -> Result<Json<Vec<Value>>, ApiError> {
    api_key_or_role(&headers, &state.config, ROLE_EDITOR)?;
    let limit = params.limit.clamp(1, 1000) as i64;
    let start = params.start as i64;
    let mut sql = String::from("SELECT * FROM export_profile");
    if params.format.is_some() {
        sql.push_str(" WHERE format = $format");
    }
    sql.push_str(" ORDER BY name LIMIT $limit START $start;");
    let mut query = state.db.inner().query(sql);
    if let Some(format) = &params.format {
        query = query.bind(("format", format.trim().to_lowercase()));
    }
    let mut response = query
        .bind(("limit", limit))
        .bind(("start", start))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    Ok(Json(rows))
}

/// Gets a single export profile.
#[utoipa::path(
    get,
    path = "/export-profiles/{id}",
    responses(
        (status = 200, description = "Export profile", body = Value),
        (status = 404, description = "Not found", body = Value)
    )
)]
pub async fn get_export_profile(
    headers: HeaderMap,
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<Json<Value>, ApiError> {
    api_key_or_role(&headers, &state.config, ROLE_EDITOR)?;
    let profile = find_profile(&state.db, &id)
        .await?
        .ok_or_else(|| ApiError::not_found(format!("export profile `{id}` not found")))?;
    Ok(Json(profile))
}

/// Creates an export profile.
#[utoipa::path(
    post,
    path = "/export-profiles",
    request_body = CreateExportProfile,
    responses(
        (status = 201, description = "Created", body = Value),
        (status = 400, description = "Invalid payload", body = Value),
        (status = 403, description = "Forbidden", body = Value),
        (status = 409, description = "Name already exists", body = Value)
    )
)]
pub async fn create_export_profile(
    headers: HeaderMap,
    State(state): State<AppState>,
    Json(body): Json<CreateExportProfile>,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    let user_id = api_key_or_role(&headers, &state.config, "admin")?;
    let name = validate_name(&body.name)?;
    let format = validate_format(&body.format)?;
    let key = record_key(&name);
    if find_profile(&state.db, &key).await?.is_some() {
        return Err(ApiError::conflict(format!(
            "export profile `{name}` already exists"
        )));
    }
    let parameters = normalize_parameters(body.parameters.unwrap_or_else(|| json!({})))?;

    let mut content = Map::new();
    content.insert("name".to_owned(), Value::String(name.clone()));
    content.insert("format".to_owned(), Value::String(format.clone()));
    content.insert("parameters".to_owned(), parameters);
    if let Some(notes) = body
        .notes
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
    {
        content.insert("notes".to_owned(), Value::String(notes.to_owned()));
    }
    content.insert("created_by".to_owned(), Value::String(user_id.clone()));

    let mut response = state
        .db
        .inner()
        .query(
            "LET $p = type::record('export_profile', $key); \
             CREATE $p CONTENT $content; SELECT * FROM $p;",
        )
        .bind(("key", key.clone()))
        .bind(("content", Value::Object(content)))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(2)?;
    let profile = rows
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::internal("created export profile was not returned"))?;
    let record_id = profile
        .get("id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &user_id,
            action: "export_profile_created",
            object_id: Some(&record_id),
            from: None,
            to: None,
            metadata: Some(json!({ "name": name, "format": format })),
        },
    )
    .await;
    Ok((StatusCode::CREATED, Json(profile)))
}

/// Updates an export profile (the `name` is immutable).
#[utoipa::path(
    patch,
    path = "/export-profiles/{id}",
    request_body = UpdateExportProfile,
    responses(
        (status = 200, description = "Updated", body = Value),
        (status = 400, description = "Invalid payload", body = Value),
        (status = 404, description = "Not found", body = Value)
    )
)]
pub async fn update_export_profile(
    headers: HeaderMap,
    State(state): State<AppState>,
    Path(id): Path<String>,
    Json(body): Json<UpdateExportProfile>,
) -> Result<Json<Value>, ApiError> {
    let user_id = api_key_or_role(&headers, &state.config, "admin")?;
    let profile = find_profile(&state.db, &id)
        .await?
        .ok_or_else(|| ApiError::not_found(format!("export profile `{id}` not found")))?;

    let mut content = Map::new();
    let mut clear_notes = false;
    if let Some(format) = &body.format {
        content.insert("format".to_owned(), Value::String(validate_format(format)?));
    }
    if let Some(parameters) = body.parameters {
        content.insert("parameters".to_owned(), normalize_parameters(parameters)?);
    }
    if let Some(notes) = body.notes.as_deref() {
        // An empty string clears the note. SurrealDB `option<string>` rejects
        // JSON `null`, so clearing is an explicit `UNSET` (the same reason the
        // object PATCH path uses one) instead of a `MERGE` value.
        match notes.trim() {
            "" => clear_notes = true,
            notes => {
                content.insert("notes".to_owned(), Value::String(notes.to_owned()));
            }
        }
    }
    if content.is_empty() && !clear_notes {
        return Err(ApiError::bad_request("no updatable field provided"));
    }

    let key = key_of(&profile);
    let mut sql = String::from("LET $r = type::record('export_profile', $key); ");
    if !content.is_empty() {
        sql.push_str("UPDATE $r MERGE $content; ");
    }
    if clear_notes {
        sql.push_str("UPDATE $r UNSET notes; ");
    }
    // The `SELECT` is a separate call below: the statement index of a trailing
    // `SELECT` would shift with the optional statements above.
    sql.push_str("UPDATE $r SET updated_at = time::now(), updated_by = $user;");
    state
        .db
        .inner()
        .query(sql)
        .bind(("key", key.clone()))
        .bind(("content", Value::Object(content)))
        .bind(("user", user_id.clone()))
        .await?
        .check()?;
    let updated = find_profile(&state.db, &key)
        .await?
        .ok_or_else(|| ApiError::not_found(format!("export profile `{id}` not found")))?;
    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &user_id,
            action: "export_profile_updated",
            object_id: updated.get("id").and_then(Value::as_str),
            from: None,
            to: None,
            metadata: Some(json!({ "name": updated.get("name") })),
        },
    )
    .await;
    Ok(Json(updated))
}

/// Deletes an export profile (no export data is touched).
#[utoipa::path(
    delete,
    path = "/export-profiles/{id}",
    responses(
        (status = 204, description = "Deleted"),
        (status = 404, description = "Not found", body = Value)
    )
)]
pub async fn delete_export_profile(
    headers: HeaderMap,
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<StatusCode, ApiError> {
    let user_id = api_key_or_role(&headers, &state.config, "admin")?;
    let profile = find_profile(&state.db, &id)
        .await?
        .ok_or_else(|| ApiError::not_found(format!("export profile `{id}` not found")))?;
    let key = key_of(&profile);
    state
        .db
        .inner()
        .query("DELETE type::record('export_profile', $key);")
        .bind(("key", key.clone()))
        .await?
        .check()?;
    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &user_id,
            action: "export_profile_deleted",
            object_id: profile.get("id").and_then(Value::as_str),
            from: None,
            to: None,
            metadata: Some(json!({ "name": profile.get("name") })),
        },
    )
    .await;
    Ok(StatusCode::NO_CONTENT)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn record_key_is_a_safe_identifier() {
        assert_eq!(record_key("Meadow BMEcat 1.2"), "meadow-bmecat-1-2");
        assert_eq!(record_key("  ETIM / Web  "), "etim-web");
        assert_eq!(record_key("---"), "");
        assert_eq!(record_key("Ölpreise"), "lpreise");
    }

    #[test]
    fn name_validation_rejects_empty_and_unnamed_values() {
        assert!(validate_name("  ").is_err());
        assert!(validate_name("---").is_err());
        assert!(validate_name(&"x".repeat(MAX_NAME + 1)).is_err());
        assert_eq!(validate_name(" BMEcat ").unwrap(), "BMEcat");
    }

    #[test]
    fn format_validation_is_case_insensitive_and_closed() {
        assert_eq!(validate_format(" BMECAT ").unwrap(), "bmecat");
        assert!(validate_format("xml").is_err());
    }

    #[test]
    fn parameters_keep_only_known_keys_and_clean_lists() {
        let clean = normalize_parameters(json!({
            "fields": ["name", "name", "", "sku"],
            "locales": ["de"],
            "bmecat": { "version": "1.2" },
        }))
        .unwrap();
        assert_eq!(clean["fields"], json!(["name", "sku"]));
        assert_eq!(clean["locales"], json!(["de"]));
        assert_eq!(clean["bmecat"]["version"], "1.2");
        assert!(clean.get("source").is_none());
    }

    #[test]
    fn unknown_parameters_and_wrong_shapes_are_rejected() {
        // A typo would otherwise be accepted silently and lose the setting.
        assert!(normalize_parameters(json!({ "sorce": {} })).is_err());
        assert!(normalize_parameters(json!({ "fields": "name" })).is_err());
        assert!(normalize_parameters(json!({ "fields": [1] })).is_err());
        assert!(normalize_parameters(json!({ "bmecat": "bmecat" })).is_err());
        assert!(normalize_parameters(json!("nope")).is_err());
        assert_eq!(normalize_parameters(Value::Null).unwrap(), json!({}));
    }

    #[test]
    fn references_are_accepted_in_every_form_the_api_returns() {
        // SurrealDB quotes a key that needs it, so `id` comes back as
        // `export_profile:`meadow-bmecat``; passing it back must work.
        for reference in [
            "meadow-bmecat",
            "export_profile:meadow-bmecat",
            "export_profile:`meadow-bmecat`",
        ] {
            assert_eq!(bare_key(reference), "meadow-bmecat");
        }
        // A name is looked up as-is, not as a key.
        assert_eq!(bare_key("Meadow BMEcat"), "Meadow BMEcat");
    }
}
