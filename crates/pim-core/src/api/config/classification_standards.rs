//! MCF section `classification_standards` (Phase 9): the registry of installed
//! classification standards.
//!
//! The class nodes, their features and the feature attribute definitions travel
//! as **data** (MEF) plus their model (MCF) in a *standard bundle*
//! (`plan/detail/bmecat-export.md` §11.9). What those documents cannot say about
//! themselves is which standard in which version they are and where they came
//! from — this section carries exactly that, so an imported bundle documents
//! itself and the BMEcat export can name the reference feature system
//! (`ETIM-10.0`) from installed facts instead of a free-text profile field.
//!
//! Entry shape (one entry per `code` + `version`; the pair is the stable key):
//!
//! ```json
//! {
//!   "code": "etim",
//!   "version": "10.0",
//!   "name": "ETIM",
//!   "source": "ETIM-10.0-ALL-SECTORS-IXF-NO-CHANGE-CODES-METRIC-EI",
//!   "node_type": "etim_class",
//!   "notes": "…"
//! }
//! ```
//!
//! `node_type` is applied as given; an unknown or non-taxonomy type is reported as
//! a warning (a document may be imported before its object types are), while the
//! interactive API rejects it with `400`.

use serde_json::{Map, Value, json};

use super::{ConfigReport, Mode, as_str, create_keyed, update_keyed, would_change};
use crate::api::classification_standards::{TABLE, find_standard, record_key};
use crate::error::ApiError;

/// The stable key of an entry as it appears in reports (`etim@10.0`).
fn label(entry: &Value) -> String {
    let code = as_str(entry, "code").unwrap_or_default();
    let version = as_str(entry, "version").unwrap_or_default();
    if code.is_empty() || version.is_empty() {
        return String::new();
    }
    format!("{code}@{version}")
}

/// Builds the section: one entry per registered standard, newest first.
pub(super) async fn classification_standards_section(
    db: &crate::db::Db,
) -> Result<Vec<Value>, ApiError> {
    let rows = super::query_rows(
        db,
        "SELECT * FROM classification_standard ORDER BY code, version;",
    )
    .await?;
    Ok(rows
        .into_iter()
        .map(|row| {
            json!({
                "code": as_str(&row, "code").unwrap_or_default(),
                "version": as_str(&row, "version").unwrap_or_default(),
                "name": row.get("name").cloned().unwrap_or(Value::Null),
                "source": row.get("source").cloned().unwrap_or(Value::Null),
                "node_type": row.get("node_type").cloned().unwrap_or(Value::Null),
                "notes": row.get("notes").cloned().unwrap_or(Value::Null),
            })
        })
        .collect())
}

/// Applies the section: registers standards, updates changed fields, reports
/// unknown node types as warnings.
///
/// The record key is derived from `code` + `version`, so an import into an empty
/// target produces the same keys as the environment the document came from.
pub(super) async fn apply_classification_standards(
    db: &crate::db::Db,
    mode: Mode,
    entries: &[Value],
    report: &mut ConfigReport,
) -> Result<(), ApiError> {
    for entry in entries {
        let code = as_str(entry, "code")
            .unwrap_or_default()
            .trim()
            .to_lowercase();
        let version = as_str(entry, "version")
            .unwrap_or_default()
            .trim()
            .to_owned();
        if code.is_empty() || version.is_empty() {
            report.error(
                "classification_standards",
                label(entry),
                "entry without `code` or `version`",
            );
            continue;
        }
        let key_label = format!("{code}@{version}");

        // A `null` name would be rejected by an `option<string>` field, so absent
        // values are simply not written.
        let mut content = Map::new();
        content.insert("code".to_owned(), Value::String(code.clone()));
        content.insert("version".to_owned(), Value::String(version.clone()));
        for field in ["name", "source", "notes"] {
            if let Some(value) = entry.get(field).and_then(Value::as_str).map(str::trim)
                && !value.is_empty()
            {
                content.insert(field.to_owned(), Value::String(value.to_owned()));
            }
        }
        let mut node_type = None;
        if let Some(value) = entry
            .get("node_type")
            .and_then(Value::as_str)
            .map(str::trim)
            && !value.is_empty()
        {
            node_type = Some(value.to_owned());
            content.insert("node_type".to_owned(), Value::String(value.to_owned()));
        }
        if let Some(node_type) = node_type.as_deref()
            && !crate::api::object_types::is_taxonomy_type(db, node_type).await?
        {
            report.warn(
                "classification_standards",
                &key_label,
                format!("`node_type` `{node_type}` is not a taxonomy type in this environment"),
            );
        }
        let content = Value::Object(content);

        match find_standard(db, &code, &version).await? {
            Some(existing) => {
                let record = super::id_of(&existing, TABLE);
                if would_change(&existing, &content, super::VOLATILE) {
                    if mode == Mode::DryRun {
                        report.updated("classification_standards", &key_label);
                    } else {
                        update_keyed(db, TABLE, &record, content).await?;
                        report.updated("classification_standards", &key_label);
                    }
                } else {
                    report.skipped("classification_standards", &key_label);
                }
            }
            None => {
                if mode == Mode::DryRun {
                    report.created("classification_standards", &key_label);
                } else {
                    create_keyed(db, TABLE, &record_key(&code, &version), content).await?;
                    report.created("classification_standards", &key_label);
                }
            }
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn label_names_code_and_version() {
        assert_eq!(
            label(&json!({ "code": "etim", "version": "10.0" })),
            "etim@10.0"
        );
        // An incomplete entry must not produce a misleading label.
        assert_eq!(label(&json!({ "code": "etim" })), "");
        assert_eq!(label(&json!({ "version": "10.0" })), "");
    }
}
