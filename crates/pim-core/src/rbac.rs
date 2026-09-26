//! RBAC resolution: which groups a user belongs to, and what that grants.
//!
//! The catalog (`crate::permissions`) knows *which* permissions exist and what a role
//! holds by default. This module answers the concrete question: given a stored user,
//! which groups apply and what is the effective set? The plan (section 9) fixes the
//! model — `user → group` is the `member_of` edge, `group → group` the `child_of` edge,
//! the effective set is role defaults **plus** the grants of every group (transitively,
//! including parent groups) **minus** every deny, and `superadmin` bypasses it all.
//!
//! **Cost model.** Resolution runs **once per login** (and on `GET /auth/me`), never per
//! request: the result travels in the JWT, so the request path stays a bit test without
//! any I/O. Each hierarchy level is one query started from an index
//! (`idx_member_of_in` / `idx_child_of_in`) and filtered with a record list literal —
//! `WHERE in IN [group:a, group:b]` drives the index, while the `array::map` shape is
//! evaluated per candidate row (measured 1000 keys: 0,18 s against 16,6 s, see
//! `api::keys::record_list_literal`).
//!
//! The graph walk is **level-wise and cycle-safe** (a `seen` set), like the inheritance
//! and subtree walks of `api::references`: a group hierarchy is small, and a bounded
//! walk cannot be turned into an endless one by bad data.

use std::collections::HashMap;
use std::sync::Arc;

use serde_json::Value;

use crate::api::groups::parse_key;
use crate::api::keys::record_list_literal;
use crate::cache::MetaCache;
use crate::db::Db;
use crate::error::ApiError;
use crate::permissions::{self, PermissionSet, Role};

/// Safety net for the hierarchy walk: a cycle that slipped past the write checks must
/// not turn a login into an endless query (the `seen` set already stops it, this caps
/// the *number of levels* a resolution may descend).
const MAX_GROUP_DEPTH: usize = 16;

/// The two graph edges of the access model.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Edge {
    /// `user → group`: the membership.
    MemberOf,
    /// `group → group`: the hierarchy (`in` is the child, `out` the parent).
    ChildOf,
}

impl Edge {
    /// The table name (never user input, so it may go into a query text).
    pub const fn table(self) -> &'static str {
        match self {
            Edge::MemberOf => "member_of",
            Edge::ChildOf => "child_of",
        }
    }
}

/// The groups a user belongs to and what they add.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GroupGrants {
    /// Group record id (`group:webshop`).
    pub id: String,
    /// Group name, so an answer that names a source does not need a second lookup.
    pub name: String,
    /// Permissions the group grants.
    pub grant: Vec<String>,
    /// Permissions the group denies.
    pub deny: Vec<String>,
}

/// The resolved access of one user.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Access {
    /// Record ids of the groups (direct and inherited), sorted.
    pub groups: Vec<String>,
    /// The effective permission set.
    pub permissions: PermissionSet,
    /// Keys in a `grant`/`deny` list the catalog does not know (reported by the admin UI).
    pub unknown: Vec<String>,
}

/// Role defaults plus the grants of every group, minus every deny.
///
/// **Deny wins over allow, whichever group states it.** The union is built first and the
/// denies are applied afterwards, so the answer cannot depend on the order in which the
/// groups are read — a per-group `union`/`without` would let a later grant undo an earlier
/// deny.
///
/// `superadmin` bypasses the resolution entirely: the role holds every permission and no
/// group can take one away (plan section 9). An unknown **role** contributes nothing
/// (fail-closed); roles are validated on the user write path.
pub fn effective(roles: &[String], groups: &[GroupGrants]) -> (PermissionSet, Vec<String>) {
    if roles
        .iter()
        .any(|role| Role::parse(role) == Some(Role::Superadmin))
    {
        return (PermissionSet::all(), Vec::new());
    }
    let mut granted = PermissionSet::empty();
    for role in roles {
        if let Some(role) = Role::parse(role) {
            granted = granted.union(permissions::role_defaults(role));
        }
    }
    let mut denied = PermissionSet::empty();
    let mut unknown: Vec<String> = Vec::new();
    for group in groups {
        let (group_grant, mut unknown_grant) = permissions::set_from_keys(&group.grant);
        let (group_deny, unknown_deny) = permissions::set_from_keys(&group.deny);
        granted = granted.union(group_grant);
        denied = denied.union(group_deny);
        unknown.append(&mut unknown_grant);
        unknown.extend(unknown_deny);
    }
    unknown.sort();
    unknown.dedup();
    (granted.without(denied), unknown)
}

/// The key part of a record id (`group:webshop` → `webshop`).
pub fn key_of(record_id: &str) -> &str {
    record_id.split_once(':').map_or(record_id, |(_, key)| key)
}

/// Reads the string record ids of a `SELECT VALUE …` answer.
///
/// A SurrealDB `RecordId` arrives as a JSON string here (`"group:webshop"`), the same
/// shape `api::references` reads for its graph walks.
fn ids_from_values(rows: Vec<Value>) -> Vec<String> {
    rows.into_iter()
        .filter_map(|row| row.as_str().map(str::to_owned))
        .collect()
}

/// Reads a JSON string array, ignoring entries of another type.
fn string_list(value: Option<&Value>) -> Vec<String> {
    value
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(|item| item.as_str().map(str::to_owned))
                .collect()
        })
        .unwrap_or_default()
}

/// The parents of the given groups, transitively (level-wise, cycle-safe).
async fn ancestors_of(db: &Db, groups: &[String]) -> Result<Vec<String>, ApiError> {
    let mut result: Vec<String> = Vec::new();
    let mut level: Vec<String> = groups.to_vec();
    for _ in 0..MAX_GROUP_DEPTH {
        if level.is_empty() {
            break;
        }
        let literal = record_list_literal("group", level.iter().map(|id| key_of(id)));
        if literal == "[]" {
            break;
        }
        let mut response = db
            .inner()
            .query(format!(
                "SELECT VALUE out FROM child_of WHERE in IN {literal};"
            ))
            .await?
            .check()?;
        let parents = ids_from_values(response.take(0)?);
        let mut next: Vec<String> = Vec::new();
        for parent in parents {
            if !result.contains(&parent) && !groups.contains(&parent) && !next.contains(&parent) {
                next.push(parent);
            }
        }
        result.extend(next.iter().cloned());
        level = next;
    }
    Ok(result)
}

/// The groups a user belongs to: the direct memberships plus their parent groups.
pub async fn groups_of(db: &Db, user_id: &str) -> Result<Vec<String>, ApiError> {
    let key = parse_key(user_id, "user")?;
    let mut response = db
        .inner()
        .query("SELECT VALUE out FROM member_of WHERE in = type::record('user', $key);")
        .bind(("key", key.to_owned()))
        .await?
        .check()?;
    let mut groups = ids_from_values(response.take(0)?);
    groups.extend(ancestors_of(db, &groups).await?);
    groups.sort();
    groups.dedup();
    Ok(groups)
}

/// The stored grants and denies of the given groups (one query, index-driven).
pub async fn grants_of(db: &Db, groups: &[String]) -> Result<Vec<GroupGrants>, ApiError> {
    if groups.is_empty() {
        return Ok(Vec::new());
    }
    let literal = record_list_literal("group", groups.iter().map(|id| key_of(id)));
    let mut response = db
        .inner()
        .query(format!(
            "SELECT id, name, grant_permissions, deny_permissions FROM group WHERE id IN {literal};"
        ))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    Ok(rows
        .into_iter()
        .map(|row| GroupGrants {
            id: row
                .get("id")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_owned(),
            name: row
                .get("name")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_owned(),
            grant: string_list(row.get("grant_permissions")),
            deny: string_list(row.get("deny_permissions")),
        })
        .collect())
}

/// The access of one stored user: their groups and the effective permission set.
///
/// Called once per login and by `GET /auth/me` — the result travels in the JWT, so the
/// request path never repeats this work.
pub async fn resolve(db: &Db, user_id: &str, roles: &[String]) -> Result<Access, ApiError> {
    let groups = groups_of(db, user_id).await?;
    let grants = grants_of(db, &groups).await?;
    let (permissions, unknown) = effective(roles, &grants);
    Ok(Access {
        groups,
        permissions,
        unknown,
    })
}

/// Whether an edge with exactly these endpoints exists.
pub async fn edge_exists(db: &Db, edge: Edge, from: &str, to: &str) -> Result<bool, ApiError> {
    let (from_table, from_key) = split_id(from)?;
    let (to_table, to_key) = split_id(to)?;
    let table = edge.table();
    let sql = format!(
        "SELECT id FROM {table} WHERE in = type::record('{from_table}', $from) \
         AND out = type::record('{to_table}', $to) LIMIT 1;"
    );
    let mut response = db
        .inner()
        .query(sql)
        .bind(("from", from_key.to_owned()))
        .bind(("to", to_key.to_owned()))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    Ok(!rows.is_empty())
}

/// Whether linking `parent` above `child` would close a cycle in the group hierarchy.
///
/// A hierarchy that contains a cycle is not merely wrong data: it makes every later
/// resolution longer than the data deserves and makes "who inherits from whom"
/// unanswerable. The check walks up from the prospective parent — reaching the child
/// (or the parent itself) means the new edge would close the loop.
pub async fn would_cycle(db: &Db, child: &str, parent: &str) -> Result<bool, ApiError> {
    if child == parent {
        return Ok(true);
    }
    Ok(ancestors_of(db, &[parent.to_owned()])
        .await?
        .iter()
        .any(|ancestor| ancestor == child))
}

/// The users that belong to a group (one query on `idx_member_of_out`).
pub async fn members_of(db: &Db, group_id: &str) -> Result<Vec<String>, ApiError> {
    let key = parse_key(group_id, "group")?;
    let mut response = db
        .inner()
        .query("SELECT VALUE in FROM member_of WHERE out = type::record('group', $key);")
        .bind(("key", key.to_owned()))
        .await?
        .check()?;
    let mut members = ids_from_values(response.take(0)?);
    members.sort();
    members.dedup();
    Ok(members)
}

/// Every edge of the access model, for the admin UI (two queries, no N+1).
///
/// `(memberships, parents)`: the `member_of` pairs `(user, group)` and the `child_of`
/// pairs `(child, parent)`. The UI builds both directions of the group dialog from this
/// one answer instead of asking per group.
pub async fn edges(db: &Db) -> Result<(Vec<(String, String)>, Vec<(String, String)>), ApiError> {
    let mut memberships: Vec<(String, String)> = Vec::new();
    let mut parents: Vec<(String, String)> = Vec::new();
    for (edge, target) in [
        (Edge::MemberOf, &mut memberships),
        (Edge::ChildOf, &mut parents),
    ] {
        let mut response = db
            .inner()
            .query(format!("SELECT in, out FROM {};", edge.table()))
            .await?
            .check()?;
        let rows: Vec<Value> = response.take(0)?;
        for row in rows {
            let from = row.get("in").and_then(Value::as_str);
            let to = row.get("out").and_then(Value::as_str);
            if let (Some(from), Some(to)) = (from, to) {
                target.push((from.to_owned(), to.to_owned()));
            }
        }
    }
    memberships.sort();
    parents.sort();
    Ok((memberships, parents))
}

/// Reads a stored counter value (SurrealDB numbers arrive as JSON numbers).
///
/// `0` and negative values count as *not initialised*: see [`auth_version`].
fn stored_version(value: &Value) -> Option<u64> {
    value
        .as_u64()
        .or_else(|| value.as_i64().and_then(|number| u64::try_from(number).ok()))
        .filter(|version| *version > 0)
}

/// Answers a counter from an update answer, whether it arrives as a bare value or as a
/// record.
fn version_from_rows(rows: &[Value]) -> Option<u64> {
    rows.first().and_then(|row| {
        stored_version(row).or_else(|| row.get("auth_version").and_then(stored_version))
    })
}

/// The stored access version, creating the counter on the first start.
///
/// The value starts at **1**, and `0` means "no version": a token that carries `0`
/// (issued before this contract, or against a database without the counter) is rejected
/// instead of being treated as the first version. That is what makes the rule fail-closed
/// — the first request after a deployment logs the session in again instead of letting a
/// token with an empty permission set through.
pub async fn auth_version(db: &Db) -> Result<u64, ApiError> {
    let mut response = db
        .inner()
        .query("SELECT VALUE auth_version FROM access_state:state;")
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    if let Some(version) = rows.first().and_then(stored_version) {
        return Ok(version);
    }
    // The record id is fixed, so a second start (or a retry) cannot create a second
    // counter row.
    db.inner()
        .query("UPSERT access_state:state CONTENT { auth_version: 1 };")
        .await?
        .check()?;
    Ok(1)
}

/// Raises the stored access version and answers the new value.
///
/// Called by [`invalidate_sessions`]; separate so the bump stays testable and the caller
/// never has to know the table.
async fn bump_auth_version(db: &Db) -> Result<u64, ApiError> {
    let mut response = db
        .inner()
        .query("UPDATE access_state:state SET auth_version += 1 RETURN AFTER;")
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    match version_from_rows(&rows) {
        Some(version) => Ok(version),
        // An unparseable answer must not become a guessed version: read the stored one
        // so the published counter always matches the database.
        None => auth_version(db).await,
    }
}

/// Ends every issued session: raises the stored access version and publishes it.
///
/// Call this **before** the write it protects. If the write then fails, the only cost is
/// an unnecessary login; the other order would let a successful change leave a revoked
/// right alive in an already issued token — the fail-safe direction is the one that may
/// log someone out too often, never the one that keeps a right it should not.
///
/// It does log out the caller as well (their token carries the old version); the password
/// change already answers with a fresh token, the admin editors can adopt that pattern
/// when they need it.
pub async fn invalidate_sessions(db: &Db, cache: &MetaCache) -> Result<u64, ApiError> {
    let version = bump_auth_version(db).await?;
    cache.set_auth_version(version);
    Ok(version)
}

/// The scopes of the given groups, cached on the access version.
///
/// Answers one entry per group id — a group without a stored scope (or one this catalogue
/// does not know, e.g. a deleted group) counts as *no rules*, which is the truth for a group
/// that restricts nothing and the safe reading for a group that is gone: it cannot widen a
/// view. The whole table is loaded in one query and cached, so the read path never asks per
/// group.
pub async fn group_scopes(
    db: &Db,
    cache: &MetaCache,
    groups: &[String],
) -> Result<Vec<crate::scope::Rules>, ApiError> {
    let version = cache.auth_version();
    let all = match cache.cached_group_scopes(version).await {
        Some(all) => all,
        None => {
            let mut response = db
                .inner()
                .query("SELECT id, scope FROM group;")
                .await?
                .check()?;
            let rows: Vec<Value> = response.take(0)?;
            let mut map: HashMap<String, crate::scope::Rules> = HashMap::new();
            for row in rows {
                let Some(id) = row.get("id").and_then(Value::as_str) else {
                    continue;
                };
                // A stored scope that does not parse (an old document, a hand-edited row) must
                // not lock a user out of the whole catalogue: it is read as "no rules". The
                // write path validates before storing, so this is the path of last resort.
                let rules = row
                    .get("scope")
                    .map(crate::scope::parse)
                    .and_then(Result::ok)
                    .unwrap_or_default();
                map.insert(id.to_owned(), rules);
            }
            let all: Arc<HashMap<String, crate::scope::Rules>> = Arc::new(map);
            cache.store_group_scopes(version, all.clone()).await;
            all
        }
    };
    Ok(groups
        .iter()
        .map(|id| all.get(id).cloned().unwrap_or_default())
        .collect())
}

/// Splits a `table:key` id and answers both parts.
fn split_id(id: &str) -> Result<(&str, &str), ApiError> {
    let (table, key) = id
        .split_once(':')
        .ok_or_else(|| ApiError::bad_request(format!("invalid id `{id}`")))?;
    if table.is_empty() || key.is_empty() {
        return Err(ApiError::bad_request(format!("invalid id `{id}`")));
    }
    Ok((table, key))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn grants(id: &str, grant: &[&str], deny: &[&str]) -> GroupGrants {
        GroupGrants {
            id: id.to_owned(),
            name: id.to_owned(),
            grant: grant.iter().map(|key| (*key).to_owned()).collect(),
            deny: deny.iter().map(|key| (*key).to_owned()).collect(),
        }
    }

    fn roles(roles: &[&str]) -> Vec<String> {
        roles.iter().map(|role| (*role).to_owned()).collect()
    }

    #[test]
    fn a_group_grant_extends_the_role_defaults() {
        let (set, unknown) = effective(
            &roles(&["editor"]),
            &[grants("group:webshop", &["object.publish"], &[])],
        );
        assert!(set.can("object.update"), "the role default stays");
        assert!(set.can("object.publish"), "the grant is added");
        assert!(unknown.is_empty());
    }

    #[test]
    fn a_deny_beats_a_grant_whichever_group_states_it() {
        let deny_first = effective(
            &roles(&["editor"]),
            &[
                grants("group:a", &[], &["object.publish"]),
                grants("group:b", &["object.publish"], &[]),
            ],
        )
        .0;
        let deny_last = effective(
            &roles(&["editor"]),
            &[
                grants("group:b", &["object.publish"], &[]),
                grants("group:a", &[], &["object.publish"]),
            ],
        )
        .0;
        assert!(!deny_first.can("object.publish"), "allow then deny");
        assert_eq!(deny_first, deny_last, "the order must not matter");
    }

    #[test]
    fn a_deny_removes_a_role_default_too() {
        // The plan allows a group to take a permission away from a role — that is the
        // whole point of an explicit deny list.
        let set = effective(
            &roles(&["editor"]),
            &[grants("group:a", &[], &["object.delete"])],
        )
        .0;
        assert!(!set.can("object.delete"));
        assert!(set.can("object.update"));
    }

    #[test]
    fn a_superadmin_cannot_be_denied() {
        let (set, unknown) = effective(
            &roles(&["superadmin"]),
            &[grants("group:a", &[], &["system.manage", "object.read"])],
        );
        assert_eq!(set, PermissionSet::all());
        assert!(unknown.is_empty());
    }

    #[test]
    fn unknown_keys_are_reported_and_grant_nothing() {
        let (set, unknown) = effective(
            &roles(&["viewer"]),
            &[grants("group:a", &["object.teleport"], &["object.publish"])],
        );
        assert!(!set.can("object.teleport"));
        assert!(
            !set.can("object.publish"),
            "a deny on an unknown grant is silent"
        );
        assert_eq!(unknown, vec!["object.teleport".to_owned()]);
    }

    #[test]
    fn unknown_roles_grant_nothing() {
        let (set, _) = effective(&roles(&["intern", "editor"]), &[]);
        assert!(set.can("object.update"));
        assert_eq!(set, permissions::role_defaults(Role::Editor));
    }

    #[test]
    fn keys_and_ids_round_trip() {
        assert_eq!(key_of("group:webshop"), "webshop");
        assert_eq!(key_of("webshop"), "webshop");
        assert_eq!(split_id("user:42").expect("valid").1, "42");
        assert_eq!(split_id("user:42").expect("valid").0, "user");
        assert!(split_id("user").is_err());
        assert!(split_id(":").is_err());
    }
}
