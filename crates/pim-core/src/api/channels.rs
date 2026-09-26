//! Channels and channel-specific releases (Phase 9).
//!
//! A **channel** is an output channel (webshop, print catalog, marketplace) and
//! a managed configuration catalog like units or enum sets: table `channel`
//! with a stable `code`, a multilingual `label`, `active` and `position`. The
//! **`release`** table holds the channel-specific approval state of an object —
//! one row per `(object, channel)` with a `status` from the same vocabulary as
//! the object status. `status = "published"` means "released for that channel";
//! withdrawing removes the row.
//!
//! The global object `status` stays independent: the house rule "release a
//! channel only when the object is globally published" is deliberately **not**
//! core logic but a `workflow_guard` rule. Every release change is therefore
//! evaluated in the `transition` mode with `ctx.channel` and
//! `ctx.release_status` set (`obj.status` keeps the global status).
//!
//! See `plan/detail/workflow-freigaben.md`.

use axum::Json;
use axum::extract::{Path, Query, State};
use axum::http::{HeaderMap, StatusCode};
use serde::Deserialize;
use serde_json::{Map, Value, json};
use utoipa::{IntoParams, ToSchema};

use crate::app::AppState;
use crate::audit::{self, AuditEntry};
use crate::auth::AuthUser;
use crate::db::Db;
use crate::error::ApiError;

/// Table holding the channel catalog.
pub const TABLE: &str = "channel";
/// Table holding the channel-specific releases.
pub const RELEASE_TABLE: &str = "release";

/// Marker passed as `ctx.release_status` when a release is withdrawn.
pub const WITHDRAWN: &str = "withdrawn";

fn default_limit() -> u32 {
    1000
}

/// Query parameters for `GET /channels`.
#[derive(Debug, Deserialize, ToSchema, IntoParams)]
#[into_params(parameter_in = Query)]
pub struct ListChannels {
    /// Only return active (or only inactive) channels.
    #[serde(default)]
    pub active: Option<bool>,
    #[serde(default = "default_limit")]
    pub limit: u32,
    #[serde(default)]
    pub start: u32,
}

/// Payload for creating a channel.
#[derive(Debug, Deserialize, ToSchema)]
pub struct CreateChannel {
    /// Stable channel code (e.g. `webshop`); used by releases and filters.
    pub code: String,
    /// Multilingual label.
    #[serde(default)]
    pub label: Option<Value>,
    #[serde(default)]
    pub description: Option<String>,
    /// Whether the channel is offered in the UI (default `true`).
    #[serde(default)]
    pub active: Option<bool>,
    /// Sort position in lists (default `0`).
    #[serde(default)]
    pub position: Option<i64>,
}

/// Payload for updating a channel. The `code` is immutable.
#[derive(Debug, Deserialize, ToSchema)]
pub struct UpdateChannel {
    #[serde(default)]
    pub label: Option<Value>,
    #[serde(default)]
    pub description: Option<String>,
    #[serde(default)]
    pub active: Option<bool>,
    #[serde(default)]
    pub position: Option<i64>,
}

/// Payload for setting a channel-specific release.
#[derive(Debug, Deserialize, ToSchema)]
pub struct SetRelease {
    /// Channel code (`webshop`) or full record id (`channel:webshop`).
    pub channel: String,
    /// Target state (`draft`, `in_review`, `approved`, `published`, `archived`).
    pub status: String,
}

/// Normalizes a channel reference (`channel:webshop` / `webshop` → `webshop`).
pub fn channel_code(raw: &str) -> String {
    let trimmed = raw.trim();
    trimmed
        .strip_prefix("channel:")
        .unwrap_or(trimmed)
        .to_owned()
}

/// Loads a channel by code first, then by record key.
pub(crate) async fn find_channel(db: &Db, code_or_key: &str) -> Result<Option<Value>, ApiError> {
    let code = channel_code(code_or_key);
    let mut response = db
        .inner()
        .query("SELECT * FROM channel WHERE code = $code LIMIT 1;")
        .bind(("code", code.clone()))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    if let Some(channel) = rows.into_iter().next() {
        return Ok(Some(channel));
    }
    let mut response = db
        .inner()
        .query("LET $c = type::record('channel', $key); SELECT * FROM $c;")
        .bind(("key", code))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(1)?;
    Ok(rows.into_iter().next())
}

/// Returns the channel code of a record (used by releases and filters).
pub(crate) fn code_of(channel: &Value) -> String {
    channel
        .get("code")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned()
}

fn validate_code(code: &str) -> Result<(), ApiError> {
    if code.is_empty() {
        return Err(ApiError::bad_request("`code` must not be empty"));
    }
    if !code
        .chars()
        .all(|c| c.is_alphanumeric() || c == '_' || c == '-')
    {
        return Err(ApiError::bad_request(
            "`code` may only contain letters, digits, `_` and `-`",
        ));
    }
    Ok(())
}

/// Counts the releases that reference a channel.
async fn release_count(db: &Db, code: &str) -> Result<i64, ApiError> {
    let mut response = db
        .inner()
        .query("SELECT count() AS total FROM release WHERE channel = $code GROUP ALL;")
        .bind(("code", code.to_owned()))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    Ok(rows
        .first()
        .and_then(|row| row.get("total"))
        .and_then(Value::as_i64)
        .unwrap_or(0))
}

/// Builds the read-only object view a `workflow_guard` rule sees for a release.
///
/// The view carries the object's **own** attributes plus inherited values (a
/// value inherited from the parent counts for a channel rule); the language
/// view is not resolved, because a release is not bound to a working language.
pub(crate) async fn release_object_view(
    state: &AppState,
    record_id: &str,
    key: &str,
) -> Result<Value, ApiError> {
    let mut response = state
        .db
        .inner()
        .query("SELECT * FROM type::record('object', $key) WHERE deleted_at IS NONE;")
        .bind(("key", key.to_owned()))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    let object = rows
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::not_found(format!("object `{record_id}` not found")))?;

    let object_type = object
        .get("type")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    let status = object
        .get("status")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    let mut attributes = object
        .get("attributes")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    crate::inheritance::resolve_inheritance(&state.cache, &state.db, key, None, &mut attributes)
        .await?;
    Ok(json!({
        "id": record_id,
        "type": object_type,
        "status": status,
        "attributes": Value::Object(attributes),
    }))
}

/// Runs the `workflow_guard` rules for a release change and enforces the verdict.
async fn guard_release(
    state: &AppState,
    user_id: &str,
    object_view: &Value,
    channel: &str,
    release_status: &str,
) -> Result<(), ApiError> {
    let guard = crate::rules::evaluate_for_write(
        &state.cache,
        &state.db,
        &state.rules,
        object_view,
        crate::rules::WriteEvaluation {
            rule_types: &["workflow_guard"],
            mode: "transition",
            language: None,
            actor: Some(user_id),
            context: crate::rules::EvaluationContext {
                channel: Some(channel),
                release_status: Some(release_status),
                // Filled by `evaluate_for_write` (class features, Stage B).
                missing_features: None,
            },
        },
    )
    .await?;
    crate::rules::enforce(&guard)
}

/// Lists the channel catalog.
#[utoipa::path(
    get,
    path = "/channels",
    params(ListChannels),
    responses((status = 200, description = "Channels", body = Vec<Value>))
)]
pub async fn list_channels(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Query(params): Query<ListChannels>,
) -> Result<Json<Vec<Value>>, ApiError> {
    claims.require("object.read")?;
    let limit = params.limit.clamp(1, 1000) as i64;
    let start = params.start as i64;
    let mut sql = String::from("SELECT * FROM channel");
    if params.active.is_some() {
        sql.push_str(" WHERE active = $active");
    }
    sql.push_str(" ORDER BY position, code LIMIT $limit START $start;");
    let mut query = state.db.inner().query(sql);
    if let Some(active) = params.active {
        query = query.bind(("active", active));
    }
    let mut response = query
        .bind(("limit", limit))
        .bind(("start", start))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    Ok(Json(rows))
}

/// Gets a single channel by code or record id.
#[utoipa::path(
    get,
    path = "/channels/{id}",
    responses((status = 200, description = "Channel", body = Value))
)]
pub async fn get_channel(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<Json<Value>, ApiError> {
    claims.require("object.read")?;
    let channel = find_channel(&state.db, &id)
        .await?
        .ok_or_else(|| ApiError::not_found(format!("channel `{id}` not found")))?;
    Ok(Json(channel))
}

/// Creates a channel.
#[utoipa::path(
    post,
    path = "/channels",
    request_body = CreateChannel,
    responses(
        (status = 201, description = "Created", body = Value),
        (status = 400, description = "Invalid payload", body = Value),
        (status = 403, description = "Forbidden", body = Value),
        (status = 409, description = "Channel code already exists", body = Value)
    )
)]
pub async fn create_channel(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Json(body): Json<CreateChannel>,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    claims.require("channel.manage")?;
    let code = channel_code(&body.code);
    validate_code(&code)?;
    if find_channel(&state.db, &code).await?.is_some() {
        return Err(ApiError::conflict(format!(
            "channel `{code}` already exists"
        )));
    }

    let mut content = Map::new();
    content.insert("code".to_owned(), Value::String(code.clone()));
    content.insert(
        "label".to_owned(),
        body.label.unwrap_or_else(|| json!({ "en": code.clone() })),
    );
    if let Some(description) = body
        .description
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
    {
        content.insert(
            "description".to_owned(),
            Value::String(description.to_owned()),
        );
    }
    content.insert(
        "active".to_owned(),
        Value::Bool(body.active.unwrap_or(true)),
    );
    content.insert("position".to_owned(), json!(body.position.unwrap_or(0)));

    let mut response = state
        .db
        .inner()
        .query(
            "LET $c = type::record('channel', $key); CREATE $c CONTENT $content; SELECT * FROM $c;",
        )
        .bind(("key", code.clone()))
        .bind(("content", Value::Object(content)))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(2)?;
    let channel = rows
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::internal("created channel was not returned"))?;
    let record_id = channel
        .get("id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &claims.sub,
            action: "channel_created",
            object_id: Some(&record_id),
            from: None,
            to: None,
            metadata: Some(json!({ "code": code })),
        },
    )
    .await;
    Ok((StatusCode::CREATED, Json(channel)))
}

/// Updates a channel (the `code` is immutable).
#[utoipa::path(
    patch,
    path = "/channels/{id}",
    request_body = UpdateChannel,
    responses(
        (status = 200, description = "Updated", body = Value),
        (status = 404, description = "Channel not found", body = Value)
    )
)]
pub async fn update_channel(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
    Json(body): Json<UpdateChannel>,
) -> Result<Json<Value>, ApiError> {
    claims.require("channel.manage")?;
    let code = channel_code(&id);
    let channel = find_channel(&state.db, &code)
        .await?
        .ok_or_else(|| ApiError::not_found(format!("channel `{id}` not found")))?;
    let key = code_of(&channel);
    if key.is_empty() {
        return Err(ApiError::internal("channel is missing its `code`"));
    }

    let mut content = Map::new();
    if let Some(label) = body.label {
        content.insert("label".to_owned(), label);
    }
    let mut clear_description = false;
    if let Some(description) = body.description {
        if description.trim().is_empty() {
            clear_description = true;
        } else {
            content.insert(
                "description".to_owned(),
                Value::String(description.trim().to_owned()),
            );
        }
    }
    if let Some(active) = body.active {
        content.insert("active".to_owned(), Value::Bool(active));
    }
    if let Some(position) = body.position {
        content.insert("position".to_owned(), json!(position));
    }

    // `option<string>` fields reject an explicit `null`, so clearing uses UNSET.
    let unset = if clear_description {
        "UPDATE $c UNSET description;\n        "
    } else {
        ""
    };
    let sql = format!(
        "LET $c = type::record('channel', $key); UPDATE $c MERGE $content;\n        {unset}UPDATE $c SET updated_at = time::now(), updated_by = $user; SELECT * FROM $c;"
    );
    let select_index = if clear_description { 4 } else { 3 };
    let mut response = state
        .db
        .inner()
        .query(sql)
        .bind(("key", key.clone()))
        .bind(("content", Value::Object(content)))
        .bind(("user", claims.sub.clone()))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(select_index)?;
    let updated = rows
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::not_found(format!("channel `{id}` not found")))?;
    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &claims.sub,
            action: "channel_updated",
            object_id: Some(&id),
            from: None,
            to: None,
            metadata: Some(json!({ "code": key })),
        },
    )
    .await;
    Ok(Json(updated))
}

/// Deletes a channel; blocked while releases reference it.
#[utoipa::path(
    delete,
    path = "/channels/{id}",
    responses(
        (status = 204, description = "Channel deleted"),
        (status = 409, description = "Channel is used by releases", body = Value)
    )
)]
pub async fn delete_channel(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<StatusCode, ApiError> {
    claims.require("channel.manage")?;
    let code = channel_code(&id);
    let channel = find_channel(&state.db, &code)
        .await?
        .ok_or_else(|| ApiError::not_found(format!("channel `{id}` not found")))?;
    let key = code_of(&channel);
    let used = release_count(&state.db, &key).await?;
    if used > 0 {
        return Err(ApiError::conflict(format!(
            "channel `{key}` is used by {used} release(s); withdraw them first"
        )));
    }
    state
        .db
        .inner()
        .query("DELETE type::record('channel', $key);")
        .bind(("key", key.clone()))
        .await?
        .check()?;
    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &claims.sub,
            action: "channel_deleted",
            object_id: Some(&id),
            from: None,
            to: None,
            metadata: Some(json!({ "code": key })),
        },
    )
    .await;
    Ok(StatusCode::NO_CONTENT)
}

/// Returns the stored release state for one `(object, channel)` pair (empty
/// when the object carries no release for that channel).
pub(crate) async fn release_status_of(
    db: &Db,
    object_key: &str,
    channel: &str,
) -> Result<String, ApiError> {
    let mut response = db
        .inner()
        .query(
            "SELECT VALUE status FROM release WHERE object_id = type::record('object', $key) AND channel = $channel LIMIT 1;",
        )
        .bind(("key", object_key.to_owned()))
        .bind(("channel", channel.to_owned()))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    Ok(rows
        .into_iter()
        .next()
        .and_then(|value| value.as_str().map(str::to_owned))
        .unwrap_or_default())
}

/// Lists the channel releases of an object.
#[utoipa::path(
    get,
    path = "/objects/{id}/releases",
    responses((status = 200, description = "Channel releases", body = Vec<Value>))
)]
pub async fn list_object_releases(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(id): Path<String>,
) -> Result<Json<Vec<Value>>, ApiError> {
    crate::auth::require_permission(&state, &headers, "object.read")?;
    let key = crate::api::objects::parse_object_key(&id)?;
    // A deleted (or unknown) object has no releases: answering `404` instead of a
    // stale list keeps callers honest.
    let mut object = state
        .db
        .inner()
        .query("SELECT id FROM type::record('object', $key) WHERE deleted_at IS NONE;")
        .bind(("key", key.to_owned()))
        .await?
        .check()?;
    let objects: Vec<Value> = object.take(0)?;
    if objects.is_empty() {
        return Err(ApiError::not_found(format!("object `{id}` not found")));
    }
    let mut response = state
        .db
        .inner()
        .query(
            "SELECT * FROM release WHERE object_id = type::record('object', $key) ORDER BY channel;",
        )
        .bind(("key", key))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    Ok(Json(rows))
}

/// Sets (or changes) a channel-specific release of an object.
#[utoipa::path(
    put,
    path = "/objects/{id}/releases",
    request_body = SetRelease,
    responses(
        (status = 200, description = "Release set", body = Value),
        (status = 400, description = "Invalid payload", body = Value),
        (status = 403, description = "Forbidden", body = Value),
        (status = 404, description = "Object or channel not found", body = Value),
        (status = 409, description = "Rejected by a workflow_guard rule", body = Value)
    )
)]
pub async fn set_object_release(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(id): Path<String>,
    Json(body): Json<SetRelease>,
) -> Result<Json<Value>, ApiError> {
    let user_id = crate::auth::require_permission(&state, &headers, "object.publish")?;
    let key = crate::api::objects::parse_object_key(&id)?;
    let status = body.status.trim().to_owned();
    if status.is_empty() {
        return Err(ApiError::bad_request("`status` must not be empty"));
    }
    let channel = find_channel(&state.db, &body.channel)
        .await?
        .ok_or_else(|| ApiError::not_found(format!("channel `{}` not found", body.channel)))?;
    let code = code_of(&channel);

    let view = release_object_view(&state, &id, key).await?;
    guard_release(&state, &user_id, &view, &code, &status).await?;

    // The previous state goes into the event; the row is replaced (not merged)
    // so the unique index on `(object_id, channel)` always holds.
    let previous = release_status_of(&state.db, key, &code).await?;
    let mut response = state
        .db
        .inner()
        .query(
            "
        BEGIN TRANSACTION;
        LET $obj = type::record('object', $key);
        DELETE release WHERE object_id = $obj AND channel = $channel;
        CREATE release CONTENT {
            object_id: $obj,
            channel: $channel,
            status: $status,
            released_at: time::now(),
            released_by: $user,
            updated_by: $user
        };
        CREATE outbox_event CONTENT $outbox;
        SELECT * FROM release WHERE object_id = $obj AND channel = $channel;
        COMMIT TRANSACTION;
        ",
        )
        .bind(("key", key.to_owned()))
        .bind(("channel", code.clone()))
        .bind(("status", status.clone()))
        .bind(("user", user_id.clone()))
        .bind((
            "outbox",
            crate::event::content(&crate::event::OutboxEvent {
                event_type: crate::event::EVENT_OBJECT_RELEASED,
                object_type: view
                    .get("type")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_owned(),
                object_id: id.clone(),
                changes: json!({
                    "channel": code,
                    "status": status,
                    "previous_status": previous,
                }),
                metadata: json!({ "user_id": user_id.clone() }),
            }),
        ))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(5)?;
    let release = rows
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::internal("release was not returned"))?;

    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &user_id,
            action: "object_released",
            object_id: Some(&id),
            from: None,
            to: Some(Value::String(status.clone())),
            metadata: Some(json!({ "channel": code })),
        },
    )
    .await;
    Ok(Json(release))
}

/// Withdraws a channel-specific release of an object.
#[utoipa::path(
    delete,
    path = "/objects/{id}/releases/{channel}",
    responses(
        (status = 204, description = "Release withdrawn"),
        (status = 404, description = "No release for that channel", body = Value),
        (status = 409, description = "Rejected by a workflow_guard rule", body = Value)
    )
)]
pub async fn delete_object_release(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path((id, channel)): Path<(String, String)>,
) -> Result<StatusCode, ApiError> {
    let user_id = crate::auth::require_permission(&state, &headers, "object.publish")?;
    let key = crate::api::objects::parse_object_key(&id)?;
    let code = channel_code(&channel);
    let previous = release_status_of(&state.db, key, &code).await?;
    if previous.is_empty() {
        return Err(ApiError::not_found(format!(
            "no release for channel `{code}`"
        )));
    }

    let view = release_object_view(&state, &id, key).await?;
    guard_release(&state, &user_id, &view, &code, WITHDRAWN).await?;

    state
        .db
        .inner()
        .query(
            "
        BEGIN TRANSACTION;
        DELETE release WHERE object_id = type::record('object', $key) AND channel = $channel;
        CREATE outbox_event CONTENT $outbox;
        COMMIT TRANSACTION;
        ",
        )
        .bind(("key", key.to_owned()))
        .bind(("channel", code.clone()))
        .bind((
            "outbox",
            crate::event::content(&crate::event::OutboxEvent {
                event_type: crate::event::EVENT_OBJECT_UNRELEASED,
                object_type: view
                    .get("type")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_owned(),
                object_id: id.clone(),
                changes: json!({ "channel": code, "previous_status": previous }),
                metadata: json!({ "user_id": user_id.clone() }),
            }),
        ))
        .await?
        .check()?;

    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &user_id,
            action: "object_release_withdrawn",
            object_id: Some(&id),
            from: Some(Value::String(previous)),
            to: None,
            metadata: Some(json!({ "channel": code })),
        },
    )
    .await;
    Ok(StatusCode::NO_CONTENT)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn channel_references_accept_code_and_record_id() {
        assert_eq!(channel_code("webshop"), "webshop");
        assert_eq!(channel_code(" channel:webshop "), "webshop");
        assert_eq!(channel_code("channel:print-catalog"), "print-catalog");
    }

    #[test]
    fn code_validation_rejects_empty_and_separators() {
        assert!(validate_code("webshop").is_ok());
        assert!(validate_code("print-catalog_2").is_ok());
        assert!(validate_code("").is_err());
        assert!(validate_code("web shop").is_err());
        assert!(validate_code("web.shop").is_err());
    }
}
