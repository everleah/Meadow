//! Class features (Stage B): attribute definitions attached to taxonomy nodes.
//!
//! A taxonomy node is a "class"; the features attached to it are the attributes
//! the class requires (`required`) or offers (`optional`). Features of an
//! ancestor class apply to its descendants, and an object that is assigned to a
//! class (any categorisation relation) gets that class's features — that is what
//! makes a classification standard (ETIM, BMEcat, eCl@ss) *checkable* instead of
//! just a hierarchy. See `plan/detail/kategorisierung.md`
//! ("Klassifizierungsstandards", Stufe B).
//!
//! - `GET /categories/{id}/attributes` — features of the class (own + inherited,
//!   the inherited ones with their source class)
//! - `POST /categories/{id}/attributes` — attach an attribute (admin)
//! - `PATCH /categories/{id}/attributes/{attribute}` — required/position (admin)
//! - `DELETE /categories/{id}/attributes/{attribute}` — detach (admin)
//! - `GET /objects/{id}/category-attributes` — the resolved features of an
//!   object, with the mandatory ones that are not set yet (`missing`)

use axum::Json;
use axum::extract::{Path, Query, State};
use axum::http::StatusCode;
use serde::Deserialize;
use serde_json::{Value, json};
use utoipa::{IntoParams, ToSchema};

use crate::api::object_types::is_taxonomy_type;
use crate::app::AppState;
use crate::audit::{self, AuditEntry};
use crate::auth::AuthUser;
use crate::class_features::{ancestor_chain_live, assigned_node_keys, key_of, resolved_features};
use crate::db::Db;
use crate::error::ApiError;

/// Query parameters for the object-side feature list.
#[derive(Debug, Deserialize, ToSchema, IntoParams)]
#[into_params(parameter_in = Query)]
pub struct ObjectFeaturesParams {
    /// Working language used to decide whether a multilingual feature is set.
    #[serde(default)]
    pub locale: Option<String>,
}

/// Payload for attaching a feature to a class.
#[derive(Debug, Deserialize, ToSchema)]
pub struct CreateCategoryAttribute {
    /// Attribute definition **name** (stable, portable).
    pub attribute: String,
    /// Mark the feature as mandatory for the class.
    #[serde(default)]
    pub required: bool,
    /// Sort position in the form/list.
    #[serde(default)]
    pub position: Option<i64>,
}

/// Payload for updating a feature of a class.
#[derive(Debug, Deserialize, ToSchema)]
pub struct UpdateCategoryAttribute {
    #[serde(default)]
    pub required: Option<bool>,
    #[serde(default)]
    pub position: Option<i64>,
}

/// Loads an object record by bare key (`None` when it does not exist or is
/// soft-deleted).
async fn fetch_object(db: &Db, key: &str) -> Result<Option<Value>, ApiError> {
    let mut response = db
        .inner()
        .query(
            "SELECT * FROM object WHERE id = type::record('object', $key) AND deleted_at IS NONE LIMIT 1;",
        )
        .bind(("key", key.to_owned()))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    Ok(rows.into_iter().next())
}

/// Loads one feature row of a class (or `None` when it is not attached there).
async fn fetch_row(db: &Db, node_key: &str, attribute: &str) -> Result<Option<Value>, ApiError> {
    let mut response = db
        .inner()
        .query(
            "SELECT * FROM category_attribute \
             WHERE category_id = type::record('object', $key) AND attribute = $attribute LIMIT 1;",
        )
        .bind(("key", node_key.to_owned()))
        .bind(("attribute", attribute.to_owned()))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    Ok(rows.into_iter().next())
}

/// Validates that `key` is an existing **taxonomy** node and that the attribute
/// definition exists (class features reference real attributes).
async fn validate_target(db: &Db, node_key: &str, attribute: &str) -> Result<Value, ApiError> {
    let object = fetch_object(db, node_key)
        .await?
        .ok_or_else(|| ApiError::not_found(format!("object `{node_key}` not found")))?;
    let object_type = object
        .get("type")
        .and_then(Value::as_str)
        .unwrap_or_default();
    if !is_taxonomy_type(db, object_type).await? {
        return Err(ApiError::bad_request(format!(
            "object type `{object_type}` is not a taxonomy type: class features require a taxonomy \
             node (see `object_type.taxonomy`)"
        )));
    }
    let mut response = db
        .inner()
        .query("SELECT name FROM attribute_definition WHERE name = $name LIMIT 1;")
        .bind(("name", attribute.to_owned()))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    if rows.is_empty() {
        return Err(ApiError::bad_request(format!(
            "unknown attribute `{attribute}`"
        )));
    }
    Ok(object)
}

/// Lists the features of a class: its own rows first, then the features inherited
/// from ancestor classes (marked `inherited` + `from`).
#[utoipa::path(
    get,
    path = "/categories/{id}/attributes",
    params(("id" = String, Path, description = "Taxonomy node id")),
    responses((status = 200, description = "Class features", body = Vec<Value>))
)]
pub async fn list_category_attributes(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<Json<Vec<Value>>, ApiError> {
    claims.require("attribute_definition.read")?;
    let node_key = key_of(crate::api::objects::parse_object_key(&id)?).to_owned();
    let chain = ancestor_chain_live(&state.db, &node_key).await?;
    let mut out: Vec<Value> = Vec::new();
    let mut seen: Vec<String> = Vec::new();
    for (index, chain_node) in chain.iter().enumerate() {
        for (name, required) in crate::class_features::node_features(&state.db, chain_node).await? {
            if seen.contains(&name) {
                continue;
            }
            seen.push(name.clone());
            out.push(json!({
                "attribute": name,
                "required": required,
                "inherited": index > 0,
                "from": if index > 0 { Value::String(chain_node.clone()) } else { Value::Null },
                "node": chain_node,
            }));
        }
    }
    // Own rows last so the class's own features are easy to spot in a plain list.
    out.sort_by_key(|row| {
        (
            row.get("inherited")
                .and_then(Value::as_bool)
                .unwrap_or(false),
            row.get("attribute")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_owned(),
        )
    });
    Ok(Json(out))
}

/// Attaches an attribute definition to a class as a feature.
#[utoipa::path(
    post,
    path = "/categories/{id}/attributes",
    request_body = CreateCategoryAttribute,
    responses(
        (status = 201, description = "Attached", body = Value),
        (status = 400, description = "Invalid payload", body = Value),
        (status = 409, description = "Already attached", body = Value)
    )
)]
pub async fn create_category_attribute(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
    Json(body): Json<CreateCategoryAttribute>,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    claims.require("attribute_definition.manage")?;
    let attribute = body.attribute.trim().to_owned();
    if attribute.is_empty() {
        return Err(ApiError::bad_request("`attribute` must not be empty"));
    }
    let node_key = key_of(crate::api::objects::parse_object_key(&id)?).to_owned();
    validate_target(&state.db, &node_key, &attribute).await?;
    if fetch_row(&state.db, &node_key, &attribute).await?.is_some() {
        return Err(ApiError::conflict(format!(
            "attribute `{attribute}` is already a feature of this class"
        )));
    }
    let position = body.position.unwrap_or(0);
    let mut response = state
        .db
        .inner()
        .query(
            "LET $c = type::record('object', $key); \
             CREATE category_attribute CONTENT { \
               category_id: $c, attribute: $attribute, required: $required, \
               position: $position, created_at: time::now(), updated_at: time::now(), \
               updated_by: $user \
             }; \
             SELECT * FROM category_attribute \
             WHERE category_id = $c AND attribute = $attribute LIMIT 1;",
        )
        .bind(("key", node_key.clone()))
        .bind(("attribute", attribute.clone()))
        .bind(("required", body.required))
        .bind(("position", position))
        .bind(("user", claims.sub.clone()))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(2)?;
    let record = rows
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::internal("created class feature was not returned"))?;
    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &claims.sub,
            action: "category_attribute_created",
            object_id: Some(&format!("object:{node_key}")),
            from: None,
            to: None,
            metadata: Some(json!({ "attribute": attribute, "required": body.required })),
        },
    )
    .await;
    // Mandatory class features take part in completeness.
    state.cache.invalidate_stats();
    Ok((StatusCode::CREATED, Json(record)))
}

/// Updates a feature of a class (`required`/`position`).
#[utoipa::path(
    patch,
    path = "/categories/{id}/attributes/{attribute}",
    request_body = UpdateCategoryAttribute,
    responses((status = 200, description = "Updated", body = Value))
)]
pub async fn update_category_attribute(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path((id, attribute)): Path<(String, String)>,
    Json(body): Json<UpdateCategoryAttribute>,
) -> Result<Json<Value>, ApiError> {
    claims.require("attribute_definition.manage")?;
    let node_key = key_of(crate::api::objects::parse_object_key(&id)?).to_owned();
    let attribute = attribute.trim().to_owned();
    let current = fetch_row(&state.db, &node_key, &attribute)
        .await?
        .ok_or_else(|| {
            ApiError::not_found(format!(
                "attribute `{attribute}` is not a feature of this class"
            ))
        })?;
    let mut content = serde_json::Map::new();
    if let Some(required) = body.required {
        content.insert("required".to_owned(), Value::Bool(required));
    }
    if let Some(position) = body.position {
        content.insert("position".to_owned(), Value::from(position));
    }
    if content.is_empty() {
        return Ok(Json(current));
    }
    let mut response = state
        .db
        .inner()
        .query(
            "LET $row = (SELECT VALUE id FROM category_attribute \
               WHERE category_id = type::record('object', $key) AND attribute = $attribute LIMIT 1); \
             UPDATE $row MERGE $content; \
             UPDATE $row SET updated_at = time::now(), updated_by = $user; \
             SELECT * FROM $row;",
        )
        .bind(("key", node_key.clone()))
        .bind(("attribute", attribute.clone()))
        .bind(("content", Value::Object(content)))
        .bind(("user", claims.sub.clone()))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(3)?;
    let record = rows
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::not_found(format!("feature `{attribute}` not found")))?;
    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &claims.sub,
            action: "category_attribute_updated",
            object_id: Some(&format!("object:{node_key}")),
            from: None,
            to: None,
            metadata: Some(json!({ "attribute": attribute })),
        },
    )
    .await;
    Ok(Json(record))
}

/// Detaches a feature from a class. Only the class's **own** row is removed —
/// features inherited from an ancestor stay in effect there.
#[utoipa::path(
    delete,
    path = "/categories/{id}/attributes/{attribute}",
    responses((status = 204, description = "Detached"))
)]
pub async fn delete_category_attribute(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path((id, attribute)): Path<(String, String)>,
) -> Result<StatusCode, ApiError> {
    claims.require("attribute_definition.manage")?;
    let node_key = key_of(crate::api::objects::parse_object_key(&id)?).to_owned();
    let attribute = attribute.trim().to_owned();
    if fetch_row(&state.db, &node_key, &attribute).await?.is_none() {
        return Err(ApiError::not_found(format!(
            "attribute `{attribute}` is not a feature of this class"
        )));
    }
    state
        .db
        .inner()
        .query(
            "DELETE category_attribute \
             WHERE category_id = type::record('object', $key) AND attribute = $attribute;",
        )
        .bind(("key", node_key.clone()))
        .bind(("attribute", attribute.clone()))
        .await?
        .check()?;
    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &claims.sub,
            action: "category_attribute_deleted",
            object_id: Some(&format!("object:{node_key}")),
            from: None,
            to: None,
            metadata: Some(json!({ "attribute": attribute })),
        },
    )
    .await;
    state.cache.invalidate_stats();
    Ok(StatusCode::NO_CONTENT)
}

/// The features an object inherits from its classes, plus the mandatory ones
/// that are not set yet.
///
/// `attributes` carries every feature: `name` (as the class names it — for a
/// classification standard that is the feature code `EF…`), `attribute` (the
/// attribute whose value carries it: a bound own attribute, else the feature
/// name), `required`, the contributing classes and `missing`.
/// `required`/`missing` list **attribute** names — the same semantics as the
/// completeness computation, so grid filter, form and dashboard agree.
///
/// `classes` lists the taxonomy nodes the object is **assigned to** (the doc
/// keys). A consumer that must name the classification block itself — the BMEcat
/// export writes one `PRODUCT_FEATURES` per class — needs exactly that
/// (`plan/detail/bmecat-export.md` §3).
#[utoipa::path(
    get,
    path = "/objects/{id}/category-attributes",
    params(ObjectFeaturesParams),
    responses(
        (status = 200, description = "Resolved class features", body = Value),
        (status = 404, description = "Object not found", body = Value)
    )
)]
pub async fn list_object_category_attributes(
    headers: axum::http::HeaderMap,
    State(state): State<AppState>,
    Path(id): Path<String>,
    Query(params): Query<ObjectFeaturesParams>,
) -> Result<Json<Value>, ApiError> {
    crate::auth::require_permission(&state, &headers, "attribute_definition.read")?;
    let key = crate::api::objects::parse_object_key(&id)?;
    let object = fetch_object(&state.db, key)
        .await?
        .ok_or_else(|| ApiError::not_found(format!("object `{id}` not found")))?;
    let object_type = object
        .get("type")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let features = resolved_features(&state.cache, &state.db, key).await?;
    // The classes the object is assigned to (the block id of an export).
    let relations = state.cache.category_relation_keys(&state.db).await?;
    let classes = assigned_node_keys(&state.db, key, &relations).await?;
    // `required`/`missing` are keyed by the **attribute** that carries a feature
    // (a bound own attribute, or the feature name itself): completeness counts
    // values, while each row still reports the feature (`name`, e.g. the ETIM
    // code `EF…`) that an export transmits.
    let mut required: Vec<String> = Vec::new();
    for feature in features.iter().filter(|feature| feature.required) {
        if !required.contains(&feature.attribute) {
            required.push(feature.attribute.clone());
        }
    }
    let own = crate::class_features::own_attributes(&object);
    let completeness = crate::completeness::compute_with_required(
        &state.cache,
        &state.db,
        &format!("object:{key}"),
        params.locale.as_deref(),
        &own,
        &required,
    )
    .await?;
    Ok(Json(json!({
        "object_id": format!("object:{key}"),
        "object_type": object_type,
        "classes": classes,
        "attributes": features
            .iter()
            .map(|feature| json!({
                "name": feature.name,
                "attribute": feature.attribute,
                "required": feature.required,
                "nodes": feature.nodes,
                "missing": completeness.missing.contains(&feature.attribute),
            }))
            .collect::<Vec<Value>>(),
        "required": required,
        "missing": completeness.missing,
    })))
}
