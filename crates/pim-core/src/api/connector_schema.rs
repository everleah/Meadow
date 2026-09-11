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
//! evaluated field is declared here. Types that no consumer reads yet carry
//! `wired: false` and declare **no** fields (they are free JSON until Stage 2,
//! see `plan/detail/konnektor-konfiguration.md`).

use serde::Serialize;
use serde_json::{Map, Value, json};

use crate::event;

/// Data type of a declared connector parameter.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum FieldKind {
    String,
    Number,
    Integer,
    Boolean,
    StringList,
    StringMap,
    Json,
}

/// How the admin UI renders a field.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Widget {
    Text,
    Password,
    Textarea,
    Select,
    Multiselect,
    Switch,
    Number,
    KeyValue,
    Json,
}

/// Conditional visibility of a field (`show_if`), evaluated by the UI only.
#[derive(Debug, Clone, Serialize)]
pub struct ShowIf {
    /// Path of the controlling field.
    pub key: &'static str,
    /// Show the field when the controlling value equals this value.
    pub equals: Value,
}

/// A single declared parameter.
#[derive(Debug, Clone, Serialize)]
pub struct Field {
    /// Path inside `parameters`; nested paths use dots (`filter.event_types`).
    pub key: &'static str,
    #[serde(rename = "type")]
    pub kind: FieldKind,
    pub widget: Widget,
    #[serde(skip_serializing_if = "is_false")]
    pub required: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub default: Option<Value>,
    #[serde(rename = "enum", skip_serializing_if = "Option::is_none")]
    pub enum_values: Option<Vec<&'static str>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub options_source: Option<&'static str>,
    /// i18n key of the field label (never a literal text).
    pub label_key: &'static str,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub help_key: Option<&'static str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub minimum: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub maximum: Option<f64>,
    #[serde(skip_serializing_if = "is_false")]
    pub secret: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub show_if: Option<ShowIf>,
}

fn is_false(value: &bool) -> bool {
    !*value
}

impl Field {
    fn new(key: &'static str, kind: FieldKind, widget: Widget) -> Self {
        Self {
            key,
            kind,
            widget,
            required: false,
            default: None,
            enum_values: None,
            options_source: None,
            label_key: "",
            help_key: None,
            minimum: None,
            maximum: None,
            secret: false,
            show_if: None,
        }
    }

    fn label(mut self, key: &'static str) -> Self {
        self.label_key = key;
        self
    }

    fn help(mut self, key: &'static str) -> Self {
        self.help_key = Some(key);
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

    fn options(mut self, values: &[&'static str]) -> Self {
        self.enum_values = Some(values.to_vec());
        self
    }

    fn options_from(mut self, source: &'static str) -> Self {
        self.options_source = Some(source);
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

    fn show_if(mut self, key: &'static str, equals: Value) -> Self {
        self.show_if = Some(ShowIf { key, equals });
        self
    }
}

/// A group of related fields (one collapsible section in the UI).
#[derive(Debug, Clone, Serialize)]
pub struct Group {
    pub key: &'static str,
    pub label_key: &'static str,
    pub fields: Vec<Field>,
}

/// Description of one connector type.
#[derive(Debug, Clone, Serialize)]
pub struct TypeSpec {
    #[serde(rename = "type")]
    pub type_key: &'static str,
    pub label_key: &'static str,
    /// `inbound` or `outbound` (display hint).
    pub direction: &'static str,
    /// Whether a consumer actually reads the parameters.
    pub wired: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub note_key: Option<&'static str>,
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
        type_key: "webhook",
        label_key: "connectors.type_webhook",
        direction: "outbound",
        wired: true,
        note_key: None,
        groups: vec![
            Group {
                key: "target",
                label_key: "connector_fields.group_target",
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
                key: "headers",
                label_key: "connector_fields.group_headers",
                fields: vec![
                    Field::new("headers", FieldKind::StringMap, Widget::KeyValue)
                        .label("connector_fields.webhook.headers.label")
                        .help("connector_fields.webhook.headers.help")
                        .secret()
                        .default(json!({})),
                ],
            },
            Group {
                key: "filter",
                label_key: "connector_fields.group_filter",
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
                key: "coalesce",
                label_key: "connector_fields.group_coalesce",
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
        ],
    }
}

/// Types without a consumer yet: free JSON, no declared fields (Stage 2 wires
/// `json_import`/`json_export` as job defaults, see the plan document).
fn unwired_spec(
    type_key: &'static str,
    label_key: &'static str,
    direction: &'static str,
) -> TypeSpec {
    TypeSpec {
        type_key,
        label_key,
        direction,
        wired: false,
        note_key: Some("connector_fields.unwired"),
        groups: Vec::new(),
    }
}

/// All connector types known to the core.
pub fn specs() -> Vec<TypeSpec> {
    vec![
        webhook_spec(),
        unwired_spec("json_import", "connectors.type_json_import", "inbound"),
        unwired_spec("json_export", "connectors.type_json_export", "outbound"),
    ]
}

/// The spec of one connector type.
pub fn spec(type_key: &str) -> Option<TypeSpec> {
    specs().into_iter().find(|spec| spec.type_key == type_key)
}

/// Whether the core knows the connector type.
pub fn is_known_type(type_key: &str) -> bool {
    spec(type_key).is_some()
}

/// The schema of every connector type (response of `GET /connector-types`).
pub fn schema_document() -> Value {
    serde_json::to_value(specs()).unwrap_or_else(|_| json!([]))
}

/// Every declared field of a type.
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
    for field in fields(type_key) {
        if get_path(parameters, field.key).is_some() {
            continue;
        }
        if let Some(default) = &field.default {
            set_path(parameters, field.key, default.clone());
        }
    }
}

/// Trims strings, drops empty list entries and removes `null` declared fields.
pub fn normalize(type_key: &str, parameters: &mut Map<String, Value>) {
    for field in fields(type_key) {
        let Some(value) = get_path(parameters, field.key).cloned() else {
            continue;
        };
        if value.is_null() {
            remove_path(parameters, field.key);
            continue;
        }
        match field.kind {
            FieldKind::String => {
                if let Some(text) = value.as_str() {
                    let trimmed = text.trim();
                    if trimmed.is_empty() {
                        remove_path(parameters, field.key);
                    } else if trimmed != text {
                        set_path(parameters, field.key, Value::String(trimmed.to_owned()));
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
                        set_path(parameters, field.key, Value::Array(cleaned));
                    }
                }
            }
            _ => {}
        }
    }
}

/// Validates `parameters` against the type's schema.
///
/// Returns one message per problem, always naming the field path so a form can
/// point at the offending field. An empty result means "valid". Types without
/// declared fields accept any object (nothing is enforced there yet).
pub fn validate(type_key: &str, parameters: &Map<String, Value>) -> Vec<String> {
    let mut errors: Vec<String> = Vec::new();
    for field in fields(type_key) {
        if let Some(prefix) = non_object_prefix(parameters, field.key) {
            push_once(
                &mut errors,
                format!("parameters.{prefix}: must be an object"),
            );
            continue;
        }
        let value = get_path(parameters, field.key).filter(|value| !value.is_null());
        let Some(value) = value else {
            if field.required {
                errors.push(format!("parameters.{}: is required", field.key));
            }
            continue;
        };
        if let Err(reason) = check_field(&field, value) {
            errors.push(format!("parameters.{}: {reason}", field.key));
        }
    }
    errors
}

fn push_once(errors: &mut Vec<String>, message: String) {
    if !errors.contains(&message) {
        errors.push(message);
    }
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
                && !allowed.contains(&text)
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

    fn object(value: Value) -> Map<String, Value> {
        value.as_object().cloned().unwrap_or_default()
    }

    #[test]
    fn schema_covers_the_known_types() {
        assert!(is_known_type("webhook"));
        assert!(is_known_type("json_import"));
        assert!(is_known_type("json_export"));
        assert!(!is_known_type("ftp"));

        let webhook = spec("webhook").expect("webhook spec");
        assert!(webhook.wired);
        let keys: Vec<&str> = webhook
            .groups
            .iter()
            .flat_map(|group| group.fields.iter().map(|field| field.key))
            .collect();
        assert!(keys.contains(&"url"));
        assert!(keys.contains(&"filter.event_types"));
        assert!(keys.contains(&"coalesce.window_seconds"));

        // Unwired types declare nothing: their parameters stay free JSON.
        let import = spec("json_import").expect("import spec");
        assert!(!import.wired);
        assert!(import.groups.is_empty());
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
        let errors = validate("webhook", &object(json!({})));
        assert_eq!(errors, vec!["parameters.url: is required".to_owned()]);

        let errors = validate("webhook", &object(json!({"url": "https://x.example"})));
        assert!(errors.is_empty());

        let errors = validate(
            "webhook",
            &object(json!({"url": "https://x.example", "method": "GETX"})),
        );
        assert_eq!(
            errors,
            vec!["parameters.method: must be one of POST, PUT, PATCH".to_owned()]
        );

        let errors = validate(
            "webhook",
            &object(json!({"url": "  ", "timeout_s": 900.0, "coalesce": {"enabled": "yes"}})),
        );
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
        let errors = validate(
            "webhook",
            &object(
                json!({"url": "https://x.example", "filter": {"event_types": "object.created"}}),
            ),
        );
        assert_eq!(
            errors,
            vec!["parameters.filter.event_types: must be a list of strings".to_owned()]
        );

        let errors = validate(
            "webhook",
            &object(json!({"url": "https://x.example", "filter": {"event_types": [1, 2]}})),
        );
        assert_eq!(
            errors,
            vec!["parameters.filter.event_types: must be a list of strings".to_owned()]
        );

        let errors = validate(
            "webhook",
            &object(json!({"url": "https://x.example", "headers": {"X": 1}})),
        );
        assert_eq!(
            errors,
            vec!["parameters.headers: must be an object of strings".to_owned()]
        );
    }

    #[test]
    fn a_non_object_parent_is_reported_once() {
        let errors = validate(
            "webhook",
            &object(json!({"url": "https://x.example", "filter": "everything"})),
        );
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
    fn unwired_types_accept_any_object() {
        assert!(validate("json_import", &object(json!({"anything": [1, 2, 3]}))).is_empty());
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
                "headers": {},
                "timeout_s": 30.0,
                "filter": {"event_types": [], "object_types": [], "attribute_changes": []},
                "coalesce": {"enabled": false, "window_seconds": 30.0}
            })
        );
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
            validate("webhook", &merged),
            vec!["parameters.url: is required".to_owned()]
        );
    }
}
