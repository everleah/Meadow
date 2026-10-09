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

/// Deterministic feature order: by `position`, then by attribute name.
fn sort_features(features: &mut [FeatureEntry]) {
    features.sort_by(|a, b| {
        a.position
            .cmp(&b.position)
            .then_with(|| a.attribute.cmp(&b.attribute))
    });
}

/// The stable key of a class node: the value of the type's first `key_attributes`
/// entry. `key_attributes` are `text` and not multilingual (enforced by the
/// object-type API), so the value is a plain string; everything else yields `""`
/// and the entry then relies on `class_id` alone.
fn class_key_value(node: Option<&Value>, key_attribute: Option<&str>) -> String {
    key_attribute
        .and_then(|name| node?.get("attributes")?.as_object()?.get(name)?.as_str())
        .unwrap_or_default()
        .to_owned()
}

/// The local id a document offers for a class, when it can be one: `class_id` without the space a
/// JSON writer may leave around it, and without the empty value. An empty id is not a missing value
/// but a wrong one — the lookup asks SurrealDB for `type::record('object','')` and the answer is an
/// error (`Found  for the Record ID but this is not a valid id`), so a document that writes it
/// answered `500` instead of the report entry that says the taxonomy data has to be imported first
/// (measured against Meadow 43c83fe6 with the artifact of SketchGround's MCF profile).
fn local_class_key(entry: &Value) -> Option<&str> {
    as_str(entry, "class_id")
        .map(str::trim)
        .filter(|value| !value.is_empty())
}

/// Report label of one entry: the stable key first, then the local `class_id`,
/// then a placeholder — so an unresolvable class stays traceable in the report.
fn class_label(entry: &Value) -> String {
    as_str(entry, "class")
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
        .or_else(|| local_class_key(entry).map(str::to_owned))
        .unwrap_or_else(|| "(unnamed class)".to_owned())
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
    // The class nodes are read by **type**, not by an id list: with a
    // classification standard installed the section carries thousands of classes,
    // and `id IN array::map(<ids>, …)` needed a lookup per table row (measured:
    // 5640 keys → 110 s). The type filter scans the 5.7 k objects instead (0.16 s).
    // Every class node is a taxonomy node, so the class ids are a subset of it.
    let nodes = query_rows(
        db,
        "SELECT id, type, attributes FROM object WHERE type IN (SELECT VALUE name FROM object_type WHERE taxonomy = true);",
    )
    .await?;
    let nodes_by_id: HashMap<String, Value> = nodes
        .into_iter()
        .filter_map(|node| Some((as_str(&node, "id")?.to_owned(), node)))
        .collect();

    let mut out = Vec::new();
    for (class_id, mut features) in by_class {
        sort_features(&mut features);
        let node = nodes_by_id.get(&class_id);
        let node_type = node
            .and_then(|node| as_str(node, "type"))
            .unwrap_or_default();
        let key_attribute = keys_by_type.get(node_type).map(String::as_str);
        let class = class_key_value(node, key_attribute);
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
    let Some(class_id) = local_class_key(entry) else {
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
        let label = class_label(entry);

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

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn record_key_strips_the_table_prefix() {
        assert_eq!(record_key("object:01ABC"), "01ABC");
        assert_eq!(record_key("01ABC"), "01ABC");
    }

    #[test]
    fn class_key_value_reads_the_configured_key_attribute() {
        let node = json!({ "type": "etim_class", "attributes": { "code": "ETIM-12345" } });
        assert_eq!(class_key_value(Some(&node), Some("code")), "ETIM-12345");
        // Without a node, without a key attribute, with an unknown attribute
        // name or with a non-string value the key stays empty; the entry then
        // falls back to `class_id` on import.
        assert_eq!(class_key_value(None, Some("code")), "");
        assert_eq!(class_key_value(Some(&node), None), "");
        assert_eq!(class_key_value(Some(&node), Some("nope")), "");
        let numeric = json!({ "attributes": { "code": 42 } });
        assert_eq!(class_key_value(Some(&numeric), Some("code")), "");
    }

    #[test]
    fn class_label_prefers_the_stable_key() {
        assert_eq!(
            class_label(&json!({ "class": "ETIM-12345", "class_id": "object:01ABC" })),
            "ETIM-12345"
        );
        assert_eq!(
            class_label(&json!({ "class": "", "class_id": "object:01ABC" })),
            "object:01ABC",
            "an empty key falls back to the local id"
        );
        assert_eq!(
            class_label(&json!({ "class_id": "object:01ABC" })),
            "object:01ABC"
        );
        assert_eq!(class_label(&json!({})), "(unnamed class)");
    }

    #[test]
    fn local_class_key_ignores_an_empty_id() {
        assert_eq!(
            local_class_key(&json!({ "class_id": "object:01ABC" })),
            Some("object:01ABC")
        );
        assert_eq!(
            local_class_key(&json!({ "class_id": "  01ABC  " })),
            Some("01ABC"),
            "surrounding space is not part of the key"
        );
        assert_eq!(
            local_class_key(&json!({ "class_id": "" })),
            None,
            "an empty id must not reach `type::record` - SurrealDB answers with an error, and a \
             document that writes one has to get the report entry about the missing taxonomy data"
        );
        assert_eq!(local_class_key(&json!({ "class_id": "   " })), None);
        assert_eq!(local_class_key(&json!({ "class": "200" })), None);
        assert_eq!(
            class_label(&json!({ "class": "200", "class_id": "" })),
            "200",
            "and it stays out of the report label too"
        );
    }

    #[test]
    fn sort_features_orders_by_position_then_attribute() {
        let mut features = vec![
            FeatureEntry {
                attribute: "b".to_owned(),
                required: false,
                position: 1,
            },
            FeatureEntry {
                attribute: "a".to_owned(),
                required: true,
                position: 1,
            },
            FeatureEntry {
                attribute: "z".to_owned(),
                required: false,
                position: 0,
            },
        ];
        sort_features(&mut features);
        let order: Vec<&str> = features.iter().map(|f| f.attribute.as_str()).collect();
        assert_eq!(order, ["z", "a", "b"]);
    }
}
