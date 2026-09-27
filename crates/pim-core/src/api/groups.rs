//! Group management handlers (admin/superadmin only).

use axum::Json;
use axum::extract::{Path, Query, State};
use axum::http::HeaderMap;
use axum::http::StatusCode;
use serde::Deserialize;
use serde_json::Value;
use utoipa::{IntoParams, ToSchema};

use crate::app::AppState;
use crate::audit::{self, AuditEntry};
use crate::auth::{self, AuthUser};
use crate::error::ApiError;
use crate::rbac;

const TABLE: &str = "group";

/// Payload for creating a group.
#[derive(Debug, Deserialize, ToSchema)]
pub struct CreateGroup {
    pub name: String,
    /// Additional granted permissions.
    #[serde(default)]
    pub grant_permissions: Vec<String>,
    /// Explicitly denied permissions.
    #[serde(default)]
    pub deny_permissions: Vec<String>,
    /// ABAC data scope (see plan section 9).
    #[serde(default = "default_object")]
    pub scope: Value,
    /// UI branding.
    #[serde(default = "default_object")]
    pub branding: Value,
}

fn default_object() -> Value {
    Value::Object(serde_json::Map::new())
}

/// Payload for a partial group update.
#[derive(Debug, Deserialize, ToSchema)]
pub struct UpdateGroup {
    #[serde(default)]
    pub name: Option<String>,
    #[serde(default)]
    pub grant_permissions: Option<Vec<String>>,
    #[serde(default)]
    pub deny_permissions: Option<Vec<String>>,
    #[serde(default)]
    pub scope: Option<Value>,
    #[serde(default)]
    pub branding: Option<Value>,
}

/// Query parameters for listing groups.
#[derive(Debug, Deserialize, ToSchema, IntoParams)]
#[into_params(parameter_in = Query)]
pub struct ListGroups {
    #[serde(default = "default_limit")]
    pub limit: u32,
    #[serde(default)]
    pub start: u32,
}

fn default_limit() -> u32 {
    100
}

/// Payload for previewing a scope document.
#[derive(Debug, Deserialize, ToSchema)]
pub struct PreviewScope {
    /// The scope document to try out, in the shape `POST /groups` stores.
    #[serde(default = "default_object")]
    pub scope: Value,
}

/// Payload for validating a group document.
#[derive(Debug, Deserialize, ToSchema)]
pub struct ValidateGroup {
    /// The permissions the group would grant (same shape as on create/update).
    #[serde(default)]
    pub grant_permissions: Vec<String>,
    /// The permissions the group would deny.
    #[serde(default)]
    pub deny_permissions: Vec<String>,
    /// The scope document (same shape as on create/update).
    #[serde(default = "default_object")]
    pub scope: Value,
}

/// Splits a `table:key` path segment and verifies the expected table.
///
/// Shared with [`crate::rbac`], which resolves user ids and group ids the same way.
pub(crate) fn parse_key<'a>(id: &'a str, table: &str) -> Result<&'a str, ApiError> {
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

/// Reads a scope document, turning the problems into one `400`.
///
/// Separate from [`check_scope`] because the preview needs the rules afterwards: the same
/// verdict as the write path, kept in one place.
fn scope_rules(scope: &Value) -> Result<crate::scope::Rules, ApiError> {
    crate::scope::parse(scope).map_err(|problems| {
        ApiError::bad_request(format!("invalid scope: {}", problems.join("; ")))
    })
}

/// Rejects a scope document the core cannot use (`400`).
///
/// Two stages, because they answer different mistakes: [`crate::scope::parse`] reads the shape
/// (unknown field, unknown operator, missing target) and [`crate::scope::validate`] checks the
/// database (does that relation exist, does that target exist). A scope that names something
/// nonexistent would silently match nothing — "the group has no effect" is the least
/// debuggable outcome, so the write path is where it stops.
async fn check_scope(state: &AppState, scope: &Value) -> Result<(), ApiError> {
    let rules = scope_rules(scope)?;
    crate::scope::validate(&state.db, &rules).await
}

/// Rejects permission keys the catalog does not know (`400`).
///
/// The core would ignore them on the read path (fail-closed), so a stored typo would be a
/// permission that looks set and does nothing. The write path is where the catalog
/// becomes a contract — the same rule as for connector and export-profile parameters.
fn reject_unknown_permissions(unknown: Vec<String>) -> Result<(), ApiError> {
    let mut unknown = unknown;
    unknown.sort();
    unknown.dedup();
    if unknown.is_empty() {
        return Ok(());
    }
    Err(ApiError::bad_request(format!(
        "unknown permission(s): {} — the catalog is at GET /permissions",
        unknown.join(", ")
    )))
}

/// Creates a new group.
#[utoipa::path(
    post,
    path = "/groups",
    request_body = CreateGroup,
    responses(
        (status = 201, description = "Created group", body = Value),
        (status = 403, description = "Forbidden", body = Value)
    )
)]
pub async fn create_group(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Json(body): Json<CreateGroup>,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    claims.require("group.manage")?;
    if body.name.trim().is_empty() {
        return Err(ApiError::bad_request("`name` must not be empty"));
    }
    reject_unknown_permissions(
        crate::permissions::unknown_keys(&body.grant_permissions)
            .into_iter()
            .chain(crate::permissions::unknown_keys(&body.deny_permissions))
            .collect(),
    )?;
    check_scope(&state, &body.scope).await?;
    // A group carries permissions: creating one can give a user rights, so every issued
    // session has to be renewed. Bumped after the validation and before the write, so a
    // rejected request does not log anybody out.
    rbac::invalidate_sessions(&state.db, &state.cache).await?;
    let content = serde_json::json!({
        "name": body.name,
        "grant_permissions": body.grant_permissions,
        "deny_permissions": body.deny_permissions,
        "scope": body.scope,
        "branding": body.branding,
    });

    let sql = "
        LET $g = type::record('group', rand::ulid());
        CREATE $g CONTENT $content;
        SELECT * FROM $g;
    ";
    let mut response = state
        .db
        .inner()
        .query(sql)
        .bind(("content", content))
        .await?
        .check()?;
    let groups: Vec<Value> = response.take(2)?;
    let group = groups
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::internal("created group was not returned"))?;
    let group_id = group
        .get("id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &claims.sub,
            action: "group_created",
            object_id: Some(&group_id),
            from: None,
            to: Some(serde_json::json!({ "name": group.get("name") })),
            metadata: None,
        },
    )
    .await;
    Ok((StatusCode::CREATED, Json(group)))
}

/// Lists groups.
#[utoipa::path(
    get,
    path = "/groups",
    params(ListGroups),
    responses((status = 200, description = "Groups", body = Vec<Value>))
)]
pub async fn list_groups(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Query(params): Query<ListGroups>,
) -> Result<Json<Vec<Value>>, ApiError> {
    claims.require("group.read")?;
    let limit = params.limit.clamp(1, 1000) as i64;
    let start = params.start as i64;
    let sql = "SELECT * FROM group ORDER BY name ASC LIMIT $limit START $start";
    let mut response = state
        .db
        .inner()
        .query(sql)
        .bind(("limit", limit))
        .bind(("start", start))
        .await?
        .check()?;
    let groups: Vec<Value> = response.take(0)?;
    Ok(Json(groups))
}

/// Validates a scope document and counts the objects it shows.
///
/// The group editor builds a scope from blocks and has to answer "what would this mean"
/// while it is being edited — a scope nobody can test is a scope nobody dares to use. The
/// editing admin is normally **not** a member of the group, so the number cannot come from
/// their own session: this endpoint compiles the document on screen, with the same parser,
/// the same validation and the same compiler the read path uses, so a preview cannot
/// promise what a save refuses.
///
/// It answers **numbers, not objects**: `count` is what the document shows, `total` is what
/// the caller sees without it — for the usual unscoped admin the whole catalogue. Both
/// exclude deleted objects, like every other count. The caller's own scope is ANDed in, so
/// a restricted admin measures inside their own view and learns nothing beyond it; the two
/// clauses are compiled with different base indices for exactly that reason
/// ([`crate::scope::clause_of`]).
#[utoipa::path(
    post,
    path = "/groups/preview-scope",
    request_body = PreviewScope,
    responses(
        (status = 200, description = "Objects the scope shows", body = Value),
        (status = 400, description = "Invalid scope", body = Value)
    )
)]
pub async fn preview_scope(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<PreviewScope>,
) -> Result<Json<Value>, ApiError> {
    // The same key the permission catalog needs: this is the second input of the group
    // editor, not a catalogue view.
    let caller = auth::require_caller(&state, &headers, "group.read")?;
    let rules = scope_rules(&body.scope)?;
    crate::scope::validate(&state.db, &rules).await?;
    let own = crate::scope::for_caller(&state, &caller).await?;
    let own_groups = caller_group_count(&caller);
    let preview = crate::scope::clause_of(&state, &rules, own_groups).await?;
    let count = count_objects_with(&state, &[&own, &preview]).await?;
    let total = count_objects_with(&state, &[&own]).await?;
    Ok(Json(serde_json::json!({ "count": count, "total": total })))
}

/// The number of groups the caller's own scope was compiled from.
///
/// The preview needs it as the base index of the document's clause: bind names carry the
/// group index, and two clauses in one query must not share a name. A caller without groups
/// is unrestricted ([`crate::scope::for_caller`] answers `None`), so nothing is added.
fn caller_group_count(caller: &crate::auth::Caller) -> usize {
    match caller {
        crate::auth::Caller::System => 0,
        crate::auth::Caller::User(claims) => claims.groups.len(),
    }
}

/// Counts the objects a list of scope clauses describes.
///
/// `None` entries add nothing (an unrestricted caller), which is the same identity rule the
/// read paths follow: a clause that is not there changes no query.
async fn count_objects_with(
    state: &AppState,
    clauses: &[&Option<crate::scope::Clause>],
) -> Result<i64, ApiError> {
    let mut conditions: Vec<String> = vec!["deleted_at IS NONE".to_owned()];
    let mut binds: Vec<(String, Value)> = Vec::new();
    for clause in clauses {
        crate::scope::apply((*clause).clone(), &mut conditions, &mut binds);
    }
    let mut query = state.db.inner().query(format!(
        "SELECT count() AS n FROM object WHERE {} GROUP ALL;",
        conditions.join(" AND ")
    ));
    for (name, value) in &binds {
        query = query.bind((name.as_str(), value.clone()));
    }
    let mut response = query.await?.check()?;
    let rows: Vec<Value> = response.take(0)?;
    Ok(rows
        .first()
        .and_then(|row| row.get("n"))
        .and_then(Value::as_i64)
        .unwrap_or(0))
}

/// Checks a group document — **without saving**.
///
/// The same two checks the write path runs (`reject_unknown_permissions` and `check_scope`),
/// with the same messages, so a caller gets exactly the verdict a save would produce: the
/// group editor before it submits an unusual document, and the AI assistant, which has to
/// judge a suggestion against the catalog and `scope::parse`/`validate`
/// (`plan/detail/ki-assistenz.md`). Nothing is written and nothing has to exist yet.
///
/// A wrong request shape is a `400`; a document the core would refuse is a `200` with
/// `{"ok": false, "errors": [...]}`, like `POST /connectors/validate` — the caller is asking
/// a question, not making a change.
#[utoipa::path(
    post,
    path = "/groups/validate",
    request_body = ValidateGroup,
    responses(
        (status = 200, description = "Validation result", body = Value),
        (status = 400, description = "Invalid payload", body = Value),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Forbidden", body = Value)
    )
)]
pub async fn validate_group(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<ValidateGroup>,
) -> Result<Json<Value>, ApiError> {
    // The write path's key, reachable with the machine key: the worker checks AI
    // suggestions with it, and whoever may not manage a group may not dry-run one either.
    auth::require_caller(&state, &headers, "group.manage")?;
    let mut errors: Vec<String> = Vec::new();
    let unknown = crate::permissions::unknown_keys(&body.grant_permissions)
        .into_iter()
        .chain(crate::permissions::unknown_keys(&body.deny_permissions))
        .collect();
    if let Err(error) = reject_unknown_permissions(unknown) {
        errors.push(error.message().to_owned());
    }
    if let Err(error) = check_scope(&state, &body.scope).await {
        errors.push(error.message().to_owned());
    }
    Ok(Json(serde_json::json!({
        "ok": errors.is_empty(),
        "errors": errors,
    })))
}

/// Loads a single group by id.
#[utoipa::path(
    get,
    path = "/groups/{id}",
    responses(
        (status = 200, description = "Group", body = Value),
        (status = 404, description = "Not found", body = Value)
    )
)]
pub async fn get_group(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<Json<Value>, ApiError> {
    claims.require("group.read")?;
    let key = parse_key(&id, TABLE)?;
    let sql = "
        LET $g = type::record('group', $key);
        SELECT * FROM $g;
    ";
    let mut response = state
        .db
        .inner()
        .query(sql)
        .bind(("key", key))
        .await?
        .check()?;
    let groups: Vec<Value> = response.take(1)?;
    let group = groups
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::not_found(format!("group `{id}` not found")))?;
    Ok(Json(group))
}

/// Partially updates a group.
#[utoipa::path(
    patch,
    path = "/groups/{id}",
    request_body = UpdateGroup,
    responses(
        (status = 200, description = "Updated group", body = Value),
        (status = 404, description = "Not found", body = Value)
    )
)]
pub async fn update_group(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
    Json(body): Json<UpdateGroup>,
) -> Result<Json<Value>, ApiError> {
    claims.require("group.manage")?;
    let key = parse_key(&id, TABLE)?;

    // Checked before anything is written, so a rejected list does not end any session.
    let mut unknown: Vec<String> = Vec::new();
    if let Some(grant) = &body.grant_permissions {
        unknown.extend(crate::permissions::unknown_keys(grant));
    }
    if let Some(deny) = &body.deny_permissions {
        unknown.extend(crate::permissions::unknown_keys(deny));
    }
    reject_unknown_permissions(unknown)?;
    if let Some(scope) = &body.scope {
        check_scope(&state, scope).await?;
    }

    // Explicit assignments instead of `MERGE`: SurrealDB merges **nested objects** field by
    // field, so `MERGE {"scope": {}}` leaves the stored rules in place. That is exactly the
    // write the group editor sends when the last block is removed — the scope looked cleared
    // and stayed in force (found 2026-09-26). `SET scope = $scope` replaces the document;
    // `{}` and `{"rules": []}` both mean "no restriction" ([`crate::scope::parse`]).
    let mut assignments: Vec<String> = Vec::new();
    let mut binds: Vec<(&'static str, Value)> = Vec::new();
    if let Some(name) = body.name {
        assignments.push("name = $name".to_owned());
        binds.push(("name", Value::String(name)));
    }
    if let Some(grant_permissions) = body.grant_permissions {
        assignments.push("grant_permissions = $grant_permissions".to_owned());
        binds.push((
            "grant_permissions",
            Value::Array(grant_permissions.into_iter().map(Value::String).collect()),
        ));
    }
    if let Some(deny_permissions) = body.deny_permissions {
        assignments.push("deny_permissions = $deny_permissions".to_owned());
        binds.push((
            "deny_permissions",
            Value::Array(deny_permissions.into_iter().map(Value::String).collect()),
        ));
    }
    if let Some(scope) = body.scope {
        assignments.push("scope = $scope".to_owned());
        binds.push(("scope", scope));
    }
    if let Some(branding) = body.branding {
        assignments.push("branding = $branding".to_owned());
        binds.push(("branding", branding));
    }
    // The audit names the changed fields, not their values: a scope document can be large
    // and the durable history is about *what* moved, not a second copy of the data.
    let fields: Vec<String> = binds.iter().map(|(name, _)| (*name).to_owned()).collect();

    rbac::invalidate_sessions(&state.db, &state.cache).await?;
    // Without a field to write the record is only read; the result sits one statement
    // further back then.
    let update = if assignments.is_empty() {
        String::new()
    } else {
        format!("UPDATE $g SET {};\n", assignments.join(", "))
    };
    let result_index = if update.is_empty() { 1 } else { 2 };
    let sql = format!("LET $g = type::record('group', $key);\n{update}SELECT * FROM $g;");
    let mut query = state.db.inner().query(sql).bind(("key", key));
    for (name, value) in binds {
        query = query.bind((name, value));
    }
    let mut response = query.await?.check()?;
    let groups: Vec<Value> = response.take(result_index)?;
    let group = groups
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::not_found(format!("group `{id}` not found")))?;
    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &claims.sub,
            action: "group_updated",
            object_id: Some(&id),
            from: None,
            to: None,
            metadata: Some(serde_json::json!({ "fields": fields })),
        },
    )
    .await;
    Ok(Json(group))
}

/// Deletes a group.
#[utoipa::path(
    delete,
    path = "/groups/{id}",
    responses(
        (status = 204, description = "Deleted"),
        (status = 404, description = "Not found", body = Value)
    )
)]
pub async fn delete_group(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<StatusCode, ApiError> {
    claims.require("group.manage")?;
    let key = parse_key(&id, TABLE)?;
    rbac::invalidate_sessions(&state.db, &state.cache).await?;
    let sql = "
        LET $g = type::record('group', $key);
        DELETE $g;
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
            action: "group_deleted",
            object_id: Some(&id),
            from: None,
            to: None,
            metadata: None,
        },
    )
    .await;
    Ok(StatusCode::NO_CONTENT)
}

// -- membership & hierarchy --------------------------------------------------
//
// The access model is two graph edges (`plan/detail/benutzer-gruppen-rechte.md`):
// `member_of` (user → group) and `child_of` (group → group, `in` = child). Both are
// written here and resolved by `crate::rbac`; the edges are indexed (`idx_member_of_in`/
// `_out`, `idx_child_of_in`/`_out`), so the login path starts at an index instead of a
// scan. The requests carry record ids (`user:42`, `group:team`) — the same form the rest
// of the core uses.

/// Payload for adding a member to a group.
#[derive(Debug, Deserialize, ToSchema)]
pub struct GroupMember {
    /// User record id (`user:42`).
    pub user_id: String,
}

/// Payload for linking a child group to the group in the path.
#[derive(Debug, Deserialize, ToSchema)]
pub struct GroupChild {
    /// Child group record id (`group:team`).
    pub group_id: String,
}

/// Verifies that a record exists (answers `404` with table and key otherwise).
async fn require_record(state: &AppState, table: &str, key: &str) -> Result<(), ApiError> {
    let sql = format!("LET $r = type::record('{table}', $key); SELECT id FROM $r;");
    let mut response = state
        .db
        .inner()
        .query(sql)
        .bind(("key", key.to_owned()))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(1)?;
    if rows.is_empty() {
        return Err(ApiError::not_found(format!("{table} `{key}` not found")));
    }
    Ok(())
}

/// Creates one edge of the access model.
///
/// `RELATE type::record(…) -> edge -> type::record(…)` does **not** parse here
/// ("Unexpected token `::`", see `api::references`), so both endpoints are bound first —
/// the form every other edge write in the core uses.
async fn relate(
    state: &AppState,
    edge: rbac::Edge,
    from: (&str, &str),
    to: (&str, &str),
) -> Result<(), ApiError> {
    let sql = format!(
        "LET $src = type::record('{}', $from); \
         LET $tgt = type::record('{}', $to); \
         RELATE $src->{}->$tgt;",
        from.0,
        to.0,
        edge.table()
    );
    state
        .db
        .inner()
        .query(sql)
        .bind(("from", from.1.to_owned()))
        .bind(("to", to.1.to_owned()))
        .await?
        .check()?;
    Ok(())
}

/// Deletes one edge of the access model.
///
/// Idempotent on purpose: removing a membership that is not there answers `204` as well,
/// so a double click or a retried request is not an error.
async fn unrelate(
    state: &AppState,
    edge: rbac::Edge,
    from: (&str, &str),
    to: (&str, &str),
) -> Result<(), ApiError> {
    let sql = format!(
        "DELETE {} WHERE in = type::record('{}', $from) AND out = type::record('{}', $to);",
        edge.table(),
        from.0,
        to.0
    );
    state
        .db
        .inner()
        .query(sql)
        .bind(("from", from.1.to_owned()))
        .bind(("to", to.1.to_owned()))
        .await?
        .check()?;
    Ok(())
}

/// Adds a user to a group (`member_of`).
///
/// Idempotent: an existing membership answers `200 {created: false}` instead of writing a
/// second edge — `RELATE` alone would happily create duplicates, and a user in the same
/// group twice would be counted twice by every later resolution.
#[utoipa::path(
    post,
    path = "/groups/{id}/members",
    request_body = GroupMember,
    responses(
        (status = 201, description = "Member added", body = Value),
        (status = 200, description = "Already a member", body = Value),
        (status = 404, description = "Unknown group or user", body = Value)
    )
)]
pub async fn add_group_member(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
    Json(body): Json<GroupMember>,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    claims.require("group.manage")?;
    let group_key = parse_key(&id, TABLE)?;
    let user_key = parse_key(&body.user_id, "user")?;
    require_record(&state, TABLE, group_key).await?;
    require_record(&state, "user", user_key).await?;
    let group = format!("{TABLE}:{group_key}");
    let user = format!("user:{user_key}");

    if rbac::edge_exists(&state.db, rbac::Edge::MemberOf, &user, &group).await? {
        return Ok((
            StatusCode::OK,
            Json(serde_json::json!({
                "created": false,
                "user": user,
                "group": group,
            })),
        ));
    }
    rbac::invalidate_sessions(&state.db, &state.cache).await?;
    relate(
        &state,
        rbac::Edge::MemberOf,
        ("user", user_key),
        (TABLE, group_key),
    )
    .await?;
    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &claims.sub,
            action: "group_member_added",
            object_id: Some(&group),
            from: None,
            to: Some(serde_json::json!({ "user": user })),
            metadata: None,
        },
    )
    .await;
    Ok((
        StatusCode::CREATED,
        Json(serde_json::json!({
            "created": true,
            "user": user,
            "group": group,
        })),
    ))
}

/// Removes a user from a group (`member_of`).
#[utoipa::path(
    delete,
    path = "/groups/{id}/members/{user_id}",
    responses(
        (status = 204, description = "Member removed"),
        (status = 404, description = "Unknown group", body = Value)
    )
)]
pub async fn remove_group_member(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path((id, user_id)): Path<(String, String)>,
) -> Result<StatusCode, ApiError> {
    claims.require("group.manage")?;
    let group_key = parse_key(&id, TABLE)?;
    let user_key = parse_key(&user_id, "user")?;
    require_record(&state, TABLE, group_key).await?;
    let group = format!("{TABLE}:{group_key}");
    let user = format!("user:{user_key}");
    // A removal that finds no membership changed nothing, so it must not end the
    // sessions of the whole instance either.
    if rbac::edge_exists(&state.db, rbac::Edge::MemberOf, &user, &group).await? {
        rbac::invalidate_sessions(&state.db, &state.cache).await?;
        unrelate(
            &state,
            rbac::Edge::MemberOf,
            ("user", user_key),
            (TABLE, group_key),
        )
        .await?;
        audit::record_best_effort(
            &state.db,
            AuditEntry {
                user_id: &claims.sub,
                action: "group_member_removed",
                object_id: Some(&group),
                from: Some(serde_json::json!({ "user": user })),
                to: None,
                metadata: None,
            },
        )
        .await;
    }
    Ok(StatusCode::NO_CONTENT)
}

/// Links a child group below the group in the path (`child_of`).
///
/// Refuses a link that would close a **cycle** in the hierarchy (`400`): a loop would
/// make "who inherits from whom" unanswerable and every later resolution walk longer
/// than the data deserves.
#[utoipa::path(
    post,
    path = "/groups/{id}/children",
    request_body = GroupChild,
    responses(
        (status = 201, description = "Child linked", body = Value),
        (status = 200, description = "Already linked", body = Value),
        (status = 400, description = "Cycle", body = Value),
        (status = 404, description = "Unknown group", body = Value)
    )
)]
pub async fn add_group_child(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
    Json(body): Json<GroupChild>,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    claims.require("group.manage")?;
    let parent_key = parse_key(&id, TABLE)?;
    let child_key = parse_key(&body.group_id, TABLE)?;
    require_record(&state, TABLE, parent_key).await?;
    require_record(&state, TABLE, child_key).await?;
    let parent = format!("{TABLE}:{parent_key}");
    let child = format!("{TABLE}:{child_key}");

    if rbac::would_cycle(&state.db, &child, &parent).await? {
        return Err(ApiError::bad_request(format!(
            "linking `{child}` below `{parent}` would close a cycle in the group hierarchy"
        )));
    }
    if rbac::edge_exists(&state.db, rbac::Edge::ChildOf, &child, &parent).await? {
        return Ok((
            StatusCode::OK,
            Json(serde_json::json!({
                "created": false,
                "child": child,
                "parent": parent,
            })),
        ));
    }
    rbac::invalidate_sessions(&state.db, &state.cache).await?;
    relate(
        &state,
        rbac::Edge::ChildOf,
        (TABLE, child_key),
        (TABLE, parent_key),
    )
    .await?;
    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &claims.sub,
            action: "group_child_added",
            object_id: Some(&parent),
            from: None,
            to: Some(serde_json::json!({ "child": child })),
            metadata: None,
        },
    )
    .await;
    Ok((
        StatusCode::CREATED,
        Json(serde_json::json!({
            "created": true,
            "child": child,
            "parent": parent,
        })),
    ))
}

/// Removes a child group from the group in the path (`child_of`).
#[utoipa::path(
    delete,
    path = "/groups/{id}/children/{child_id}",
    responses(
        (status = 204, description = "Child unlinked"),
        (status = 404, description = "Unknown group", body = Value)
    )
)]
pub async fn remove_group_child(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path((id, child_id)): Path<(String, String)>,
) -> Result<StatusCode, ApiError> {
    claims.require("group.manage")?;
    let parent_key = parse_key(&id, TABLE)?;
    let child_key = parse_key(&child_id, TABLE)?;
    require_record(&state, TABLE, parent_key).await?;
    let parent = format!("{TABLE}:{parent_key}");
    let child = format!("{TABLE}:{child_key}");
    // Like the member removal: no edge, no change, no logout.
    if rbac::edge_exists(&state.db, rbac::Edge::ChildOf, &child, &parent).await? {
        rbac::invalidate_sessions(&state.db, &state.cache).await?;
        unrelate(
            &state,
            rbac::Edge::ChildOf,
            (TABLE, child_key),
            (TABLE, parent_key),
        )
        .await?;
        audit::record_best_effort(
            &state.db,
            AuditEntry {
                user_id: &claims.sub,
                action: "group_child_removed",
                object_id: Some(&parent),
                from: Some(serde_json::json!({ "child": child })),
                to: None,
                metadata: None,
            },
        )
        .await;
    }
    Ok(StatusCode::NO_CONTENT)
}

/// Every edge of the access model in one answer, for the group dialog.
///
/// The UI builds both directions from this instead of asking per group (`N+1`): which
/// users are in a group and which groups a user belongs to come from the same two lists.
/// Both queries read the whole edge tables — they hold one row per membership, not one
/// per object, so this is a few rows even on a large catalogue.
#[utoipa::path(
    get,
    path = "/memberships",
    responses(
        (status = 200, description = "Memberships and group hierarchy", body = Value),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Forbidden", body = Value)
    )
)]
pub async fn list_memberships(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
) -> Result<Json<Value>, ApiError> {
    claims.require("group.read")?;
    let (memberships, parents) = rbac::edges(&state.db).await?;
    let members: Vec<Value> = memberships
        .into_iter()
        .map(|(user, group)| serde_json::json!({ "user": user, "group": group }))
        .collect();
    let parents: Vec<Value> = parents
        .into_iter()
        .map(|(child, parent)| serde_json::json!({ "child": child, "parent": parent }))
        .collect();
    Ok(Json(serde_json::json!({
        "members": members,
        "parents": parents,
    })))
}
