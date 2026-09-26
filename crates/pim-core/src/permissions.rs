//! The permission catalog: which permissions exist, and which role holds them.
//!
//! **Why this module exists:** the permissions of `plan/detail/benutzer-gruppen-rechte.md`
//! (§„Berechtigungen") existed only as prose. `group.grant_permissions` and
//! `group.deny_permissions` were free-form string arrays — nothing rejected a typo,
//! nothing could tell the UI which permissions to offer, and an AI assistant had no
//! source of truth (the same gap `job_schema.rs` and `connector_schema.rs` close for
//! job parameters and connector parameters). This module is that source of truth:
//! one table, read by the write path (validation), by the login path (the effective
//! set), by `GET /permissions` (the UI matrix) and later by the AI area.
//!
//! **The catalog is append-only.** A permission's `index` is its bit position in a
//! [`PermissionSet`], and the bit set travels in the JWT (see `auth::Claims::perms`).
//! Reordering or reusing an index would silently give an old token a *different*
//! meaning — a removed permission's bit stays reserved, it is never handed to a new
//! permission. Unknown keys are ignored on the read path (fail-closed: an unknown key
//! grants nothing) and reported on the write path.
//!
//! **Why a bit set and not a list of strings:** authorization runs on every request,
//! and the core answers one in microseconds today (the JWT is verified in memory, no
//! database round trip — see `auth::AuthUser`). A set of 26 strings would mean a lookup
//! or an allocation per check; a fixed `[u64; WORDS]` makes `can()` a bit test, so the
//! whole check stays allocation-free. The set is small, `Copy`, and lives in the claims.
//!
//! **Role defaults are the `min_role`.** `admin` is a special form of `editor`, so a
//! permission belongs to the lowest role that holds it and every higher role inherits
//! it — the same hierarchy the core enforced before this catalog existed
//! (`auth::Claims::is_admin`/`is_editor`). Stage B replaces those role checks with
//! `can(...)` calls; the mapping here is what keeps the behaviour identical for users
//! without groups, so every changed call site must be checked against it.

use serde_json::{Value, json};

use crate::auth::{ROLE_ADMIN, ROLE_EDITOR, ROLE_SUPERADMIN, ROLE_VIEWER};

/// The four built-in roles of the plan, ordered by rank.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum Role {
    Viewer,
    Editor,
    Admin,
    Superadmin,
}

impl Role {
    /// Every role, weakest first.
    pub const ALL: [Role; 4] = [Role::Viewer, Role::Editor, Role::Admin, Role::Superadmin];

    /// The stored name (matches the constants in [`crate::auth`]).
    pub const fn as_str(self) -> &'static str {
        match self {
            Role::Viewer => ROLE_VIEWER,
            Role::Editor => ROLE_EDITOR,
            Role::Admin => ROLE_ADMIN,
            Role::Superadmin => ROLE_SUPERADMIN,
        }
    }

    /// Parses a stored role name (case-sensitive, like the rest of the core).
    pub fn parse(value: &str) -> Option<Role> {
        Role::ALL.into_iter().find(|role| role.as_str() == value)
    }

    /// The frontend translation key of the role (`locales/*.json`).
    pub const fn label_key(self) -> &'static str {
        match self {
            Role::Viewer => "role.viewer",
            Role::Editor => "role.editor",
            Role::Admin => "role.admin",
            Role::Superadmin => "role.superadmin",
        }
    }
}

/// One permission of the catalog.
#[derive(Debug, Clone, Copy)]
pub struct Permission {
    /// The stored key (`<area>.<action>`, e.g. `object.publish`).
    pub key: &'static str,
    /// The area the permission belongs to (the matrix groups by it).
    pub area: &'static str,
    /// The action inside the area.
    pub action: &'static str,
    /// Bit position in a [`PermissionSet`] — append-only, never reused.
    pub index: u16,
    /// The frontend translation key (`locales/*.json`).
    pub label_key: &'static str,
    /// One sentence for developers and the AI context (English, like all source).
    pub description: &'static str,
    /// The lowest role that holds this permission by default.
    pub min_role: Role,
}

/// Every permission of the core, in bit order (append-only, see the module docs).
pub const PERMISSIONS: &[Permission] = &[
    Permission {
        key: "object.read",
        area: "object",
        action: "read",
        index: 0,
        label_key: "perm.object.read",
        description: "Read objects, their attribute values, releases and references.",
        min_role: Role::Viewer,
    },
    Permission {
        key: "object.create",
        area: "object",
        action: "create",
        index: 1,
        label_key: "perm.object.create",
        description: "Create objects, single, in batches and through imports.",
        min_role: Role::Editor,
    },
    Permission {
        key: "object.update",
        area: "object",
        action: "update",
        index: 2,
        label_key: "perm.object.update",
        description: "Change objects, attribute values and the workflow status.",
        min_role: Role::Editor,
    },
    Permission {
        key: "object.delete",
        area: "object",
        action: "delete",
        index: 3,
        label_key: "perm.object.delete",
        description: "Delete objects, single, in batches and as an import cleanup.",
        min_role: Role::Editor,
    },
    Permission {
        key: "object.publish",
        area: "object",
        action: "publish",
        index: 4,
        label_key: "perm.object.publish",
        description: "Release objects for a channel and withdraw those releases.",
        min_role: Role::Editor,
    },
    Permission {
        key: "reference.read",
        area: "reference",
        action: "read",
        index: 5,
        label_key: "perm.reference.read",
        description: "Read references and the composition children of an object.",
        min_role: Role::Viewer,
    },
    Permission {
        key: "reference.create",
        area: "reference",
        action: "create",
        index: 6,
        label_key: "perm.reference.create",
        description: "Create references and re-parent an object.",
        min_role: Role::Editor,
    },
    Permission {
        key: "reference.delete",
        area: "reference",
        action: "delete",
        index: 7,
        label_key: "perm.reference.delete",
        description: "Delete a reference.",
        min_role: Role::Editor,
    },
    Permission {
        key: "attribute_definition.read",
        area: "attribute_definition",
        action: "read",
        index: 8,
        label_key: "perm.attribute_definition.read",
        description: "Read attribute definitions, groups, enum sets and units.",
        min_role: Role::Viewer,
    },
    Permission {
        key: "attribute_definition.manage",
        area: "attribute_definition",
        action: "manage",
        index: 9,
        label_key: "perm.attribute_definition.manage",
        description: "Create, change and delete attribute definitions, enum sets and units.",
        min_role: Role::Admin,
    },
    Permission {
        key: "business_rule.read",
        area: "business_rule",
        action: "read",
        index: 10,
        label_key: "perm.business_rule.read",
        description: "Read business rules and their state.",
        min_role: Role::Viewer,
    },
    Permission {
        key: "business_rule.manage",
        area: "business_rule",
        action: "manage",
        index: 11,
        label_key: "perm.business_rule.manage",
        description: "Create, change, activate and dry-run business rules.",
        min_role: Role::Admin,
    },
    Permission {
        key: "attribute_group.manage",
        area: "attribute_group",
        action: "manage",
        index: 12,
        label_key: "perm.attribute_group.manage",
        description: "Create, change and delete attribute groups.",
        min_role: Role::Admin,
    },
    Permission {
        key: "language_tree.read",
        area: "language_tree",
        action: "read",
        index: 13,
        label_key: "perm.language_tree.read",
        description: "Read the language tree and the active languages.",
        min_role: Role::Viewer,
    },
    Permission {
        key: "language_tree.manage",
        area: "language_tree",
        action: "manage",
        index: 14,
        label_key: "perm.language_tree.manage",
        description: "Create, change and delete languages.",
        min_role: Role::Admin,
    },
    Permission {
        key: "job.read",
        area: "job",
        action: "read",
        index: 15,
        label_key: "perm.job.read",
        description: "Read job definitions and their runs.",
        min_role: Role::Viewer,
    },
    Permission {
        key: "job.run",
        area: "job",
        action: "run",
        index: 16,
        label_key: "perm.job.run",
        description: "Start, rerun and cancel a job run.",
        // **Editor, not admin** (decision 2026-09-26): the code let every editor start a run,
        // and the plan's table said `admin`. Running a job is a data operation on the catalogue
        // (export, import, bulk, recompute), not administration — and a group can still take it
        // away from an editor with `deny_permissions`.
        min_role: Role::Editor,
    },
    Permission {
        key: "job.manage",
        area: "job",
        action: "manage",
        index: 17,
        label_key: "perm.job.manage",
        description: "Create, change and delete job definitions.",
        min_role: Role::Admin,
    },
    Permission {
        key: "audit.read",
        area: "audit",
        action: "read",
        index: 18,
        label_key: "perm.audit.read",
        description: "Read the audit log.",
        min_role: Role::Admin,
    },
    Permission {
        key: "log.read",
        area: "log",
        action: "read",
        index: 19,
        label_key: "perm.log.read",
        description: "Read technical system logs and the runtime settings.",
        min_role: Role::Admin,
    },
    Permission {
        key: "log.manage",
        area: "log",
        action: "manage",
        index: 20,
        label_key: "perm.log.manage",
        description: "Change the logging settings and delete log entries.",
        min_role: Role::Admin,
    },
    Permission {
        key: "user.read",
        area: "user",
        action: "read",
        index: 21,
        label_key: "perm.user.read",
        description: "Read users.",
        min_role: Role::Admin,
    },
    Permission {
        key: "user.manage",
        area: "user",
        action: "manage",
        index: 22,
        label_key: "perm.user.manage",
        description: "Create, change, reset the password of and delete users.",
        min_role: Role::Admin,
    },
    Permission {
        key: "group.read",
        area: "group",
        action: "read",
        index: 23,
        label_key: "perm.group.read",
        description: "Read groups, their permissions and their scopes.",
        min_role: Role::Admin,
    },
    Permission {
        key: "group.manage",
        area: "group",
        action: "manage",
        index: 24,
        label_key: "perm.group.manage",
        description: "Create, change and delete groups, their members and their scopes.",
        min_role: Role::Admin,
    },
    Permission {
        key: "system.manage",
        area: "system",
        action: "manage",
        index: 25,
        label_key: "perm.system.manage",
        description: "Change system-wide configuration reserved for the superadmin.",
        min_role: Role::Superadmin,
    },
    // The areas below were discovered while the enforcement was built (stage B, 2026-09-26):
    // the plan's list did not name them, the code guarded them with an admin check. They keep
    // the admin default so the change stays behaviour-preserving — and because a grant can add
    // them to any role, a group can still hand one to an editor.
    Permission {
        key: "channel.manage",
        area: "channel",
        action: "manage",
        index: 26,
        label_key: "perm.channel.manage",
        description: "Create, change and delete channels.",
        min_role: Role::Admin,
    },
    Permission {
        key: "integration.manage",
        area: "integration",
        action: "manage",
        index: 27,
        label_key: "perm.integration.manage",
        description: "Manage connectors, export profiles and the configuration import.",
        min_role: Role::Admin,
    },
    Permission {
        key: "search.manage",
        area: "search",
        action: "manage",
        index: 28,
        label_key: "perm.search.manage",
        description: "Create, change and delete saved searches and collections.",
        min_role: Role::Admin,
    },
    Permission {
        key: "ai.use",
        area: "ai",
        action: "use",
        index: 29,
        label_key: "perm.ai.use",
        description: "Ask the AI assistance for a suggestion.",
        min_role: Role::Admin,
    },
];

/// Number of permissions in the catalog.
pub const PERMISSION_COUNT: usize = PERMISSIONS.len();

/// The 64-bit words a [`PermissionSet`] needs.
pub const WORDS: usize = PERMISSION_COUNT.div_ceil(64);

// The catalog must fit the bit set — `insert`/`contains` index into the words. A
// permission past the last word would panic on the request path, so the limit is a
// compile-time invariant, not a test.
const _: () = assert!(PERMISSION_COUNT > 0);
const _: () = assert!(PERMISSION_COUNT <= WORDS * 64);

/// Looks a permission up by its stored key.
pub fn find(key: &str) -> Option<&'static Permission> {
    PERMISSIONS.iter().find(|permission| permission.key == key)
}

/// A set of permissions as a bit field (see the module docs for the append-only rule).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PermissionSet([u64; WORDS]);

impl Default for PermissionSet {
    fn default() -> Self {
        Self::empty()
    }
}

impl PermissionSet {
    /// A set without any permission.
    pub const fn empty() -> Self {
        Self([0; WORDS])
    }

    /// Every permission of the catalog (the `superadmin` default, not a bypass).
    pub fn all() -> Self {
        let mut set = Self::empty();
        for permission in PERMISSIONS {
            set.insert(permission);
        }
        set
    }

    /// Adds a permission.
    pub fn insert(&mut self, permission: &Permission) -> &mut Self {
        let index = permission.index as usize;
        self.0[index / 64] |= 1 << (index % 64);
        self
    }

    /// Removes a permission.
    pub fn remove(&mut self, permission: &Permission) -> &mut Self {
        let index = permission.index as usize;
        self.0[index / 64] &= !(1 << (index % 64));
        self
    }

    /// Whether the set carries this permission.
    pub fn contains(&self, permission: &Permission) -> bool {
        let index = permission.index as usize;
        self.0[index / 64] & (1 << (index % 64)) != 0
    }

    /// Whether the set carries the permission with this key.
    ///
    /// **Fail-closed:** an unknown key (a permission this catalog version does not
    /// know, or a typo) is answered with `false` — it grants nothing.
    pub fn can(&self, key: &str) -> bool {
        find(key).is_some_and(|permission| self.contains(permission))
    }

    /// Union of two sets ("allow wins" for the grants of role and groups).
    pub fn union(self, other: Self) -> Self {
        let mut words = self.0;
        for (word, extra) in words.iter_mut().zip(other.0) {
            *word |= extra;
        }
        Self(words)
    }

    /// The permissions of `self` without the permissions of `denied` (explicit deny).
    pub fn without(self, denied: Self) -> Self {
        let mut words = self.0;
        for (word, remove) in words.iter_mut().zip(denied.0) {
            *word &= !remove;
        }
        Self(words)
    }

    /// Whether the set carries every permission of `other`.
    pub fn is_superset_of(self, other: Self) -> bool {
        self.0
            .iter()
            .zip(other.0)
            .all(|(word, required)| word & required == required)
    }

    /// Whether the set is empty.
    pub fn is_empty(self) -> bool {
        self.0.iter().all(|word| *word == 0)
    }

    /// The keys of every permission in the set (catalog order).
    ///
    /// Used for the answer of `GET /auth/me` and the UI, not on the request path.
    pub fn keys(self) -> Vec<&'static str> {
        PERMISSIONS
            .iter()
            .filter(|permission| self.contains(permission))
            .map(|permission| permission.key)
            .collect()
    }

    /// The bit words as stored in the JWT.
    pub fn words(&self) -> &[u64] {
        &self.0
    }

    /// Rebuilds a set from the stored words.
    ///
    /// **Fail-closed:** missing words count as zero, and bits beyond the catalog are
    /// dropped — a token from a newer core can therefore only ever grant *less* here.
    pub fn from_words(words: &[u64]) -> Self {
        let mut set = Self::empty();
        for (word, stored) in set.0.iter_mut().zip(words) {
            *word = *stored;
        }
        set.mask_unknown_bits();
        set
    }

    /// Clears the bits of the last word that belong to no permission, so a set built
    /// from stored words is as canonical as a set built from the catalog (two sets are
    /// equal exactly when they carry the same permissions).
    fn mask_unknown_bits(&mut self) {
        let used = PERMISSION_COUNT % 64;
        if used != 0 {
            let last = self.0.len() - 1;
            self.0[last] &= (1u64 << used) - 1;
        }
    }
}

/// The permissions a role holds by default.
pub fn role_defaults(role: Role) -> PermissionSet {
    let mut set = PermissionSet::empty();
    for permission in PERMISSIONS {
        if permission.min_role <= role {
            set.insert(permission);
        }
    }
    set
}

/// Reads a stored key list into a set and answers the keys the catalog does not know.
///
/// The two answers serve the two sides of the contract: the set is what the read path
/// uses (unknown keys grant nothing), the unknown list is what the write path reports.
pub fn set_from_keys(keys: &[String]) -> (PermissionSet, Vec<String>) {
    let mut set = PermissionSet::empty();
    let mut unknown = Vec::new();
    for key in keys {
        match find(key.trim()) {
            Some(permission) => {
                set.insert(permission);
            }
            None => unknown.push(key.clone()),
        }
    }
    (set, unknown)
}

/// The keys of a stored list that the catalog does not know.
///
/// The write path rejects them: the read path ignores an unknown key (fail-closed), so
/// accepting one would store a permission that looks granted and does nothing — the typo
/// would only ever surface as "the group has no effect".
pub fn unknown_keys(keys: &[String]) -> Vec<String> {
    set_from_keys(keys).1
}

/// The areas of the catalog in first-appearance order, with their label keys.
fn areas() -> Vec<Value> {
    let mut areas: Vec<Value> = Vec::new();
    for permission in PERMISSIONS {
        let known = areas
            .iter()
            .any(|area| area["key"].as_str() == Some(permission.area));
        if !known {
            areas.push(json!({
                "key": permission.area,
                "label_key": format!("perm.area.{}", permission.area),
            }));
        }
    }
    areas
}

/// The catalog as the `GET /permissions` document.
///
/// Every permission carries the **expanded** roles (not the `min_role`): the UI
/// renders the three states of the matrix (inherited from the role, granted by a
/// group, denied by a group) without having to know the role hierarchy.
pub fn document() -> Value {
    let roles: Vec<Value> = Role::ALL
        .iter()
        .map(|role| {
            json!({
                "key": role.as_str(),
                "label_key": role.label_key(),
            })
        })
        .collect();
    let permissions: Vec<Value> = PERMISSIONS
        .iter()
        .map(|permission| {
            let roles: Vec<&str> = Role::ALL
                .iter()
                .filter(|role| permission.min_role <= **role)
                .map(|role| role.as_str())
                .collect();
            json!({
                "key": permission.key,
                "area": permission.area,
                "action": permission.action,
                "index": permission.index,
                "roles": roles,
                "label_key": permission.label_key,
                "description": permission.description,
            })
        })
        .collect();
    json!({
        "roles": roles,
        "areas": areas(),
        "permissions": permissions,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_catalog_is_consistent() {
        let mut keys: Vec<&str> = Vec::new();
        for (position, permission) in PERMISSIONS.iter().enumerate() {
            assert_eq!(
                permission.index as usize, position,
                "`{}` must keep its bit position",
                permission.key
            );
            assert!(
                !permission.description.is_empty(),
                "`{}` has no description",
                permission.key
            );
            assert!(
                permission.label_key.starts_with("perm."),
                "`{}` has no label key",
                permission.key
            );
            assert_eq!(
                permission.key,
                format!("{}.{}", permission.area, permission.action),
                "`{}` does not match its area and action",
                permission.key
            );
            keys.push(permission.key);
        }
        let declared = keys.len();
        keys.sort_unstable();
        keys.dedup();
        assert_eq!(declared, keys.len(), "duplicate permission key");
    }

    #[test]
    fn the_bit_set_holds_the_whole_catalog() {
        // `all` must carry every declared permission, `empty` none of them.
        let all = PermissionSet::all();
        assert_eq!(all.keys().len(), PERMISSION_COUNT);
        assert!(PermissionSet::empty().keys().is_empty());
    }

    #[test]
    fn the_role_hierarchy_gives_every_higher_role_the_lower_set() {
        for pair in Role::ALL.windows(2) {
            let lower = role_defaults(pair[0]);
            let higher = role_defaults(pair[1]);
            assert!(
                higher.is_superset_of(lower),
                "`{}` must hold everything `{}` holds",
                pair[1].as_str(),
                pair[0].as_str()
            );
        }
        assert_eq!(role_defaults(Role::Superadmin), PermissionSet::all());
    }

    #[test]
    fn the_role_defaults_match_the_plan() {
        let viewer = role_defaults(Role::Viewer);
        let editor = role_defaults(Role::Editor);
        let admin = role_defaults(Role::Admin);
        // Read is for everyone, writing is not.
        assert!(viewer.can("object.read"));
        assert!(!viewer.can("object.create"));
        assert!(!viewer.can("object.publish"));
        assert!(editor.can("object.update"));
        assert!(!editor.can("attribute_definition.manage"));
        assert!(!editor.can("business_rule.manage"));
        assert!(!editor.can("log.read"));
        assert!(admin.can("attribute_definition.manage"));
        assert!(admin.can("user.manage"));
        // `job.run` is editor since the enforcement was built (decision 1b, 2026-09-26).
        assert!(admin.can("job.run"));
        assert!(editor.can("job.run"));
        // The areas the plan did not name keep the admin default.
        assert!(!editor.can("channel.manage"));
        assert!(!editor.can("integration.manage"));
        assert!(!editor.can("search.manage"));
        assert!(!editor.can("ai.use"));
        assert!(admin.can("channel.manage"));
        // `system.manage` stays with the superadmin alone.
        assert!(!admin.can("system.manage"));
        assert!(role_defaults(Role::Superadmin).can("system.manage"));
    }

    #[test]
    fn unknown_keys_grant_nothing_and_are_reported() {
        let keys = vec![
            "object.read".to_owned(),
            " object.publish ".to_owned(),
            "object.teleport".to_owned(),
        ];
        let (set, unknown) = set_from_keys(&keys);
        assert!(set.can("object.read"));
        assert!(set.can("object.publish"), "a trimmed key is accepted");
        assert_eq!(unknown, vec!["object.teleport".to_owned()]);
        // Fail-closed on the read path as well.
        assert!(!set.can("object.teleport"));
        assert!(!PermissionSet::empty().can("object.read"));
    }

    #[test]
    fn deny_removes_a_granted_permission() {
        let granted =
            role_defaults(Role::Editor).union(set_from_keys(&["object.publish".into()]).0);
        assert!(granted.can("object.publish"));
        let denied = set_from_keys(&["object.publish".into()]).0;
        assert!(!granted.without(denied).can("object.publish"));
        assert!(granted.without(denied).can("object.read"));
    }

    #[test]
    fn the_words_round_trip() {
        let set = role_defaults(Role::Admin);
        assert_eq!(PermissionSet::from_words(set.words()), set);
        // A set whose last permission is beyond the first word survives.
        let last = PERMISSIONS.last().expect("the catalog is not empty");
        let mut only_last = PermissionSet::empty();
        only_last.insert(last);
        assert_eq!(PermissionSet::from_words(only_last.words()), only_last);
        assert!(only_last.can(last.key));
        // Missing words count as zero, extra bits are dropped (fail-closed).
        assert!(PermissionSet::from_words(&[]).is_empty());
        assert_eq!(
            PermissionSet::from_words(&[u64::MAX; WORDS]),
            PermissionSet::all()
        );
    }

    #[test]
    fn the_role_names_agree_with_the_auth_constants() {
        assert_eq!(Role::Viewer.as_str(), ROLE_VIEWER);
        assert_eq!(Role::Editor.as_str(), ROLE_EDITOR);
        assert_eq!(Role::Admin.as_str(), ROLE_ADMIN);
        assert_eq!(Role::Superadmin.as_str(), ROLE_SUPERADMIN);
        assert_eq!(Role::parse("admin"), Some(Role::Admin));
        assert_eq!(Role::parse("root"), None);
    }

    #[test]
    fn the_document_lists_every_permission_with_its_roles() {
        let document = document();
        let permissions = document["permissions"].as_array().expect("array");
        assert_eq!(permissions.len(), PERMISSION_COUNT);
        assert_eq!(document["roles"].as_array().expect("array").len(), 4);
        // Every area appears once, and every permission of the document points at one.
        let areas = document["areas"].as_array().expect("array");
        for permission in permissions {
            let area = permission["area"].as_str().expect("area");
            assert!(
                areas
                    .iter()
                    .any(|entry| entry["key"].as_str() == Some(area)),
                "area `{area}` is missing from the document"
            );
            assert!(!permission["label_key"].as_str().expect("label").is_empty());
            assert!(!permission["description"].as_str().expect("text").is_empty());
            let roles = permission["roles"].as_array().expect("roles");
            assert!(!roles.is_empty(), "`{}` has no role", permission["key"]);
            // The expanded roles start at the lowest role that holds it.
            let first = roles[0].as_str().expect("role");
            let min = find(permission["key"].as_str().expect("key"))
                .expect("known permission")
                .min_role;
            assert_eq!(first, min.as_str());
            let from_min = Role::ALL.iter().filter(|role| min <= **role).count();
            assert_eq!(roles.len(), from_min);
        }
    }
}
