//! Classification standards installed in this catalogue (Phase 9).
//!
//! A **classification standard** (ETIM, ECLASS, UNSPSC) is *configuration
//! anchored to data*: the class nodes, their features and the feature attribute
//! definitions arrive through a **standard bundle** (MCF + MEF, see
//! `plan/detail/bmecat-export.md` §11.9). What a bundle cannot express by itself
//! is *which* standard in *which* version the catalogue was loaded with and where
//! it came from — that is this table.
//!
//! Why it exists: the BMEcat export names the reference feature system of every
//! class block (`ETIM-10.0`). Before, that version came from a free-text profile
//! field, so a catalogue could silently claim a version it never installed. The
//! export now reads the **installed** standard of a class (`node_type`) here and
//! reports which version it used.
//!
//! The row is deliberately small and portable: `code` (stable key, e.g. `etim`),
//! `version` (e.g. `10.0`), `name`, `source` (the artefact the package was built
//! from), `node_type` (the object type of the class nodes) and `notes`. The MCF
//! section `classification_standards` exports and imports it, so a bundle
//! documents itself and a re-import updates the record idempotently.
//!
//! Two versions of the same standard can be registered at once (an update path:
//! install ETIM 11.0 next to 10.0 — the class codes are stable, the feature set
//! is replaced by the package). [`list_standards`] marks the newest version per
//! `code` as `current`, which is the one an export names. **Open:** per-node
//! versioning (every class node carrying the version it was installed with) —
//! today the registry is per `node_type`, which is what the export needs.

use axum::Json;
use axum::extract::{Path, Query, State};
use axum::http::{HeaderMap, StatusCode};
use serde::Deserialize;
use serde_json::{Map, Value, json};
use utoipa::{IntoParams, ToSchema};

use crate::app::AppState;
use crate::audit::{self, AuditEntry};
use crate::auth::{AuthUser, ROLE_VIEWER, api_key_or_role};
use crate::db::Db;
use crate::error::ApiError;

/// Table holding the registry of installed classification standards.
pub const TABLE: &str = "classification_standard";

fn default_limit() -> u32 {
    200
}

/// Query parameters for `GET /classification-standards`.
#[derive(Debug, Deserialize, ToSchema, IntoParams)]
#[into_params(parameter_in = Query)]
pub struct ListStandards {
    /// Only the standards of this standard code (e.g. `etim`).
    #[serde(default)]
    pub code: Option<String>,
    /// Only standards whose class nodes have this object type (e.g. `etim_class`).
    #[serde(default)]
    pub node_type: Option<String>,
    /// Also count the installed classes and feature rows (two extra queries).
    #[serde(default)]
    pub counts: bool,
    #[serde(default = "default_limit")]
    pub limit: u32,
    #[serde(default)]
    pub start: u32,
}

/// Payload for registering a standard.
#[derive(Debug, Deserialize, ToSchema)]
pub struct CreateStandard {
    /// Stable standard code (`etim`, `eclass`, `unspsc`).
    pub code: String,
    /// Standard version as the standard writes it (`10.0`, `14.0`).
    pub version: String,
    /// Display name (default: the upper-case code, e.g. `ETIM`).
    #[serde(default)]
    pub name: Option<String>,
    /// Artefact the package was built from (e.g. the IXF export name).
    #[serde(default)]
    pub source: Option<String>,
    /// Object type of the class nodes (e.g. `etim_class`); must be a taxonomy type.
    #[serde(default)]
    pub node_type: Option<String>,
    #[serde(default)]
    pub notes: Option<String>,
}

/// Payload for updating a standard. `code` and `version` are immutable.
#[derive(Debug, Deserialize, ToSchema)]
pub struct UpdateStandard {
    #[serde(default)]
    pub name: Option<String>,
    #[serde(default)]
    pub source: Option<String>,
    #[serde(default)]
    pub node_type: Option<String>,
    #[serde(default)]
    pub notes: Option<String>,
}

/// Sort key of a version string: the numeric groups first (`10.0` > `9.1`,
/// `10.0.1` > `10.0`), the raw text as the tie-breaker. A version without digits
/// (`beta`) sorts before every numbered one — documented, not guessed.
pub fn version_key(version: &str) -> (Vec<u64>, String) {
    let numbers: Vec<u64> = version
        .split(|c: char| !c.is_ascii_digit())
        .filter(|part| !part.is_empty())
        .filter_map(|part| part.parse().ok())
        .collect();
    (numbers, version.to_owned())
}

/// Deterministic record key of a standard (`etim` + `10.0` → `etim-10-0`).
///
/// The fields `code`/`version` keep the exact values; the key only has to be a
/// safe, stable identifier (a version may contain characters SurrealDB would read
/// as part of the record id syntax).
pub fn record_key(code: &str, version: &str) -> String {
    let raw = format!("{code}-{version}");
    raw.chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || c == '-' || c == '_' {
                c
            } else {
                '-'
            }
        })
        .collect()
}

fn validate_code(code: &str) -> Result<(), ApiError> {
    if code.is_empty() {
        return Err(ApiError::bad_request("`code` must not be empty"));
    }
    if !code
        .chars()
        .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-')
    {
        return Err(ApiError::bad_request(
            "`code` may only contain letters, digits, `_` and `-`",
        ));
    }
    Ok(())
}

fn validate_version(version: &str) -> Result<(), ApiError> {
    if version.is_empty() {
        return Err(ApiError::bad_request("`version` must not be empty"));
    }
    if !version
        .chars()
        .all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-'))
    {
        return Err(ApiError::bad_request(
            "`version` may only contain letters, digits, `.`, `_` and `-`",
        ));
    }
    Ok(())
}

/// The reference feature system name of a standard (`ETIM` + `10.0` →
/// `ETIM-10.0`) — the value a BMEcat `REFERENCE_FEATURE_SYSTEM_NAME` carries.
pub fn feature_system_name(code: &str, version: &str) -> String {
    format!("{}-{}", code.to_uppercase(), version)
}

/// The record key of a registry row.
///
/// Derived from the row's own fields: SurrealDB serialises a record id that needs
/// quoting back as ``classification_standard:`etim-11-0` ``, so splitting the
/// `id` would carry the backticks into the key and address a different record.
fn key_of(row: &Value) -> String {
    let code = row.get("code").and_then(Value::as_str).unwrap_or_default();
    let version = row
        .get("version")
        .and_then(Value::as_str)
        .unwrap_or_default();
    record_key(code, version)
}

/// Loads one standard by its stable key (`code` + `version`).
pub(crate) async fn find_standard(
    db: &Db,
    code: &str,
    version: &str,
) -> Result<Option<Value>, ApiError> {
    let mut response = db
        .inner()
        .query("SELECT * FROM classification_standard WHERE code = $code AND version = $version LIMIT 1;")
        .bind(("code", code.to_owned()))
        .bind(("version", version.to_owned()))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    Ok(rows.into_iter().next())
}

/// Loads a standard by `code@version`, by record key or by record id.
async fn resolve_standard(db: &Db, reference: &str) -> Result<Option<Value>, ApiError> {
    let trimmed = reference.trim();
    let bare = trimmed
        .strip_prefix("classification_standard:")
        .unwrap_or(trimmed);
    if let Some((code, version)) = bare.split_once('@')
        && let Some(found) = find_standard(db, code, version).await?
    {
        return Ok(Some(found));
    }
    let mut response = db
        .inner()
        .query(
            "SELECT * FROM classification_standard \
             WHERE id = type::record('classification_standard', $key) LIMIT 1;",
        )
        .bind(("key", bare.to_owned()))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    Ok(rows.into_iter().next())
}

/// Number of class nodes of a type and feature rows hanging off them.
async fn counts_for(db: &Db, node_type: &str) -> Result<(i64, i64), ApiError> {
    // Two queries instead of a subquery: the class keys are the natural join key
    // and `category_id` stores them as native records.
    let mut response = db
        .inner()
        .query("SELECT VALUE id FROM object WHERE type = $type AND deleted_at IS NONE;")
        .bind(("type", node_type.to_owned()))
        .await?
        .check()?;
    let keys: Vec<Value> = response.take(0)?;
    let classes = keys.len() as i64;
    if classes == 0 {
        return Ok((0, 0));
    }
    let mut response = db
        .inner()
        .query(
            "SELECT count() AS total FROM category_attribute \
             WHERE category_id IN array::map($keys, |$k| type::record($k)) GROUP ALL;",
        )
        .bind(("keys", Value::Array(keys)))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    let features = rows
        .first()
        .and_then(|row| row.get("total"))
        .and_then(Value::as_i64)
        .unwrap_or(0);
    Ok((classes, features))
}

/// Adds the derived fields of a registry row: `current` (newest version of its
/// code) and, on request, the live `class_count`/`feature_count`.
async fn decorate(db: &Db, row: &Value, current: &str, counts: bool) -> Result<Value, ApiError> {
    let mut out = row.as_object().cloned().unwrap_or_default();
    let code = out
        .get("code")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    let version = out
        .get("version")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    out.insert(
        "feature_system_name".to_owned(),
        Value::String(feature_system_name(&code, &version)),
    );
    out.insert(
        "current".to_owned(),
        Value::Bool(current == version && !code.is_empty()),
    );
    if counts
        && let Some(node_type) = out
            .get("node_type")
            .and_then(Value::as_str)
            .map(str::to_owned)
    {
        let (classes, features) = counts_for(db, &node_type).await?;
        out.insert("class_count".to_owned(), json!(classes));
        out.insert("feature_count".to_owned(), json!(features));
    }
    Ok(Value::Object(out))
}

/// Lists the registered standards — newest version per code first.
///
/// The table holds a handful of rows (one per standard and version), so the list
/// is assembled in Rust: that keeps the `current` flag and the ordering in one
/// place instead of in SQL. `viewer+` or the machine `X-Api-Key` (the export
/// worker needs the registry to name the reference feature system).
#[utoipa::path(
    get,
    path = "/classification-standards",
    params(ListStandards),
    responses((status = 200, description = "Registered standards", body = Vec<Value>))
)]
pub async fn list_classification_standards(
    headers: HeaderMap,
    State(state): State<AppState>,
    Query(params): Query<ListStandards>,
) -> Result<Json<Vec<Value>>, ApiError> {
    api_key_or_role(&headers, &state.config, ROLE_VIEWER)?;
    let all =
        crate::api::config::query_rows(&state.db, "SELECT * FROM classification_standard;").await?;

    // Newest version per code: the one an export names.
    let mut newest: std::collections::HashMap<String, (Vec<u64>, String)> = Default::default();
    for row in &all {
        let code = row.get("code").and_then(Value::as_str).unwrap_or_default();
        let version = row
            .get("version")
            .and_then(Value::as_str)
            .unwrap_or_default();
        let key = version_key(version);
        let entry = newest.entry(code.to_owned()).or_insert_with(|| key.clone());
        if key > *entry {
            *entry = key;
        }
    }

    let code_filter = params
        .code
        .as_deref()
        .map(str::trim)
        .filter(|v| !v.is_empty());
    let type_filter = params
        .node_type
        .as_deref()
        .map(str::trim)
        .filter(|v| !v.is_empty());

    let mut rows: Vec<&Value> = all
        .iter()
        .filter(|row| {
            let code = row.get("code").and_then(Value::as_str).unwrap_or_default();
            let node_type = row
                .get("node_type")
                .and_then(Value::as_str)
                .unwrap_or_default();
            code_filter.is_none_or(|wanted| code == wanted)
                && type_filter.is_none_or(|wanted| node_type == wanted)
        })
        .collect();
    rows.sort_by(|a, b| {
        let code_a = a.get("code").and_then(Value::as_str).unwrap_or_default();
        let code_b = b.get("code").and_then(Value::as_str).unwrap_or_default();
        let version_a = a.get("version").and_then(Value::as_str).unwrap_or_default();
        let version_b = b.get("version").and_then(Value::as_str).unwrap_or_default();
        code_a
            .cmp(code_b)
            .then_with(|| version_key(version_b).cmp(&version_key(version_a)))
    });

    let start = params.start as usize;
    let limit = params.limit.clamp(1, 1000) as usize;
    let mut out = Vec::new();
    for row in rows.into_iter().skip(start).take(limit) {
        let code = row.get("code").and_then(Value::as_str).unwrap_or_default();
        let current = newest
            .get(code)
            .map(|(_, text)| text.clone())
            .unwrap_or_default();
        out.push(decorate(&state.db, row, &current, params.counts).await?);
    }
    Ok(Json(out))
}

/// Gets one standard by `code@version`, record key or record id.
#[utoipa::path(
    get,
    path = "/classification-standards/{id}",
    responses(
        (status = 200, description = "Standard", body = Value),
        (status = 404, description = "Not found", body = Value)
    )
)]
pub async fn get_classification_standard(
    headers: HeaderMap,
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<Json<Value>, ApiError> {
    api_key_or_role(&headers, &state.config, ROLE_VIEWER)?;
    let standard = resolve_standard(&state.db, &id)
        .await?
        .ok_or_else(|| ApiError::not_found(format!("classification standard `{id}` not found")))?;
    let code = standard
        .get("code")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    let rows =
        crate::api::config::query_rows(&state.db, "SELECT * FROM classification_standard;").await?;
    let current = rows
        .iter()
        .filter(|row| row.get("code").and_then(Value::as_str) == Some(code.as_str()))
        .map(|row| {
            row.get("version")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_owned()
        })
        .max_by_key(|version| version_key(version))
        .unwrap_or_default();
    Ok(Json(decorate(&state.db, &standard, &current, true).await?))
}

/// Registers a standard (admin).
#[utoipa::path(
    post,
    path = "/classification-standards",
    request_body = CreateStandard,
    responses(
        (status = 201, description = "Registered", body = Value),
        (status = 400, description = "Invalid payload", body = Value),
        (status = 403, description = "Forbidden", body = Value),
        (status = 409, description = "Standard and version already registered", body = Value)
    )
)]
pub async fn create_classification_standard(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Json(body): Json<CreateStandard>,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    if !claims.is_admin() {
        return Err(ApiError::forbidden("requires admin role"));
    }
    let code = body.code.trim().to_lowercase();
    let version = body.version.trim().to_owned();
    validate_code(&code)?;
    validate_version(&version)?;
    if find_standard(&state.db, &code, &version).await?.is_some() {
        return Err(ApiError::conflict(format!(
            "standard `{code}` version `{version}` is already registered"
        )));
    }
    let node_type = match body.node_type.as_deref().map(str::trim) {
        Some(value) if !value.is_empty() => {
            check_node_type(&state.db, value).await?;
            Some(value.to_owned())
        }
        _ => None,
    };

    let mut content = Map::new();
    content.insert("code".to_owned(), Value::String(code.clone()));
    content.insert("version".to_owned(), Value::String(version.clone()));
    content.insert(
        "name".to_owned(),
        Value::String(
            body.name
                .map(|name| name.trim().to_owned())
                .filter(|name| !name.is_empty())
                .unwrap_or_else(|| code.to_uppercase()),
        ),
    );
    if let Some(source) = body.source.filter(|value| !value.trim().is_empty()) {
        content.insert("source".to_owned(), Value::String(source.trim().to_owned()));
    }
    if let Some(node_type) = node_type {
        content.insert("node_type".to_owned(), Value::String(node_type));
    }
    if let Some(notes) = body.notes.filter(|value| !value.trim().is_empty()) {
        content.insert("notes".to_owned(), Value::String(notes.trim().to_owned()));
    }

    let key = record_key(&code, &version);
    let sql = "LET $r = type::record('classification_standard', $key); \
               CREATE $r CONTENT $content; SELECT * FROM $r;";
    let mut response = state
        .db
        .inner()
        .query(sql)
        .bind(("key", key.clone()))
        .bind(("content", Value::Object(content)))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(2)?;
    let created = rows
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::internal("created standard was not returned"))?;
    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &claims.sub,
            action: "classification_standard_created",
            object_id: Some(&format!("classification_standard:{key}")),
            from: None,
            to: None,
            metadata: Some(json!({ "code": code, "version": version })),
        },
    )
    .await;
    Ok((StatusCode::CREATED, Json(created)))
}

/// Updates a standard; `code` and `version` are immutable (admin).
#[utoipa::path(
    patch,
    path = "/classification-standards/{id}",
    request_body = UpdateStandard,
    responses(
        (status = 200, description = "Updated", body = Value),
        (status = 400, description = "Invalid payload", body = Value),
        (status = 404, description = "Not found", body = Value)
    )
)]
pub async fn update_classification_standard(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
    Json(body): Json<UpdateStandard>,
) -> Result<Json<Value>, ApiError> {
    if !claims.is_admin() {
        return Err(ApiError::forbidden("requires admin role"));
    }
    let existing = resolve_standard(&state.db, &id)
        .await?
        .ok_or_else(|| ApiError::not_found(format!("classification standard `{id}` not found")))?;
    let mut content = Map::new();
    if let Some(name) = body.name {
        content.insert("name".to_owned(), Value::String(name.trim().to_owned()));
    }
    if let Some(source) = body.source {
        content.insert("source".to_owned(), Value::String(source.trim().to_owned()));
    }
    if let Some(notes) = body.notes {
        content.insert("notes".to_owned(), Value::String(notes.trim().to_owned()));
    }
    let clear_node_type = match body.node_type.as_deref().map(str::trim) {
        Some("") => true,
        Some(value) => {
            check_node_type(&state.db, value).await?;
            content.insert("node_type".to_owned(), Value::String(value.to_owned()));
            false
        }
        None => false,
    };
    if content.is_empty() && !clear_node_type {
        return Err(ApiError::bad_request("no updatable field provided"));
    }
    let key = key_of(&existing);
    // SurrealDB `option<string>` rejects JSON `null`, so clearing a field is an
    // explicit `UNSET` (the same reason the object PATCH path uses one).
    let mut sql = String::from("LET $r = type::record('classification_standard', $key); ");
    if !content.is_empty() {
        sql.push_str("UPDATE $r MERGE $content; ");
    }
    if clear_node_type {
        sql.push_str("UPDATE $r UNSET node_type; ");
    }
    sql.push_str("UPDATE $r SET updated_at = time::now(), updated_by = $user; SELECT * FROM $r;");
    let mut response = state
        .db
        .inner()
        .query(sql)
        .bind(("key", key.clone()))
        .bind(("content", Value::Object(content)))
        .bind(("user", claims.sub.clone()))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(3)?;
    let updated = rows
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::internal("updated standard was not returned"))?;
    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &claims.sub,
            action: "classification_standard_updated",
            object_id: Some(&format!("classification_standard:{key}")),
            from: None,
            to: None,
            metadata: None,
        },
    )
    .await;
    Ok(Json(updated))
}

/// Removes a standard from the registry (admin).
///
/// The class nodes, features and attribute definitions are **data** and stay
/// untouched: forgetting a standard only stops the export from naming it (the
/// license hint of the row). To remove the standard's data, delete its objects
/// and reference attributes — this endpoint deliberately does not cascade.
#[utoipa::path(
    delete,
    path = "/classification-standards/{id}",
    responses(
        (status = 204, description = "Removed from the registry"),
        (status = 404, description = "Not found", body = Value)
    )
)]
pub async fn delete_classification_standard(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<StatusCode, ApiError> {
    if !claims.is_admin() {
        return Err(ApiError::forbidden("requires admin role"));
    }
    let existing = resolve_standard(&state.db, &id)
        .await?
        .ok_or_else(|| ApiError::not_found(format!("classification standard `{id}` not found")))?;
    let key = key_of(&existing);
    state
        .db
        .inner()
        .query("DELETE type::record('classification_standard', $key);")
        .bind(("key", key.clone()))
        .await?
        .check()?;
    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &claims.sub,
            action: "classification_standard_deleted",
            object_id: Some(&format!("classification_standard:{key}")),
            from: None,
            to: None,
            metadata: Some(json!({
                "code": existing.get("code").cloned().unwrap_or(Value::Null),
                "version": existing.get("version").cloned().unwrap_or(Value::Null),
            })),
        },
    )
    .await;
    Ok(StatusCode::NO_CONTENT)
}

/// Rejects a `node_type` that is not a registered taxonomy type.
async fn check_node_type(db: &Db, node_type: &str) -> Result<(), ApiError> {
    if crate::api::object_types::is_taxonomy_type(db, node_type).await? {
        return Ok(());
    }
    let allowed = crate::api::object_types::taxonomy_types(db).await?;
    Err(ApiError::bad_request(format!(
        "`node_type` `{node_type}` is not a taxonomy type (allowed: {})",
        allowed.join(", ")
    )))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn version_key_orders_numerically_not_lexically() {
        let mut versions = vec!["9.1", "10.0", "10.0.1", "2.0"];
        versions.sort_by_key(|version| version_key(version));
        assert_eq!(versions, vec!["2.0", "9.1", "10.0", "10.0.1"]);
        assert!(
            version_key("10.0") > version_key("9.1"),
            "10 must sort after 9"
        );
        // A version without digits sorts before every numbered one.
        assert!(version_key("beta") < version_key("1.0"));
    }

    #[test]
    fn record_key_is_a_safe_identifier() {
        assert_eq!(record_key("etim", "10.0"), "etim-10-0");
        assert_eq!(record_key("etim", "10.0.1"), "etim-10-0-1");
        assert_eq!(record_key("eclass", "14.0"), "eclass-14-0");
        assert!(!record_key("etim", "10.0").contains('.'));
    }

    #[test]
    fn feature_system_name_follows_the_bmecat_convention() {
        assert_eq!(feature_system_name("etim", "10.0"), "ETIM-10.0");
        assert_eq!(feature_system_name("ECLASS", "14.0"), "ECLASS-14.0");
    }

    #[test]
    fn code_and_version_validation_rejects_unsafe_values() {
        assert!(validate_code("etim").is_ok());
        assert!(validate_code("e_class-2").is_ok());
        assert!(validate_code("").is_err());
        assert!(validate_code("et im").is_err());
        // A `@` would break the `code@version` reference syntax.
        assert!(validate_code("etim@10").is_err());

        assert!(validate_version("10.0").is_ok());
        assert!(validate_version("2026-09-13").is_ok());
        assert!(validate_version("").is_err());
        assert!(validate_version("10 0").is_err());
        assert!(validate_version("10/0").is_err());
    }
}
