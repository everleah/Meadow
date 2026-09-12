//! MCF section `category_attributes` (Stage B): the class features.
//!
//! Class features are **configuration anchored to data**: the feature rows hang
//! off taxonomy nodes (categories), which are ordinary objects and therefore
//! *not* part of an MCF document (MCF = model, MEF = data). To stay portable the
//! section references a class by its **stable business key** - the value of the
//! node type's first `key_attributes` entry (e.g. the ETIM class number) - and
//! keeps the local `class_id` only as a fallback. A class that cannot be
//! resolved in the target is reported as an error entry with the hint that the
//! taxonomy data has to be imported first.
//!
//! Entry shape (one entry per class; its feature list is authoritative):
//!
//! ```json
//! {
//!   "class": "ETIM-12345",          // stable key value ("" when the node has none)
//!   "class_key": "code",            // attribute that carried the key
//!   "class_id": "object:01M...",    // local id, fallback for the same environment
//!   "features": [{ "attribute": "power", "required": true, "position": 0 }]
//! }
//! ```
//!
//! See `plan/detail/konfiguration-export-import.md` (section 4.9) and
//! `plan/detail/kategorisierung.md` (Stufe B).

use std::collections::{BTreeMap, HashMap};

use serde_json::{Map, Value, json};

use super::{ConfigReport, Mode, as_bool, as_str, as_string_array, query_rows};
use crate::error::ApiError;

/// A feature row of one class inside the document.
struct FeatureEntry {
    attribute: String,
    required: bool,
    position: i64,
}

/// First `key_attributes` entry per object type: the stable key of a class.
async fn key_attribute_by_type(db: &crate::db::Db) -> Result<HashMap<String, String>, ApiError> {
    let rows = query_rows(db, "SELECT * FROM object_type;").await?;
    Ok(rows
        .into_iter()
        .filter_map(|row| {
            let name = as_str(&row, "name")?.to_owned();
            let key = as_string_array(&row, "key_attributes").into_iter().next()?;
            Some((name, key))
        })
        .collect())
}

/// `object:<key>` -> `<key>` (the record key SurrealDB expects).
fn record_key(id: &str) -> &str {
    id.split_once(':').map(|(_, key)| key).unwrap_or(id)
}

/// Renders a JSON array of strings as a SurrealDB list literal.
fn sql_string_list(values: impl IntoIterator<Item = String>) -> String {
    Value::Array(values.into_iter().map(Value::String).collect()).to_string()
}

/// Builds the section: one entry per class that carries feature rows.
pub(super) async fn category_attributes_section(
    db: &crate::db::Db,
) -> Result<Vec<Value>, ApiError> {
    let rows = query_rows(db, "SELECT * FROM category_attribute;").await?;
    if rows.is_empty() {
        return Ok(Vec::new());
    }
    // Group the feature rows by class (BTreeMap keeps the output stable).
    let mut by_class: BTreeMap<String, Vec<FeatureEntry>> = BTreeMap::new();
    for row in rows {
        let Some(class_id) = as_str(&row, "category_id").map(str::to_owned) else {
            continue;
        };
        let Some(attribute) = as_str(&row, "attribute").map(str::to_owned) else {
            continue;
        };
        by_class.entry(class_id).or_default().push(FeatureEntry {
            attribute,
            required: as_bool(&row, "required"),
            position: row.get("position").and_then(Value::as_i64).unwrap_or(0),
        });
    }
    let keys_by_type = key_attribute_by_type(db).await?;
    // The class nodes carry `type` (to find the key attribute) and `attributes`
    // (the key value itself); loaded in one query. The ids must be turned into
    // records (`type::record`) — a plain string list never matches `record<object>`.
    let node_keys: Vec<String> = by_class
        .keys()
        .map(|id| record_key(id).to_owned())
        .collect();
    let nodes = query_rows(
        db,
        &format!(
            "SELECT * FROM object WHERE id IN array::map({}, |$k| type::record('object', $k));",
            sql_string_list(node_keys)
        ),
    )
    .await?;
    let nodes_by_id: HashMap<String, Value> = nodes
        .into_iter()
        .filter_map(|node| Some((as_str(&node, "id")?.to_owned(), node)))
        .collect();

    let mut out = Vec::new();
    for (class_id, mut features) in by_class {
        features.sort_by(|a, b| {
            a.position
                .cmp(&b.position)
                .then_with(|| a.attribute.cmp(&b.attribute))
        });
        let node = nodes_by_id.get(&class_id);
        let node_type = node
            .and_then(|node| as_str(node, "type"))
            .unwrap_or_default();
        let key_attribute = keys_by_type.get(node_type).map(String::as_str);
        // `key_attributes` are `text` and not multilingual (enforced by the
        // object-type API), so the value is a plain string.
        let class = key_attribute
            .and_then(|name| {
                node?
                    .get("attributes")
                    .and_then(Value::as_object)
                    .and_then(|attributes| attributes.get(name))
                    .and_then(Value::as_str)
            })
            .unwrap_or_default()
            .to_owned();
        out.push(json!({
            "class": class,
            "class_key": key_attribute,
            "class_id": class_id,
            "features": features
                .iter()
                .map(|feature| json!({
                    "attribute": feature.attribute,
                    "required": feature.required,
                    "position": feature.position,
                }))
                .collect::<Vec<Value>>(),
        }));
    }
    Ok(out)
}

/// Resolves the class node in the target: over the stable key first (portable),
/// then over the local `class_id` (same environment only).
async fn resolve_class(
    db: &crate::db::Db,
    entry: &Value,
    taxonomy_types: &[String],
) -> Result<Option<String>, ApiError> {
    let class = as_str(entry, "class")
        .map(str::to_owned)
        .unwrap_or_default();
    let class_key = as_str(entry, "class_key").map(str::to_owned);
    if !class.is_empty()
        && let Some(key) = class_key
        && !taxonomy_types.is_empty()
    {
        let rows = query_rows(
            db,
            &format!(
                "SELECT id, attributes FROM object WHERE type IN {} AND deleted_at IS NONE;",
                sql_string_list(taxonomy_types.iter().cloned())
            ),
        )
        .await?;
        for row in rows {
            let matches = row
                .get("attributes")
                .and_then(Value::as_object)
                .and_then(|attributes| attributes.get(&key))
                .and_then(Value::as_str)
                .map(|candidate| candidate == class)
                .unwrap_or(false);
            if matches && let Some(id) = as_str(&row, "id") {
                return Ok(Some(id.to_owned()));
            }
        }
    }
    let Some(class_id) = as_str(entry, "class_id") else {
        return Ok(None);
    };
    let rows = query_rows(
        db,
        &format!(
            "SELECT id FROM object WHERE id = type::record('object', {}) AND deleted_at IS NONE LIMIT 1;",
            json!(record_key(class_id).to_owned())
        ),
    )
    .await?;
    Ok(rows
        .into_iter()
        .find_map(|row| as_str(&row, "id").map(str::to_owned)))
}

/// Applies the section. The feature list of every **listed** class is
/// authoritative (rows of that class which the document does not name are
/// removed); classes the document does not mention stay untouched. Unknown
/// classes and unknown attributes are reported and skipped.
pub(super) async fn apply_category_attributes(
    db: &crate::db::Db,
    mode: Mode,
    entries: &[Value],
    report: &mut ConfigReport,
) -> Result<(), ApiError> {
    if entries.is_empty() {
        return Ok(());
    }
    let taxonomy_types = crate::api::object_types::taxonomy_types(db).await?;
    let known_attributes: std::collections::HashSet<String> =
        query_rows(db, "SELECT VALUE name FROM attribute_definition;")
            .await?
            .into_iter()
            .filter_map(|name| name.as_str().map(str::to_owned))
            .collect();

    for entry in entries {
        let label = as_str(entry, "class")
            .filter(|value| !value.is_empty())
            .map(str::to_owned)
            .or_else(|| as_str(entry, "class_id").map(str::to_owned))
            .unwrap_or_else(|| "(unnamed class)".to_owned());

        let Some(node_id) = resolve_class(db, entry, &taxonomy_types).await? else {
            report.error(
                "category_attributes",
                &label,
                "class not found in the target; import the taxonomy data first",
            );
            continue;
        };
        let node_key = record_key(&node_id).to_owned();

        // Desired rows (unknown attribute definitions are reported, not applied).
        let mut desired: BTreeMap<String, FeatureEntry> = BTreeMap::new();
        for feature in entry
            .get("features")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default()
        {
            let Some(attribute) = as_str(&feature, "attribute").map(str::to_owned) else {
                continue;
            };
            if !known_attributes.contains(&attribute) {
                report.error(
                    "category_attributes",
                    &label,
                    format!("unknown attribute `{attribute}`"),
                );
                continue;
            }
            desired.insert(
                attribute.clone(),
                FeatureEntry {
                    attribute,
                    required: as_bool(&feature, "required"),
                    position: feature.get("position").and_then(Value::as_i64).unwrap_or(0),
                },
            );
        }

        // Current rows of that class.
        let current = query_rows(
            db,
            &format!(
                "SELECT id, attribute, required, position FROM category_attribute                  WHERE category_id = type::record('object', {});",
                json!(node_key)
            ),
        )
        .await?;
        let mut existing: BTreeMap<String, Value> = current
            .into_iter()
            .filter_map(|row| Some((as_str(&row, "attribute")?.to_owned(), row)))
            .collect();

        let mut changed = false;
        for (attribute, feature) in &desired {
            match existing.remove(attribute) {
                Some(row) => {
                    let unchanged = as_bool(&row, "required") == feature.required
                        && row.get("position").and_then(Value::as_i64).unwrap_or(0)
                            == feature.position;
                    if unchanged {
                        continue;
                    }
                    changed = true;
                    if mode == Mode::DryRun {
                        continue;
                    }
                    let row_id = as_str(&row, "id").unwrap_or_default().to_owned();
                    let mut content = Map::new();
                    content.insert("required".to_owned(), Value::Bool(feature.required));
                    content.insert("position".to_owned(), Value::from(feature.position));
                    db.inner()
                        .query(format!(
                            "UPDATE type::record('category_attribute', {}) MERGE $content;",
                            json!(record_key(&row_id).to_owned())
                        ))
                        .bind(("content", Value::Object(content)))
                        .await?
                        .check()?;
                }
                None => {
                    changed = true;
                    if mode == Mode::DryRun {
                        continue;
                    }
                    db.inner()
                        .query(
                            "CREATE category_attribute CONTENT {                                category_id: type::record('object', $key), attribute: $attribute,                                required: $required, position: $position,                                created_at: time::now(), updated_at: time::now() };",
                        )
                        .bind(("key", node_key.clone()))
                        .bind(("attribute", feature.attribute.clone()))
                        .bind(("required", feature.required))
                        .bind(("position", feature.position))
                        .await?
                        .check()?;
                }
            }
        }
        // The feature list of a listed class is authoritative: drop the rest.
        for (_, row) in existing {
            changed = true;
            if mode == Mode::DryRun {
                continue;
            }
            let row_id = as_str(&row, "id").unwrap_or_default().to_owned();
            db.inner()
                .query(format!(
                    "DELETE type::record('category_attribute', {});",
                    json!(record_key(&row_id).to_owned())
                ))
                .await?
                .check()?;
        }

        if changed {
            report.updated("category_attributes", &label);
        } else {
            report.skipped("category_attributes", &label);
        }
    }
    Ok(())
}
