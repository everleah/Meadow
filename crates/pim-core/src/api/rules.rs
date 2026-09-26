//! Business-rule CRUD handlers (admin/superadmin for writes).
//!
//! Rules are stored in `business_rule`; every create/update is compile-checked
//! by the Python worker (subject `rules.compile`) before it is persisted. New
//! rules start in `draft` and are activated explicitly.

use axum::Json;
use axum::extract::{Path, Query, State};
use axum::http::{HeaderMap, StatusCode};
use serde::Deserialize;
use serde_json::{Value, json};
use utoipa::{IntoParams, ToSchema};

use crate::app::AppState;
use crate::audit::{self, AuditEntry};
use crate::auth::AuthUser;
use crate::db::Db;
use crate::error::ApiError;

const TABLE: &str = "business_rule";
const VALID_RULE_TYPES: [&str; 5] = [
    "validation",
    "computed",
    "normalization",
    "workflow_guard",
    "bulk",
];

/// Payload for creating a rule (always created as `draft`).
#[derive(Debug, Deserialize, ToSchema)]
pub struct CreateRule {
    /// Unique rule name (business identifier).
    pub name: String,
    #[serde(default)]
    pub description: Option<String>,
    /// Rule type: validation | computed | normalization | workflow_guard | bulk.
    #[serde(rename = "type")]
    pub rule_type: String,
    /// Object types the rule applies to (empty = all).
    #[serde(default)]
    pub applies_to: Option<Vec<String>>,
    /// Python script source (must compile under the sandbox).
    pub script: String,
}

/// Payload for a partial rule update (`type` and `state` are managed elsewhere).
#[derive(Debug, Deserialize, ToSchema)]
pub struct UpdateRule {
    #[serde(default)]
    pub name: Option<String>,
    /// `null` clears the description, `"text"` sets it.
    #[serde(default)]
    pub description: Option<Option<String>>,
    #[serde(default)]
    pub applies_to: Option<Vec<String>>,
    #[serde(default)]
    pub script: Option<String>,
}

/// Query parameters for listing rules.
#[derive(Debug, Deserialize, ToSchema, IntoParams)]
#[into_params(parameter_in = Query)]
pub struct ListRules {
    /// Filter by rule type.
    #[serde(rename = "type")]
    #[param(rename = "type")]
    pub rule_type: Option<String>,
    /// Filter by state (draft | active | disabled).
    pub state: Option<String>,
    /// Only rules that apply to this object type (empty rules always match).
    pub applies_to: Option<String>,
    #[serde(default = "default_limit")]
    pub limit: u32,
    #[serde(default)]
    pub start: u32,
}

fn default_limit() -> u32 {
    100
}

fn default_status() -> String {
    "draft".to_owned()
}

fn default_attributes() -> Value {
    Value::Object(serde_json::Map::new())
}

/// A constructed candidate object for a dry run.
#[derive(Debug, Deserialize, ToSchema)]
pub struct DryRunObject {
    #[serde(rename = "type")]
    pub object_type: String,
    #[serde(default = "default_status")]
    pub status: String,
    #[serde(default = "default_attributes")]
    pub attributes: Value,
}

/// Payload for a rule dry run: evaluate against a real object or a constructed
/// candidate, without persisting anything.
#[derive(Debug, Deserialize, ToSchema)]
pub struct DryRunRule {
    /// Real object id to evaluate against (mutually exclusive with `object`).
    #[serde(default)]
    pub object_id: Option<String>,
    /// Constructed candidate object (mutually exclusive with `object_id`).
    #[serde(default)]
    pub object: Option<DryRunObject>,
}

/// Payload for a rule test-batch (Rule Lab): evaluate against a selection of
/// real objects, read-only.
#[derive(Debug, Deserialize, ToSchema)]
pub struct TestBatch {
    /// Object ids to evaluate the rule against (each `table:key`).
    pub object_ids: Vec<String>,
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

/// Loads a single rule record.
async fn fetch_rule(db: &Db, key: &str) -> Result<Option<Value>, ApiError> {
    let sql = "
        LET $r = type::record('business_rule', $key);
        SELECT * FROM $r;
    ";
    let mut response = db.inner().query(sql).bind(("key", key)).await?.check()?;
    let rows: Vec<Value> = response.take(1)?;
    Ok(rows.into_iter().next())
}

/// Checks that a rule name is not already taken (ignores the record `except`).
async fn ensure_unique_name(db: &Db, name: &str, except: Option<&str>) -> Result<(), ApiError> {
    let sql = "SELECT id FROM business_rule WHERE name = $name";
    let rows: Vec<Value> = db
        .inner()
        .query(sql)
        .bind(("name", name.to_owned()))
        .await?
        .check()?
        .take(0)?;
    let conflict = rows
        .iter()
        .any(|row| row.get("id").and_then(Value::as_str) != except);
    if conflict {
        return Err(ApiError::conflict(format!(
            "rule name `{name}` already exists"
        )));
    }
    Ok(())
}

/// Validates a script via the Python worker and maps failures to API errors.
async fn compile_check(state: &AppState, script: &str) -> Result<(), ApiError> {
    match state.rules.compile_script(script).await {
        Ok(errors) if errors.is_empty() => Ok(()),
        Ok(errors) => Err(ApiError::bad_request(format!(
            "script does not compile: {}",
            errors.join("; ")
        ))),
        Err(error) => Err(ApiError::unavailable(format!(
            "rule validation unavailable: {error}"
        ))),
    }
}

/// Creates a new rule as `draft` after a compile check.
#[utoipa::path(
    post,
    path = "/rules",
    request_body = CreateRule,
    responses(
        (status = 201, description = "Created rule", body = Value),
        (status = 400, description = "Invalid payload or script", body = Value),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Forbidden", body = Value),
        (status = 409, description = "Name already exists", body = Value),
        (status = 503, description = "Rule service unavailable", body = Value)
    )
)]
pub async fn create_rule(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Json(body): Json<CreateRule>,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    claims.require("business_rule.manage")?;
    let name = body.name.trim().to_owned();
    if name.is_empty() {
        return Err(ApiError::bad_request("`name` must not be empty"));
    }
    if !VALID_RULE_TYPES.contains(&body.rule_type.as_str()) {
        return Err(ApiError::bad_request(format!(
            "unknown rule type `{}`",
            body.rule_type
        )));
    }
    if body.script.trim().is_empty() {
        return Err(ApiError::bad_request("`script` must not be empty"));
    }
    ensure_unique_name(&state.db, &name, None).await?;
    compile_check(&state, &body.script).await?;

    let mut content = serde_json::Map::new();
    content.insert("name".to_owned(), Value::String(name));
    if let Some(description) = body.description {
        content.insert("description".to_owned(), Value::String(description));
    }
    content.insert("type".to_owned(), Value::String(body.rule_type));
    content.insert(
        "applies_to".to_owned(),
        Value::Array(
            body.applies_to
                .unwrap_or_default()
                .into_iter()
                .map(Value::String)
                .collect(),
        ),
    );
    content.insert("script".to_owned(), Value::String(body.script));
    content.insert("state".to_owned(), Value::String("draft".to_owned()));
    content.insert("version".to_owned(), Value::from(1));
    content.insert("updated_by".to_owned(), Value::String(claims.sub.clone()));
    let content = Value::Object(content);

    // Statements: LET(0), CREATE(1), SELECT(2).
    let sql = "
        LET $r = type::record('business_rule', rand::ulid());
        CREATE $r CONTENT $content;
        SELECT * FROM $r;
    ";
    let mut response = state
        .db
        .inner()
        .query(sql)
        .bind(("content", content))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(2)?;
    let rule = rows
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::internal("created rule was not returned"))?;
    let rule_id = rule
        .get("id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();

    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &claims.sub,
            action: "rule_created",
            object_id: Some(&rule_id),
            from: None,
            to: None,
            metadata: Some(json!({ "name": rule.get("name") })),
        },
    )
    .await;
    // The write path reads the applicable active rules from the cache.
    state.cache.invalidate_rules().await;

    Ok((StatusCode::CREATED, Json(rule)))
}

/// Loads a single rule.
#[utoipa::path(
    get,
    path = "/rules/{id}",
    responses(
        (status = 200, description = "Rule", body = Value),
        (status = 400, description = "Invalid id", body = Value),
        (status = 404, description = "Rule not found", body = Value)
    )
)]
pub async fn get_rule(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<Json<Value>, ApiError> {
    claims.require("business_rule.read")?;
    let key = parse_key(&id, TABLE)?;
    let rule = fetch_rule(&state.db, key)
        .await?
        .ok_or_else(|| ApiError::not_found(format!("rule `{id}` not found")))?;
    Ok(Json(rule))
}

/// Lists rules, optionally filtered by type, state, or applies_to.
#[utoipa::path(
    get,
    path = "/rules",
    params(ListRules),
    responses((status = 200, description = "Rules", body = Vec<Value>))
)]
pub async fn list_rules(
    State(state): State<AppState>,
    headers: HeaderMap,
    Query(params): Query<ListRules>,
) -> Result<Json<Vec<Value>>, ApiError> {
    // Any authenticated role may read rules; the import worker reads active
    // `normalization` rules via the machine-to-machine API key.
    crate::auth::require_permission(&state, &headers, "business_rule.read")?;
    let limit = params.limit.clamp(1, 1000) as i64;
    let start = params.start as i64;

    let mut sql = String::from("SELECT * FROM business_rule");
    let mut conditions: Vec<&str> = Vec::new();
    if params.rule_type.is_some() {
        conditions.push("type = $type");
    }
    if params.state.is_some() {
        conditions.push("state = $state");
    }
    if params.applies_to.is_some() {
        conditions.push("(applies_to = [] OR applies_to CONTAINS $applies_to)");
    }
    if !conditions.is_empty() {
        sql.push_str(" WHERE ");
        sql.push_str(&conditions.join(" AND "));
    }
    sql.push_str(" ORDER BY name LIMIT $limit START $start");

    let mut query = state.db.inner().query(sql);
    if let Some(rule_type) = &params.rule_type {
        query = query.bind(("type", rule_type.clone()));
    }
    if let Some(state_value) = &params.state {
        query = query.bind(("state", state_value.clone()));
    }
    if let Some(applies_to) = &params.applies_to {
        query = query.bind(("applies_to", applies_to.clone()));
    }
    let mut response = query
        .bind(("limit", limit))
        .bind(("start", start))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    Ok(Json(rows))
}

/// Partially updates a rule (name, description, applies_to, script).
#[utoipa::path(
    patch,
    path = "/rules/{id}",
    request_body = UpdateRule,
    responses(
        (status = 200, description = "Updated rule", body = Value),
        (status = 400, description = "Invalid id, payload or script", body = Value),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Forbidden", body = Value),
        (status = 404, description = "Rule not found", body = Value),
        (status = 409, description = "Name already exists", body = Value),
        (status = 503, description = "Rule service unavailable", body = Value)
    )
)]
pub async fn update_rule(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
    Json(body): Json<UpdateRule>,
) -> Result<Json<Value>, ApiError> {
    claims.require("business_rule.manage")?;
    let key = parse_key(&id, TABLE)?;
    let current = fetch_rule(&state.db, key)
        .await?
        .ok_or_else(|| ApiError::not_found(format!("rule `{id}` not found")))?;

    let mut changes = serde_json::Map::new();
    let mut changed: Vec<&str> = Vec::new();
    let mut clear_description = false;

    if let Some(name) = body.name {
        let name = name.trim().to_owned();
        if name.is_empty() {
            return Err(ApiError::bad_request("`name` must not be empty"));
        }
        ensure_unique_name(&state.db, &name, Some(id.as_str())).await?;
        changes.insert("name".to_owned(), Value::String(name));
        changed.push("name");
    }
    if let Some(description) = body.description {
        match description {
            Some(value) => {
                changes.insert("description".to_owned(), Value::String(value));
            }
            None => {
                clear_description = true;
            }
        }
        changed.push("description");
    }
    if let Some(applies_to) = body.applies_to {
        changes.insert("applies_to".to_owned(), json!(applies_to));
        changed.push("applies_to");
    }
    if let Some(script) = body.script {
        if script.trim().is_empty() {
            return Err(ApiError::bad_request("`script` must not be empty"));
        }
        compile_check(&state, &script).await?;
        changes.insert("script".to_owned(), Value::String(script));
        changed.push("script");
    }

    if changes.is_empty() && !clear_description {
        return Ok(Json(current));
    }

    // Statements: LET(0), UPDATE MERGE(1), UPDATE SET(2), SELECT(3). Clearing
    // `description` needs SurrealQL `NONE` (JSON null maps to the distinct NULL
    // value, which `option<string>` rejects).
    let set_clause = if clear_description {
        "UPDATE $r SET description = NONE, version = version + 1, updated_at = time::now(), updated_by = $user;"
    } else {
        "UPDATE $r SET version = version + 1, updated_at = time::now(), updated_by = $user;"
    };
    let sql = format!(
        "
        LET $r = type::record('business_rule', $key);
        UPDATE $r MERGE $changes;
        {set_clause}
        SELECT * FROM $r;
        "
    );
    let mut response = state
        .db
        .inner()
        .query(sql)
        .bind(("key", key))
        .bind(("changes", Value::Object(changes)))
        .bind(("user", claims.sub.clone()))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(3)?;
    let rule = rows
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::not_found(format!("rule `{id}` not found")))?;

    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &claims.sub,
            action: "rule_updated",
            object_id: Some(&id),
            from: None,
            to: None,
            metadata: Some(json!({ "fields": changed })),
        },
    )
    .await;
    state.cache.invalidate_rules().await;

    Ok(Json(rule))
}

/// Activates a rule (`draft`/`disabled` → `active`) after re-checking its script.
#[utoipa::path(
    post,
    path = "/rules/{id}/activate",
    responses(
        (status = 200, description = "Activated rule", body = Value),
        (status = 400, description = "Invalid id or script", body = Value),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Forbidden", body = Value),
        (status = 404, description = "Rule not found", body = Value),
        (status = 503, description = "Rule service unavailable", body = Value)
    )
)]
pub async fn activate_rule(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<Json<Value>, ApiError> {
    claims.require("business_rule.manage")?;
    let key = parse_key(&id, TABLE)?;
    let current = fetch_rule(&state.db, key)
        .await?
        .ok_or_else(|| ApiError::not_found(format!("rule `{id}` not found")))?;
    let old_state = current
        .get("state")
        .and_then(Value::as_str)
        .map(str::to_owned);

    if let Some(script) = current.get("script").and_then(Value::as_str) {
        compile_check(&state, script).await?;
    }

    // Statements: LET(0), UPDATE SET(1), SELECT(2).
    let sql = "
        LET $r = type::record('business_rule', $key);
        UPDATE $r SET state = 'active', version = version + 1, updated_at = time::now(), updated_by = $user;
        SELECT * FROM $r;
    ";
    let mut response = state
        .db
        .inner()
        .query(sql)
        .bind(("key", key))
        .bind(("user", claims.sub.clone()))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(2)?;
    let rule = rows
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::not_found(format!("rule `{id}` not found")))?;

    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &claims.sub,
            action: "rule_activated",
            object_id: Some(&id),
            from: old_state.map(Value::String),
            to: Some(Value::String("active".to_owned())),
            metadata: None,
        },
    )
    .await;
    state.cache.invalidate_rules().await;

    Ok(Json(rule))
}

/// Deactivates a rule (`active` → `disabled`).
#[utoipa::path(
    post,
    path = "/rules/{id}/disable",
    responses(
        (status = 200, description = "Disabled rule", body = Value),
        (status = 400, description = "Invalid id", body = Value),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Forbidden", body = Value),
        (status = 404, description = "Rule not found", body = Value)
    )
)]
pub async fn disable_rule(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<Json<Value>, ApiError> {
    claims.require("business_rule.manage")?;
    let key = parse_key(&id, TABLE)?;
    let current = fetch_rule(&state.db, key)
        .await?
        .ok_or_else(|| ApiError::not_found(format!("rule `{id}` not found")))?;
    let old_state = current
        .get("state")
        .and_then(Value::as_str)
        .map(str::to_owned);

    // Statements: LET(0), UPDATE SET(1), SELECT(2).
    let sql = "
        LET $r = type::record('business_rule', $key);
        UPDATE $r SET state = 'disabled', version = version + 1, updated_at = time::now(), updated_by = $user;
        SELECT * FROM $r;
    ";
    let mut response = state
        .db
        .inner()
        .query(sql)
        .bind(("key", key))
        .bind(("user", claims.sub.clone()))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(2)?;
    let rule = rows
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::not_found(format!("rule `{id}` not found")))?;

    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &claims.sub,
            action: "rule_disabled",
            object_id: Some(&id),
            from: old_state.map(Value::String),
            to: Some(Value::String("disabled".to_owned())),
            metadata: None,
        },
    )
    .await;
    state.cache.invalidate_rules().await;

    Ok(Json(rule))
}

/// Deletes a rule (admin/superadmin).
#[utoipa::path(
    delete,
    path = "/rules/{id}",
    responses(
        (status = 204, description = "Deleted rule", body = Value),
        (status = 400, description = "Invalid id", body = Value),
        (status = 404, description = "Rule not found", body = Value)
    )
)]
pub async fn delete_rule(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<StatusCode, ApiError> {
    claims.require("business_rule.manage")?;
    let key = parse_key(&id, TABLE)?;
    if fetch_rule(&state.db, key).await?.is_none() {
        return Err(ApiError::not_found(format!("rule `{id}` not found")));
    }
    let sql = "
        LET $r = type::record('business_rule', $key);
        DELETE $r;
    ";
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
            action: "rule_deleted",
            object_id: Some(&id),
            from: None,
            to: None,
            metadata: None,
        },
    )
    .await;
    state.cache.invalidate_rules().await;

    Ok(StatusCode::NO_CONTENT)
}

/// Evaluates a rule against a real or constructed object, read-only (no
/// persistence). Available to `admin`/`superadmin` for drafts and active rules.
#[utoipa::path(
    post,
    path = "/rules/{id}/dry-run",
    request_body = DryRunRule,
    responses(
        (status = 200, description = "Dry-run outcome", body = Value),
        (status = 400, description = "Invalid id or payload", body = Value),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Forbidden", body = Value),
        (status = 404, description = "Rule not found", body = Value),
        (status = 503, description = "Rule service unavailable", body = Value)
    )
)]
pub async fn dry_run_rule(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
    Json(body): Json<DryRunRule>,
) -> Result<Json<Value>, ApiError> {
    claims.require("business_rule.manage")?;
    let key = parse_key(&id, TABLE)?;
    let rule = fetch_rule(&state.db, key)
        .await?
        .ok_or_else(|| ApiError::not_found(format!("rule `{id}` not found")))?;
    let rule_name = rule
        .get("name")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    let rule_type = rule
        .get("type")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    let script = rule
        .get("script")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();

    let candidate = build_dry_run_candidate(&state, body).await?;
    // A single-rule dry run derives the evaluation mode from the rule type so
    // that e.g. `workflow_guard` rules run in transition mode.
    let mode = match rule_type.as_str() {
        "workflow_guard" => "transition",
        "normalization" => "import",
        "bulk" => "bulk",
        _ => "save",
    };
    let spec = crate::rules::RuleSpec {
        name: rule_name.clone(),
        rule_type: rule_type.clone(),
        script,
    };
    let outcome = state
        .rules
        .evaluate(
            &[spec],
            &candidate,
            mode,
            None,
            Some(&claims.sub),
            crate::rules::EvaluationContext::default(),
        )
        .await
        .map_err(|error| ApiError::unavailable(format!("rule service unavailable: {error}")))?;

    // Audit the lab run (metadata only, never the payloads).
    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &claims.sub,
            action: "rule_dry_run",
            object_id: Some(&id),
            from: None,
            to: None,
            metadata: Some(json!({
                "rule": rule_name,
                "mode": mode,
                "object_id": candidate.get("id"),
                "object_type": candidate.get("type"),
            })),
        },
    )
    .await;

    let outcome = serde_json::to_value(outcome).map_err(|error| {
        ApiError::internal(format!("failed to serialize dry-run outcome: {error}"))
    })?;
    Ok(Json(outcome))
}

/// Builds a candidate object view from a real object id (read-only).
async fn build_candidate_from_object_id(
    state: &AppState,
    object_id: &str,
) -> Result<Value, ApiError> {
    let key = parse_key(object_id, "object")?;
    let sql = "
        LET $o = type::record('object', $key);
        SELECT * FROM $o;
    ";
    let mut response = state
        .db
        .inner()
        .query(sql)
        .bind(("key", key))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(1)?;
    let object = rows
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::not_found(format!("object `{object_id}` not found")))?;
    Ok(json!({
        "id": object_id,
        "type": object.get("type").cloned().unwrap_or(Value::Null),
        "status": object.get("status").cloned().unwrap_or(Value::Null),
        "attributes": object.get("attributes").cloned().unwrap_or_else(|| json!({})),
    }))
}

/// Builds the candidate object view for a dry run from a real or constructed
/// object.
async fn build_dry_run_candidate(state: &AppState, body: DryRunRule) -> Result<Value, ApiError> {
    match (body.object_id, body.object) {
        (Some(object_id), None) => build_candidate_from_object_id(state, &object_id).await,
        (None, Some(object)) => Ok(json!({
            "id": "",
            "type": object.object_type,
            "status": object.status,
            "attributes": object.attributes,
        })),
        (Some(_), Some(_)) => Err(ApiError::bad_request(
            "provide either `object_id` or a constructed `object`, not both",
        )),
        (None, None) => Err(ApiError::bad_request(
            "provide either `object_id` or a constructed `object`",
        )),
    }
}

/// Evaluates a rule against a selection of real objects, read-only (Rule Lab).
/// Available to `admin`/`superadmin`; no persistence.
#[utoipa::path(
    post,
    path = "/rules/{id}/test-batch",
    request_body = TestBatch,
    responses(
        (status = 200, description = "Per-object test-batch outcomes", body = Vec<Value>),
        (status = 400, description = "Invalid id or payload", body = Value),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Forbidden", body = Value),
        (status = 404, description = "Rule not found", body = Value),
        (status = 503, description = "Rule service unavailable", body = Value)
    )
)]
pub async fn test_batch_rule(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
    Json(body): Json<TestBatch>,
) -> Result<Json<Value>, ApiError> {
    claims.require("business_rule.manage")?;
    if body.object_ids.is_empty() {
        return Err(ApiError::bad_request("`object_ids` must not be empty"));
    }
    let key = parse_key(&id, TABLE)?;
    let rule = fetch_rule(&state.db, key)
        .await?
        .ok_or_else(|| ApiError::not_found(format!("rule `{id}` not found")))?;
    let rule_name = rule
        .get("name")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    let rule_type = rule
        .get("type")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    let script = rule
        .get("script")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();

    // A single-rule lab run derives the evaluation mode from the rule type.
    let mode = match rule_type.as_str() {
        "workflow_guard" => "transition",
        "normalization" => "import",
        "bulk" => "bulk",
        _ => "save",
    };
    let spec = crate::rules::RuleSpec {
        name: rule_name.clone(),
        rule_type: rule_type.clone(),
        script,
    };

    let mut results = Vec::new();
    for object_id in &body.object_ids {
        let candidate = build_candidate_from_object_id(&state, object_id).await?;
        let outcome = state
            .rules
            .evaluate(
                std::slice::from_ref(&spec),
                &candidate,
                mode,
                None,
                Some(&claims.sub),
                crate::rules::EvaluationContext::default(),
            )
            .await
            .map_err(|error| ApiError::unavailable(format!("rule service unavailable: {error}")))?;
        results.push(json!({
            "object_id": object_id,
            "outcome": outcome,
        }));
    }

    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &claims.sub,
            action: "rule_test_batch",
            object_id: Some(&id),
            from: None,
            to: None,
            metadata: Some(json!({
                "rule": rule_name,
                "mode": mode,
                "object_count": results.len(),
                "object_ids": body.object_ids,
            })),
        },
    )
    .await;

    Ok(Json(Value::Array(results)))
}

/// Query parameters for a rule recompute job.
#[derive(Debug, Deserialize, ToSchema, IntoParams)]
#[into_params(parameter_in = Query)]
pub struct RecomputeRule {
    /// Comma-separated object ids; if omitted, all objects of the rule's
    /// `applies_to` types are recomputed.
    pub object_ids: Option<String>,
}

/// Triggers a `business_rule` recompute job over the rule's object set.
#[utoipa::path(
    post,
    path = "/rules/{id}/recompute",
    params(RecomputeRule),
    responses(
        (status = 202, description = "Recompute job queued", body = Value),
        (status = 400, description = "Rule not recomputable", body = Value),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Forbidden", body = Value),
        (status = 404, description = "Rule not found", body = Value),
        (status = 503, description = "Job queue unavailable", body = Value)
    )
)]
pub async fn recompute_rule(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
    Query(params): Query<RecomputeRule>,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    claims.require("business_rule.manage")?;
    let key = parse_key(&id, TABLE)?;
    let rule = fetch_rule(&state.db, key)
        .await?
        .ok_or_else(|| ApiError::not_found(format!("rule `{id}` not found")))?;
    let rule_state = rule
        .get("state")
        .and_then(Value::as_str)
        .unwrap_or_default();
    if rule_state != "active" {
        return Err(ApiError::bad_request("only active rules can be recomputed"));
    }
    let rule_type = rule.get("type").and_then(Value::as_str).unwrap_or_default();
    if rule_type != "computed" && rule_type != "bulk" {
        return Err(ApiError::bad_request(format!(
            "rule type `{rule_type}` is not a recompute job type"
        )));
    }
    let object_ids = params
        .object_ids
        .map(|ids| {
            ids.split(',')
                .map(str::trim)
                .filter(|token| !token.is_empty())
                .map(str::to_owned)
                .collect::<Vec<String>>()
        })
        .unwrap_or_default();
    let applies_to = rule.get("applies_to").cloned().unwrap_or_else(|| json!([]));
    let parameters = json!({
        "rule_id": id.clone(),
        "rule": {
            "name": rule.get("name").cloned().unwrap_or(Value::Null),
            "type": rule_type,
            "script": rule.get("script").cloned().unwrap_or(Value::Null),
        },
        "applies_to": applies_to,
        "object_ids": object_ids,
    });

    crate::api::jobs::queue_run(
        &state.db,
        &state.config,
        None,
        "business_rule",
        parameters,
        "manual",
        &claims.sub,
    )
    .await
}
