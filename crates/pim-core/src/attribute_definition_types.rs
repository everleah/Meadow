//! Data-type conversion for attribute definitions (type change).
//!
//! The data type lives in the `attribute_definition`, the values stay raw JSON
//! in `object.attributes`, so a type change is possible — but only with the
//! **migration of every existing value**. This module owns the *pure* part:
//! which transitions are allowed and how a single value is converted.
//!
//! The endpoint (`api::attribute_definitions::change_attribute_type`) loads the
//! values, plans the conversion with these helpers and applies it atomically.
//! Design and matrix: `plan/detail/datenmodell.md` (Datentypwechsel).

use serde_json::{Map, Value};

use crate::attribute_values::is_valid_url;

/// All data types an attribute may have (the list is open, existing types are
/// never renamed).
pub const TYPES: &[&str] = &[
    "text",
    "url",
    "number",
    "boolean",
    "date",
    "enum",
    "reference",
];

/// Whether the transition `from → to` is supported by the matrix.
///
/// `text` is the universal target (everything can be rendered as text); the
/// loose transitions (`text` → `url`/`number`/`boolean`/`date`/`enum`/
/// `reference`) are checked per value.
pub fn is_allowed_transition(from: &str, to: &str, enum_set: bool) -> bool {
    if from == to {
        return false;
    }
    if to == "text" {
        return matches!(
            from,
            "url" | "number" | "boolean" | "date" | "enum" | "reference"
        );
    }
    if from == "text" {
        return match to {
            // An `enum` target needs the option list the codes are checked against.
            "enum" => enum_set,
            "url" | "number" | "boolean" | "date" | "reference" => true,
            _ => false,
        };
    }
    false
}

/// Target types offered for a source type (for the error message and the UI).
pub fn allowed_targets(from: &str) -> Vec<&'static str> {
    TYPES
        .iter()
        .copied()
        .filter(|target| is_allowed_transition(from, target, true))
        .collect()
}

/// Renders a JSON value as text (for a change **into** `text`).
fn as_text(value: &Value) -> Result<String, String> {
    match value {
        Value::String(text) => Ok(text.clone()),
        Value::Bool(flag) => Ok(flag.to_string()),
        Value::Number(number) => Ok(canonical_number(number)),
        _ => Err("must be a string, number or boolean".to_owned()),
    }
}

/// Canonical decimal rendering of a JSON number (`1.0` → `1`).
fn canonical_number(number: &serde_json::Number) -> String {
    let rendered = number.to_string();
    match rendered.strip_suffix(".0") {
        Some(trimmed) if number.is_f64() => trimmed.to_owned(),
        _ => rendered,
    }
}

/// Parses a text value as a JSON number, keeping integers integral.
pub fn parse_number(text: &str) -> Option<Value> {
    let trimmed = text.trim();
    if trimmed.is_empty() {
        return None;
    }
    if let Ok(integer) = trimmed.parse::<i64>() {
        return Some(Value::Number(integer.into()));
    }
    let float: f64 = trimmed.parse().ok()?;
    serde_json::Number::from_f64(float).map(Value::Number)
}

/// Whether the text is a date in the stored form `JJJJ-MM-TT`.
pub fn is_iso_date(text: &str) -> bool {
    let trimmed = text.trim();
    let bytes = trimmed.as_bytes();
    if bytes.len() != 10 || bytes[4] != b'-' || bytes[7] != b'-' {
        return false;
    }
    if !bytes
        .iter()
        .enumerate()
        .all(|(index, byte)| index == 4 || index == 7 || byte.is_ascii_digit())
    {
        return false;
    }
    let year = trimmed[0..4].parse::<u32>().unwrap_or(0);
    let month = trimmed[5..7].parse::<u32>().unwrap_or(0);
    let day = trimmed[8..10].parse::<u32>().unwrap_or(0);
    year >= 1 && (1..=12).contains(&month) && (1..=31).contains(&day)
}

/// Whether the text names an object record (`object:<key>`).
pub fn is_object_reference(text: &str) -> bool {
    text.trim()
        .strip_prefix("object:")
        .is_some_and(|key| !key.is_empty())
}

/// Converts one scalar value to the target type.
///
/// `enum_codes` is the option list of the target enum set (required for an
/// `enum` target). Returns the reason as `Err` when the value cannot be
/// converted — the caller then either rejects the change or drops the value.
pub fn convert_scalar(
    from: &str,
    to: &str,
    value: &Value,
    enum_codes: Option<&[String]>,
) -> Result<Value, String> {
    match to {
        "text" => as_text(value).map(Value::String),
        "url" => {
            let text = value.as_str().unwrap_or_default();
            if is_valid_url(text) {
                Ok(value.clone())
            } else {
                Err("must be an absolute http(s) URL".to_owned())
            }
        }
        "number" => {
            let text = value.as_str().unwrap_or_default();
            parse_number(text).ok_or_else(|| "must be a number".to_owned())
        }
        "boolean" => match value.as_str().unwrap_or_default().trim() {
            "true" => Ok(Value::Bool(true)),
            "false" => Ok(Value::Bool(false)),
            _ => Err("must be `true` or `false`".to_owned()),
        },
        "date" => {
            let text = value.as_str().unwrap_or_default();
            if is_iso_date(text) {
                Ok(value.clone())
            } else {
                Err("must be a date in the form JJJJ-MM-TT".to_owned())
            }
        }
        "enum" => {
            let text = value.as_str().unwrap_or_default();
            let known = enum_codes.unwrap_or_default();
            if known.iter().any(|code| code == text) {
                Ok(value.clone())
            } else {
                Err(format!("`{text}` is not an option of the enum set"))
            }
        }
        "reference" => {
            let text = value.as_str().unwrap_or_default();
            if is_object_reference(text) {
                Ok(value.clone())
            } else {
                Err("must be an object id (`object:<key>`)".to_owned())
            }
        }
        _ => Err(format!("unsupported target type `{to}` (from `{from}`)")),
    }
}

/// Converts an attribute value, covering multilingual maps.
///
/// A multilingual attribute stores `{language: value}`; each entry is converted
/// on its own, so a single bad language does not hide the others. `null` means
/// "no value" and is converted to `null` (it keeps breaking inheritance).
pub fn convert_value(
    from: &str,
    to: &str,
    value: &Value,
    enum_codes: Option<&[String]>,
) -> Result<Value, String> {
    match value {
        Value::Null => Ok(Value::Null),
        Value::Object(entries) => {
            let mut converted = Map::new();
            let mut problems: Vec<String> = Vec::new();
            for (language, entry) in entries {
                match convert_value(from, to, entry, enum_codes) {
                    Ok(item) => {
                        converted.insert(language.clone(), item);
                    }
                    Err(reason) => problems.push(format!("{language}: {reason}")),
                }
            }
            if !problems.is_empty() {
                return Err(problems.join(", "));
            }
            Ok(Value::Object(converted))
        }
        _ => convert_scalar(from, to, value, enum_codes),
    }
}

/// One object whose value cannot be converted.
#[derive(Debug, Clone, PartialEq)]
pub struct Incompatible {
    pub object_id: String,
    pub value: Value,
    pub reason: String,
}

/// Result of planning a type change over all existing values.
#[derive(Debug, Clone, Default)]
pub struct ConversionPlan {
    /// Values that carry the attribute (including the incompatible ones).
    pub checked: usize,
    /// Converted values `(object id, new value)`.
    pub converted: Vec<(String, Value)>,
    /// Object ids whose value must be dropped when applying with
    /// `drop_incompatible`.
    pub dropped: Vec<String>,
    /// The offending values (capped for the report).
    pub incompatible: Vec<Incompatible>,
}

impl ConversionPlan {
    /// Number of values that can be converted.
    pub fn convertible(&self) -> usize {
        self.converted.len()
    }
}

/// Plans the conversion for every value: `from → to`.
///
/// Incompatible values end up in `incompatible` (and in `dropped` for the
/// "drop and continue" mode) instead of failing the whole plan, so the handler
/// can report them.
pub fn plan_conversion(
    from: &str,
    to: &str,
    values: &[(String, Value)],
    enum_codes: Option<&[String]>,
) -> ConversionPlan {
    let mut plan = ConversionPlan {
        checked: values.len(),
        ..ConversionPlan::default()
    };
    for (object_id, value) in values {
        match convert_value(from, to, value, enum_codes) {
            Ok(converted) => plan.converted.push((object_id.clone(), converted)),
            Err(reason) => {
                plan.dropped.push(object_id.clone());
                plan.incompatible.push(Incompatible {
                    object_id: object_id.clone(),
                    value: value.clone(),
                    reason,
                });
            }
        }
    }
    plan
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn transitions_follow_the_compatibility_matrix() {
        assert!(is_allowed_transition("text", "number", false));
        assert!(is_allowed_transition("text", "url", false));
        assert!(!is_allowed_transition("text", "enum", false)); // needs an enum set
        assert!(is_allowed_transition("text", "enum", true));
        assert!(is_allowed_transition("number", "text", false));
        assert!(is_allowed_transition("url", "text", false));
        assert!(is_allowed_transition("reference", "text", false));
        // Not supported: silent interpretations between structural types.
        assert!(!is_allowed_transition("number", "boolean", false));
        assert!(!is_allowed_transition("boolean", "number", false));
        assert!(!is_allowed_transition("date", "number", false));
        assert!(!is_allowed_transition("number", "url", false));
        assert!(!is_allowed_transition("text", "text", false));
    }

    #[test]
    fn allowed_targets_are_listed_for_text_and_others() {
        assert_eq!(
            allowed_targets("text"),
            vec!["url", "number", "boolean", "date", "enum", "reference"]
        );
        assert_eq!(allowed_targets("number"), vec!["text"]);
        assert_eq!(allowed_targets("reference"), vec!["text"]);
    }

    #[test]
    fn text_converts_to_number_boolean_and_date() {
        assert_eq!(
            convert_scalar("text", "number", &json!("12.5"), None),
            Ok(json!(12.5))
        );
        assert_eq!(
            convert_scalar("text", "number", &json!(" 7 "), None),
            Ok(json!(7))
        );
        assert_eq!(
            convert_scalar("text", "boolean", &json!("true"), None),
            Ok(json!(true))
        );
        assert!(convert_scalar("text", "number", &json!("teuer"), None).is_err());
        assert!(convert_scalar("text", "boolean", &json!("1"), None).is_err());
        assert_eq!(
            convert_scalar("text", "date", &json!("2026-09-12"), None),
            Ok(json!("2026-09-12"))
        );
        assert!(convert_scalar("text", "date", &json!("12.09.2026"), None).is_err());
        assert!(convert_scalar("text", "date", &json!("2026-13-01"), None).is_err());
    }

    #[test]
    fn text_converts_to_url_enum_and_reference() {
        assert_eq!(
            convert_scalar("text", "url", &json!("https://example.com/a"), None),
            Ok(json!("https://example.com/a"))
        );
        assert!(convert_scalar("text", "url", &json!("example.com"), None).is_err());

        let codes = vec!["red".to_owned(), "green".to_owned()];
        assert_eq!(
            convert_scalar("text", "enum", &json!("red"), Some(&codes)),
            Ok(json!("red"))
        );
        assert!(convert_scalar("text", "enum", &json!("blue"), Some(&codes)).is_err());
        assert!(convert_scalar("text", "enum", &json!("red"), None).is_err());

        assert_eq!(
            convert_scalar("text", "reference", &json!("object:abc"), None),
            Ok(json!("object:abc"))
        );
        assert!(convert_scalar("text", "reference", &json!("abc"), None).is_err());
    }

    #[test]
    fn structural_types_convert_to_text() {
        assert_eq!(
            convert_scalar("number", "text", &json!(12.5), None),
            Ok(json!("12.5"))
        );
        // `1.0` is rendered as `1` (canonical, no trailing zeros).
        assert_eq!(
            convert_scalar("number", "text", &json!(1.0), None),
            Ok(json!("1"))
        );
        assert_eq!(
            convert_scalar("boolean", "text", &json!(true), None),
            Ok(json!("true"))
        );
        assert_eq!(
            convert_scalar("reference", "text", &json!("object:abc"), None),
            Ok(json!("object:abc"))
        );
    }

    #[test]
    fn null_values_stay_null() {
        assert_eq!(
            convert_value("text", "number", &Value::Null, None),
            Ok(Value::Null)
        );
    }

    #[test]
    fn multilingual_values_are_converted_per_language() {
        let value = json!({"de": "12.5", "en": "7"});
        assert_eq!(
            convert_value("text", "number", &value, None),
            Ok(json!({"de": 12.5, "en": 7}))
        );
        // One bad language names the language in the reason.
        let broken = json!({"de": "12.5", "en": "teuer"});
        let reason = convert_value("text", "number", &broken, None).unwrap_err();
        assert!(reason.starts_with("en: "), "{reason}");
    }

    #[test]
    fn plan_reports_incompatible_values_per_object() {
        let values = vec![
            ("object:a".to_owned(), json!("12.5")),
            ("object:b".to_owned(), json!("teuer")),
            ("object:c".to_owned(), json!("7")),
        ];
        let plan = plan_conversion("text", "number", &values, None);
        assert_eq!(plan.checked, 3);
        assert_eq!(plan.convertible(), 2);
        assert_eq!(plan.converted[0], ("object:a".to_owned(), json!(12.5)));
        assert_eq!(plan.dropped, vec!["object:b".to_owned()]);
        assert_eq!(plan.incompatible.len(), 1);
        assert_eq!(plan.incompatible[0].object_id, "object:b");
        assert_eq!(plan.incompatible[0].value, json!("teuer"));
    }
}
