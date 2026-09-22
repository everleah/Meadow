//! Attribute value inheritance along the composition (`depends_on`) chain.
//!
//! Only `inheritable: true` attribute definitions participate. An attribute
//! is considered "set" when its key exists in the object's `attributes`
//! (even `null` or an empty string) — a set value stops inheritance. Missing
//! keys are resolved by walking up to the nearest ancestor that has the key.
//!
//! Two shapes exist, and both must answer the same thing:
//! * **single object** ([`resolve_inheritance`]) — one query per level, each
//!   reading the parent together with its attributes. Depth is a handful of
//!   levels, so the round trips are cheap.
//! * **many objects** ([`resolve_inheritance_batch`], the grid) — the parent map
//!   of the whole composition tree and the ancestors' attributes in **two**
//!   queries, then an in-memory walk per object. A query per level per page is
//!   wrong here: the level query passes the page's keys as an `IN` list, and
//!   SurrealDB evaluates such a list **per scanned row** (measured 2026-09-22:
//!   `reference WHERE in IN [<1000 ids>]` 2.6 s, repeated per level → 6.5 s for
//!   a 1000-row product page).
//!
//! Besides filling the values, the resolvers report the **source** of every
//! inherited value (`{from, label}`), which the UI shows as "inherited from …".

use std::collections::{HashMap, HashSet};
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
    // The parent filter is rendered as a **record literal**, never as a bound
    // record (or `type::record('object', $key)`): a bound record is planned as a
    // filter on top of an index scan over the whole definition, which is why
    // `keys::record_literal` exists (see its doc comment for the numbers). A key
    // outside the allowed charset cannot exist, so `None` means "no parent".
    let Some(record) = crate::api::keys::record_literal("object", key) else {
        return Ok(None);
    };
    let sql = format!(
        "SELECT out AS parent, out.attributes AS attributes, out.type AS parent_type
        FROM reference WHERE in = {record} AND definition_id = 'rel_depends_on' LIMIT 1;"
    );
    let mut response = db.inner().query(sql).await?.check()?;
    let rows: Vec<Value> = response.take(0)?;
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

/// The composition facts one batch needs, loaded **once per page**: the
/// `depends_on` map plus the attributes and types of the ancestors that are not
/// part of the batch.
///
/// [`crate::completeness::compute_batch`] and [`resolve_inheritance_batch`]
/// describe the same page and used to load this separately (two extra queries and
/// a duplicate walk per page); they now share one instance.
#[derive(Debug)]
pub(crate) struct Composition {
    /// `child key → parent key` of the whole composition tree (shared, cached).
    parents: Arc<HashMap<String, String>>,
    /// Ancestor key → attributes (only ancestors **outside** the batch).
    ancestors: HashMap<String, Map<String, Value>>,
    /// Ancestor key → object type, for the label fallback of [`source_entry`].
    object_types: HashMap<String, String>,
}

impl Composition {
    /// Loads the map and the ancestors' attributes of the given object keys.
    ///
    /// The map comes from the version-bound [`MetaCache::composition_map`] — it is
    /// a full scan of `reference` and was previously read twice per page (once for
    /// completeness, once for inheritance), each time costing 0,15–0,23 s on a
    /// quiet machine.
    pub(crate) async fn load<'a>(
        cache: &MetaCache,
        db: &Db,
        keys: impl IntoIterator<Item = &'a str>,
    ) -> Result<Self, ApiError> {
        let parents = cache.composition_map(db).await?;
        let mut ancestors = HashMap::new();
        let mut object_types = HashMap::new();
        for (key, attributes, object_type) in load_ancestor_attributes(db, &parents, keys).await? {
            ancestors.insert(key.clone(), attributes);
            object_types.insert(key, object_type);
        }
        Ok(Self {
            parents,
            ancestors,
            object_types,
        })
    }

    pub(crate) fn parents(&self) -> &HashMap<String, String> {
        &self.parents
    }

    pub(crate) fn ancestors(&self) -> &HashMap<String, Map<String, Value>> {
        &self.ancestors
    }
}

/// Resolves inheritance for many objects from the composition of the page plus an
/// in-memory walk, instead of one query per level (used by the object list / grid).
///
/// `objects` holds `(record id, object type, attributes)` triples — the same
/// shape [`crate::completeness::compute_batch`] takes, because both describe the
/// same page — and the attributes are filled in place. The return value maps
/// each record id to its `{name > {from, label}}` sources; an object without
/// inherited values is absent from it.
pub(crate) async fn resolve_inheritance_batch(
    cache: &MetaCache,
    db: &Db,
    locale: Option<&str>,
    objects: &mut [(String, String, Map<String, Value>)],
    composition: &Composition,
) -> Result<Map<String, Value>, ApiError> {
    let mut sources: Map<String, Value> = Map::new();
    if objects.is_empty() {
        return Ok(sources);
    }
    let names = cache.inheritable_names(db).await?;
    let fallback_chain: Option<Arc<Vec<String>>> = match locale {
        Some(locale) => Some(cache.fallback_chain(db, locale).await?),
        None => None,
    };
    let chain_slice = fallback_chain.as_deref().map(|chain| chain.as_slice());

    let (attrs_by_key, types_by_key) = lookup_maps(objects, composition);
    let walk_started = std::time::Instant::now();
    for (object_id, _, attributes) in objects.iter_mut() {
        let missing: Vec<String> = names
            .iter()
            .filter(|name| !attributes.contains_key(name.as_str()))
            .cloned()
            .collect();
        if missing.is_empty() {
            continue;
        }
        let start = key_of(object_id).to_owned();
        let (resolved, origins) = resolve_missing_in_memory(
            attributes,
            &missing,
            composition.parents(),
            &attrs_by_key,
            locale,
            chain_slice,
            &start,
        );
        *attributes = resolved;
        if origins.is_empty() {
            continue;
        }
        let mut object_sources = Map::new();
        for (name, origin) in origins {
            let Some(parent) = origin.as_str() else {
                continue;
            };
            let parent_attributes = attrs_by_key.get(parent).cloned().unwrap_or_default();
            let parent_type = types_by_key.get(parent).map(String::as_str).unwrap_or("");
            object_sources.insert(
                name,
                source_entry(parent, parent_type, &parent_attributes, locale, chain_slice),
            );
        }
        sources.insert(object_id.clone(), Value::Object(object_sources));
    }
    tracing::debug!(
        objects = objects.len(),
        walk_ms = walk_started.elapsed().as_millis() as u64,
        "inheritance batch resolved"
    );
    Ok(sources)
}

/// The attribute/type lookup of a batch: **the page's own objects**, overlayed by
/// the composition's ancestors.
///
/// The page's own values must be part of it: an object and its parent can appear in
/// the same page (a re-parented object is older than its new parent, and the list
/// orders by `created_at DESC`, so the parent can come first) — a walk that only
/// knew the separately loaded ancestors would then stop at the parent and report
/// nothing. Pure, so the rule is unit-tested.
pub(crate) fn lookup_maps(
    objects: &[(String, String, Map<String, Value>)],
    composition: &Composition,
) -> (HashMap<String, Map<String, Value>>, HashMap<String, String>) {
    let mut attrs_by_key: HashMap<String, Map<String, Value>> = objects
        .iter()
        .map(|(object_id, _, attributes)| (key_of(object_id).to_owned(), attributes.clone()))
        .collect();
    let mut types_by_key: HashMap<String, String> = objects
        .iter()
        .map(|(object_id, object_type, _)| (key_of(object_id).to_owned(), object_type.clone()))
        .collect();
    for (key, attributes) in composition.ancestors() {
        attrs_by_key.insert(key.clone(), attributes.clone());
    }
    for (key, object_type) in &composition.object_types {
        types_by_key.insert(key.clone(), object_type.clone());
    }
    (attrs_by_key, types_by_key)
}

/// Loads the single-parent composition map (`child key → parent key`) in one
/// query, for the batched in-memory inheritance resolution (cached version-bound
/// by [`MetaCache::composition_map`]).
pub(crate) async fn load_parent_map(db: &Db) -> Result<HashMap<String, String>, ApiError> {
    let sql =
        "SELECT in AS child, out AS parent FROM reference WHERE definition_id = 'rel_depends_on';";
    let mut response = db.inner().query(sql).await?.check()?;
    let rows: Vec<Value> = response.take(0)?;
    let mut map: HashMap<String, String> = HashMap::new();
    for row in rows {
        let child = row.get("child").and_then(Value::as_str).map(key_of);
        let parent = row.get("parent").and_then(Value::as_str).map(key_of);
        if let (Some(child), Some(parent)) = (child, parent) {
            map.entry(child.to_owned())
                .or_insert_with(|| parent.to_owned());
        }
    }
    Ok(map)
}

/// Loads the attributes and type of every ancestor of the given objects that is
/// not part of `known` (the batch itself). Walks the in-memory parent map and
/// reads the remaining keys in **one** query, so a partial batch resolves
/// inheritance with the same result as the per-object walk in
/// [`resolve_inheritance`].
///
/// Keys that are part of `known` are **not** fetched — the caller already holds
/// their attributes (see [`lookup_maps`]). An ancestor is skipped when an earlier
/// iteration already collected it, so the result does not depend on the order of
/// `known`.
async fn load_ancestor_attributes<'a>(
    db: &Db,
    parent_by_key: &HashMap<String, String>,
    known: impl IntoIterator<Item = &'a str>,
) -> Result<Vec<(String, Map<String, Value>, String)>, ApiError> {
    let mut wanted: Vec<String> = Vec::new();
    let mut seen: HashSet<String> = HashSet::new();
    for key in known {
        let key = key.to_owned();
        seen.insert(key.clone());
        let mut current = key;
        for _ in 0..MAX_DEPTH {
            let Some(parent) = parent_by_key.get(&current) else {
                break;
            };
            if !seen.insert(parent.clone()) {
                break;
            }
            wanted.push(parent.clone());
            current = parent.clone();
        }
    }
    if wanted.is_empty() {
        return Ok(Vec::new());
    }
    // Record list literal, not `array::map`: a page with many distinct parents can
    // reach thousands of keys (see `keys::record_list_literal` for the numbers).
    let sql = format!(
        "SELECT id, attributes, type FROM object WHERE id IN {};",
        crate::api::keys::record_list_literal("object", wanted.iter().map(String::as_str))
    );
    let mut response = db.inner().query(sql).await?.check()?;
    let rows: Vec<Value> = response.take(0)?;
    Ok(rows
        .into_iter()
        .filter_map(|row| {
            let key = row
                .get("id")
                .and_then(Value::as_str)
                .map(key_of)?
                .to_owned();
            let attributes = row
                .get("attributes")
                .and_then(Value::as_object)
                .cloned()
                .unwrap_or_default();
            let parent_type = row
                .get("type")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_owned();
            Some((key, attributes, parent_type))
        })
        .collect())
}

/// Resolves the given `missing` (inheritable) attribute names from an in-memory
/// parent map, starting at `start_key`. Returns `own` with the inherited values
/// filled (`NULL` where an ancestor declares the key but the value is empty) and,
/// for every **non-null** inherited value, the key of the ancestor it came from.
///
/// Mirrors [`resolve_inheritance`]'s semantics: walking one query per level is
/// replaced by the `parent_by_key`/`attrs_by_key` lookups, so a page (or a whole
/// dataset, in [`crate::completeness::compute_batch`]) needs no per-ancestor
/// round trips.
pub(crate) fn resolve_missing_in_memory(
    own: &Map<String, Value>,
    missing: &[String],
    parent_by_key: &HashMap<String, String>,
    attrs_by_key: &HashMap<String, Map<String, Value>>,
    locale: Option<&str>,
    fallback_chain: Option<&[String]>,
    start_key: &str,
) -> (Map<String, Value>, Map<String, Value>) {
    let mut resolved = own.clone();
    let mut origins: Map<String, Value> = Map::new();
    let mut current = start_key.to_owned();
    let mut todo = missing.to_vec();
    for _ in 0..MAX_DEPTH {
        let Some(parent) = parent_by_key.get(&current).cloned() else {
            break;
        };
        let Some(parent_attrs) = attrs_by_key.get(&parent) else {
            break;
        };
        let mut still_missing = Vec::new();
        for name in todo {
            match parent_attrs.get(&name) {
                Some(value) => {
                    let inherited = match (locale, fallback_chain, value.is_object()) {
                        (Some(locale), Some(chain), true) => {
                            crate::language::resolve_value_with_chain(locale, value, chain)
                                .unwrap_or(Value::Null)
                        }
                        _ => value.clone(),
                    };
                    if !inherited.is_null() {
                        origins.insert(name.clone(), Value::from(parent.clone()));
                    }
                    resolved.insert(name, inherited);
                }
                None => still_missing.push(name),
            }
        }
        todo = still_missing;
        if todo.is_empty() {
            break;
        }
        current = parent;
    }
    (resolved, origins)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn attrs(entries: &[(&str, Value)]) -> Map<String, Value> {
        entries
            .iter()
            .map(|(name, value)| ((*name).to_owned(), value.clone()))
            .collect()
    }

    #[test]
    fn lookup_maps_include_the_pages_own_objects() {
        // A page can hold a child *and* its parent (a re-parented object is older
        // than its new parent, and the list orders by `created_at DESC`). The
        // parent's attributes then come from the page, not from the separately
        // loaded ancestors — without them the walk would stop at the parent and
        // report nothing.
        let objects = vec![
            (
                "object:parent".to_owned(),
                "product".to_owned(),
                attrs(&[("sku", Value::from("PARENT-SKU"))]),
            ),
            ("object:child".to_owned(), "variant".to_owned(), Map::new()),
        ];
        let composition = Composition {
            parents: Arc::new(HashMap::from([("child".to_owned(), "parent".to_owned())])),
            ancestors: HashMap::new(),
            object_types: HashMap::new(),
        };
        let (attrs_by_key, types_by_key) = lookup_maps(&objects, &composition);
        assert!(attrs_by_key.contains_key("parent"));
        assert_eq!(
            types_by_key.get("parent").map(String::as_str),
            Some("product")
        );

        // And the walk finds it: the child inherits the parent's own value.
        let (resolved, origins) = resolve_missing_in_memory(
            &objects[1].2,
            &["sku".to_owned()],
            composition.parents(),
            &attrs_by_key,
            None,
            None,
            "child",
        );
        assert_eq!(
            resolved.get("sku").and_then(Value::as_str),
            Some("PARENT-SKU")
        );
        assert_eq!(origins.get("sku").and_then(Value::as_str), Some("parent"));
    }

    #[test]
    fn resolve_missing_inherits_from_parent() {
        let own = Map::new();
        let missing = vec!["sku".to_owned()];
        let mut parents = HashMap::new();
        parents.insert("child".to_owned(), "parent".to_owned());
        let mut attrs_by_key = HashMap::new();
        attrs_by_key.insert("parent".to_owned(), attrs(&[("sku", Value::from("SKU-1"))]));
        let (resolved, origins) =
            resolve_missing_in_memory(&own, &missing, &parents, &attrs_by_key, None, None, "child");
        assert_eq!(resolved.get("sku").and_then(Value::as_str), Some("SKU-1"));
        assert_eq!(origins.get("sku").and_then(Value::as_str), Some("parent"));
    }

    #[test]
    fn resolve_missing_stops_at_nearest_ancestor() {
        // Child misses `sku`; grandparent also has it, but the nearest parent wins.
        let own = Map::new();
        let missing = vec!["sku".to_owned()];
        let mut parents = HashMap::new();
        parents.insert("child".to_owned(), "parent".to_owned());
        parents.insert("parent".to_owned(), "grandparent".to_owned());
        let mut attrs_by_key = HashMap::new();
        attrs_by_key.insert(
            "parent".to_owned(),
            attrs(&[("sku", Value::from("nearest"))]),
        );
        attrs_by_key.insert(
            "grandparent".to_owned(),
            attrs(&[("sku", Value::from("farther"))]),
        );
        let (resolved, origins) =
            resolve_missing_in_memory(&own, &missing, &parents, &attrs_by_key, None, None, "child");
        assert_eq!(resolved.get("sku").and_then(Value::as_str), Some("nearest"));
        assert_eq!(origins.get("sku").and_then(Value::as_str), Some("parent"));
    }

    #[test]
    fn resolve_missing_null_value_is_not_set() {
        // A parent declares `sku` as null → it stops inheritance but does not set
        // the value (the further ancestor is never reached) and is not reported as
        // an inherited value either.
        let own = Map::new();
        let missing = vec!["sku".to_owned()];
        let mut parents = HashMap::new();
        parents.insert("child".to_owned(), "parent".to_owned());
        parents.insert("parent".to_owned(), "grandparent".to_owned());
        let mut attrs_by_key = HashMap::new();
        attrs_by_key.insert("parent".to_owned(), attrs(&[("sku", Value::Null)]));
        attrs_by_key.insert(
            "grandparent".to_owned(),
            attrs(&[("sku", Value::from("farther"))]),
        );
        let (resolved, origins) =
            resolve_missing_in_memory(&own, &missing, &parents, &attrs_by_key, None, None, "child");
        assert_eq!(resolved.get("sku"), Some(&Value::Null));
        assert!(origins.is_empty());
    }

    #[test]
    fn resolve_missing_multilingual_locale() {
        let own = Map::new();
        let missing = vec!["title".to_owned()];
        let mut parents = HashMap::new();
        parents.insert("child".to_owned(), "parent".to_owned());
        let mut attrs_by_key = HashMap::new();
        attrs_by_key.insert(
            "parent".to_owned(),
            attrs(&[(
                "title",
                json!({ "root": "Produkt", "de": "Produkt DE", "en": "Product" }),
            )]),
        );
        let chain = vec!["root".to_owned()];
        let (resolved, _) = resolve_missing_in_memory(
            &own,
            &missing,
            &parents,
            &attrs_by_key,
            Some("de"),
            Some(&chain),
            "child",
        );
        assert_eq!(
            resolved.get("title").and_then(Value::as_str),
            Some("Produkt DE")
        );
    }
}
