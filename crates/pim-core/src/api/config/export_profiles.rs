//! MCF section `export_profiles` (Phase 9, `plan/detail/bmecat-export.md` §5):
//! the saved configurations of the export wizard.
//!
//! A profile is the `ExportRequest` **without `source`** (format, field list,
//! locales, options, `bmecat` block), so a catalogue migration carries the
//! settings a user curated — otherwise a target environment would have the data
//! but not the way it is exported. Entry shape:
//!
//! ```json
//! {
//!   "name": "Meadow BMEcat 1.2",
//!   "format": "bmecat",
//!   "notes": "…",
//!   "parameters": { "bmecat": { "version": "1.2" } }
//! }
//! ```
//!
//! The record key is derived from `name`, so an import into an empty catalogue
//! produces the same keys as the environment the document came from. An entry
//! with an unknown format or an invalid `parameters` block is reported as an
//! error and skipped — the rest of the document is applied.

use serde_json::{Map, Value, json};

use super::{ConfigReport, Mode, as_str, create_keyed, update_keyed, would_change};
use crate::api::export_profiles::{FORMATS, TABLE, find_profile, normalize_parameters, record_key};
use crate::error::ApiError;

/// Builds the section: every profile, ordered by name.
pub(super) async fn export_profiles_section(db: &crate::db::Db) -> Result<Vec<Value>, ApiError> {
    let rows = super::query_rows(db, "SELECT * FROM export_profile ORDER BY name;").await?;
    Ok(rows
        .into_iter()
        .map(|row| {
            json!({
                "name": as_str(&row, "name").unwrap_or_default(),
                "format": as_str(&row, "format").unwrap_or_default(),
                "notes": row.get("notes").cloned().unwrap_or(Value::Null),
                "parameters": row.get("parameters").cloned().unwrap_or(json!({})),
            })
        })
        .collect())
}

/// Applies the section: creates missing profiles, updates changed ones.
pub(super) async fn apply_export_profiles(
    db: &crate::db::Db,
    mode: Mode,
    entries: &[Value],
    report: &mut ConfigReport,
) -> Result<(), ApiError> {
    for entry in entries {
        let name = as_str(entry, "name").unwrap_or_default().trim().to_owned();
        let format = as_str(entry, "format")
            .unwrap_or_default()
            .trim()
            .to_lowercase();
        if name.is_empty() {
            report.error("export_profiles", String::new(), "entry without `name`");
            continue;
        }
        if !FORMATS.contains(&format.as_str()) {
            report.error(
                "export_profiles",
                name.clone(),
                format!("unsupported format `{format}`"),
            );
            continue;
        }
        let parameters =
            match normalize_parameters(entry.get("parameters").cloned().unwrap_or(json!({}))) {
                Ok(parameters) => parameters,
                Err(error) => {
                    report.error("export_profiles", name.clone(), error.message());
                    continue;
                }
            };

        let mut content = Map::new();
        content.insert("name".to_owned(), Value::String(name.clone()));
        content.insert("format".to_owned(), Value::String(format));
        content.insert("parameters".to_owned(), parameters);
        if let Some(notes) = entry
            .get("notes")
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|value| !value.is_empty())
        {
            content.insert("notes".to_owned(), Value::String(notes.to_owned()));
        }
        let content = Value::Object(content);

        match find_profile(db, &record_key(&name)).await? {
            Some(existing) => {
                // `mode=replace` writes every listed entry, `merge` only changes
                // what differs (the same contract as the other sections).
                if mode == Mode::Replace || would_change(&existing, &content, super::VOLATILE) {
                    if mode == Mode::DryRun {
                        report.updated("export_profiles", &name);
                    } else {
                        let record = super::id_of(&existing, TABLE);
                        update_keyed(db, TABLE, &record, content).await?;
                        report.updated("export_profiles", &name);
                    }
                } else {
                    report.skipped("export_profiles", &name);
                }
            }
            None => {
                if mode == Mode::DryRun {
                    report.created("export_profiles", &name);
                } else {
                    create_keyed(db, TABLE, &record_key(&name), content).await?;
                    report.created("export_profiles", &name);
                }
            }
        }
    }
    Ok(())
}
