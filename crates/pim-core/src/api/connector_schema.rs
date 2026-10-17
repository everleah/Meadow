//! Declarative parameter schema of the connector types.
//!
//! A connector stores its configuration as the free-form `parameters` object.
//! This module owns the **description** of that object per connector type and
//! the rules derived from it, so that the core, the admin UI and any other
//! client share one source of truth (`GET /connector-types`):
//!
//! * `schema_document()` — the machine-readable description the UI renders.
//! * `validate()` / `normalize()` — server-side enforcement of types, required
//!   fields, value ranges and enums (messages name the field path, e.g.
//!   `parameters.url: is required`).
//! * `apply_defaults()` — fills declared defaults when a connector is created.
//! * `merge_parameters()` — recursive merge for form-based partial updates
//!   (`PATCH /connectors/{id}` with `parameters_mode: "merge"`, `null` deletes).
//!
//! Contract: every declared field is evaluated by a consumer, and every
//! evaluated field is declared here. All three built-in types are wired today:
//! `webhook` is read by the webhook outbound worker, `json_import`/`json_export`
//! are the default profiles of `POST /imports` and `POST /exports` (see
//! `plan/detail/konnektor-konfiguration.md`). The `wired`/`note_key` fields stay
//! in the contract so a future type without a consumer can be declared honestly.

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, json};

use crate::event;

/// Data type of a declared connector parameter.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FieldKind {
    String,
    Number,
    Integer,
    Boolean,
    StringList,
    /// A list of numbers — one value per repetition, e.g. a stepped backoff.
    NumberList,
    StringMap,
    Json,
}

/// How the admin UI renders a field.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Widget {
    Text,
    Password,
    Textarea,
    Select,
    Multiselect,
    Switch,
    Number,
    /// Comma-separated numbers (`number_list`).
    NumberList,
    KeyValue,
    Json,
}

/// Conditional visibility of a field (`show_if`), evaluated by the UI only.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ShowIf {
    /// Path of the controlling field.
    pub key: String,
    /// Show the field when the controlling value equals this value.
    pub equals: Value,
}

/// A single declared parameter.
///
/// Owned strings, because a registered connector type brings its schema from the
/// database (`connector_type`); the built-in types fill the same struct from
/// literals, so one validator serves both.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Field {
    /// Path inside `parameters`; nested paths use dots (`filter.event_types`).
    pub key: String,
    #[serde(rename = "type")]
    pub kind: FieldKind,
    pub widget: Widget,
    #[serde(default, skip_serializing_if = "is_false")]
    pub required: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub default: Option<Value>,
    #[serde(rename = "enum", default, skip_serializing_if = "Option::is_none")]
    pub enum_values: Option<Vec<String>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub options_source: Option<String>,
    /// i18n key of the field label (never a literal text).
    #[serde(default)]
    pub label_key: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub help_key: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub minimum: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub maximum: Option<f64>,
    #[serde(default, skip_serializing_if = "is_false")]
    pub secret: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub show_if: Option<ShowIf>,
}

fn is_false(value: &bool) -> bool {
    !*value
}

impl Field {
    fn new(key: impl Into<String>, kind: FieldKind, widget: Widget) -> Self {
        Self {
            key: key.into(),
            kind,
            widget,
            required: false,
            default: None,
            enum_values: None,
            options_source: None,
            label_key: String::new(),
            help_key: None,
            minimum: None,
            maximum: None,
            secret: false,
            show_if: None,
        }
    }

    fn label(mut self, key: impl Into<String>) -> Self {
        self.label_key = key.into();
        self
    }

    fn help(mut self, key: impl Into<String>) -> Self {
        self.help_key = Some(key.into());
        self
    }

    fn required(mut self) -> Self {
        self.required = true;
        self
    }

    fn default(mut self, value: Value) -> Self {
        self.default = Some(value);
        self
    }

    fn options(mut self, values: &[&str]) -> Self {
        self.enum_values = Some(values.iter().map(|value| (*value).to_owned()).collect());
        self
    }

    fn options_from(mut self, source: impl Into<String>) -> Self {
        self.options_source = Some(source.into());
        self
    }

    fn range(mut self, minimum: f64, maximum: f64) -> Self {
        self.minimum = Some(minimum);
        self.maximum = Some(maximum);
        self
    }

    fn secret(mut self) -> Self {
        self.secret = true;
        self
    }

    fn show_if(mut self, key: impl Into<String>, equals: Value) -> Self {
        self.show_if = Some(ShowIf {
            key: key.into(),
            equals,
        });
        self
    }
}

/// A group of related fields (one collapsible section in the UI).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Group {
    pub key: String,
    pub label_key: String,
    pub fields: Vec<Field>,
}

/// Description of one connector type.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TypeSpec {
    #[serde(rename = "type")]
    pub type_key: String,
    pub label_key: String,
    /// `inbound` or `outbound` (display hint).
    pub direction: String,
    /// Whether a consumer actually reads the parameters.
    #[serde(default)]
    pub wired: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub note_key: Option<String>,
    #[serde(default)]
    pub groups: Vec<Group>,
}

/// The event types the core publishes (`event.rs`), offered in the filter.
fn event_type_values() -> Vec<&'static str> {
    vec![
        event::EVENT_OBJECT_CREATED,
        event::EVENT_OBJECT_UPDATED,
        event::EVENT_OBJECT_DELETED,
        event::EVENT_REFERENCE_CREATED,
        event::EVENT_REFERENCE_DELETED,
        event::EVENT_ATTRIBUTE_DEFINITION_CREATED,
        event::EVENT_ATTRIBUTE_DEFINITION_UPDATED,
        event::EVENT_ATTRIBUTE_DEFINITION_DELETED,
        event::EVENT_IMPORT_COMPLETED,
    ]
}

fn webhook_spec() -> TypeSpec {
    TypeSpec {
        type_key: "webhook".into(),
        label_key: "connectors.type_webhook".into(),
        direction: "outbound".into(),
        wired: true,
        note_key: None,
        groups: vec![
            Group {
                key: "target".into(),
                label_key: "connector_fields.group_target".into(),
                fields: vec![
                    Field::new("url", FieldKind::String, Widget::Text)
                        .label("connector_fields.webhook.url.label")
                        .help("connector_fields.webhook.url.help")
                        .required(),
                    Field::new("method", FieldKind::String, Widget::Select)
                        .label("connector_fields.webhook.method.label")
                        .options(&["POST", "PUT", "PATCH"])
                        .default(json!("POST")),
                    Field::new("timeout_s", FieldKind::Number, Widget::Number)
                        .label("connector_fields.webhook.timeout_s.label")
                        .help("connector_fields.webhook.timeout_s.help")
                        .range(1.0, 600.0)
                        .default(json!(30.0)),
                ],
            },
            Group {
                key: "headers".into(),
                label_key: "connector_fields.group_headers".into(),
                fields: vec![
                    Field::new("headers", FieldKind::StringMap, Widget::KeyValue)
                        .label("connector_fields.webhook.headers.label")
                        .help("connector_fields.webhook.headers.help")
                        .secret()
                        .default(json!({})),
                ],
            },
            Group {
                key: "payload".into(),
                label_key: "connector_fields.group_payload".into(),
                fields: vec![
                    Field::new("payload_mode", FieldKind::String, Widget::Select)
                        .label("connector_fields.webhook.payload_mode.label")
                        .help("connector_fields.webhook.payload_mode.help")
                        .options(&["event", "object"])
                        .default(json!("event")),
                ],
            },
            Group {
                key: "filter".into(),
                label_key: "connector_fields.group_filter".into(),
                fields: vec![
                    Field::new(
                        "filter.event_types",
                        FieldKind::StringList,
                        Widget::Multiselect,
                    )
                    .label("connector_fields.webhook.filter_event_types.label")
                    .help("connector_fields.webhook.filter_event_types.help")
                    .options(&event_type_values())
                    .default(json!([])),
                    Field::new(
                        "filter.object_types",
                        FieldKind::StringList,
                        Widget::Multiselect,
                    )
                    .label("connector_fields.webhook.filter_object_types.label")
                    .help("connector_fields.webhook.filter_object_types.help")
                    .options_from("object_types")
                    .default(json!([])),
                    Field::new(
                        "filter.attribute_changes",
                        FieldKind::StringList,
                        Widget::Multiselect,
                    )
                    .label("connector_fields.webhook.filter_attribute_changes.label")
                    .help("connector_fields.webhook.filter_attribute_changes.help")
                    .options_from("attribute_names")
                    .default(json!([])),
                ],
            },
            Group {
                key: "coalesce".into(),
                label_key: "connector_fields.group_coalesce".into(),
                fields: vec![
                    Field::new("coalesce.enabled", FieldKind::Boolean, Widget::Switch)
                        .label("connector_fields.webhook.coalesce_enabled.label")
                        .help("connector_fields.webhook.coalesce_enabled.help")
                        .default(json!(false)),
                    Field::new("coalesce.window_seconds", FieldKind::Number, Widget::Number)
                        .label("connector_fields.webhook.coalesce_window_seconds.label")
                        .help("connector_fields.webhook.coalesce_window_seconds.help")
                        .range(1.0, 3600.0)
                        .default(json!(30.0))
                        .show_if("coalesce.enabled", json!(true)),
                ],
            },
            Group {
                key: "retry".into(),
                label_key: "connector_fields.group_retry".into(),
                fields: vec![
                    Field::new(
                        "retry_policy.max_attempts",
                        FieldKind::Integer,
                        Widget::Number,
                    )
                    .label("connector_fields.webhook.retry_max_attempts.label")
                    .help("connector_fields.webhook.retry_max_attempts.help")
                    .range(1.0, 10.0)
                    .default(json!(5)),
                    Field::new(
                        "retry_policy.backoff_seconds",
                        FieldKind::NumberList,
                        Widget::NumberList,
                    )
                    .label("connector_fields.webhook.retry_backoff_seconds.label")
                    .help("connector_fields.webhook.retry_backoff_seconds.help")
                    .range(0.1, 3600.0)
                    .default(json!([5.0])),
                ],
            },
        ],
    }
}

/// `json_import`: profile for `POST /imports` (the job takes the values the
/// request does not set).
fn json_import_spec() -> TypeSpec {
    TypeSpec {
        type_key: "json_import".into(),
        label_key: "connectors.type_json_import".into(),
        direction: "inbound".into(),
        wired: true,
        note_key: None,
        groups: vec![Group {
            key: "import".into(),
            label_key: "connector_fields.group_import".into(),
            fields: vec![
                Field::new("match_by_key", FieldKind::String, Widget::Text)
                    .label("connector_fields.json_import.match_by_key.label")
                    .help("connector_fields.json_import.match_by_key.help"),
                Field::new("match_by_id", FieldKind::Boolean, Widget::Switch)
                    .label("connector_fields.json_import.match_by_id.label")
                    .help("connector_fields.json_import.match_by_id.help")
                    .default(json!(false)),
                Field::new("emit_events", FieldKind::Boolean, Widget::Switch)
                    .label("connector_fields.json_import.emit_events.label")
                    .help("connector_fields.json_import.emit_events.help")
                    .default(json!(true)),
                Field::new("source", FieldKind::String, Widget::Text)
                    .label("connector_fields.json_import.source.label")
                    .help("connector_fields.json_import.source.help"),
            ],
        }],
    }
}

/// `json_export`: profile for `POST /exports` (the request wins where it sets a
/// value).
fn json_export_spec() -> TypeSpec {
    TypeSpec {
        type_key: "json_export".into(),
        label_key: "connectors.type_json_export".into(),
        direction: "outbound".into(),
        wired: true,
        note_key: None,
        groups: vec![Group {
            key: "export".into(),
            label_key: "connector_fields.group_export".into(),
            fields: vec![
                Field::new("format", FieldKind::String, Widget::Select)
                    .label("connector_fields.json_export.format.label")
                    .help("connector_fields.json_export.format.help")
                    .options(&["csv", "xlsx", "json"])
                    .default(json!("csv")),
                Field::new("fields", FieldKind::StringList, Widget::Multiselect)
                    .label("connector_fields.json_export.fields.label")
                    .help("connector_fields.json_export.fields.help")
                    .options_from("attribute_names")
                    .default(json!([])),
                Field::new("locales", FieldKind::StringList, Widget::Multiselect)
                    .label("connector_fields.json_export.locales.label")
                    .help("connector_fields.json_export.locales.help")
                    .options_from("languages")
                    .default(json!([])),
                Field::new("channel", FieldKind::String, Widget::Select)
                    .label("connector_fields.json_export.channel.label")
                    .help("connector_fields.json_export.channel.help")
                    .options_from("channels")
                    .default(json!("")),
                Field::new(
                    "options.email_to",
                    FieldKind::StringList,
                    Widget::Multiselect,
                )
                .label("connector_fields.json_export.options_email_to.label")
                .help("connector_fields.json_export.options_email_to.help")
                .default(json!([])),
            ],
        }],
    }
}

/// All **built-in** connector types known to the core.
pub fn specs() -> Vec<TypeSpec> {
    vec![webhook_spec(), json_import_spec(), json_export_spec()]
}

/// The spec of one built-in connector type.
pub fn spec(type_key: &str) -> Option<TypeSpec> {
    specs().into_iter().find(|spec| spec.type_key == type_key)
}

/// The connector types a request may use: the built-ins plus the registered ones.
///
/// A registered type carries its schema from the `connector_type` table
/// (`plan/detail/konnektor-konfiguration.md` §14.1), so one validator, one form and
/// one `GET /connector-types` serve both kinds. Registered types are `wired: false`
/// for now: no consumer reads their parameters yet, and the UI says so instead of
/// pretending. A registered name that matches a built-in is ignored — the built-in
/// wins, so a document cannot shadow `webhook`.
#[derive(Debug, Clone, Default)]
pub struct Catalog {
    registered: Vec<TypeSpec>,
}

impl Catalog {
    /// Only the built-in types (tests, and callers that read no database).
    pub fn builtin() -> Self {
        Self::default()
    }

    /// Adds the specs stored in `connector_type` (built-in names win, first wins).
    pub fn with_registered(mut self, mut specs: Vec<TypeSpec>) -> Self {
        specs.retain(|candidate| spec(type_key_of(candidate)).is_none());
        for spec in specs {
            if !self
                .registered
                .iter()
                .any(|known| known.type_key == spec.type_key)
            {
                self.registered.push(spec);
            }
        }
        self
    }

    /// The spec of a type — built-in first, then a registered one.
    pub fn spec(&self, type_key: &str) -> Option<TypeSpec> {
        spec(type_key).or_else(|| {
            self.registered
                .iter()
                .find(|spec| spec.type_key == type_key)
                .cloned()
        })
    }

    /// Every type this catalog knows (built-ins first).
    pub fn specs(&self) -> Vec<TypeSpec> {
        let mut all = specs();
        all.extend(self.registered.iter().cloned());
        all
    }

    /// Whether a connector may use this type.
    pub fn is_known_type(&self, type_key: &str) -> bool {
        self.spec(type_key).is_some()
    }

    /// The registered (non-built-in) types only — the admin page edits these.
    pub fn registered(&self) -> &[TypeSpec] {
        &self.registered
    }

    /// The schema document of every type (`GET /connector-types`).
    pub fn schema_document(&self) -> Value {
        serde_json::to_value(self.specs()).unwrap_or_else(|_| json!([]))
    }

    /// Every declared field of a type.
    pub fn fields(&self, type_key: &str) -> Vec<Field> {
        self.spec(type_key)
            .map(|spec| {
                spec.groups
                    .into_iter()
                    .flat_map(|group| group.fields)
                    .collect()
            })
            .unwrap_or_default()
    }

    // The schema rules over this catalog: a registered type is validated,
    // normalized and exported exactly like a built-in one.

    /// Fills declared defaults for the fields that are not set yet.
    pub fn apply_defaults(&self, type_key: &str, parameters: &mut Map<String, Value>) {
        apply_defaults_fields(&self.fields(type_key), parameters);
    }

    /// Trims strings, drops empty list entries, wraps a legacy scalar into a list.
    pub fn normalize(&self, type_key: &str, parameters: &mut Map<String, Value>) {
        normalize_fields(&self.fields(type_key), parameters);
    }

    /// One problem per violating field (`plan/detail/konnektor-konfiguration.md` §14.4).
    pub fn validate(&self, type_key: &str, parameters: &Map<String, Value>) -> Vec<Problem> {
        validate_fields(&self.fields(type_key), parameters)
    }

    /// [`Catalog::validate`] plus the undeclared-key rule when `strict` is on.
    pub fn validate_strict(
        &self,
        type_key: &str,
        parameters: &Map<String, Value>,
        strict: bool,
    ) -> Vec<Problem> {
        validate_strict_fields(&self.fields(type_key), parameters, strict)
    }

    /// Top-level keys no declared field uses.
    pub fn unknown_keys(&self, type_key: &str, parameters: &Map<String, Value>) -> Vec<String> {
        unknown_keys_fields(&self.fields(type_key), parameters)
    }

    /// The parameters without their secret fields (MCF export).
    pub fn without_secrets(
        &self,
        type_key: &str,
        parameters: &Map<String, Value>,
    ) -> Map<String, Value> {
        without_secrets_fields(&self.fields(type_key), parameters)
    }

    /// Splits the declared secret fields out of `parameters` (write path).
    pub fn split_secrets(
        &self,
        connector_id: &str,
        type_key: &str,
        parameters: &Map<String, Value>,
    ) -> SplitSecrets {
        split_secrets_fields(connector_id, &self.fields(type_key), parameters)
    }

    /// Replaces secret references with their stored values (the worker's view).
    pub fn resolve_secret_references(
        &self,
        type_key: &str,
        parameters: &Map<String, Value>,
        values: &Map<String, Value>,
    ) -> Map<String, Value> {
        resolve_secret_references_fields(&self.fields(type_key), parameters, values)
    }
}

/// The type key of a spec (the `type` field, not the Rust field name).
fn type_key_of(spec: &TypeSpec) -> &str {
    spec.type_key.as_str()
}

/// The schema of every **built-in** type (`GET /connector-types` without a database).
pub fn schema_document() -> Value {
    serde_json::to_value(specs()).unwrap_or_else(|_| json!([]))
}

/// Every declared field of a built-in type.
fn fields(type_key: &str) -> Vec<Field> {
    spec(type_key)
        .map(|spec| {
            spec.groups
                .into_iter()
                .flat_map(|group| group.fields)
                .collect()
        })
        .unwrap_or_default()
}

/// Reads a dotted path from `parameters`.
fn get_path<'a>(parameters: &'a Map<String, Value>, key: &str) -> Option<&'a Value> {
    let mut parts = key.split('.');
    let mut current = parameters.get(parts.next()?)?;
    for part in parts {
        current = current.as_object()?.get(part)?;
    }
    Some(current)
}

/// Writes a dotted path, creating the parent objects as needed.
fn set_path(parameters: &mut Map<String, Value>, key: &str, value: Value) {
    let parts: Vec<&str> = key.split('.').collect();
    let Some((head, tail)) = parts.split_first() else {
        return;
    };
    if tail.is_empty() {
        parameters.insert((*head).to_owned(), value);
        return;
    }
    let entry = parameters
        .entry((*head).to_owned())
        .or_insert_with(|| Value::Object(Map::new()));
    if !entry.is_object() {
        *entry = Value::Object(Map::new());
    }
    if let Some(child) = entry.as_object_mut() {
        set_path(child, &tail.join("."), value);
    }
}

/// Removes a dotted path (missing parents are ignored).
fn remove_path(parameters: &mut Map<String, Value>, key: &str) {
    let parts: Vec<&str> = key.split('.').collect();
    let Some((head, tail)) = parts.split_first() else {
        return;
    };
    if tail.is_empty() {
        parameters.remove(*head);
        return;
    }
    if let Some(Value::Object(child)) = parameters.get_mut(*head) {
        remove_path(child, &tail.join("."));
    }
}

/// Returns the first path prefix that exists but is not an object.
///
/// A declared nested field (`filter.event_types`) is silently ignored when
/// `filter` is a string, which is exactly the kind of misconfiguration the
/// schema is supposed to catch.
fn non_object_prefix(parameters: &Map<String, Value>, key: &str) -> Option<String> {
    let parts: Vec<&str> = key.split('.').collect();
    for index in 1..parts.len() {
        let prefix = parts[..index].join(".");
        if let Some(value) = get_path(parameters, &prefix)
            && !value.is_object()
        {
            return Some(prefix);
        }
    }
    None
}

/// Fills declared defaults for every field that is not set yet.
pub fn apply_defaults(type_key: &str, parameters: &mut Map<String, Value>) {
    apply_defaults_fields(&fields(type_key), parameters);
}

/// [`apply_defaults`] over a resolved field list (built-in or registered type).
fn apply_defaults_fields(declared: &[Field], parameters: &mut Map<String, Value>) {
    for field in declared {
        if get_path(parameters, &field.key).is_some() {
            continue;
        }
        if let Some(default) = &field.default {
            set_path(parameters, &field.key, default.clone());
        }
    }
}

/// Trims strings, drops empty list entries and removes `null` declared fields.
pub fn normalize(type_key: &str, parameters: &mut Map<String, Value>) {
    normalize_fields(&fields(type_key), parameters);
}

/// [`normalize`] over a resolved field list.
fn normalize_fields(declared: &[Field], parameters: &mut Map<String, Value>) {
    for field in declared {
        let Some(value) = get_path(parameters, &field.key).cloned() else {
            continue;
        };
        if value.is_null() {
            remove_path(parameters, &field.key);
            continue;
        }
        match field.kind {
            FieldKind::String => {
                if let Some(text) = value.as_str() {
                    let trimmed = text.trim();
                    if trimmed.is_empty() {
                        remove_path(parameters, &field.key);
                    } else if trimmed != text {
                        set_path(parameters, &field.key, Value::String(trimmed.to_owned()));
                    }
                }
            }
            FieldKind::StringList => {
                if let Some(items) = value.as_array() {
                    let mut cleaned: Vec<Value> = Vec::new();
                    for item in items {
                        let Some(text) = item.as_str() else { continue };
                        let trimmed = text.trim();
                        if trimmed.is_empty() {
                            continue;
                        }
                        if cleaned.iter().any(|entry| entry.as_str() == Some(trimmed)) {
                            continue;
                        }
                        cleaned.push(Value::String(trimmed.to_owned()));
                    }
                    if &cleaned != items {
                        set_path(parameters, &field.key, Value::Array(cleaned));
                    }
                }
            }
            // A single number where a list is declared is the pre-`number_list`
            // form of the field (`retry_policy.backoff_seconds` was a scalar).
            // Wrapping it keeps stored connectors editable instead of failing the
            // check on their next save.
            FieldKind::NumberList => {
                if let Some(number) = value.as_f64() {
                    set_path(parameters, &field.key, json!([number]));
                }
            }
            _ => {}
        }
    }
}

/// One violation of the parameter schema.
///
/// The path travels separately from the text so a form can mark the offending
/// widget instead of only showing a sentence, and the API can answer
/// `{"error": …, "fields": {"parameters.url": "is required"}}`
/// (`plan/detail/konnektor-konfiguration.md` §14.4).
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Problem {
    /// Dotted path inside `parameters` (`url`, `retry_policy.backoff_seconds`).
    pub field: String,
    /// What is wrong with the field.
    pub message: String,
}

impl Problem {
    fn new(field: impl Into<String>, message: impl Into<String>) -> Self {
        Self {
            field: field.into(),
            message: message.into(),
        }
    }

    /// `parameters.url: is required` — the flat wording used in logs and notices.
    pub fn text(&self) -> String {
        format!("parameters.{}: {}", self.field, self.message)
    }
}

/// Validates `parameters` against the type's schema.
///
/// Returns one problem per violating field, each naming the field path. An empty
/// result means "valid". Types without declared fields accept any object (nothing
/// is enforced there yet).
pub fn validate(type_key: &str, parameters: &Map<String, Value>) -> Vec<Problem> {
    validate_fields(&fields(type_key), parameters)
}

/// [`validate`] over a resolved field list.
fn validate_fields(declared: &[Field], parameters: &Map<String, Value>) -> Vec<Problem> {
    let mut errors: Vec<Problem> = Vec::new();
    for field in declared {
        if let Some(prefix) = non_object_prefix(parameters, &field.key) {
            push_once(&mut errors, Problem::new(prefix, "must be an object"));
            continue;
        }
        let value = get_path(parameters, &field.key).filter(|value| !value.is_null());
        let Some(value) = value else {
            if field.required {
                errors.push(Problem::new(&field.key, "is required"));
            }
            continue;
        };
        if let Err(reason) = check_field(field, value) {
            errors.push(Problem::new(&field.key, reason));
        }
    }
    errors
}

fn push_once(errors: &mut Vec<Problem>, problem: Problem) {
    if !errors.iter().any(|seen| seen.field == problem.field) {
        errors.push(problem);
    }
}

/// The parameters with every `secret` field removed.
///
/// Used where the values must not travel — the MCF export — and where an incoming
/// document must not overwrite them (the import); see
/// `plan/detail/konnektor-konfiguration.md` §14.2/§14.3. `headers` is declared
/// `secret` as a whole, so a map field disappears entirely: its entries are the
/// credentials.
pub fn without_secrets(type_key: &str, parameters: &Map<String, Value>) -> Map<String, Value> {
    without_secrets_fields(&fields(type_key), parameters)
}

/// [`without_secrets`] over a resolved field list.
fn without_secrets_fields(
    declared: &[Field],
    parameters: &Map<String, Value>,
) -> Map<String, Value> {
    let mut copy = parameters.clone();
    for field in declared.iter().filter(|field| field.secret) {
        remove_path(&mut copy, &field.key);
    }
    copy
}

/// One declared secret field as it is stored apart from `parameters`.
///
/// The write path splits a configuration before it is saved: the values go into the
/// `connector_secret` record, `parameters` keeps a reference (`{$secret: id#path}`),
/// and the rest of the configuration is stored unchanged
/// (`plan/detail/konnektor-konfiguration.md` §14.2).
#[derive(Debug, Default)]
pub struct SplitSecrets {
    /// `parameters` with a reference in place of every stored secret.
    pub parameters: Map<String, Value>,
    /// Paths whose value has to be written to the secret record.
    pub store: Map<String, Value>,
    /// Paths that were emptied: the stored value and its reference go away.
    pub cleared: Vec<String>,
}

/// The reference that stands in for a secret value.
pub fn secret_reference(connector_id: &str, path: &str) -> Value {
    json!({ "$secret": format!("{connector_id}#{path}") })
}

/// Whether a value is such a reference (a marker, never a secret).
pub fn is_secret_reference(value: &Value) -> bool {
    value
        .as_object()
        .is_some_and(|map| map.len() == 1 && map.get("$secret").is_some_and(Value::is_string))
}

/// An empty value: nothing to keep apart.
fn is_empty(value: &Value) -> bool {
    match value {
        Value::Null => true,
        Value::String(text) => text.trim().is_empty(),
        Value::Array(items) => items.is_empty(),
        Value::Object(map) => map.is_empty(),
        Value::Bool(flag) => !*flag,
        Value::Number(_) => false,
    }
}

/// Pulls the declared secret fields out of `parameters`.
///
/// A value that is **already** a reference means "unchanged": the form never holds
/// the real value, so re-saving a connector must not clear the stored one. An empty
/// or missing value clears it — that is how a rotation to "no token" is expressed.
pub fn split_secrets(
    connector_id: &str,
    type_key: &str,
    parameters: &Map<String, Value>,
) -> SplitSecrets {
    split_secrets_fields(connector_id, &fields(type_key), parameters)
}

/// [`split_secrets`] over a resolved field list.
fn split_secrets_fields(
    connector_id: &str,
    declared: &[Field],
    parameters: &Map<String, Value>,
) -> SplitSecrets {
    let mut split = SplitSecrets {
        parameters: parameters.clone(),
        ..SplitSecrets::default()
    };
    for field in declared.iter().filter(|field| field.secret) {
        match get_path(parameters, &field.key) {
            Some(value) if is_secret_reference(value) => {}
            Some(value) if !is_empty(value) => {
                split.store.insert(field.key.to_owned(), value.clone());
                set_path(
                    &mut split.parameters,
                    &field.key,
                    secret_reference(connector_id, &field.key),
                );
            }
            _ => {
                remove_path(&mut split.parameters, &field.key);
                split.cleared.push(field.key.to_owned());
            }
        }
    }
    split
}

/// Replaces the secret references with the stored values (the worker's view).
///
/// A reference without a stored value is **dropped**, never forwarded: a payload must
/// not carry `{"$secret": …}` to a middleware.
pub fn resolve_secret_references(
    type_key: &str,
    parameters: &Map<String, Value>,
    values: &Map<String, Value>,
) -> Map<String, Value> {
    resolve_secret_references_fields(&fields(type_key), parameters, values)
}

/// [`resolve_secret_references`] over a resolved field list.
fn resolve_secret_references_fields(
    declared: &[Field],
    parameters: &Map<String, Value>,
    values: &Map<String, Value>,
) -> Map<String, Value> {
    let mut resolved = parameters.clone();
    for field in declared.iter().filter(|field| field.secret) {
        let Some(reference) = get_path(&resolved, &field.key) else {
            continue;
        };
        if !is_secret_reference(reference) {
            continue;
        }
        match values.get(&field.key) {
            Some(value) => set_path(&mut resolved, &field.key, value.clone()),
            None => remove_path(&mut resolved, &field.key),
        }
    }
    resolved
}

/// Top-level keys of `parameters` that no declared field uses.
///
/// Only the first path segment is judged: a declared object (`filter`, `headers`)
/// carries free-form content by design (a map field, a `json` field), so nested
/// names are not second-guessed. Sorted, so the message is stable.
pub fn unknown_keys(type_key: &str, parameters: &Map<String, Value>) -> Vec<String> {
    unknown_keys_fields(&fields(type_key), parameters)
}

/// [`unknown_keys`] over a resolved field list.
fn unknown_keys_fields(declared: &[Field], parameters: &Map<String, Value>) -> Vec<String> {
    let known: Vec<&str> = declared
        .iter()
        .filter_map(|field| field.key.split('.').next())
        .collect();
    let mut unknown: Vec<String> = parameters
        .keys()
        .filter(|key| !known.contains(&key.as_str()))
        .cloned()
        .collect();
    unknown.sort();
    unknown
}

/// [`validate`] plus the undeclared-key rule (`connector_strict_parameters`).
///
/// Strict mode is opt-in (`plan/detail/konnektor-konfiguration.md` §13): with it
/// off, an installation may keep the parameters it grew over the years — they are
/// only reported as a hint in the form. With it on, a typo (`retry_polcy`) is an
/// error instead of a silently ignored value.
pub fn validate_strict(
    type_key: &str,
    parameters: &Map<String, Value>,
    strict: bool,
) -> Vec<Problem> {
    validate_strict_fields(&fields(type_key), parameters, strict)
}

/// [`validate_strict`] over a resolved field list.
fn validate_strict_fields(
    declared: &[Field],
    parameters: &Map<String, Value>,
    strict: bool,
) -> Vec<Problem> {
    let mut problems = validate_fields(declared, parameters);
    if strict {
        for key in unknown_keys_fields(declared, parameters) {
            push_once(
                &mut problems,
                Problem::new(key, "is not a declared parameter"),
            );
        }
    }
    problems
}

fn check_field(field: &Field, value: &Value) -> Result<(), String> {
    match field.kind {
        FieldKind::String => {
            let text = value
                .as_str()
                .ok_or_else(|| "must be a string".to_owned())?;
            if text.trim().is_empty() {
                return Err("must not be empty".to_owned());
            }
            if let Some(allowed) = &field.enum_values
                && !allowed.iter().any(|value| value == text)
            {
                return Err(format!("must be one of {}", allowed.join(", ")));
            }
            Ok(())
        }
        FieldKind::Number => {
            let number = value
                .as_f64()
                .ok_or_else(|| "must be a number".to_owned())?;
            check_range(field, number)
        }
        FieldKind::Integer => {
            let number = value
                .as_i64()
                .ok_or_else(|| "must be an integer".to_owned())?;
            check_range(field, number as f64)
        }
        FieldKind::Boolean => {
            if value.is_boolean() {
                Ok(())
            } else {
                Err("must be a boolean".to_owned())
            }
        }
        FieldKind::StringList => {
            let items = value
                .as_array()
                .ok_or_else(|| "must be a list of strings".to_owned())?;
            if items.iter().all(Value::is_string) {
                Ok(())
            } else {
                Err("must be a list of strings".to_owned())
            }
        }
        FieldKind::NumberList => {
            let items = value
                .as_array()
                .ok_or_else(|| "must be a list of numbers".to_owned())?;
            for item in items {
                let number = item
                    .as_f64()
                    .ok_or_else(|| "must be a list of numbers".to_owned())?;
                check_range(field, number)?;
            }
            Ok(())
        }
        FieldKind::StringMap => {
            let entries = value
                .as_object()
                .ok_or_else(|| "must be an object of strings".to_owned())?;
            if entries.values().all(Value::is_string) {
                Ok(())
            } else {
                Err("must be an object of strings".to_owned())
            }
        }
        FieldKind::Json => Ok(()),
    }
}

fn check_range(field: &Field, number: f64) -> Result<(), String> {
    if let Some(minimum) = field.minimum
        && number < minimum
    {
        return Err(format!("must be at least {minimum}"));
    }
    if let Some(maximum) = field.maximum
        && number > maximum
    {
        return Err(format!("must be at most {maximum}"));
    }
    Ok(())
}

/// Recursively merges `patch` into `current` (form-based partial updates).
///
/// A `null` value in the patch **deletes** the key; nested objects are merged
/// key by key, everything else replaces the previous value.
pub fn merge_parameters(
    current: &Map<String, Value>,
    patch: &Map<String, Value>,
) -> Map<String, Value> {
    let mut merged = current.clone();
    merge_into(&mut merged, patch);
    merged
}

fn merge_into(target: &mut Map<String, Value>, patch: &Map<String, Value>) {
    for (key, value) in patch {
        if value.is_null() {
            target.remove(key);
            continue;
        }
        match (target.get_mut(key), value) {
            (Some(Value::Object(existing)), Value::Object(incoming)) => {
                merge_into(existing, incoming)
            }
            _ => {
                target.insert(key.clone(), value.clone());
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The flat wording of the problems (what the API answers as `errors`).
    fn messages(problems: Vec<Problem>) -> Vec<String> {
        problems.iter().map(Problem::text).collect()
    }

    fn object(value: Value) -> Map<String, Value> {
        value.as_object().cloned().unwrap_or_default()
    }

    #[test]
    fn schema_covers_the_known_types() {
        let catalog = Catalog::builtin();
        assert!(catalog.is_known_type("webhook"));
        assert!(catalog.is_known_type("json_import"));
        assert!(catalog.is_known_type("json_export"));
        assert!(!catalog.is_known_type("ftp"));

        let webhook = spec("webhook").expect("webhook spec");
        assert!(webhook.wired);
        let keys: Vec<String> = webhook
            .groups
            .iter()
            .flat_map(|group| group.fields.iter().map(|field| field.key.clone()))
            .collect();
        assert!(keys.contains(&"url".to_owned()));
        assert!(keys.contains(&"filter.event_types".to_owned()));
        assert!(keys.contains(&"coalesce.window_seconds".to_owned()));
        assert!(keys.contains(&"retry_policy.max_attempts".to_owned()));
        assert!(keys.contains(&"retry_policy.backoff_seconds".to_owned()));

        // Every built-in type is wired to a consumer today.
        for type_key in ["webhook", "json_import", "json_export"] {
            assert!(spec(type_key).expect("spec").wired, "{type_key}");
        }
    }

    #[test]
    fn schema_document_is_serializable_with_type_keys() {
        let document = schema_document();
        let entries = document.as_array().cloned().unwrap_or_default();
        assert_eq!(entries.len(), 3);
        let webhook = entries
            .iter()
            .find(|entry| entry.get("type").and_then(Value::as_str) == Some("webhook"))
            .expect("webhook entry");
        let fields = webhook["groups"][0]["fields"]
            .as_array()
            .cloned()
            .unwrap_or_default();
        assert_eq!(fields[0]["key"], json!("url"));
        assert_eq!(fields[0]["type"], json!("string"));
        assert_eq!(fields[0]["widget"], json!("text"));
        assert_eq!(fields[0]["required"], json!(true));
        assert_eq!(
            fields[0]["label_key"],
            json!("connector_fields.webhook.url.label")
        );
    }

    #[test]
    fn required_and_typed_fields_are_enforced() {
        let errors = messages(validate("webhook", &object(json!({}))));
        assert_eq!(errors, vec!["parameters.url: is required".to_owned()]);

        let errors = validate("webhook", &object(json!({"url": "https://x.example"})));
        assert!(errors.is_empty());

        let errors = messages(validate(
            "webhook",
            &object(json!({"url": "https://x.example", "method": "GETX"})),
        ));
        assert_eq!(
            errors,
            vec!["parameters.method: must be one of POST, PUT, PATCH".to_owned()]
        );

        let errors = messages(validate(
            "webhook",
            &object(json!({"url": "  ", "timeout_s": 900.0, "coalesce": {"enabled": "yes"}})),
        ));
        assert_eq!(
            errors,
            vec![
                "parameters.url: must not be empty".to_owned(),
                "parameters.timeout_s: must be at most 600".to_owned(),
                "parameters.coalesce.enabled: must be a boolean".to_owned(),
            ]
        );
    }

    #[test]
    fn list_and_map_fields_are_typed() {
        let errors = messages(validate(
            "webhook",
            &object(
                json!({"url": "https://x.example", "filter": {"event_types": "object.created"}}),
            ),
        ));
        assert_eq!(
            errors,
            vec!["parameters.filter.event_types: must be a list of strings".to_owned()]
        );

        let errors = messages(validate(
            "webhook",
            &object(json!({"url": "https://x.example", "filter": {"event_types": [1, 2]}})),
        ));
        assert_eq!(
            errors,
            vec!["parameters.filter.event_types: must be a list of strings".to_owned()]
        );

        let errors = messages(validate(
            "webhook",
            &object(json!({"url": "https://x.example", "headers": {"X": 1}})),
        ));
        assert_eq!(
            errors,
            vec!["parameters.headers: must be an object of strings".to_owned()]
        );
    }

    #[test]
    fn a_non_object_parent_is_reported_once() {
        let errors = messages(validate(
            "webhook",
            &object(json!({"url": "https://x.example", "filter": "everything"})),
        ));
        assert_eq!(
            errors,
            vec!["parameters.filter: must be an object".to_owned()]
        );
    }

    #[test]
    fn undeclared_keys_are_allowed() {
        let parameters = object(json!({
            "url": "https://x.example",
            "retry_policy": {"max_attempts": 5}
        }));
        assert!(validate("webhook", &parameters).is_empty());
    }

    #[test]
    fn strict_mode_rejects_undeclared_top_level_keys() {
        let parameters = object(json!({
            "url": "https://x.example",
            "retry_polcy": {"max_attempts": 3}
        }));
        // Off (the default): a leftover key is a hint in the form, not an error.
        assert!(validate_strict("webhook", &parameters, false).is_empty());
        assert_eq!(
            unknown_keys("webhook", &parameters),
            vec!["retry_polcy".to_owned()]
        );
        assert_eq!(
            messages(validate_strict("webhook", &parameters, true)),
            vec!["parameters.retry_polcy: is not a declared parameter".to_owned()]
        );
    }

    #[test]
    fn strict_mode_judges_only_the_top_level() {
        // The content of a declared object stays free: `headers` is a map, and a
        // `json` field may hold anything.
        let parameters = object(json!({
            "url": "https://x.example",
            "headers": {"X-Anything": "1"},
            "filter": {"event_types": ["object.created"]}
        }));
        assert!(validate_strict("webhook", &parameters, true).is_empty());
    }

    #[test]
    fn import_and_export_profiles_are_declared() {
        // `json_import`/`json_export` are the default profiles of the
        // import/export endpoints, so their fields are validated too.
        let import = spec("json_import").expect("import spec");
        assert!(import.wired);
        assert_eq!(import.direction, "inbound");
        let keys: Vec<String> = import
            .groups
            .iter()
            .flat_map(|group| group.fields.iter().map(|field| field.key.clone()))
            .collect();
        assert_eq!(
            keys,
            vec!["match_by_key", "match_by_id", "emit_events", "source"]
        );

        let export = spec("json_export").expect("export spec");
        assert!(export.wired);
        assert_eq!(export.direction, "outbound");
        let keys: Vec<String> = export
            .groups
            .iter()
            .flat_map(|group| group.fields.iter().map(|field| field.key.clone()))
            .collect();
        assert_eq!(
            keys,
            vec!["format", "fields", "locales", "channel", "options.email_to"]
        );

        assert!(validate("json_import", &object(json!({"anything": true}))).is_empty());
        let errors = messages(validate(
            "json_import",
            &object(json!({"emit_events": "yes", "match_by_id": 1})),
        ));
        assert_eq!(
            errors,
            vec![
                "parameters.match_by_id: must be a boolean".to_owned(),
                "parameters.emit_events: must be a boolean".to_owned()
            ]
        );

        let errors = messages(validate("json_export", &object(json!({"format": "xml"}))));
        assert_eq!(
            errors,
            vec!["parameters.format: must be one of csv, xlsx, json".to_owned()]
        );

        let mut parameters = object(json!({}));
        apply_defaults("json_export", &mut parameters);
        assert_eq!(
            Value::Object(parameters),
            json!({
                "format": "csv",
                "fields": [],
                "locales": [],
                "channel": "",
                "options": {"email_to": []}
            })
        );
    }

    #[test]
    fn defaults_fill_the_declared_fields() {
        let mut parameters = object(json!({"url": "https://x.example"}));
        apply_defaults("webhook", &mut parameters);
        assert_eq!(
            Value::Object(parameters),
            json!({
                "url": "https://x.example",
                "method": "POST",
                "payload_mode": "event",
                "headers": {},
                "timeout_s": 30.0,
                "filter": {"event_types": [], "object_types": [], "attribute_changes": []},
                "coalesce": {"enabled": false, "window_seconds": 30.0},
                "retry_policy": {"max_attempts": 5, "backoff_seconds": [5.0]}
            })
        );
    }

    #[test]
    fn retry_policy_range_is_enforced() {
        let errors = messages(validate(
            "webhook",
            &object(json!({
                "url": "https://x.example",
                "retry_policy": {"max_attempts": 0, "backoff_seconds": [7200]}
            })),
        ));
        assert_eq!(
            errors,
            vec![
                "parameters.retry_policy.max_attempts: must be at least 1".to_owned(),
                "parameters.retry_policy.backoff_seconds: must be at most 3600".to_owned(),
            ]
        );

        let errors = messages(validate(
            "webhook",
            &object(json!({
                "url": "https://x.example",
                "retry_policy": {"max_attempts": 3, "backoff_seconds": [15]}
            })),
        ));
        assert!(errors.is_empty());
    }

    #[test]
    fn a_single_backoff_number_becomes_a_list() {
        // The field was a scalar before Stage 3; a stored connector must stay
        // editable, so `normalize` wraps it instead of failing the check.
        let mut parameters = object(json!({
            "url": "https://x.example",
            "retry_policy": {"backoff_seconds": 120}
        }));
        normalize("webhook", &mut parameters);
        assert_eq!(
            parameters.get("retry_policy"),
            Some(&json!({"backoff_seconds": [120.0]}))
        );
        assert!(validate("webhook", &parameters).is_empty());
    }

    #[test]
    fn a_number_list_rejects_non_numbers_and_out_of_range_entries() {
        let errors = messages(validate(
            "webhook",
            &object(json!({
                "url": "https://x.example",
                "retry_policy": {"backoff_seconds": [5, "30"]}
            })),
        ));
        assert_eq!(
            errors,
            vec!["parameters.retry_policy.backoff_seconds: must be a list of numbers".to_owned()]
        );

        let errors = messages(validate(
            "webhook",
            &object(json!({
                "url": "https://x.example",
                "retry_policy": {"backoff_seconds": [5, 0.05]}
            })),
        ));
        assert_eq!(
            errors,
            vec!["parameters.retry_policy.backoff_seconds: must be at least 0.1".to_owned()]
        );
    }

    #[test]
    fn a_secret_moves_out_of_the_parameters() {
        let parameters = object(json!({
            "url": "https://x.example",
            "headers": {"Authorization": "Bearer abc"},
            "timeout_s": 30.0
        }));
        let split = split_secrets("connector:01ABC", "webhook", &parameters);
        assert_eq!(
            split.parameters.get("headers"),
            Some(&json!({"$secret": "connector:01ABC#headers"}))
        );
        assert_eq!(
            split.store.get("headers"),
            Some(&json!({"Authorization": "Bearer abc"}))
        );
        assert_eq!(
            split.parameters.get("url"),
            Some(&json!("https://x.example"))
        );
        assert!(split.cleared.is_empty());
    }

    #[test]
    fn a_reference_means_unchanged_and_an_empty_value_clears() {
        let referenced = object(json!({
            "url": "https://x.example",
            "headers": {"$secret": "connector:01ABC#headers"}
        }));
        let split = split_secrets("connector:01ABC", "webhook", &referenced);
        // Re-saving the form must not clear the token it never saw.
        assert!(split.store.is_empty());
        assert!(split.cleared.is_empty());
        assert_eq!(
            split.parameters.get("headers"),
            Some(&json!({"$secret": "connector:01ABC#headers"}))
        );

        let emptied = object(json!({"url": "https://x.example", "headers": {}}));
        let split = split_secrets("connector:01ABC", "webhook", &emptied);
        assert_eq!(split.cleared, vec!["headers".to_owned()]);
        assert!(!split.parameters.contains_key("headers"));
    }

    #[test]
    fn secrets_are_resolved_for_the_worker_and_dropped_when_missing() {
        let stored = object(json!({
            "url": "https://x.example",
            "headers": {"$secret": "connector:01ABC#headers"}
        }));
        let mut values = Map::new();
        values.insert("headers".to_owned(), json!({"Authorization": "Bearer abc"}));
        let resolved = resolve_secret_references("webhook", &stored, &values);
        assert_eq!(
            resolved.get("headers"),
            Some(&json!({"Authorization": "Bearer abc"}))
        );

        // Nothing stored: the reference must not travel to a middleware.
        let dropped = resolve_secret_references("webhook", &stored, &Map::new());
        assert!(!dropped.contains_key("headers"));
        assert_eq!(dropped.get("url"), Some(&json!("https://x.example")));
    }

    #[test]
    fn secrets_are_removed_from_a_connector_configuration() {
        let parameters = object(json!({
            "url": "https://x.example",
            "headers": {"Authorization": "Bearer secret"},
            "retry_policy": {"backoff_seconds": [5]}
        }));
        let cleaned = without_secrets("webhook", &parameters);
        assert_eq!(
            Value::Object(cleaned),
            json!({
                "url": "https://x.example",
                "retry_policy": {"backoff_seconds": [5]}
            })
        );
        // The original map is untouched (the caller may still write it).
        assert!(parameters.contains_key("headers"));
        // A type without secret fields keeps everything.
        let profile = object(json!({"match_by_key": true}));
        assert_eq!(without_secrets("json_import", &profile), profile);
    }

    #[test]
    fn the_backoff_is_declared_as_a_number_list() {
        let declared = spec("webhook")
            .expect("webhook is a built-in type")
            .groups
            .into_iter()
            .flat_map(|group| group.fields)
            .find(|field| field.key == "retry_policy.backoff_seconds")
            .expect("the retry group declares the backoff");
        assert_eq!(declared.kind, FieldKind::NumberList);
        assert_eq!(declared.widget, Widget::NumberList);
        assert_eq!(declared.default, Some(json!([5.0])));
    }

    #[test]
    fn defaults_do_not_overwrite_existing_values() {
        let mut parameters = object(json!({"url": "https://x.example", "method": "PUT"}));
        apply_defaults("webhook", &mut parameters);
        assert_eq!(parameters.get("method"), Some(&json!("PUT")));
    }

    #[test]
    fn normalize_trims_and_cleans() {
        let mut parameters = object(json!({
            "url": "  https://x.example  ",
            "filter": {"event_types": ["object.created", " object.updated ", "", "object.created"]},
            "coalesce": {"window_seconds": null}
        }));
        normalize("webhook", &mut parameters);
        assert_eq!(parameters.get("url"), Some(&json!("https://x.example")));
        assert_eq!(
            parameters.get("filter"),
            Some(&json!({"event_types": ["object.created", "object.updated"]}))
        );
        // A null value removes the key instead of storing NULL.
        assert_eq!(parameters.get("coalesce"), Some(&json!({})));
    }

    #[test]
    fn merge_updates_nested_keys_and_keeps_the_rest() {
        let current = object(json!({
            "url": "https://x.example",
            "headers": {"Authorization": "Bearer token", "X-Keep": "1"},
            "coalesce": {"enabled": false, "window_seconds": 30.0},
            "retry_policy": {"max_attempts": 5}
        }));
        let patch = object(json!({"coalesce": {"enabled": true}}));
        let merged = merge_parameters(&current, &patch);
        assert_eq!(
            merged.get("headers"),
            Some(&json!({"Authorization": "Bearer token", "X-Keep": "1"}))
        );
        assert_eq!(
            merged.get("coalesce"),
            Some(&json!({"enabled": true, "window_seconds": 30.0}))
        );
        // Undeclared keys survive a form-based update.
        assert_eq!(
            merged.get("retry_policy"),
            Some(&json!({"max_attempts": 5}))
        );
    }

    #[test]
    fn merge_deletes_with_null() {
        let current = object(json!({"coalesce": {"enabled": true, "window_seconds": 15.0}}));
        let patch = object(json!({"coalesce": {"window_seconds": null}}));
        let merged = merge_parameters(&current, &patch);
        assert_eq!(merged.get("coalesce"), Some(&json!({"enabled": true})));
    }

    #[test]
    fn merge_validates_the_result_not_the_patch() {
        let current = object(json!({"url": "https://x.example"}));
        let patch = object(json!({"url": null}));
        let merged = merge_parameters(&current, &patch);
        assert_eq!(
            messages(validate("webhook", &merged)),
            vec!["parameters.url: is required".to_owned()]
        );
    }
}
