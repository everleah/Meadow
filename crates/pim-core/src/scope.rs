//! ABAC data scopes: the rules of a group, compiled into a `WHERE` condition.
//!
//! Permissions answer *what* a user may do, scopes answer *which data* they may do it on
//! (`plan/detail/benutzer-gruppen-rechte.md`). A group carries
//! `scope: { rules: [ … ] }`; the core translates those rules into SurrealQL for
//! **server-side** filtering and pushes them into the very same `WHERE` clause as every
//! other filter of a view (`api::objects::ObjectFilter`). Two consequences are deliberate:
//!
//! * **Nothing is filtered in Rust and nothing after the page slice.** Filtering after the
//!   page would make the pager lie — "1–200 of 9633" while the next page shows something
//!   else — and the total would count objects the user may not see.
//! * **An unrestricted caller adds no condition at all** ([`for_caller`] answers `None`),
//!   so the query is byte-identical to what it was before scopes existed. That is the normal
//!   case and the reason the feature costs nothing when nobody uses it.
//!
//! **The shape of a scope clause is a measurement, not a taste** (2026-09-25, dev data,
//! 16 926 products, the largest demo category with 455 members):
//!
//! | clause | count of a 455-member scope |
//! |---|---|
//! | `id IN (SELECT VALUE in FROM reference WHERE …)` | **28 918 ms** |
//! | the same with a bound target list | 29 911 ms |
//! | materialising the member keys (one query) | 29 ms |
//! | `id IN [<455 literal keys>]` | **896 ms** |
//! | the unscoped count | 33 ms |
//!
//! The subquery is evaluated per candidate row and is thirty times worse than the literal;
//! it is therefore **not** used, even though it is the shorter code. The literal costs
//! ~2 ms per hundred ids on top of the unscoped query (69 members: +31 ms on the list,
//! 455 members: +126 ms), it drives the id index, and the resolved key list is cached on the
//! data version — the same idiom as the category, release and ancestor filters
//! (`api::keys::record_list_literal` documents the trap in full).

use std::sync::Arc;

use serde_json::Value;

use crate::app::AppState;
use crate::auth::Caller;
use crate::error::ApiError;

/// The fields a scope rule may filter on.
///
/// Deliberately short: every entry is a field with an index and a documented meaning. A
/// scope that could name any attribute would turn a form field into a query builder, and
/// an unindexed one into a full scan (measured: a `status IN […]` rule costs ~200 ms on
/// 16 926 products against 33 ms for `type`).
pub const FIELDS: [&str; 2] = ["type", "status"];

/// The operator every field rule uses (a list of allowed values).
pub const OPERATOR: &str = "in";

/// One rule of a group's scope.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Rule {
    /// The object's field must be one of the listed values.
    Field { field: String, values: Vec<String> },
    /// The object must be a member of one of the listed targets through that relation
    /// (a `reference` edge with the given `definition_id`).
    Reference {
        relation: String,
        targets: Vec<String>,
    },
}

/// The validated rules of one group.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Rules {
    pub rules: Vec<Rule>,
}

impl Rules {
    /// Whether the group restricts anything (a group without rules restricts nothing).
    pub fn is_empty(&self) -> bool {
        self.rules.is_empty()
    }
}

/// A compiled scope: conditions to `AND` into the object view plus their bindings.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Clause {
    /// Conditions, joined with `AND` by the caller (one entry: the whole scope).
    pub conditions: Vec<String>,
    /// Bindings the conditions refer to by name.
    pub binds: Vec<(String, Value)>,
}

/// Reads the stored `scope` document of a group.
///
/// Collects **every** problem instead of stopping at the first: the admin editor shows the
/// list at once, and a rejected write should not reveal its issues one round trip at a time.
/// The relational checks (does the relation exist, does the target exist) need the database
/// and live in [`validate`].
pub fn parse(scope: &Value) -> Result<Rules, Vec<String>> {
    let mut problems: Vec<String> = Vec::new();
    let Some(object) = scope.as_object() else {
        if scope.is_null() {
            return Ok(Rules::default());
        }
        return Err(vec![format!(
            "`scope` must be an object with a `rules` list, got {}",
            kind_of(scope)
        )]);
    };
    let Some(rules) = object.get("rules") else {
        // A scope without rules is the documented way to say "no restriction".
        return Ok(Rules::default());
    };
    let Some(rules) = rules.as_array() else {
        return Err(vec![format!(
            "`scope.rules` must be a list, got {}",
            kind_of(rules)
        )]);
    };
    let mut parsed: Vec<Rule> = Vec::new();
    for (index, rule) in rules.iter().enumerate() {
        match parse_rule(rule) {
            Ok(rule) => parsed.push(rule),
            Err(issues) => problems.append(
                &mut issues
                    .iter()
                    .map(|issue| format!("rules[{index}]: {issue}"))
                    .collect(),
            ),
        }
    }
    if !problems.is_empty() {
        return Err(problems);
    }
    Ok(Rules { rules: parsed })
}

/// Reads one rule of a stored scope.
fn parse_rule(rule: &Value) -> Result<Rule, Vec<String>> {
    let Some(object) = rule.as_object() else {
        return Err(vec![format!("must be an object, got {}", kind_of(rule))]);
    };
    let field = object.get("field");
    let reference = object.get("reference");
    match (field, reference) {
        (Some(_), Some(_)) => Err(vec![
            "carries both `field` and `reference` — a rule is one or the other".to_owned(),
        ]),
        (None, None) => Err(vec!["carries neither `field` nor `reference`".to_owned()]),
        (Some(field), None) => parse_field_rule(object, field),
        (None, Some(reference)) => parse_reference_rule(reference),
    }
}

/// Reads a `{field, operator, value}` rule.
fn parse_field_rule(
    object: &serde_json::Map<String, Value>,
    field: &Value,
) -> Result<Rule, Vec<String>> {
    let mut problems: Vec<String> = Vec::new();
    let name = field.as_str().unwrap_or_default();
    if !FIELDS.contains(&name) {
        problems.push(format!(
            "unknown field `{name}` (the scope fields are: {})",
            FIELDS.join(", ")
        ));
    }
    if let Some(operator) = object.get("operator")
        && operator.as_str() != Some(OPERATOR)
    {
        problems.push(format!(
            "unknown operator `{}` (a scope uses `{OPERATOR}`)",
            operator.as_str().unwrap_or(kind_of(operator))
        ));
    }
    let values = match object.get("value") {
        Some(Value::Array(values)) => values
            .iter()
            .filter_map(Value::as_str)
            .map(str::to_owned)
            .collect::<Vec<String>>(),
        Some(other) => {
            problems.push(format!(
                "`value` must be a list of strings, got {}",
                kind_of(other)
            ));
            Vec::new()
        }
        None => {
            problems.push("`value` is missing".to_owned());
            Vec::new()
        }
    };
    if values.is_empty() && problems.is_empty() {
        problems.push("`value` must name at least one value".to_owned());
    }
    if !problems.is_empty() {
        return Err(problems);
    }
    Ok(Rule::Field {
        field: name.to_owned(),
        values,
    })
}

/// Reads a `{reference: {type, out}}` rule.
fn parse_reference_rule(reference: &Value) -> Result<Rule, Vec<String>> {
    let mut problems: Vec<String> = Vec::new();
    let Some(object) = reference.as_object() else {
        return Err(vec![format!(
            "`reference` must be an object, got {}",
            kind_of(reference)
        )]);
    };
    let relation = object
        .get("type")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .trim()
        .to_owned();
    if relation.is_empty() {
        problems.push(
            "`reference.type` (the relation key, e.g. `rel_category_member`) is missing".to_owned(),
        );
    }
    let targets = match object.get("out") {
        Some(Value::String(target)) => vec![target.clone()],
        Some(Value::Array(targets)) => targets
            .iter()
            .filter_map(Value::as_str)
            .map(str::to_owned)
            .collect(),
        Some(other) => {
            problems.push(format!(
                "`reference.out` must be a record id or a list of them, got {}",
                kind_of(other)
            ));
            Vec::new()
        }
        None => {
            problems.push("`reference.out` is missing".to_owned());
            Vec::new()
        }
    };
    if targets.is_empty() && problems.is_empty() {
        problems.push("`reference.out` must name at least one target".to_owned());
    }
    for target in &targets {
        match target.split_once(':') {
            Some((table, key)) if !table.is_empty() && !key.is_empty() => {}
            _ => problems.push(format!("`{target}` is not a record id (`table:key`)")),
        }
    }
    if !problems.is_empty() {
        return Err(problems);
    }
    Ok(Rule::Reference { relation, targets })
}

/// The kind of a JSON value, for error messages.
fn kind_of(value: &Value) -> &'static str {
    match value {
        Value::Null => "null",
        Value::Bool(_) => "a boolean",
        Value::Number(_) => "a number",
        Value::String(_) => "a string",
        Value::Array(_) => "a list",
        Value::Object(_) => "an object",
    }
}

/// Checks a stored scope against the database: the relation and every target must exist.
///
/// Runs on the group **write** path (an admin action, rare), never on the read path: a scope
/// that names a relation or a target that does not exist would silently match nothing, and
/// "the group has no effect" is the least debuggable outcome there is.
pub async fn validate(db: &crate::db::Db, rules: &Rules) -> Result<(), ApiError> {
    for rule in &rules.rules {
        let Rule::Reference { relation, targets } = rule else {
            continue;
        };
        let mut response = db
            .inner()
            .query("SELECT VALUE id FROM relation_definition WHERE id = type::record('relation_definition', $key);")
            .bind(("key", crate::rbac::key_of(relation).to_owned()))
            .await?
            .check()?;
        let found: Vec<Value> = response.take(0)?;
        if found.is_empty() {
            return Err(ApiError::bad_request(format!(
                "unknown relation `{relation}` in a scope rule (see GET /relation-definitions)"
            )));
        }
        let literal = crate::api::keys::record_list_literal(
            "object",
            targets.iter().map(String::as_str).map(crate::rbac::key_of),
        );
        let mut response = db
            .inner()
            .query(format!(
                "SELECT VALUE id FROM object WHERE id IN {literal} AND deleted_at IS NONE;"
            ))
            .await?
            .check()?;
        let found: Vec<Value> = response.take(0)?;
        if found.len() != targets.len() {
            let known: Vec<&str> = found.iter().filter_map(Value::as_str).collect();
            let missing: Vec<&str> = targets
                .iter()
                .map(String::as_str)
                .filter(|target| !known.contains(target))
                .collect();
            return Err(ApiError::bad_request(format!(
                "unknown scope target(s): {} — a scope names existing objects",
                missing.join(", ")
            )));
        }
    }
    Ok(())
}

/// Compiles the scopes of the caller's groups into one condition for the object view.
///
/// `None` means **unrestricted**: no condition is added at all, so an unscoped caller runs
/// exactly the query they ran before scopes existed. The worker (`Caller::System`) is
/// unrestricted by design — an import must be able to write and read the whole catalogue.
pub async fn for_caller(state: &AppState, caller: &Caller) -> Result<Option<Clause>, ApiError> {
    let Caller::User(claims) = caller else {
        return Ok(None);
    };
    if claims.groups.is_empty() {
        // A user without groups cannot have a scope: no lookup, no clause.
        return Ok(None);
    }
    let scopes = crate::rbac::group_scopes(&state.db, &state.cache, &claims.groups).await?;
    compile(state, &scopes).await
}

/// The condition of a set of group scopes.
///
/// **Rules within a group are ANDed** (a group's scope is a conjunction: "type is product
/// *and* the object is in one of these categories"), **groups with rules are ORed** (a user
/// sees what any of their groups allows), and a **group without rules makes the whole user
/// unrestricted** — the plan's "ohne Scope-Regeln gelten die Standardrechte".
async fn compile(state: &AppState, groups: &[Rules]) -> Result<Option<Clause>, ApiError> {
    if groups.is_empty() || groups.iter().any(Rules::is_empty) {
        return Ok(None);
    }
    let mut alternatives: Vec<String> = Vec::new();
    let mut binds: Vec<(String, Value)> = Vec::new();
    for (group_index, group) in groups.iter().enumerate() {
        // Only the reference rules need the database: their member keys are resolved once per
        // data version and cached, everything else is string building.
        let mut resolved: Vec<Arc<Vec<String>>> = Vec::new();
        for rule in &group.rules {
            if let Rule::Reference { relation, targets } = rule {
                resolved.push(keys_for(state, relation, targets).await?);
            }
        }
        let (condition, mut group_binds) = group_condition(group_index, &group.rules, &resolved);
        alternatives.push(condition);
        binds.append(&mut group_binds);
    }
    Ok(Some(Clause {
        conditions: vec![join_alternatives(alternatives)],
        binds,
    }))
}

/// The condition of **one** group: its rules are `AND`ed.
///
/// `keys` carries the resolved member keys of the reference rules in their order; a missing
/// entry counts as an empty list. An empty list must stay a condition (`id IN []`): dropping the
/// rule would widen the view to the whole catalogue, the opposite of what was asked — the same
/// fail-closed reading a malformed scope gets.
fn group_condition(
    group_index: usize,
    rules: &[Rule],
    keys: &[Arc<Vec<String>>],
) -> (String, Vec<(String, Value)>) {
    let mut resolved = keys.iter();
    let mut parts: Vec<String> = Vec::new();
    let mut binds: Vec<(String, Value)> = Vec::new();
    for (rule_index, rule) in rules.iter().enumerate() {
        match rule {
            Rule::Field { field, values } => {
                let name = format!("scope_{group_index}_{rule_index}");
                parts.push(format!("{field} IN ${name}"));
                binds.push((
                    name,
                    Value::Array(values.iter().cloned().map(Value::String).collect()),
                ));
            }
            Rule::Reference { .. } => {
                let member_keys = resolved.next().map(|keys| keys.as_slice()).unwrap_or(&[]);
                let literal = crate::api::keys::record_list_literal(
                    "object",
                    member_keys.iter().map(String::as_str),
                );
                parts.push(format!("id IN {literal}"));
            }
        }
    }
    (format!("({})", parts.join(" AND ")), binds)
}

/// Joins the groups' conditions: one group stays as it is, several are `OR`ed.
///
/// The answer is a single condition, so the caller can append it to a chain that is `AND`ed
/// without having to think about precedence.
fn join_alternatives(alternatives: Vec<String>) -> String {
    if alternatives.len() == 1 {
        return alternatives.into_iter().next().unwrap_or_default();
    }
    format!("({})", alternatives.join(" OR "))
}

/// The object keys of a relation rule, cached on the **data** version.
///
/// The set changes when an object is categorised — which does not touch the access model —
/// so it is bound to `stats_version` (like the completeness view keys), not to
/// `auth_version`. The walk is one indexed query on `idx_ref_out_def`.
async fn keys_for(
    state: &AppState,
    relation: &str,
    targets: &[String],
) -> Result<Arc<Vec<String>>, ApiError> {
    let key = format!("{relation}|{}", targets.join(","));
    let version = state.cache.stats_version();
    if let Some(keys) = state.cache.cached_scope_keys(&key, version).await {
        return Ok(keys);
    }
    let literal = crate::api::keys::record_list_literal(
        "object",
        targets.iter().map(String::as_str).map(crate::rbac::key_of),
    );
    let mut response = state
        .db
        .inner()
        .query(format!(
            "SELECT VALUE in FROM reference WHERE definition_id = $relation AND out IN {literal};"
        ))
        .bind(("relation", relation.to_owned()))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    let mut keys: Vec<String> = rows
        .into_iter()
        .filter_map(|row| row.as_str().map(crate::rbac::key_of).map(str::to_owned))
        .collect();
    keys.sort();
    keys.dedup();
    let keys = Arc::new(keys);
    state
        .cache
        .store_scope_keys(&key, version, keys.clone())
        .await;
    Ok(keys)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn a_scope_without_rules_restricts_nothing() {
        assert_eq!(parse(&json!({})).expect("ok"), Rules::default());
        assert_eq!(parse(&json!({"rules": []})).expect("ok"), Rules::default());
        assert_eq!(parse(&Value::Null).expect("ok"), Rules::default());
        assert!(Rules::default().is_empty());
    }

    #[test]
    fn both_rule_kinds_are_read() {
        let rules = parse(&json!({
            "rules": [
                {"field": "type", "operator": "in", "value": ["product"]},
                {"reference": {"type": "rel_category_member", "out": "object:webshop"}},
            ]
        }))
        .expect("valid scope");
        assert_eq!(
            rules.rules,
            vec![
                Rule::Field {
                    field: "type".to_owned(),
                    values: vec!["product".to_owned()],
                },
                Rule::Reference {
                    relation: "rel_category_member".to_owned(),
                    targets: vec!["object:webshop".to_owned()],
                },
            ]
        );
    }

    #[test]
    fn a_reference_rule_accepts_a_list_of_targets() {
        let rules = parse(&json!({
            "rules": [{"reference": {"type": "rel_category_member", "out": ["object:a", "object:b"]}}]
        }))
        .expect("valid scope");
        assert_eq!(
            rules.rules,
            vec![Rule::Reference {
                relation: "rel_category_member".to_owned(),
                targets: vec!["object:a".to_owned(), "object:b".to_owned()],
            }]
        );
    }

    #[test]
    fn unknown_fields_operators_and_shapes_are_reported_with_their_index() {
        let problems = parse(&json!({
            "rules": [
                {"field": "attributes", "operator": "in", "value": ["x"]},
                {"field": "type", "operator": "contains", "value": ["x"]},
                {"field": "type", "value": []},
                {"reference": {"out": "object:a"}},
                {"field": "type", "reference": {"type": "rel_x"}},
                "not a rule",
            ]
        }))
        .expect_err("must be rejected");
        assert!(
            problems
                .iter()
                .any(|problem| problem.starts_with("rules[0]: unknown field `attributes`"))
        );
        assert!(
            problems
                .iter()
                .any(|problem| problem.contains("unknown operator `contains`"))
        );
        assert!(
            problems
                .iter()
                .any(|problem| problem.contains("at least one value"))
        );
        assert!(
            problems
                .iter()
                .any(|problem| problem.contains("reference.type"))
        );
        assert!(
            problems
                .iter()
                .any(|problem| problem.contains("both `field` and `reference`"))
        );
        assert!(
            problems
                .iter()
                .any(|problem| problem.contains("rules[5]: must be an object"))
        );
    }

    #[test]
    fn a_broken_scope_container_is_rejected() {
        assert!(parse(&json!({"rules": "everything"})).is_err());
        assert!(parse(&json!("open")).is_err());
    }

    #[test]
    fn the_rules_of_one_group_are_anded() {
        let rules = parse(&json!({
            "rules": [
                {"field": "type", "value": ["product", "variant"]},
                {"reference": {"type": "rel_category_member", "out": "object:webshop"}},
            ]
        }))
        .expect("valid scope");
        let keys = vec![Arc::new(vec!["a".to_owned(), "b".to_owned()])];
        let (condition, binds) = group_condition(0, &rules.rules, &keys);
        assert_eq!(
            condition,
            "(type IN $scope_0_0 AND id IN [object:a, object:b])"
        );
        assert_eq!(
            binds,
            vec![("scope_0_0".to_owned(), json!(["product", "variant"]))]
        );
    }

    #[test]
    fn groups_with_rules_are_ored_into_one_condition() {
        let one = join_alternatives(vec!["(type IN $scope_0_0)".to_owned()]);
        assert_eq!(one, "(type IN $scope_0_0)");
        let two = join_alternatives(vec![
            "(type IN $scope_0_0)".to_owned(),
            "(status IN $scope_1_0)".to_owned(),
        ]);
        assert_eq!(two, "((type IN $scope_0_0) OR (status IN $scope_1_0))");
    }

    #[test]
    fn a_scope_that_matches_nothing_stays_a_condition() {
        // Dropping the rule would widen the view to the whole catalogue.
        let rules = Rules {
            rules: vec![Rule::Reference {
                relation: "rel_category_member".to_owned(),
                targets: vec!["object:gone".to_owned()],
            }],
        };
        let (condition, binds) = group_condition(0, &rules.rules, &[Arc::new(Vec::new())]);
        assert_eq!(condition, "(id IN [])");
        assert!(binds.is_empty());
        // A missing resolution counts as empty as well (fail-closed, never "everything").
        let (condition, _) = group_condition(0, &rules.rules, &[]);
        assert_eq!(condition, "(id IN [])");
    }
}
