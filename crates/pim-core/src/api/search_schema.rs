//! Field catalog, operators and validation of the saved-search query DSL.
//!
//! A saved search stores a structured query (`where`/`fulltext`/`references`/
//! `sort`/`start`/`limit`). This module owns the **description** of what can be
//! filtered and the rules derived from it, so the core and the admin UI share
//! one source of truth (`GET /search-fields`):
//!
//! * `Catalog` / `load()` — the machine-readable description the UI renders
//!   (fields with value type, widget, allowed operators, enum options and
//!   languages; the operator table with `arity`/`multi`; relation definitions
//!   for reference restrictions).
//! * `validate_query()` — server-side enforcement with messages that name the
//!   field path inside the condition tree
//!   (`query.where.conditions[1].field: unknown field \`attributes.x\``).
//! * `normalize_query()` — canonical operators/group ops, empty groups pruned.
//!
//! Contract: every field/operator pair offered here is executed by the core, and
//! every executed operator is declared here. See
//! `plan/detail/suche-formular.md` for the design.

use serde::Serialize;
use serde_json::{Map, Value, json};

use crate::api::saved_searches::{QueryCondition, SavedSearchQuery};
use crate::db::Db;
use crate::error::ApiError;

/// Maximum nesting depth of the condition tree (root group = 1).
pub const MAX_DEPTH: usize = 6;
/// Maximum number of filter conditions (leaves) in one query.
pub const MAX_CONDITIONS: usize = 50;
/// Lower bound of `limit`.
pub const MIN_LIMIT: u32 = 1;
/// Upper bound of `limit` (mirrors the executor's clamp).
pub const MAX_LIMIT: u32 = 10_000;
/// Default `limit` of a stored query.
pub const DEFAULT_LIMIT: u32 = 1000;

/// Value type of a filterable field (drives the value widget and validation).
///
/// `Date` fields compare **as stored** (attribute values are raw JSON, usually
/// ISO-8601 strings, so lexicographic comparison is chronological). `DateTime`
/// marks real datetime columns (`created_at`/`updated_at`); the executor wraps
/// those operands in `type::datetime(…)` so they are compared as timestamps.
/// `Url` is text as well, but the value must be an absolute `http(s)` URL
/// (enforced on write, see `crate::attribute_values`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ValueType {
    String,
    Number,
    Boolean,
    Date,
    DateTime,
    Enum,
    Url,
}

/// Widget the admin UI renders for the value.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Widget {
    Text,
    Number,
    Switch,
    Select,
    Date,
}

/// An operator of the query DSL.
#[derive(Debug, Clone, Copy, Serialize)]
pub struct Operator {
    #[serde(rename = "type")]
    pub name: &'static str,
    /// i18n key of the operator label (never a literal text).
    pub label_key: &'static str,
    /// UI grouping of the operator (`compare`, `text`, `list`, `empty`).
    pub group: &'static str,
    /// Number of values: 0 (no value), 1 (one value) or 2 (`between`).
    pub arity: u8,
    /// The value is a list (`in`/`not_in`).
    pub multi: bool,
}

/// Every operator the executor understands.
pub const OPERATORS: &[Operator] = &[
    Operator {
        name: "eq",
        label_key: "search_form.op_eq",
        group: "compare",
        arity: 1,
        multi: false,
    },
    Operator {
        name: "ne",
        label_key: "search_form.op_ne",
        group: "compare",
        arity: 1,
        multi: false,
    },
    Operator {
        name: "gt",
        label_key: "search_form.op_gt",
        group: "compare",
        arity: 1,
        multi: false,
    },
    Operator {
        name: "gte",
        label_key: "search_form.op_gte",
        group: "compare",
        arity: 1,
        multi: false,
    },
    Operator {
        name: "lt",
        label_key: "search_form.op_lt",
        group: "compare",
        arity: 1,
        multi: false,
    },
    Operator {
        name: "lte",
        label_key: "search_form.op_lte",
        group: "compare",
        arity: 1,
        multi: false,
    },
    Operator {
        name: "between",
        label_key: "search_form.op_between",
        group: "compare",
        arity: 2,
        multi: false,
    },
    Operator {
        name: "not_between",
        label_key: "search_form.op_not_between",
        group: "compare",
        arity: 2,
        multi: false,
    },
    Operator {
        name: "contains",
        label_key: "search_form.op_contains",
        group: "text",
        arity: 1,
        multi: false,
    },
    Operator {
        name: "not_contains",
        label_key: "search_form.op_not_contains",
        group: "text",
        arity: 1,
        multi: false,
    },
    Operator {
        name: "starts_with",
        label_key: "search_form.op_starts_with",
        group: "text",
        arity: 1,
        multi: false,
    },
    Operator {
        name: "ends_with",
        label_key: "search_form.op_ends_with",
        group: "text",
        arity: 1,
        multi: false,
    },
    Operator {
        name: "in",
        label_key: "search_form.op_in",
        group: "list",
        arity: 1,
        multi: true,
    },
    Operator {
        name: "not_in",
        label_key: "search_form.op_not_in",
        group: "list",
        arity: 1,
        multi: true,
    },
    Operator {
        name: "not_empty",
        label_key: "search_form.op_not_empty",
        group: "empty",
        arity: 0,
        multi: false,
    },
    Operator {
        name: "is_null",
        label_key: "search_form.op_is_null",
        group: "empty",
        arity: 0,
        multi: false,
    },
];

/// Operators allowed for a value type (order = display order in the UI).
pub fn operators_for(value_type: ValueType) -> &'static [&'static str] {
    match value_type {
        ValueType::String => &[
            "eq",
            "ne",
            "contains",
            "not_contains",
            "starts_with",
            "ends_with",
            "not_empty",
            "is_null",
        ],
        ValueType::Url => &[
            "eq",
            "ne",
            "contains",
            "not_contains",
            "starts_with",
            "ends_with",
            "in",
            "not_in",
            "not_empty",
            "is_null",
        ],
        ValueType::Number | ValueType::Date | ValueType::DateTime => &[
            "eq",
            "ne",
            "gt",
            "gte",
            "lt",
            "lte",
            "between",
            "not_between",
            "not_empty",
            "is_null",
        ],
        ValueType::Boolean => &["eq", "ne", "not_empty", "is_null"],
        ValueType::Enum => &["eq", "ne", "in", "not_in", "not_empty", "is_null"],
    }
}

/// A filterable field of the catalog.
#[derive(Debug, Clone, Serialize)]
pub struct SearchField {
    /// Path inside the query (`type`, `status`, `attributes.<name>[.<lang>]`).
    pub key: String,
    /// Section in the field picker (`system`, `attribute`).
    pub group: &'static str,
    /// i18n key of the label (system fields only).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub label_key: Option<String>,
    /// Multilingual label of a data field (attribute definitions).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub label: Option<Value>,
    pub value_type: ValueType,
    pub widget: Widget,
    /// Fixed options (`{value, label_key}` or `{value, label}`).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub options: Option<Vec<Value>>,
    /// Dynamic option source resolved by the client (`object_types`).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub options_source: Option<&'static str>,
    /// The field has one value per language (`attributes.<name>.<lang>`).
    #[serde(skip_serializing_if = "is_false")]
    pub multilingual: bool,
    /// Languages of a multilingual field.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub languages: Option<Vec<String>>,
    /// Display hint for quantified values (`{symbol, factor}`).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub unit: Option<Value>,
    /// Allowed operators for this field.
    pub operators: Vec<&'static str>,
    /// The field may be used in `sort`.
    pub sortable: bool,
    /// A computed value (maintained by rules, not by users).
    #[serde(skip_serializing_if = "is_false")]
    pub computed: bool,
    /// Reference data of a classification standard (`attribute_definition.reference_data`).
    ///
    /// The field stays **filterable** (a stored query keeps validating) — the
    /// flag only lets the client keep the standard's vocabulary out of the
    /// offered list until the user asks for it
    /// (`plan/detail/kategorisierung.md`, "Referenzdaten").
    #[serde(skip_serializing_if = "is_false")]
    pub reference_data: bool,
}

impl SearchField {
    fn new(
        key: impl Into<String>,
        group: &'static str,
        value_type: ValueType,
        widget: Widget,
    ) -> Self {
        Self {
            key: key.into(),
            group,
            label_key: None,
            label: None,
            value_type,
            widget,
            options: None,
            options_source: None,
            multilingual: false,
            languages: None,
            unit: None,
            operators: operators_for(value_type).to_vec(),
            sortable: true,
            computed: false,
            reference_data: false,
        }
    }

    fn label_key(mut self, key: &str) -> Self {
        self.label_key = Some(key.to_owned());
        self
    }

    fn label(mut self, label: Option<Value>) -> Self {
        self.label = label;
        self
    }

    fn options(mut self, options: Option<Vec<Value>>) -> Self {
        self.options = options;
        self
    }

    fn options_source(mut self, source: &'static str) -> Self {
        self.options_source = Some(source);
        self
    }

    fn not_sortable(mut self) -> Self {
        self.sortable = false;
        self
    }

    fn computed(mut self, computed: bool) -> Self {
        self.computed = computed;
        self
    }

    fn reference_data(mut self, reference_data: bool) -> Self {
        self.reference_data = reference_data;
        self
    }
}

fn is_false(value: &bool) -> bool {
    !*value
}

/// The field/operator catalog (response of `GET /search-fields`).
#[derive(Debug, Clone, Serialize)]
pub struct Catalog {
    pub groups: Vec<Value>,
    pub operators: Vec<Operator>,
    pub fields: Vec<SearchField>,
    pub languages: Vec<Value>,
    pub references: Vec<Value>,
    pub limits: Value,
}

impl Catalog {
    /// The field with this exact key, or the base field of a multilingual
    /// variant (`attributes.name.de` resolves when `de` is a declared language).
    pub fn field(&self, key: &str) -> Option<&SearchField> {
        if let Some(field) = self.fields.iter().find(|field| field.key == key) {
            return Some(field);
        }
        let (base, language) = key.rsplit_once('.')?;
        let field = self.fields.iter().find(|field| field.key == base)?;
        let known = field
            .languages
            .as_ref()
            .is_some_and(|languages| languages.iter().any(|code| code == language));
        if field.multilingual && known {
            Some(field)
        } else {
            None
        }
    }

    /// The operator declaration with this name.
    pub fn operator(&self, name: &str) -> Option<&Operator> {
        self.operators.iter().find(|operator| operator.name == name)
    }

    /// The reference (relation type) with this name.
    pub fn reference(&self, name: &str) -> Option<&Value> {
        self.references
            .iter()
            .find(|entry| entry.get("type").and_then(Value::as_str) == Some(name))
    }
}

/// Raw rows the catalog is built from (one query per source table).
#[derive(Debug, Default, Clone)]
pub struct CatalogSources {
    pub attributes: Vec<Value>,
    pub enum_sets: Vec<Value>,
    pub enum_options: Vec<Value>,
    pub units: Vec<Value>,
    pub languages: Vec<Value>,
    pub object_types: Vec<Value>,
    pub statuses: Vec<Value>,
    pub relations: Vec<Value>,
}

/// Loads the catalog from the database.
pub async fn load(db: &Db) -> Result<Catalog, ApiError> {
    let sources = CatalogSources {
        attributes: rows(
            db,
            "SELECT name, label, type, multilingual, enum_set_id, unit_id, computed, reference_data FROM attribute_definition;",
        )
        .await?,
        enum_sets: rows(db, "SELECT id, options FROM enum_set;").await?,
        enum_options: rows(db, "SELECT id, code, labels FROM enum_option;").await?,
        units: rows(db, "SELECT id, symbol, factor FROM unit;").await?,
        languages: rows(
            db,
            "SELECT code, label FROM language_tree WHERE active = true AND code != 'root' ORDER BY code;",
        )
        .await?,
        object_types: rows(db, "SELECT name, label FROM object_type;").await?,
        statuses: rows(db, "SELECT status FROM object GROUP BY status;").await?,
        relations: rows(
            db,
            "SELECT name, label, role, target_types FROM relation_definition;",
        )
        .await?,
    };
    Ok(build_catalog(&sources))
}

async fn rows(db: &Db, sql: &str) -> Result<Vec<Value>, ApiError> {
    let mut response = db.inner().query(sql).await?.check()?;
    let rows: Vec<Value> = response.take(0)?;
    Ok(rows)
}

/// Builds the catalog from raw rows (pure; unit-tested without a database).
pub fn build_catalog(sources: &CatalogSources) -> Catalog {
    let languages: Vec<Value> = sources.languages.clone();
    let language_codes: Vec<String> = languages
        .iter()
        .filter_map(|row| string_at(row, "code"))
        .collect();

    let mut fields: Vec<SearchField> = Vec::new();

    // --- System fields -----------------------------------------------------
    fields.push(
        SearchField::new("type", "system", ValueType::String, Widget::Select)
            .label_key("search_form.field_type")
            .options_source("object_types"),
    );
    if !sources.object_types.is_empty() {
        let options = sorted_options(
            sources
                .object_types
                .iter()
                .map(|row| {
                    json!({
                        "value": string_at(row, "name").unwrap_or_default(),
                        "label": row.get("label").cloned().unwrap_or(Value::Null),
                    })
                })
                .collect(),
        );
        fields[0].options = Some(options);
    }
    fields.push(
        SearchField::new("status", "system", ValueType::String, Widget::Select)
            .label_key("search_form.field_status")
            .options(Some(status_options(&sources.statuses))),
    );
    fields.push(
        SearchField::new("id", "system", ValueType::String, Widget::Text)
            .label_key("search_form.field_id")
            .options(None)
            .not_sortable(),
    );
    fields.push(
        SearchField::new("created_at", "system", ValueType::DateTime, Widget::Date)
            .label_key("search_form.field_created_at"),
    );
    fields.push(
        SearchField::new("updated_at", "system", ValueType::DateTime, Widget::Date)
            .label_key("search_form.field_updated_at"),
    );
    fields.push(
        SearchField::new("source", "system", ValueType::String, Widget::Text)
            .label_key("search_form.field_source")
            .not_sortable(),
    );

    // --- Attribute fields --------------------------------------------------
    let mut attributes: Vec<&Value> = sources.attributes.iter().collect();
    attributes.sort_by_key(|row| sort_key(row));
    for row in attributes {
        let Some(name) = string_at(row, "name") else {
            continue;
        };
        let attribute_type = string_at(row, "type").unwrap_or_default();
        let Some((value_type, widget)) = attribute_widget(&attribute_type) else {
            continue;
        };
        let multilingual = row
            .get("multilingual")
            .and_then(Value::as_bool)
            .unwrap_or(false);
        let computed = row
            .get("computed")
            .and_then(Value::as_bool)
            .unwrap_or(false);
        let reference_data = row
            .get("reference_data")
            .and_then(Value::as_bool)
            .unwrap_or(false);
        let options = if value_type == ValueType::Enum {
            enum_options(row.get("enum_set_id"), sources)
        } else {
            None
        };
        let mut field = SearchField::new(
            format!("attributes.{name}"),
            "attribute",
            value_type,
            widget,
        )
        .label(row.get("label").cloned())
        .options(options)
        .computed(computed)
        .reference_data(reference_data);
        if multilingual {
            field.multilingual = true;
            field.languages = Some(language_codes.clone());
        }
        if value_type == ValueType::Boolean {
            field.sortable = false;
        }
        if let Some(unit) = unit_hint(row.get("unit_id"), sources) {
            field.unit = Some(unit);
        }
        fields.push(field);
    }

    let references: Vec<Value> = sorted_options(
        sources
            .relations
            .iter()
            .filter_map(|row| {
                let name = string_at(row, "name")?;
                Some(json!({
                    "type": name,
                    "label": row.get("label").cloned().unwrap_or(Value::Null),
                    "role": row.get("role").cloned().unwrap_or(Value::Null),
                    "target_types": row.get("target_types").cloned().unwrap_or(json!([])),
                }))
            })
            .collect(),
    );

    Catalog {
        groups: vec![
            json!({ "key": "system", "label_key": "search_form.group_system" }),
            json!({ "key": "attribute", "label_key": "search_form.group_attributes" }),
        ],
        operators: OPERATORS.to_vec(),
        fields,
        languages,
        references,
        limits: json!({
            "min": MIN_LIMIT,
            "max": MAX_LIMIT,
            "default": DEFAULT_LIMIT,
            "max_depth": MAX_DEPTH,
            "max_conditions": MAX_CONDITIONS,
        }),
    }
}

fn string_at(row: &Value, key: &str) -> Option<String> {
    row.get(key)
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
}

/// Sorts by localized label, falling back to the value (and deduplicated).
fn sorted_options(mut options: Vec<Value>) -> Vec<Value> {
    options.sort_by_key(|option| {
        let label = option
            .get("label")
            .and_then(Value::as_object)
            .and_then(|labels| labels.values().next())
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_lowercase();
        let value = option
            .get("value")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_lowercase();
        (label, value)
    });
    options
}

fn sort_key(row: &Value) -> String {
    let label = row
        .get("label")
        .and_then(Value::as_object)
        .and_then(|labels| labels.values().next())
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_lowercase();
    let name = string_at(row, "name").unwrap_or_default().to_lowercase();
    format!("{label}\u{0}{name}")
}

/// Value type and widget of an attribute data type (unknown types are skipped).
fn attribute_widget(attribute_type: &str) -> Option<(ValueType, Widget)> {
    match attribute_type {
        "text" => Some((ValueType::String, Widget::Text)),
        "url" => Some((ValueType::Url, Widget::Text)),
        "number" => Some((ValueType::Number, Widget::Number)),
        "boolean" => Some((ValueType::Boolean, Widget::Switch)),
        "date" => Some((ValueType::Date, Widget::Date)),
        "enum" => Some((ValueType::Enum, Widget::Select)),
        _ => None,
    }
}

/// Status options from the stored data: known workflow states get an i18n key,
/// everything else falls back to the raw value.
fn status_options(statuses: &[Value]) -> Vec<Value> {
    const KNOWN: &[&str] = &["draft", "in_review", "approved", "published", "archived"];
    let mut values: Vec<String> = statuses
        .iter()
        .filter_map(|row| string_at(row, "status"))
        .collect();
    values.sort();
    values.dedup();
    values
        .into_iter()
        .map(|value| {
            if KNOWN.contains(&value.as_str()) {
                json!({ "value": value.clone(), "label_key": format!("status.{value}") })
            } else {
                json!({ "value": value.clone(), "label": { "en": value } })
            }
        })
        .collect()
}

/// Stored option codes/labels of an enum attribute (via its `enum_set_id`).
fn enum_options(enum_set_id: Option<&Value>, sources: &CatalogSources) -> Option<Vec<Value>> {
    let set_key = enum_set_id
        .and_then(Value::as_str)
        .map(record_key)
        .filter(|key| !key.is_empty())?;
    let set = sources.enum_sets.iter().find(|row| {
        row.get("id").and_then(Value::as_str).map(record_key) == Some(set_key.clone())
    })?;
    let option_keys: Vec<String> = set
        .get("options")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(Value::as_str)
                .map(record_key)
                .collect()
        })
        .unwrap_or_default();
    let mut options: Vec<Value> = Vec::new();
    for key in option_keys {
        let Some(option) = sources
            .enum_options
            .iter()
            .find(|row| row.get("id").and_then(Value::as_str).map(record_key) == Some(key.clone()))
        else {
            continue;
        };
        let Some(code) = string_at(option, "code") else {
            continue;
        };
        options.push(json!({
            "value": code,
            "label": option.get("labels").cloned().unwrap_or(Value::Null),
        }));
    }
    // Unit/attribute definitions may still carry inline enum values.
    if options.is_empty() {
        return None;
    }
    Some(options)
}

/// Display hint of a quantified attribute (`{symbol, factor}`).
fn unit_hint(unit_id: Option<&Value>, sources: &CatalogSources) -> Option<Value> {
    let key = unit_id
        .and_then(Value::as_str)
        .map(record_key)
        .filter(|key| !key.is_empty())?;
    let unit = sources
        .units
        .iter()
        .find(|row| row.get("id").and_then(Value::as_str).map(record_key) == Some(key.clone()))?;
    Some(json!({
        "symbol": unit.get("symbol").cloned().unwrap_or(Value::Null),
        "factor": unit.get("factor").cloned().unwrap_or(json!(1)),
    }))
}

/// Strips the table prefix of a record id (`enum_set:brand` -> `brand`).
fn record_key(value: &str) -> String {
    value.rsplit(':').next().unwrap_or(value).to_owned()
}

/// Validates a query against the catalog.
///
/// Returns one message per problem, always naming the path inside the query so
/// a form can point at the offending condition. An empty result means "valid".
pub fn validate_query(query: &SavedSearchQuery, catalog: &Catalog) -> Vec<String> {
    let mut errors: Vec<String> = Vec::new();
    if !query.filters.is_empty() {
        push_once(
            &mut errors,
            "query.filters: the flat filter list was replaced by `query.where`".to_owned(),
        );
    }
    let mut leaves = 0usize;
    if let Some(root) = &query.where_ {
        check_condition(root, "query.where", 1, &mut leaves, catalog, &mut errors);
    }
    if leaves > MAX_CONDITIONS {
        push_once(
            &mut errors,
            format!("query.where: at most {MAX_CONDITIONS} conditions are allowed"),
        );
    }
    for (index, reference) in query.references.iter().enumerate() {
        let path = format!("query.references[{index}]");
        if catalog.reference(reference.reference_type.trim()).is_none() {
            push_once(
                &mut errors,
                format!(
                    "{path}.type: unknown relation type `{}`",
                    reference.reference_type
                ),
            );
        }
        if reference.out.trim().is_empty() {
            push_once(&mut errors, format!("{path}.out: must not be empty"));
        }
        if let Some(direction) = &reference.direction
            && direction != "incoming"
            && direction != "outgoing"
        {
            push_once(
                &mut errors,
                format!("{path}.direction: must be incoming or outgoing"),
            );
        }
    }
    for (index, sort) in query.sort.iter().enumerate() {
        let path = format!("query.sort[{index}]");
        match catalog.field(sort.field.trim()) {
            None => push_once(
                &mut errors,
                format!("{path}.field: unknown field `{}`", sort.field),
            ),
            Some(field) if !field.sortable => push_once(
                &mut errors,
                format!("{path}.field: `{}` cannot be sorted", sort.field),
            ),
            Some(_) => {}
        }
        if sort.order != "asc" && sort.order != "desc" {
            push_once(&mut errors, format!("{path}.order: must be asc or desc"));
        }
    }
    if query.limit < MIN_LIMIT || query.limit > MAX_LIMIT {
        push_once(
            &mut errors,
            format!("query.limit: must be between {MIN_LIMIT} and {MAX_LIMIT}"),
        );
    }
    errors
}

fn check_condition(
    condition: &QueryCondition,
    path: &str,
    depth: usize,
    leaves: &mut usize,
    catalog: &Catalog,
    errors: &mut Vec<String>,
) {
    let Some(conditions) = &condition.conditions else {
        *leaves += 1;
        check_filter(condition, path, catalog, errors);
        return;
    };
    if condition.field.is_some() || condition.operator.is_some() {
        push_once(
            errors,
            format!("{path}: a group must not carry `field` or `operator`"),
        );
    }
    if let Some(op) = &condition.op
        && op != "and"
        && op != "or"
    {
        push_once(errors, format!("{path}.op: must be and or or"));
    }
    if conditions.is_empty() {
        push_once(
            errors,
            format!("{path}: a group needs at least one condition"),
        );
        return;
    }
    if depth >= MAX_DEPTH {
        push_once(
            errors,
            format!("{path}: groups may be nested at most {MAX_DEPTH} levels deep"),
        );
        return;
    }
    for (index, child) in conditions.iter().enumerate() {
        check_condition(
            child,
            &format!("{path}.conditions[{index}]"),
            depth + 1,
            leaves,
            catalog,
            errors,
        );
    }
}

fn check_filter(
    condition: &QueryCondition,
    path: &str,
    catalog: &Catalog,
    errors: &mut Vec<String>,
) {
    if condition.op.is_some() {
        push_once(errors, format!("{path}: a condition must not carry `op`"));
    }
    let Some(field_key) = condition
        .field
        .as_deref()
        .map(str::trim)
        .filter(|key| !key.is_empty())
    else {
        push_once(errors, format!("{path}.field: is required"));
        return;
    };
    let Some(operator_name) = condition
        .operator
        .as_deref()
        .map(str::trim)
        .filter(|name| !name.is_empty())
    else {
        push_once(errors, format!("{path}.operator: is required"));
        return;
    };
    let Some(field) = catalog.field(field_key) else {
        push_once(errors, format!("{path}.field: unknown field `{field_key}`"));
        return;
    };
    let Some(operator) = catalog.operator(operator_name) else {
        push_once(
            errors,
            format!("{path}.operator: unknown operator `{operator_name}`"),
        );
        return;
    };
    if !field.operators.contains(&operator.name) {
        push_once(
            errors,
            format!(
                "{path}.operator: `{}` is not allowed for `{}` (use {})",
                operator.name,
                field.key,
                field.operators.join(", ")
            ),
        );
        return;
    }
    check_value(condition, operator, field, path, errors);
}

fn check_value(
    condition: &QueryCondition,
    operator: &Operator,
    field: &SearchField,
    path: &str,
    errors: &mut Vec<String>,
) {
    let null_value = condition.value.as_ref().is_some_and(Value::is_null);
    if operator.arity == 0 {
        if condition
            .value
            .as_ref()
            .is_some_and(|value| !value.is_null())
        {
            push_once(
                errors,
                format!("{path}.value: `{}` does not take a value", operator.name),
            );
        }
        return;
    }
    let Some(value) = condition.value.as_ref().filter(|value| !value.is_null()) else {
        // `eq null` / `ne null` are the "has no value" spellings of the DSL.
        if null_value && matches!(operator.name, "eq" | "ne") {
            return;
        }
        push_once(
            errors,
            format!("{path}.value: is required for `{}`", operator.name),
        );
        return;
    };
    if operator.multi {
        let Some(items) = value.as_array() else {
            push_once(errors, format!("{path}.value: must be a list"));
            return;
        };
        if items.is_empty() {
            push_once(errors, format!("{path}.value: must not be empty"));
            return;
        }
        for item in items {
            check_scalar(item, field, path, errors);
        }
        return;
    }
    if operator.arity == 2 {
        let Some(items) = value.as_array() else {
            push_once(
                errors,
                format!("{path}.value: must be a list of two values"),
            );
            return;
        };
        if items.len() != 2 {
            push_once(
                errors,
                format!("{path}.value: must be a list of two values"),
            );
            return;
        }
        for item in items {
            check_scalar(item, field, path, errors);
        }
        return;
    }
    check_scalar(value, field, path, errors);
}

fn check_scalar(value: &Value, field: &SearchField, path: &str, errors: &mut Vec<String>) {
    let ok = match field.value_type {
        ValueType::String
        | ValueType::Enum
        | ValueType::Date
        | ValueType::DateTime
        | ValueType::Url => value.is_string(),
        ValueType::Number => value.is_number(),
        ValueType::Boolean => value.is_boolean(),
    };
    if !ok {
        push_once(
            errors,
            format!(
                "{path}.value: must be {}",
                value_type_hint(field.value_type)
            ),
        );
    }
}

fn value_type_hint(value_type: ValueType) -> &'static str {
    match value_type {
        ValueType::String => "a string",
        ValueType::Number => "a number",
        ValueType::Boolean => "a boolean",
        ValueType::Date | ValueType::DateTime => "an ISO date string",
        ValueType::Enum => "an option code",
        ValueType::Url => "a URL",
    }
}

fn push_once(errors: &mut Vec<String>, message: String) {
    if !errors.contains(&message) {
        errors.push(message);
    }
}

/// Canonicalizes a query before it is validated/stored.
///
/// Operator aliases become their canonical names, group ops are always set,
/// strings are trimmed and empty groups are pruned (an empty root group means
/// "no restriction"). Returns whether anything changed.
pub fn normalize_query(query: &mut SavedSearchQuery) -> bool {
    let mut changed = false;
    if let Some(root) = query.where_.as_mut() {
        changed |= normalize_condition(root);
        if root.conditions.as_ref().is_some_and(Vec::is_empty) {
            query.where_ = None;
            changed = true;
        }
    }
    if let Some(fulltext) = query.fulltext.as_mut() {
        let text = fulltext.query.trim().to_owned();
        if fulltext.query != text {
            fulltext.query = text;
            changed = true;
        }
        let empty =
            fulltext.query.is_empty() && fulltext.fields.is_none() && fulltext.language.is_none();
        if empty {
            query.fulltext = None;
            changed = true;
        }
    }
    for reference in &mut query.references {
        let trimmed = reference.reference_type.trim().to_owned();
        if reference.reference_type != trimmed {
            reference.reference_type = trimmed;
            changed = true;
        }
        let out = reference.out.trim().to_owned();
        if reference.out != out {
            reference.out = out;
            changed = true;
        }
    }
    for sort in &mut query.sort {
        let field = sort.field.trim().to_owned();
        if sort.field != field {
            sort.field = field;
            changed = true;
        }
        let order = sort.order.trim().to_ascii_lowercase();
        if sort.order != order {
            sort.order = order;
            changed = true;
        }
    }
    changed
}

fn normalize_condition(condition: &mut QueryCondition) -> bool {
    let mut changed = false;
    if let Some(field) = condition.field.as_mut() {
        let trimmed = field.trim().to_owned();
        if *field != trimmed {
            *field = trimmed;
            changed = true;
        }
    }
    if let Some(operator) = condition.operator.as_mut() {
        let canonical = canonical_operator(operator.trim()).to_owned();
        if *operator != canonical {
            *operator = canonical;
            changed = true;
        }
    }
    let Some(conditions) = condition.conditions.take() else {
        return changed;
    };
    let mut kept: Vec<QueryCondition> = Vec::with_capacity(conditions.len());
    for mut child in conditions {
        changed |= normalize_condition(&mut child);
        if child.conditions.as_ref().is_some_and(Vec::is_empty) {
            changed = true;
            continue;
        }
        kept.push(child);
    }
    let op = condition
        .op
        .as_deref()
        .map(|op| op.trim().to_ascii_lowercase())
        .filter(|op| !op.is_empty())
        .unwrap_or_else(|| "and".to_owned());
    if condition.op.as_deref() != Some(op.as_str()) {
        condition.op = Some(op);
        changed = true;
    }
    condition.conditions = Some(kept);
    changed
}

/// Documented operator aliases are folded into the canonical names.
fn canonical_operator(name: &str) -> &str {
    match name {
        "neq" => "ne",
        "ge" => "gte",
        "le" => "lte",
        "is_empty" => "is_null",
        other => other,
    }
}

// ---------------------------------------------------------------------------
// Rendering (shared by the saved-search executor and the object list filter)
// ---------------------------------------------------------------------------
/// A rendered filter: SurrealQL clauses plus the parameters they bind.
#[derive(Debug, Default)]
pub struct RenderedFilter {
    /// Predicates to be joined with `AND` by the caller.
    pub clauses: Vec<String>,
    /// Bound parameters (`f0`, `f1`, … — collision-free by construction).
    pub params: Vec<(String, Value)>,
}

/// Validates a filter/sort field reference against a strict whitelist.
pub fn sanitize_field(field: &str) -> Result<String, ApiError> {
    // System fields offered by the catalog.
    if matches!(
        field,
        "type" | "status" | "id" | "created_at" | "updated_at" | "source"
    ) {
        return Ok(field.to_owned());
    }
    if let Some(rest) = field.strip_prefix("attributes.") {
        let segments: Vec<&str> = rest.split('.').collect();
        if segments.len() > 2 || segments.iter().any(|s| s.is_empty()) {
            return Err(ApiError::bad_request(format!(
                "unsupported filter field `{field}`"
            )));
        }
        for segment in &segments {
            if !segment
                .chars()
                .all(|c| c.is_alphanumeric() || c == '_' || c == '-' || c == '.')
            {
                return Err(ApiError::bad_request(format!(
                    "unsupported filter field `{field}`"
                )));
            }
        }
        return Ok(field.to_owned());
    }
    Err(ApiError::bad_request(format!(
        "unsupported filter field `{field}`"
    )))
}

/// Binds a value and returns its `$name` reference (names are counted across
/// the whole tree so nested groups cannot collide).
fn push_param(params: &mut Vec<(String, Value)>, counter: &mut usize, value: Value) -> String {
    let name = format!("f{}", *counter);
    *counter += 1;
    params.push((name.clone(), value));
    format!("${name}")
}

/// Binds an operand of a condition.
///
/// System timestamps (`created_at`/`updated_at`) are real datetime columns, so
/// their operand is wrapped in `type::datetime(…)`; attribute values are stored
/// as given (usually ISO strings) and compared literally.
fn push_operand(
    catalog: &Catalog,
    field_key: &str,
    value: Value,
    params: &mut Vec<(String, Value)>,
    counter: &mut usize,
) -> String {
    let name = push_param(params, counter, value);
    let is_datetime = catalog
        .field(field_key)
        .is_some_and(|field| field.value_type == ValueType::DateTime);
    if is_datetime {
        format!("type::datetime({name})")
    } else {
        name
    }
}

/// Renders one node of the condition tree into a SurrealQL predicate.
pub fn render_condition(
    condition: &QueryCondition,
    params: &mut Vec<(String, Value)>,
    counter: &mut usize,
    catalog: &Catalog,
) -> Result<String, ApiError> {
    let Some(children) = &condition.conditions else {
        return render_filter(condition, params, counter, catalog);
    };
    if children.is_empty() {
        return Ok("true".to_owned());
    }
    let separator = match condition.op.as_deref().unwrap_or("and") {
        "or" => " OR ",
        _ => " AND ",
    };
    let mut rendered: Vec<String> = Vec::with_capacity(children.len());
    for child in children {
        rendered.push(render_condition(child, params, counter, catalog)?);
    }
    Ok(format!("({})", rendered.join(separator)))
}

/// Translates a single condition into a SurrealQL clause.
///
/// Composite clauses (`between`, `not_between`, `not_empty`, `is_null`) are
/// parenthesized: inside a group they are joined with `AND`/`OR`, and `AND`
/// binds tighter.
///
/// Comparisons only match objects that **have** a value: in SurrealDB
/// `NONE < 0` is true (missing values sort before numbers) and `NONE != "x"` is
/// true as well, so every negative/ordering operator is guarded with
/// `IS NOT NONE AND != NULL`. "Has no value" is expressed with
/// `is_null` (or the `eq null` spelling).
fn render_filter(
    condition: &QueryCondition,
    params: &mut Vec<(String, Value)>,
    counter: &mut usize,
    catalog: &Catalog,
) -> Result<String, ApiError> {
    let field_key = condition.field.as_deref().unwrap_or_default();
    let field = sanitize_field(field_key)?;
    let operator = condition.operator.as_deref().unwrap_or_default();
    let value = condition.value.clone();
    let null_value = value.as_ref().is_some_and(Value::is_null);
    let operand = |params: &mut Vec<(String, Value)>, counter: &mut usize| {
        push_operand(
            catalog,
            field_key,
            value.clone().unwrap_or(Value::Null),
            params,
            counter,
        )
    };
    // Guard for every operator that must not match a missing value.
    let present = format!("({field} IS NOT NONE AND {field} != NULL)");
    let clause = match operator {
        "eq" if null_value => format!("{field} IS NONE"),
        "ne" if null_value => format!("{field} IS NOT NONE"),
        "eq" => format!("{field} = {}", operand(params, counter)),
        "ne" => format!("{present} AND {field} != {}", operand(params, counter)),
        "gt" => format!("{present} AND {field} > {}", operand(params, counter)),
        "gte" => format!("{present} AND {field} >= {}", operand(params, counter)),
        "lt" => format!("{present} AND {field} < {}", operand(params, counter)),
        "lte" => format!("{present} AND {field} <= {}", operand(params, counter)),
        "between" | "not_between" => {
            let items = value.as_ref().and_then(Value::as_array);
            let low = items
                .and_then(|items| items.first())
                .cloned()
                .unwrap_or(Value::Null);
            let high = items
                .and_then(|items| items.get(1))
                .cloned()
                .unwrap_or(Value::Null);
            let low_param = push_operand(catalog, field_key, low, params, counter);
            let high_param = push_operand(catalog, field_key, high, params, counter);
            if operator == "between" {
                format!("{present} AND ({field} >= {low_param} AND {field} <= {high_param})")
            } else {
                format!("{present} AND ({field} < {low_param} OR {field} > {high_param})")
            }
        }
        "in" => format!("{field} IN {}", operand(params, counter)),
        "not_in" => format!("{present} AND {field} NOT IN {}", operand(params, counter)),
        "contains" => format!("{field} CONTAINS {}", operand(params, counter)),
        "not_contains" => format!(
            "{present} AND {field} NOT CONTAINS {}",
            operand(params, counter)
        ),
        "starts_with" => {
            let param = operand(params, counter);
            format!("string::startsWith(string::lower({field}), string::lower({param}))")
        }
        "ends_with" => {
            let param = operand(params, counter);
            format!("string::endsWith(string::lower({field}), string::lower({param}))")
        }
        "not_empty" => present,
        "is_null" => format!("({field} IS NONE OR {field} = NULL)"),
        op => {
            return Err(ApiError::bad_request(format!(
                "unsupported filter operator `{op}`"
            )));
        }
    };
    Ok(clause)
}

/// Renders the **filter** part of a query: `where`, `fulltext` and `references`.
///
/// `sort`/`start`/`limit` are left to the caller — the saved-search executor
/// orders and slices, the object list applies its own paging.
pub fn render_query(
    query: &SavedSearchQuery,
    catalog: &Catalog,
) -> Result<RenderedFilter, ApiError> {
    let mut rendered = RenderedFilter::default();
    let mut counter = 0usize;
    if let Some(root) = &query.where_ {
        let clause = render_condition(root, &mut rendered.params, &mut counter, catalog)?;
        if clause != "true" {
            rendered.clauses.push(clause);
        }
    }
    if let Some(fulltext) = &query.fulltext {
        let text = fulltext.query.trim().to_owned();
        if !text.is_empty() {
            let param = push_param(&mut rendered.params, &mut counter, Value::String(text));
            rendered.clauses.push(format!("attributes @@ {param}"));
        }
    }
    for reference in &query.references {
        let definition = format!("rel_{}", reference.reference_type);
        let out_key = reference.out.rsplit(':').next().unwrap_or(&reference.out);
        let def_param = push_param(
            &mut rendered.params,
            &mut counter,
            Value::String(definition),
        );
        let out_param = push_param(
            &mut rendered.params,
            &mut counter,
            Value::String(out_key.to_owned()),
        );
        let incoming = reference
            .direction
            .as_deref()
            .map(|direction| direction != "outgoing")
            .unwrap_or(true);
        if incoming {
            rendered.clauses.push(format!(
                "id IN (SELECT VALUE in FROM reference WHERE definition_id = {def_param} AND out = type::record('object', {out_param}))"
            ));
        } else {
            rendered.clauses.push(format!(
                "id IN (SELECT VALUE out FROM reference WHERE definition_id = {def_param} AND in = type::record('object', {out_param}))"
            ));
        }
    }
    Ok(rendered)
}

/// Summary of a query for list columns and log messages.
pub fn summarize(query: &SavedSearchQuery) -> Map<String, Value> {
    let mut summary = Map::new();
    if let Some(root) = &query.where_ {
        summary.insert("conditions".to_owned(), json!(count_conditions(root)));
        summary.insert("groups".to_owned(), json!(count_groups(root)));
        summary.insert(
            "op".to_owned(),
            json!(root.op.clone().unwrap_or_else(|| "and".to_owned())),
        );
    } else {
        summary.insert("conditions".to_owned(), json!(0));
        summary.insert("groups".to_owned(), json!(0));
    }
    if let Some(fulltext) = &query.fulltext {
        summary.insert("fulltext".to_owned(), json!(fulltext.query.clone()));
    }
    summary.insert("references".to_owned(), json!(query.references.len()));
    summary.insert("sort".to_owned(), json!(query.sort.len()));
    summary.insert("limit".to_owned(), json!(query.limit));
    summary
}

fn count_conditions(condition: &QueryCondition) -> usize {
    match &condition.conditions {
        Some(children) => children.iter().map(count_conditions).sum(),
        None => 1,
    }
}

fn count_groups(condition: &QueryCondition) -> usize {
    match &condition.conditions {
        Some(children) => 1 + children.iter().map(count_groups).sum::<usize>(),
        None => 0,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::api::saved_searches::{QueryFilter, QueryReference, QuerySort};

    fn sources() -> CatalogSources {
        CatalogSources {
            attributes: vec![
                json!({"name": "name", "label": {"en": "Name", "de": "Name"}, "type": "text", "multilingual": true}),
                json!({"name": "price", "label": {"en": "Price"}, "type": "number"}),
                json!({"name": "active", "label": {"en": "Active"}, "type": "boolean"}),
                json!({"name": "brand", "label": {"en": "Brand"}, "type": "enum", "enum_set_id": "enum_set:brand"}),
                json!({"name": "link", "label": {"en": "Link"}, "type": "reference"}),
                json!({"name": "homepage", "label": {"en": "Homepage"}, "type": "url", "multilingual": true}),
                json!({"name": "EF000040", "label": {"en": "Width"}, "type": "number", "reference_data": true}),
            ],
            enum_sets: vec![json!({"id": "enum_set:brand", "options": ["enum_option:acme"]})],
            enum_options: vec![
                json!({"id": "enum_option:acme", "code": "ACME", "labels": {"en": "ACME"}}),
            ],
            units: vec![json!({"id": "unit:eur", "symbol": "€", "factor": 1.0})],
            languages: vec![
                json!({"code": "de", "label": {"en": "German"}}),
                json!({"code": "en", "label": {"en": "English"}}),
            ],
            object_types: vec![json!({"name": "product", "label": {"en": "Product"}})],
            statuses: vec![json!({"status": "published"}), json!({"status": "draft"})],
            relations: vec![
                json!({"name": "category_member", "label": {"en": "Category"}, "role": "category", "target_types": ["category"]}),
            ],
        }
    }

    fn leaf(field: &str, operator: &str, value: Value) -> QueryCondition {
        QueryCondition {
            field: Some(field.to_owned()),
            operator: Some(operator.to_owned()),
            value: Some(value),
            ..Default::default()
        }
    }

    fn query(where_: Option<QueryCondition>) -> SavedSearchQuery {
        SavedSearchQuery {
            where_,
            ..Default::default()
        }
    }

    #[test]
    fn catalog_lists_system_and_attribute_fields() {
        let catalog = build_catalog(&sources());
        assert!(catalog.field("type").is_some());
        assert!(catalog.field("status").is_some());
        assert_eq!(
            catalog
                .field("status")
                .unwrap()
                .options
                .as_ref()
                .unwrap()
                .len(),
            2
        );
        let price = catalog.field("attributes.price").unwrap();
        assert_eq!(price.value_type, ValueType::Number);
        assert!(price.operators.contains(&"between"));
        assert!(catalog.field("attributes.name").unwrap().multilingual);
        // `reference` attributes are not filterable.
        assert!(catalog.field("attributes.link").is_none());
        // Reference data stays **filterable** — the flag only lets the client
        // keep the standard's vocabulary out of the offered list, and a stored
        // query using such a field must keep validating.
        let feature = catalog.field("attributes.EF000040").unwrap();
        assert!(feature.reference_data);
        assert!(!price.reference_data);
        assert!(
            validate_query(
                &query(Some(leaf("attributes.EF000040", "gt", json!(1)))),
                &catalog
            )
            .is_empty()
        );
        // Multilingual variants resolve through the base field.
        assert!(catalog.field("attributes.name.de").is_some());
        assert!(catalog.field("attributes.name.zz").is_none());
        // Enum options come from the referenced set.
        let brand = catalog.field("attributes.brand").unwrap();
        assert_eq!(brand.options.as_ref().unwrap()[0]["value"], json!("ACME"));
    }

    #[test]
    fn catalog_declares_references_and_operators() {
        let catalog = build_catalog(&sources());
        assert!(catalog.reference("category_member").is_some());
        assert!(catalog.reference("nope").is_none());
        assert_eq!(catalog.operator("between").unwrap().arity, 2);
        assert!(catalog.operator("in").unwrap().multi);
        assert_eq!(catalog.operator("not_empty").unwrap().arity, 0);
    }

    #[test]
    fn valid_nested_query_passes() {
        let catalog = build_catalog(&sources());
        let query = query(Some(QueryCondition {
            op: Some("and".to_owned()),
            conditions: Some(vec![
                leaf("type", "eq", json!("product")),
                leaf("attributes.price", "between", json!([10, 50])),
                QueryCondition {
                    op: Some("or".to_owned()),
                    conditions: Some(vec![
                        leaf("status", "eq", json!("published")),
                        leaf("status", "eq", json!("draft")),
                    ]),
                    ..Default::default()
                },
            ]),
            ..Default::default()
        }));
        assert!(validate_query(&query, &catalog).is_empty());
    }

    #[test]
    fn unknown_field_and_operator_are_reported_with_path() {
        let catalog = build_catalog(&sources());
        let query = query(Some(QueryCondition {
            op: Some("and".to_owned()),
            conditions: Some(vec![
                leaf("attributes.nope", "eq", json!("x")),
                leaf("attributes.brand", "gt", json!(1)),
            ]),
            ..Default::default()
        }));
        let errors = validate_query(&query, &catalog);
        assert_eq!(errors.len(), 2);
        assert!(errors[0].starts_with("query.where.conditions[0].field: unknown field"));
        assert!(errors[1].starts_with("query.where.conditions[1].operator: `gt` is not allowed"));
    }

    #[test]
    fn value_shape_is_enforced() {
        let catalog = build_catalog(&sources());
        let cases = vec![
            (
                leaf("attributes.brand", "in", json!("ACME")),
                "must be a list",
            ),
            (
                leaf("attributes.brand", "in", json!([])),
                "must not be empty",
            ),
            (
                leaf("attributes.price", "between", json!([1])),
                "must be a list of two values",
            ),
            (
                leaf("attributes.brand", "eq", json!(1)),
                "must be an option code",
            ),
            (
                leaf("attributes.active", "eq", json!("yes")),
                "must be a boolean",
            ),
            (
                leaf("attributes.name", "not_empty", json!("x")),
                "does not take a value",
            ),
        ];
        for (condition, hint) in cases {
            let errors = validate_query(&query(Some(condition)), &catalog);
            assert_eq!(errors.len(), 1, "expected one error for {hint}");
            assert!(
                errors[0].contains(hint),
                "unexpected message: {}",
                errors[0]
            );
        }
    }

    #[test]
    fn eq_null_and_missing_value_are_distinguished() {
        let catalog = build_catalog(&sources());
        assert!(
            validate_query(
                &query(Some(leaf("attributes.price", "eq", Value::Null))),
                &catalog
            )
            .is_empty()
        );
        let errors = validate_query(
            &query(Some(leaf("attributes.price", "gt", Value::Null))),
            &catalog,
        );
        assert!(errors[0].contains("is required for `gt`"));
    }

    #[test]
    fn depth_and_empty_group_are_rejected() {
        let catalog = build_catalog(&sources());
        // One nesting level more than `MAX_DEPTH` allows.
        let mut deep = leaf("type", "eq", json!("product"));
        for _ in 0..MAX_DEPTH {
            deep = group("and", vec![deep]);
        }
        let errors = validate_query(&query(Some(deep)), &catalog);
        assert!(
            errors
                .iter()
                .any(|error| error.contains(&format!("at most {MAX_DEPTH} levels"))),
            "{errors:?}"
        );

        let empty = query(Some(QueryCondition {
            op: Some("and".to_owned()),
            conditions: Some(vec![]),
            ..Default::default()
        }));
        let errors = validate_query(&empty, &catalog);
        assert!(
            errors
                .iter()
                .any(|error| error.contains("at least one condition"))
        );
    }

    #[test]
    fn legacy_filters_are_reported() {
        let catalog = build_catalog(&sources());
        let mut query = query(None);
        query.filters = vec![QueryFilter {
            field: "type".to_owned(),
            operator: "eq".to_owned(),
            value: Some(json!("product")),
        }];
        let errors = validate_query(&query, &catalog);
        assert_eq!(errors.len(), 1);
        assert!(errors[0].starts_with("query.filters: the flat filter list"));
    }

    #[test]
    fn references_sort_and_limit_are_validated() {
        let catalog = build_catalog(&sources());
        let mut query = query(None);
        query.references = vec![QueryReference {
            reference_type: "nope".to_owned(),
            out: String::new(),
            direction: Some("sideways".to_owned()),
        }];
        query.sort = vec![QuerySort {
            field: "attributes.active".to_owned(),
            order: "up".to_owned(),
        }];
        query.limit = 99_999;
        let errors = validate_query(&query, &catalog);
        assert_eq!(errors.len(), 6, "{errors:?}");
        assert!(errors[0].contains("unknown relation type"));
        assert!(errors[1].contains("must not be empty"));
        assert!(errors[2].contains("must be incoming or outgoing"));
        assert!(errors[3].contains("cannot be sorted"));
        assert!(errors[4].contains("must be asc or desc"));
        assert!(errors[5].contains("must be between 1 and 10000"));
    }

    #[test]
    fn normalization_canonicalizes_and_prunes() {
        let mut query = SavedSearchQuery {
            where_: Some(QueryCondition {
                op: None,
                conditions: Some(vec![
                    QueryCondition {
                        field: Some(" type ".to_owned()),
                        operator: Some("neq".to_owned()),
                        value: Some(json!("root")),
                        ..Default::default()
                    },
                    QueryCondition {
                        op: Some("or".to_owned()),
                        conditions: Some(vec![]),
                        ..Default::default()
                    },
                ]),
                ..Default::default()
            }),
            fulltext: Some(crate::api::saved_searches::QueryFulltext {
                fields: None,
                query: "  ".to_owned(),
                language: None,
            }),
            ..Default::default()
        };
        assert!(normalize_query(&mut query));
        let root = query.where_.as_ref().unwrap();
        assert_eq!(root.op.as_deref(), Some("and"));
        let children = root.conditions.as_ref().unwrap();
        assert_eq!(children.len(), 1);
        assert_eq!(children[0].field.as_deref(), Some("type"));
        assert_eq!(children[0].operator.as_deref(), Some("ne"));
        assert!(query.fulltext.is_none());
    }

    #[test]
    fn normalization_drops_an_empty_root() {
        let mut query = query(Some(QueryCondition {
            op: Some("or".to_owned()),
            conditions: Some(vec![]),
            ..Default::default()
        }));
        assert!(normalize_query(&mut query));
        assert!(query.where_.is_none());
        assert!(!normalize_query(&mut query));
    }

    #[test]
    fn summary_counts_conditions_and_groups() {
        let query = query(Some(QueryCondition {
            op: Some("or".to_owned()),
            conditions: Some(vec![
                leaf("type", "eq", json!("product")),
                QueryCondition {
                    op: Some("and".to_owned()),
                    conditions: Some(vec![leaf("status", "eq", json!("draft"))]),
                    ..Default::default()
                },
            ]),
            ..Default::default()
        }));
        let summary = summarize(&query);
        assert_eq!(summary["conditions"], json!(2));
        assert_eq!(summary["groups"], json!(2));
        assert_eq!(summary["op"], json!("or"));
    }

    // -- Rendering ---------------------------------------------------------
    fn group(op: &str, conditions: Vec<QueryCondition>) -> QueryCondition {
        QueryCondition {
            op: Some(op.to_owned()),
            conditions: Some(conditions),
            ..Default::default()
        }
    }

    fn flat_catalog() -> Catalog {
        build_catalog(&CatalogSources::default())
    }

    fn render(condition: &QueryCondition) -> (String, Vec<(String, Value)>) {
        let catalog = flat_catalog();
        let mut params: Vec<(String, Value)> = Vec::new();
        let mut counter = 0usize;
        let sql =
            render_condition(condition, &mut params, &mut counter, &catalog).expect("renders");
        (sql, params)
    }

    #[test]
    fn groups_keep_their_parentheses_and_parameters_stay_unique() {
        let tree = group(
            "and",
            vec![
                leaf("type", "eq", json!("product")),
                group(
                    "or",
                    vec![
                        leaf("status", "eq", json!("published")),
                        group("and", vec![leaf("attributes.price", "gt", json!(10))]),
                    ],
                ),
            ],
        );
        let (sql, params) = render(&tree);
        assert_eq!(
            sql,
            "(type = $f0 AND (status = $f1 OR ((attributes.price IS NOT NONE AND \
             attributes.price != NULL) AND attributes.price > $f2)))"
        );
        let names: Vec<&str> = params.iter().map(|(name, _)| name.as_str()).collect();
        assert_eq!(names, vec!["f0", "f1", "f2"]);
    }

    #[test]
    fn composite_clauses_are_parenthesized() {
        let present = "(attributes.price IS NOT NONE AND attributes.price != NULL)";

        let (sql, params) = render(&leaf("attributes.price", "between", json!([10, 50])));
        assert_eq!(
            sql,
            format!("{present} AND (attributes.price >= $f0 AND attributes.price <= $f1)")
        );
        assert_eq!(params.len(), 2);

        let (sql, _) = render(&leaf("attributes.price", "not_between", json!([10, 50])));
        assert_eq!(
            sql,
            format!("{present} AND (attributes.price < $f0 OR attributes.price > $f1)")
        );

        // `NONE < 0` is true in SurrealDB, so comparisons must require a value.
        let (sql, _) = render(&leaf("attributes.price", "lt", json!(10)));
        assert_eq!(sql, format!("{present} AND attributes.price < $f0"));

        let (sql, params) = render(&leaf("attributes.name", "is_null", Value::Null));
        assert_eq!(sql, "(attributes.name IS NONE OR attributes.name = NULL)");
        assert!(params.is_empty());

        let (sql, params) = render(&leaf("attributes.name", "not_empty", Value::Null));
        assert_eq!(
            sql,
            "(attributes.name IS NOT NONE AND attributes.name != NULL)"
        );
        assert!(params.is_empty());
    }

    #[test]
    fn null_comparisons_render_is_none() {
        let (sql, params) = render(&leaf("attributes.price", "eq", Value::Null));
        assert_eq!(sql, "attributes.price IS NONE");
        assert!(params.is_empty());

        let (sql, _) = render(&leaf("attributes.price", "ne", Value::Null));
        assert_eq!(sql, "attributes.price IS NOT NONE");
    }

    #[test]
    fn text_operators_and_multilingual_fields_render() {
        let (sql, params) = render(&leaf("attributes.name.de", "starts_with", json!("Edel")));
        assert_eq!(
            sql,
            "string::startsWith(string::lower(attributes.name.de), string::lower($f0))"
        );
        assert_eq!(params[0].1, json!("Edel"));

        let (sql, params) = render(&leaf("status", "in", json!(["published", "draft"])));
        assert_eq!(sql, "status IN $f0");
        assert_eq!(params[0].1, json!(["published", "draft"]));
    }

    #[test]
    fn unsupported_fields_and_operators_are_rejected() {
        let catalog = flat_catalog();
        let mut params: Vec<(String, Value)> = Vec::new();
        let mut counter = 0usize;
        assert!(
            render_filter(
                &leaf("attributes.name`; DROP", "eq", json!("x")),
                &mut params,
                &mut counter,
                &catalog
            )
            .is_err()
        );
        assert!(
            render_filter(
                &leaf("type", "matches", json!("x")),
                &mut params,
                &mut counter,
                &catalog
            )
            .is_err()
        );
    }

    #[test]
    fn datetime_system_fields_are_wrapped() {
        let (sql, params) = render(&leaf("created_at", "gte", json!("2026-01-01")));
        assert_eq!(
            sql,
            "(created_at IS NOT NONE AND created_at != NULL) AND created_at >= type::datetime($f0)"
        );
        assert_eq!(params.len(), 1);
        assert_eq!(params[0].1, json!("2026-01-01"));

        let (sql, _) = render(&leaf(
            "updated_at",
            "between",
            json!(["2026-01-01", "2026-02-01"]),
        ));
        assert_eq!(
            sql,
            "(updated_at IS NOT NONE AND updated_at != NULL) AND (updated_at >= \
             type::datetime($f0) AND updated_at <= type::datetime($f1))"
        );

        let (sql, _) = render(&leaf("attributes.price", "gte", json!(1)));
        assert_eq!(
            sql,
            "(attributes.price IS NOT NONE AND attributes.price != NULL) AND \
             attributes.price >= $f0"
        );
    }

    #[test]
    fn render_query_covers_fulltext_and_references() {
        let catalog = flat_catalog();
        let query = SavedSearchQuery {
            where_: Some(leaf("type", "eq", json!("product"))),
            fulltext: Some(crate::api::saved_searches::QueryFulltext {
                fields: None,
                query: " Edelstahl ".to_owned(),
                language: None,
            }),
            references: vec![QueryReference {
                reference_type: "category_member".to_owned(),
                out: "object:cat_1".to_owned(),
                direction: None,
            }],
            ..Default::default()
        };
        let rendered = render_query(&query, &catalog).expect("renders");
        assert_eq!(rendered.clauses.len(), 3);
        assert_eq!(rendered.clauses[0], "type = $f0");
        assert_eq!(rendered.clauses[1], "attributes @@ $f1");
        assert!(rendered.clauses[2].contains("definition_id = $f2"));
        assert!(rendered.clauses[2].contains("out = type::record('object', $f3)"));
        let values: Vec<&Value> = rendered.params.iter().map(|(_, value)| value).collect();
        assert_eq!(values[0], &json!("product"));
        assert_eq!(values[1], &json!("Edelstahl"));
        assert_eq!(values[2], &json!("rel_category_member"));
        assert_eq!(values[3], &json!("cat_1"));
    }

    #[test]
    fn render_query_skips_an_empty_tree_and_empty_fulltext() {
        let catalog = flat_catalog();
        let mut query = SavedSearchQuery::default();
        query.fulltext = Some(crate::api::saved_searches::QueryFulltext {
            fields: None,
            query: "   ".to_owned(),
            language: None,
        });
        let rendered = render_query(&query, &catalog).expect("renders");
        assert!(rendered.clauses.is_empty());
        assert!(rendered.params.is_empty());
    }

    #[test]
    fn max_depth_allows_six_levels() {
        assert_eq!(MAX_DEPTH, 6);
        let catalog = flat_catalog();
        let mut node = leaf("type", "eq", json!("product"));
        // `MAX_DEPTH - 1` nested groups around a leaf = the deepest valid tree.
        for _ in 0..(MAX_DEPTH - 1) {
            node = group("and", vec![node]);
        }
        assert!(validate_query(&query(Some(node.clone())), &catalog).is_empty());
        let errors = validate_query(&query(Some(group("and", vec![node]))), &catalog);
        assert!(
            errors
                .iter()
                .any(|error| error.contains("at most 6 levels")),
            "{errors:?}"
        );
    }

    #[test]
    fn url_attributes_are_filterable_text_fields() {
        let catalog = build_catalog(&sources());
        let homepage = catalog.field("attributes.homepage").unwrap();
        assert_eq!(homepage.value_type, ValueType::Url);
        assert_eq!(homepage.widget, Widget::Text);
        assert!(homepage.multilingual);
        for operator in [
            "eq",
            "ne",
            "contains",
            "starts_with",
            "in",
            "not_empty",
            "is_null",
        ] {
            assert!(
                homepage.operators.contains(&operator),
                "expected `{operator}` for a url field"
            );
        }
        // Ordering operators make no sense for an address.
        for operator in ["gt", "between", "not_between"] {
            assert!(
                !homepage.operators.contains(&operator),
                "`{operator}` must not be offered for a url field"
            );
        }
        // The value must be a string (the type hint names the URL).
        let invalid = query(Some(leaf("attributes.homepage", "eq", json!(42))));
        let errors = validate_query(&invalid, &catalog);
        assert_eq!(errors, vec!["query.where.value: must be a URL".to_owned()]);
    }

    #[test]
    fn not_between_is_offered_for_numeric_fields_only() {
        let catalog = build_catalog(&sources());
        assert!(
            catalog
                .field("attributes.price")
                .unwrap()
                .operators
                .contains(&"not_between")
        );
        assert!(
            !catalog
                .field("attributes.brand")
                .unwrap()
                .operators
                .contains(&"not_between")
        );
        assert_eq!(catalog.operator("not_between").unwrap().arity, 2);
        let invalid = query(Some(leaf("attributes.price", "not_between", json!([1]))));
        let errors = validate_query(&invalid, &catalog);
        assert!(errors[0].contains("must be a list of two values"));
    }
}
