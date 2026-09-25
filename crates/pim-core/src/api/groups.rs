//! Group management handlers (admin/superadmin only).

use axum::Json;
use axum::extract::{Path, Query, State};
use axum::http::StatusCode;
use serde::Deserialize;
use serde_json::Value;
use utoipa::{IntoParams, ToSchema};

use crate::app::AppState;
use crate::audit::{self, AuditEntry};
use crate::auth::AuthUser;
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

fn require_admin(claims: &crate::auth::Claims) -> Result<(), ApiError> {
    if claims.is_admin() {
        Ok(())
    } else {
        Err(ApiError::forbidden("requires admin role"))
    }
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
    require_admin(&claims)?;
    if body.name.trim().is_empty() {
        return Err(ApiError::bad_request("`name` must not be empty"));
    }
    reject_unknown_permissions(
        crate::permissions::unknown_keys(&body.grant_permissions)
            .into_iter()
            .chain(crate::permissions::unknown_keys(&body.deny_permissions))
            .collect(),
    )?;
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
    require_admin(&claims)?;
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
    require_admin(&claims)?;
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
    require_admin(&claims)?;
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

    let mut changes = serde_json::Map::new();
    if let Some(name) = body.name {
        changes.insert("name".to_owned(), Value::String(name));
    }
    if let Some(grant_permissions) = body.grant_permissions {
        changes.insert(
            "grant_permissions".to_owned(),
            Value::Array(grant_permissions.into_iter().map(Value::String).collect()),
        );
    }
    if let Some(deny_permissions) = body.deny_permissions {
        changes.insert(
            "deny_permissions".to_owned(),
            Value::Array(deny_permissions.into_iter().map(Value::String).collect()),
        );
    }
    if let Some(scope) = body.scope {
        changes.insert("scope".to_owned(), scope);
    }
    if let Some(branding) = body.branding {
        changes.insert("branding".to_owned(), branding);
    }
    let changes = Value::Object(changes);
    // The audit names the changed fields, not their values: a scope document can be large
    // and the durable history is about *what* moved, not a second copy of the data.
    let fields: Vec<String> = changes
        .as_object()
        .map(|map| map.keys().cloned().collect())
        .unwrap_or_default();

    rbac::invalidate_sessions(&state.db, &state.cache).await?;
    let sql = "
        LET $g = type::record('group', $key);
        UPDATE $g MERGE $changes;
        SELECT * FROM $g;
    ";
    let mut response = state
        .db
        .inner()
        .query(sql)
        .bind(("key", key))
        .bind(("changes", changes))
        .await?
        .check()?;
    let groups: Vec<Value> = response.take(2)?;
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
    require_admin(&claims)?;
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
    require_admin(&claims)?;
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
    require_admin(&claims)?;
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
    require_admin(&claims)?;
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
    require_admin(&claims)?;
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
    require_admin(&claims)?;
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
