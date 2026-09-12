//! Value-level validation of attribute values.
//!
//! Most data types are stored as raw JSON without server-side checks — the
//! admin UI picks the widget and rules (Business Rules) express business
//! constraints. The **`url`** type is the exception: a URL is only useful when
//! it is a real, openable address, so the core rejects anything that is not an
//! absolute `http(s)` URL on every object write.
//!
//! `plan/detail/datenmodell.md` documents the data types; the frontend mirrors
//! the same rule (`meadow/frontend/url_values.py`) for its link rendering.

use serde_json::{Map, Value};
use url::Url;

use crate::cache::{MetaCache, UrlAttribute};
use crate::db::Db;
use crate::error::ApiError;

/// Whether `value` is an absolute `http(s)` URL with a host.
///
/// Relative addresses (`example.com`), other schemes (`ftp:`, `mailto:`,
/// `javascript:`) and unparsable text are rejected — a value of this type is
/// rendered as a link and opened by the browser, so only web addresses qualify.
/// Surrounding whitespace is ignored (pasted values often carry some).
pub fn is_valid_url(value: &str) -> bool {
    let trimmed = value.trim();
    if trimmed.is_empty() {
        return false;
    }
    Url::parse(trimmed).is_ok_and(|url| {
        matches!(url.scheme(), "http" | "https")
            && url.host_str().is_some_and(|host| !host.is_empty())
    })
}

/// Whether the value means "no value" (an empty string clears the attribute).
fn is_empty(value: &Value) -> bool {
    matches!(value, Value::String(text) if text.trim().is_empty())
}

/// One message per value that cannot be used as a URL.
///
/// `null` (clear/break inheritance) and empty strings pass. A multilingual
/// attribute stores `{language: value}`, so its entries are checked
/// individually and the message names the language.
fn invalid_url_messages(attribute: &UrlAttribute, value: &Value) -> Vec<String> {
    let name = &attribute.name;
    let invalid = |path: String| format!("{path}: must be an absolute http(s) URL");
    match value {
        Value::Null => Vec::new(),
        Value::String(_) if is_empty(value) => Vec::new(),
        Value::String(_) if !is_valid_url(value.as_str().unwrap_or_default()) => {
            vec![invalid(format!("`attributes.{name}`"))]
        }
        Value::String(_) => Vec::new(),
        Value::Object(entries) => entries
            .iter()
            .flat_map(|(language, entry)| match entry {
                Value::Null => Vec::new(),
                Value::String(_) if is_empty(entry) => Vec::new(),
                Value::String(text) => (!is_valid_url(text))
                    .then(|| invalid(format!("`attributes.{name}.{language}`")))
                    .into_iter()
                    .collect::<Vec<_>>(),
                _ => vec![format!(
                    "`attributes.{name}.{language}`: must be a string containing a URL"
                )],
            })
            .collect(),
        _ => vec![format!(
            "`attributes.{name}`: must be a string containing a URL"
        )],
    }
}

/// Validates the given attribute values against the declared data types.
///
/// Only the values present in `attributes` are checked (a `PATCH` sends just
/// the changed attributes). Returns `400` with every offending path, joined by
/// `; ` like the other validators.
pub async fn validate_attribute_values(
    cache: &MetaCache,
    db: &Db,
    attributes: &Map<String, Value>,
) -> Result<(), ApiError> {
    let definitions = cache.url_attributes(db).await?;
    if definitions.is_empty() {
        return Ok(());
    }
    let messages: Vec<String> = definitions
        .iter()
        .filter_map(|attribute| {
            let value = attributes.get(&attribute.name)?;
            Some(invalid_url_messages(attribute, value))
        })
        .flatten()
        .collect();
    if messages.is_empty() {
        return Ok(());
    }
    Err(ApiError::bad_request(messages.join("; ")))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn attribute(name: &str, multilingual: bool) -> UrlAttribute {
        UrlAttribute {
            name: name.to_owned(),
            multilingual,
        }
    }

    #[test]
    fn absolute_http_urls_are_accepted() {
        for value in [
            "https://example.com",
            "http://example.com/path?q=1#frag",
            "https://example.com:8443/a/b",
            "  https://example.com  ",
            "http://localhost:8080/products",
            "https://sub.domain.co.uk/x/y",
        ] {
            assert!(is_valid_url(value), "expected `{value}` to be valid");
        }
    }

    #[test]
    fn relative_and_foreign_schemes_are_rejected() {
        for value in [
            "example.com",
            "www.example.com/x",
            "/products/1",
            "https://",
            "ftp://example.com/file",
            "mailto:info@example.com",
            "javascript:alert(1)",
            "data:image/png;base64,AAAA",
            "not a url",
            "",
            "   ",
        ] {
            assert!(!is_valid_url(value), "expected `{value}` to be invalid");
        }
    }

    #[test]
    fn missing_values_and_empty_strings_pass() {
        let link = attribute("homepage", false);
        assert!(invalid_url_messages(&link, &Value::Null).is_empty());
        assert!(invalid_url_messages(&link, &json!("")).is_empty());
        assert!(invalid_url_messages(&link, &json!("   ")).is_empty());
        assert!(invalid_url_messages(&link, &json!("https://example.com")).is_empty());
    }

    #[test]
    fn invalid_scalars_name_the_attribute() {
        let link = attribute("homepage", false);
        assert_eq!(
            invalid_url_messages(&link, &json!("example.com")),
            vec!["`attributes.homepage`: must be an absolute http(s) URL".to_owned()]
        );
        assert_eq!(
            invalid_url_messages(&link, &json!(42)),
            vec!["`attributes.homepage`: must be a string containing a URL".to_owned()]
        );
        assert_eq!(
            invalid_url_messages(&link, &json!(["https://example.com"])),
            vec!["`attributes.homepage`: must be a string containing a URL".to_owned()]
        );
    }

    #[test]
    fn multilingual_values_are_checked_per_language() {
        let link = attribute("homepage", true);
        assert!(invalid_url_messages(&link, &json!({"de": "https://example.de"})).is_empty());
        assert!(invalid_url_messages(&link, &json!({"de": null})).is_empty());
        assert_eq!(
            invalid_url_messages(
                &link,
                &json!({"de": "https://example.de", "en": "example.com"})
            ),
            vec!["`attributes.homepage.en`: must be an absolute http(s) URL".to_owned()]
        );
        assert_eq!(
            invalid_url_messages(&link, &json!({"de": 5})),
            vec!["`attributes.homepage.de`: must be a string containing a URL".to_owned()]
        );
    }
}
