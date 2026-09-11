//! Attribute value inheritance along the composition (`depends_on`) chain.
//!
//! Only `inheritable: true` attribute definitions participate. An attribute
//! is considered "set" when its key exists in the object's `attributes`
//! (even `null` or an empty string) — a set value stops inheritance. Missing
//! keys are resolved by walking up to the nearest ancestor that has the key.
//!
//! Each ancestor is read with its attributes in a single query, so resolving
//! the chain costs one query per level (the attribute-name list is cached).
//! Besides filling the values, the resolvers report the **source** of every
//! inherited value (`{from, label}`), which the UI shows as "inherited from …".

use std::collections::HashMap;
use std::sync::Arc;

use serde_json::{Map, Value, json};

use crate::cache::MetaCache;
use crate::db::Db;
use crate::error::ApiError;

const MAX_DEPTH: usize = 1000;

/// Extracts the key part of a record id (`table:key` → `key`).
fn key_of(record_id: &str) -> &str {
    record_id
        .split_once(':')
        .map(|(_, key)| key)
        .unwrap_or(record_id)
}

/// Returns the composition parent's key together with its `attributes` and type
/// in a single query (`None` for the root or when there is no parent).
async fn parent_with_attributes(
    db: &Db,
    key: &str,
) -> Result<Option<(String, Map<String, Value>, String)>, ApiError> {
    let sql = "
        LET $src = type::record('object', $key);
        SELECT out AS parent, out.attributes AS attributes, out.type AS parent_type
        FROM reference WHERE in = $src AND definition_id = 'rel_depends_on' LIMIT 1;
    ";
    let mut response = db
        .inner()
        .query(sql)
        .bind(("key", key.to_owned()))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(1)?;
    let Some(row) = rows.into_iter().next() else {
        return Ok(None);
    };
    let Some(parent) = row
        .get("parent")
        .and_then(Value::as_str)
        .map(key_of)
        .map(str::to_owned)
    else {
        return Ok(None);
    };
    let attributes = row
        .get("attributes")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    let parent_type = row
        .get("parent_type")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_owned();
    Ok(Some((parent, attributes, parent_type)))
}

/// Builds the `{from, label}` source entry of a value inherited from `parent`.
fn source_entry(
    parent: &str,
    parent_type: &str,
    parent_attributes: &Map<String, Value>,
    locale: Option<&str>,
    fallback_chain: Option<&[String]>,
) -> Value {
    let label =
        crate::language::resolve_label(locale, fallback_chain, parent_attributes.get("name"))
            .unwrap_or_else(|| {
                if parent_type.is_empty() {
                    parent.to_owned()
                } else {
                    parent_type.to_owned()
                }
            });
    json!({ "from": format!("object:{parent}"), "label": label })
}

/// Resolves inheritable attributes that are missing from `attributes` by
/// walking up the `depends_on` chain. `locale` optionally resolves
/// multilingual ancestor values before inheriting them.
///
/// Returns one entry per **inherited** attribute (`name > {from, label}`); a
/// value explicitly emptied by an ancestor (`null`) is filled but not reported
/// as inherited, because it carries no ancestor value.
pub async fn resolve_inheritance(
    cache: &MetaCache,
    db: &Db,
    object_key: &str,
    locale: Option<&str>,
    attributes: &mut Map<String, Value>,
) -> Result<Map<String, Value>, ApiError> {
    let names = cache.inheritable_names(db).await?;
    let mut missing: Vec<String> = names
        .iter()
        .filter(|name| !attributes.contains_key(name.as_str()))
        .cloned()
        .collect();
    let mut sources = Map::new();
    if missing.is_empty() {
        return Ok(sources);
    }

    let fallback_chain: Option<Arc<Vec<String>>> = match locale {
        Some(locale) => Some(cache.fallback_chain(db, locale).await?),
        None => None,
    };

    let mut current = object_key.to_owned();
    for _ in 0..MAX_DEPTH {
        let Some((parent, parent_attributes, parent_type)) =
            parent_with_attributes(db, &current).await?
        else {
            break;
        };
        let mut still_missing: Vec<String> = Vec::new();
        for name in missing {
            match parent_attributes.get(&name) {
                Some(value) => {
                    let inherited = match (locale, &fallback_chain, value.is_object()) {
                        (Some(locale), Some(chain), true) => {
                            crate::language::resolve_value_with_chain(locale, value, chain)
                                .unwrap_or(Value::Null)
                        }
                        _ => value.clone(),
                    };
                    if !inherited.is_null() {
                        sources.insert(
                            name.clone(),
                            source_entry(
                                &parent,
                                &parent_type,
                                &parent_attributes,
                                locale,
                                fallback_chain.as_deref().map(|chain| chain.as_slice()),
                            ),
                        );
                    }
                    attributes.insert(name, inherited);
                }
                None => still_missing.push(name),
            }
        }
        missing = still_missing;
        if missing.is_empty() {
            break;
        }
        current = parent;
    }
    Ok(sources)
}

/// Resolves inheritance for many objects with **one query per level** instead
/// of one per object (used by the object list / grid).
///
/// `objects` holds `(record id, attributes)` pairs which are filled in place;
/// the return value maps each record id to its `{name > {from, label}}` sources.
pub async fn resolve_inheritance_batch(
    cache: &MetaCache,
    db: &Db,
    locale: Option<&str>,
    objects: &mut [(String, Map<String, Value>)],
) -> Result<Map<String, Value>, ApiError> {
    let names = cache.inheritable_names(db).await?;
    let fallback_chain: Option<Arc<Vec<String>>> = match locale {
        Some(locale) => Some(cache.fallback_chain(db, locale).await?),
        None => None,
    };
    let chain_slice = fallback_chain.as_deref().map(|chain| chain.as_slice());

    // Walk level by level: `current` is the node whose parent still has to be
    // read for that object, `missing` the attribute names it still lacks.
    let mut current: Vec<Option<String>> = objects
        .iter()
        .map(|(id, _)| Some(key_of(id).to_owned()))
        .collect();
    let mut missing: Vec<Vec<String>> = objects
        .iter()
        .map(|(_, attributes)| {
            names
                .iter()
                .filter(|name| !attributes.contains_key(name.as_str()))
                .cloned()
                .collect()
        })
        .collect();
    let mut sources: Map<String, Value> = Map::new();

    for _ in 0..MAX_DEPTH {
        let keys: Vec<String> = current
            .iter()
            .enumerate()
            .filter(|(index, _)| !missing[*index].is_empty())
            .filter_map(|(_, key)| key.clone())
            .collect();
        if keys.is_empty() {
            break;
        }
        let sql = "
            LET $srcs = array::map($keys, |$k| type::record('object', $k));
            SELECT in AS child, out AS parent, out.attributes AS attributes,
                   out.type AS parent_type
            FROM reference WHERE in IN $srcs AND definition_id = 'rel_depends_on';
        ";
        let mut response = db.inner().query(sql).bind(("keys", keys)).await?.check()?;
        let rows: Vec<Value> = response.take(1)?;
        let mut parents: HashMap<String, (String, Map<String, Value>, String)> = HashMap::new();
        for row in rows {
            let Some(child) = row.get("child").and_then(Value::as_str) else {
                continue;
            };
            let Some(parent) = row.get("parent").and_then(Value::as_str) else {
                continue;
            };
            let attributes = row
                .get("attributes")
                .and_then(Value::as_object)
                .cloned()
                .unwrap_or_default();
            let parent_type = row
                .get("parent_type")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_owned();
            parents.insert(
                key_of(child).to_owned(),
                (key_of(parent).to_owned(), attributes, parent_type),
            );
        }

        for index in 0..objects.len() {
            if missing[index].is_empty() {
                continue;
            }
            let Some(child_key) = current[index].clone() else {
                missing[index].clear();
                continue;
            };
            let Some((parent, parent_attributes, parent_type)) = parents.get(&child_key) else {
                // Reached the root: nothing else to inherit.
                missing[index].clear();
                current[index] = None;
                continue;
            };
            let object_id = objects[index].0.clone();
            let mut still_missing: Vec<String> = Vec::new();
            for name in std::mem::take(&mut missing[index]) {
                match parent_attributes.get(&name) {
                    Some(value) => {
                        let inherited = match (locale, &fallback_chain, value.is_object()) {
                            (Some(locale), Some(chain), true) => {
                                crate::language::resolve_value_with_chain(locale, value, chain)
                                    .unwrap_or(Value::Null)
                            }
                            _ => value.clone(),
                        };
                        if !inherited.is_null() {
                            let entry = source_entry(
                                parent,
                                parent_type,
                                parent_attributes,
                                locale,
                                chain_slice,
                            );
                            match sources.get_mut(&object_id) {
                                Some(Value::Object(map)) => {
                                    map.insert(name.clone(), entry);
                                }
                                _ => {
                                    let mut map = Map::new();
                                    map.insert(name.clone(), entry);
                                    sources.insert(object_id.clone(), Value::Object(map));
                                }
                            }
                        }
                        objects[index].1.insert(name, inherited);
                    }
                    None => still_missing.push(name),
                }
            }
            missing[index] = still_missing;
            current[index] = Some(parent.clone());
        }
    }
    Ok(sources)
}
