//! MCF section `connectors` (Stage 3, `plan/detail/konnektor-konfiguration.md` §14.3):
//! the configured external connections — **without their secrets**.
//!
//! Entry shape:
//!
//! ```json
//! {
//!   "name": "Middleware",
//!   "type": "webhook",
//!   "enabled": true,
//!   "parameters": { "url": "https://…", "filter": { … } }
//! }
//! ```
//!
//! A token belongs to the environment it was issued for: the export **removes**
//! every `secret` field of the type's schema (today the webhook `headers`), and the
//! import **ignores** one that a document still carries — it never overwrites the
//! local value with a stale one. Every entry whose type declares secrets is reported
//! as a warning, so a target that does not deliver yet explains itself instead of
//! looking broken.
//!
//! Identified by `name` (unique in the table): an existing connector is merged
//! (imported parameters over the local ones, so local secrets and extras survive),
//! a missing one is created with a key derived from the name.

use serde_json::{Map, Value, json};

use super::{ConfigReport, Mode, as_str, create_keyed, update_keyed, would_change};
use crate::api::connector_schema;
use crate::api::connector_types;
use crate::error::ApiError;

/// Table name (mirrors `api::connectors::TABLE`, which stays private there).
const TABLE: &str = "connector";

/// Builds the section: every connector, ordered by name, secrets removed.
pub(super) async fn connectors_section(db: &crate::db::Db) -> Result<Vec<Value>, ApiError> {
    let catalog = connector_types::catalog(db).await?;
    let rows = super::query_rows(db, "SELECT * FROM connector ORDER BY name;").await?;
    Ok(rows
        .into_iter()
        .map(|row| {
            let connector_type = as_str(&row, "type").unwrap_or_default().to_owned();
            let parameters = row
                .get("parameters")
                .and_then(Value::as_object)
                .cloned()
                .unwrap_or_default();
            json!({
                "name": as_str(&row, "name").unwrap_or_default(),
                "type": connector_type,
                "enabled": row.get("enabled").and_then(Value::as_bool).unwrap_or(true),
                "parameters": Value::Object(catalog.without_secrets(&connector_type, &parameters)),
            })
        })
        .collect())
}

/// A record key for a connector name (`connector:<slug>`), like the other sections.
fn record_key(name: &str) -> String {
    let mut key = String::new();
    let mut previous_dash = false;
    for character in name.trim().chars() {
        if character.is_ascii_alphanumeric() {
            key.push(character.to_ascii_lowercase());
            previous_dash = false;
        } else if !previous_dash && !key.is_empty() {
            key.push('-');
            previous_dash = true;
        }
    }
    while key.ends_with('-') {
        key.pop();
    }
    key
}

/// Loads a connector by its (unique) name.
async fn find_connector(db: &crate::db::Db, name: &str) -> Result<Option<Value>, ApiError> {
    let rows: Vec<Value> = db
        .inner()
        .query("SELECT * FROM connector WHERE name = $name LIMIT 1;")
        .bind(("name", name.to_owned()))
        .await?
        .check()?
        .take(0)?;
    Ok(rows.into_iter().next())
}

/// Applies the section: creates missing connectors, merges existing ones.
pub(super) async fn apply_connectors(
    db: &crate::db::Db,
    mode: Mode,
    entries: &[Value],
    report: &mut ConfigReport,
) -> Result<(), ApiError> {
    let catalog = connector_types::catalog(db).await?;
    for entry in entries {
        let name = as_str(entry, "name").unwrap_or_default().trim().to_owned();
        if name.is_empty() {
            report.error("connectors", String::new(), "entry without `name`");
            continue;
        }
        let connector_type = as_str(entry, "type")
            .unwrap_or_default()
            .trim()
            .to_lowercase();
        if !catalog.is_known_type(&connector_type) {
            report.error(
                "connectors",
                name.clone(),
                format!("unknown connector type `{connector_type}`"),
            );
            continue;
        }
        let declared = entry
            .get("parameters")
            .and_then(Value::as_object)
            .cloned()
            .unwrap_or_default();
        let incoming = catalog.without_secrets(&connector_type, &declared);
        if incoming != declared {
            report.warn(
                "connectors",
                name.clone(),
                "secret fields are not imported; the value of this environment stays",
            );
        }

        let existing = find_connector(db, &name).await?;
        let mut parameters = match &existing {
            Some(existing) => {
                // Keep what the environment has (secrets, local extras) and put the
                // document's values over it — the same merge the form uses.
                let current = existing
                    .get("parameters")
                    .and_then(Value::as_object)
                    .cloned()
                    .unwrap_or_default();
                connector_schema::merge_parameters(&current, &incoming)
            }
            None => incoming,
        };
        catalog.normalize(&connector_type, &mut parameters);
        catalog.apply_defaults(&connector_type, &mut parameters);
        let problems = catalog.validate(&connector_type, &parameters);
        if let Some(problem) = problems.first() {
            report.error("connectors", name.clone(), problem.text());
            continue;
        }

        let mut content = Map::new();
        content.insert("name".to_owned(), Value::String(name.clone()));
        content.insert("type".to_owned(), Value::String(connector_type));
        content.insert(
            "enabled".to_owned(),
            Value::Bool(
                entry
                    .get("enabled")
                    .and_then(Value::as_bool)
                    .unwrap_or(true),
            ),
        );
        content.insert("parameters".to_owned(), Value::Object(parameters));
        let content = Value::Object(content);

        match existing {
            Some(existing) => {
                if mode == Mode::Replace || would_change(&existing, &content, super::VOLATILE) {
                    if mode == Mode::DryRun {
                        report.updated("connectors", &name);
                    } else {
                        let record = super::id_of(&existing, TABLE);
                        update_keyed(db, TABLE, &record, content).await?;
                        report.updated("connectors", &name);
                    }
                } else {
                    report.skipped("connectors", &name);
                }
            }
            None => {
                if mode == Mode::DryRun {
                    report.created("connectors", &name);
                } else {
                    create_keyed(db, TABLE, &record_key(&name), content).await?;
                    report.created("connectors", &name);
                }
            }
        }
    }
    Ok(())
}
