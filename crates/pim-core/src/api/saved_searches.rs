//! Saved searches and materialized collections (Phase 9).
//!
//! A `saved_search` is a generic object (`type: saved_search`) whose
//! `attributes.query` holds a structured search query (the Query DSL):
//! `{ filters, fulltext, references, sort, start, limit }`. It can be executed
//! at any time (`POST /saved-searches/{id}/execute`) and returns the matching
//! object ids.
//!
//! A `collection` is a generic object (`type: collection`) that materializes
//! the result of a saved search as `collection_member` relations (member `in`
//! -> collection `out`) carrying `rank`/`added_at` metadata.
//! `POST /collections/{id}/refresh` re-runs the saved search and atomically
//! replaces the members; a non-empty `refresh_interval` cron on the collection
//! auto-syncs a `collection_refresh` job definition so the refresh can run
//! scheduled via the existing scheduler/worker path.

use axum::Json;
use axum::extract::{Path, Query, State};
use axum::http::{HeaderMap, StatusCode};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, json};
use utoipa::{IntoParams, ToSchema};

use crate::app::AppState;
use crate::audit::{self, AuditEntry};
use crate::auth::{AuthUser, ROLE_EDITOR, api_key_or_role};
use crate::db::Db;
use crate::error::ApiError;

const SAVED_SEARCH_TYPE: &str = "saved_search";
const COLLECTION_TYPE: &str = "collection";
const COLLECTION_MEMBER_DEF: &str = "rel_collection_member";

/// A single filter condition in the query DSL.
#[derive(Debug, Clone, Deserialize, Serialize, ToSchema)]
pub struct QueryFilter {
    /// Field to filter on (`type`, `status`, `attributes.<name>`,
    /// `attributes.<name>.<lang>`).
    pub field: String,
    /// Operator: `eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `in`, `not_in`,
    /// `contains`, `not_contains`, `not_empty`, `is_null`, `starts_with`,
    /// `ends_with`.
    pub operator: String,
    /// Operand (array for `in`/`not_in`).
    #[serde(default)]
    pub value: Option<Value>,
}

/// Full-text restriction.
#[derive(Debug, Clone, Deserialize, Serialize, ToSchema)]
pub struct QueryFulltext {
    /// Fields to restrict the search to (best-effort; the full-text index is on
    /// the whole `attributes` object).
    #[serde(default)]
    pub fields: Option<Vec<String>>,
    /// Search terms.
    pub query: String,
    /// Language code (accepted, used by clients for field selection).
    #[serde(default)]
    pub language: Option<String>,
}

/// A reference (relation) restriction.
#[derive(Debug, Clone, Deserialize, Serialize, ToSchema)]
pub struct QueryReference {
    /// Relation type short name (e.g. `category_member` -> `rel_category_member`).
    #[serde(rename = "type")]
    pub reference_type: String,
    /// Container object id / key the reference points to.
    pub out: String,
    /// `incoming` (default: the object is the `in` member) or `outgoing`.
    #[serde(default)]
    pub direction: Option<String>,
}

/// A sort clause.
#[derive(Debug, Clone, Deserialize, Serialize, ToSchema)]
pub struct QuerySort {
    pub field: String,
    #[serde(default = "default_order")]
    pub order: String,
}

fn default_order() -> String {
    "asc".to_owned()
}

/// The saved-search query DSL.
#[derive(Debug, Default, Clone, Deserialize, Serialize, ToSchema)]
pub struct SavedSearchQuery {
    #[serde(default)]
    pub filters: Vec<QueryFilter>,
    #[serde(default)]
    pub fulltext: Option<QueryFulltext>,
    #[serde(default)]
    pub references: Vec<QueryReference>,
    #[serde(default)]
    pub sort: Vec<QuerySort>,
    #[serde(default)]
    pub start: u32,
    #[serde(default = "default_query_limit")]
    pub limit: u32,
}

fn default_query_limit() -> u32 {
    1000
}

/// Payload for creating a saved search.
#[derive(Debug, Deserialize, ToSchema)]
pub struct CreateSavedSearch {
    pub name: String,
    pub query: SavedSearchQuery,
    #[serde(default)]
    pub refresh_interval: Option<String>,
}

/// Payload for updating a saved search.
#[derive(Debug, Deserialize, ToSchema)]
pub struct UpdateSavedSearch {
    #[serde(default)]
    pub name: Option<String>,
    #[serde(default)]
    pub query: Option<SavedSearchQuery>,
    #[serde(default)]
    pub refresh_interval: Option<Option<String>>,
}

/// Payload for creating a collection.
#[derive(Debug, Deserialize, ToSchema)]
pub struct CreateCollection {
    pub name: String,
    /// Id of the `saved_search` object this collection is based on.
    pub saved_search_id: String,
    #[serde(default)]
    pub refresh_interval: Option<String>,
}

/// Payload for updating a collection.
#[derive(Debug, Deserialize, ToSchema)]
pub struct UpdateCollection {
    #[serde(default)]
    pub name: Option<String>,
    #[serde(default)]
    pub saved_search_id: Option<String>,
    #[serde(default)]
    pub refresh_interval: Option<Option<String>>,
}

/// Query parameters for listing saved searches / collections.
#[derive(Debug, Deserialize, ToSchema, IntoParams)]
#[into_params(parameter_in = Query)]
pub struct ListParams {
    #[serde(default = "default_list_limit")]
    pub limit: u32,
    #[serde(default)]
    pub start: u32,
}

fn default_list_limit() -> u32 {
    100
}

/// Splits a `table:key` id / checks the table. Returns the bare key.
fn parse_key<'a>(id: &'a str, table: &str) -> Result<&'a str, ApiError> {
    let (actual_table, key) = id
        .split_once(':')
        .ok_or_else(|| ApiError::bad_request(format!("invalid id `{id}`")))?;
    if actual_table != table {
        return Err(ApiError::bad_request(format!(
            "id `{id}` does not reference table `{table}`"
        )));
    }
    Ok(key)
}

/// Loads a full object by id (full record id or bare key).
async fn fetch_object(db: &Db, id: &str) -> Result<Option<Value>, ApiError> {
    let key = id.rsplit(':').next().unwrap_or(id);
    let mut response = db
        .inner()
        .query("LET $o = type::record('object', $key); SELECT * FROM $o;")
        .bind(("key", key.to_owned()))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(1)?;
    Ok(rows.into_iter().next())
}

/// Validates a filter/sort field reference against a strict whitelist.
fn sanitize_field(field: &str) -> Result<String, ApiError> {
    if field == "type" || field == "status" || field == "id" {
        return Ok(field.to_owned());
    }
    if let Some(rest) = field.strip_prefix("attributes.") {
        let segments: Vec<&str> = rest.split('.').collect();
        if segments.len() > 2 || segments.iter().any(|s| s.is_empty()) {
            return Err(ApiError::bad_request(format!(
                "unsupported filter field `{field}`"
            )));
        }
        for segment in &segments {
            if !segment
                .chars()
                .all(|c| c.is_alphanumeric() || c == '_' || c == '-' || c == '.')
            {
                return Err(ApiError::bad_request(format!(
                    "unsupported filter field `{field}`"
                )));
            }
        }
        return Ok(field.to_owned());
    }
    Err(ApiError::bad_request(format!(
        "unsupported filter field `{field}`"
    )))
}

/// Executes the query DSL and returns the ordered object ids.
async fn execute_query(db: &Db, query: &SavedSearchQuery) -> Result<Vec<String>, ApiError> {
    let mut conditions: Vec<String> = vec!["deleted_at IS NONE".to_owned()];
    let mut params: Vec<(String, Value)> = Vec::new();
    let mut order: Vec<String> = Vec::new();

    for (idx, filter) in query.filters.iter().enumerate() {
        let field = sanitize_field(&filter.field)?;
        let p = format!("f{idx}");
        let null_value = filter.value.as_ref().map(Value::is_null).unwrap_or(false);
        let clause = match filter.operator.as_str() {
            "eq" if null_value => format!("{field} IS NONE"),
            "ne" | "neq" if null_value => format!("{field} IS NOT NONE"),
            "eq" => format!("{field} = ${p}"),
            "ne" | "neq" => format!("{field} != ${p}"),
            "gt" => format!("{field} > ${p}"),
            "gte" | "ge" => format!("{field} >= ${p}"),
            "lt" => format!("{field} < ${p}"),
            "lte" | "le" => format!("{field} <= ${p}"),
            "in" => format!("{field} IN ${p}"),
            "not_in" => format!("{field} NOT IN ${p}"),
            "contains" => format!("{field} CONTAINS ${p}"),
            "not_contains" => format!("{field} NOT CONTAINS ${p}"),
            "starts_with" => {
                format!("string::startsWith(string::lower({field}), string::lower(${p}))")
            }
            "ends_with" => format!("string::endsWith(string::lower({field}), string::lower(${p}))"),
            "not_empty" => format!("{field} IS NOT NONE AND {field} != NULL"),
            "is_null" | "is_empty" => format!("{field} IS NONE OR {field} = NULL"),
            op => {
                return Err(ApiError::bad_request(format!(
                    "unsupported filter operator `{op}`"
                )));
            }
        };
        conditions.push(clause);
        if !matches!(
            filter.operator.as_str(),
            "not_empty" | "is_null" | "is_empty"
        ) && !null_value
        {
            params.push((p, filter.value.clone().unwrap_or(Value::Null)));
        }
    }

    if let Some(fulltext) = &query.fulltext {
        let text = fulltext.query.trim().to_owned();
        if !text.is_empty() {
            conditions.push("attributes @@ $q".to_owned());
            params.push(("q".to_owned(), Value::String(text)));
        }
    }

    for (idx, reference) in query.references.iter().enumerate() {
        let def = format!("rel_{}", reference.reference_type);
        let out_key = reference.out.rsplit(':').next().unwrap_or(&reference.out);
        let out_param = format!("ref_out{idx}");
        let incoming = reference
            .direction
            .as_deref()
            .map(|d| d != "outgoing")
            .unwrap_or(true);
        if incoming {
            conditions.push(format!(
                "id IN (SELECT VALUE in FROM reference WHERE definition_id = $ref_def{idx} AND out = type::record('object', ${out_param}))"
            ));
        } else {
            conditions.push(format!(
                "id IN (SELECT VALUE out FROM reference WHERE definition_id = $ref_def{idx} AND in = type::record('object', ${out_param}))"
            ));
        }
        params.push((format!("ref_def{idx}"), Value::String(def)));
        params.push((out_param, Value::String(out_key.to_owned())));
    }

    for sort in &query.sort {
        let field = sanitize_field(&sort.field)?;
        let direction = if sort.order.eq_ignore_ascii_case("desc") {
            "DESC"
        } else {
            "ASC"
        };
        order.push(format!("{field} {direction}"));
    }

    let limit = query.limit.clamp(1, 10_000) as i64;
    let start = query.start as i64;

    let where_clause = conditions.join(" AND ");
    let mut sql = format!("SELECT id FROM object WHERE {where_clause}");
    if !order.is_empty() {
        sql.push_str(" ORDER BY ");
        sql.push_str(&order.join(", "));
    }
    sql.push_str(" LIMIT $limit START $start");

    let mut request = db.inner().query(sql);
    for (key, value) in &params {
        request = request.bind((key.as_str(), value.clone()));
    }
    let mut response = request
        .bind(("limit", limit))
        .bind(("start", start))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    Ok(rows
        .iter()
        .filter_map(|row| row.get("id").and_then(Value::as_str).map(str::to_owned))
        .collect())
}

/// Lists all saved searches.
#[utoipa::path(
    get,
    path = "/saved-searches",
    params(ListParams),
    responses((status = 200, description = "Saved searches", body = Vec<Value>))
)]
pub async fn list_saved_searches(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Query(params): Query<ListParams>,
) -> Result<Json<Vec<Value>>, ApiError> {
    if !claims.is_editor() {
        return Err(ApiError::forbidden("requires editor role"));
    }
    let limit = params.limit.clamp(1, 1000) as i64;
    let start = params.start as i64;
    let mut response = state
        .db
        .inner()
        .query(
            "SELECT * FROM object WHERE type = $t AND deleted_at IS NONE ORDER BY created_at DESC LIMIT $limit START $start",
        )
        .bind(("t", SAVED_SEARCH_TYPE))
        .bind(("limit", limit))
        .bind(("start", start))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(0)?;
    Ok(Json(rows))
}

/// Gets a single saved search.
#[utoipa::path(
    get,
    path = "/saved-searches/{id}",
    responses((status = 200, description = "Saved search", body = Value))
)]
pub async fn get_saved_search(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<Json<Value>, ApiError> {
    if !claims.is_editor() {
        return Err(ApiError::forbidden("requires editor role"));
    }
    let key = parse_key(&id, "object")?;
    let object = fetch_object(&state.db, key)
        .await?
        .ok_or_else(|| ApiError::not_found(format!("saved search `{id}` not found")))?;
    if object.get("type").and_then(Value::as_str) != Some(SAVED_SEARCH_TYPE) {
        return Err(ApiError::bad_request("object is not a saved search"));
    }
    Ok(Json(object))
}

/// Creates a saved search.
#[utoipa::path(
    post,
    path = "/saved-searches",
    request_body = CreateSavedSearch,
    responses((status = 201, description = "Created", body = Value))
)]
pub async fn create_saved_search(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Json(body): Json<CreateSavedSearch>,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    if !claims.is_admin() {
        return Err(ApiError::forbidden("requires admin role"));
    }
    let name = body.name.trim().to_owned();
    if name.is_empty() {
        return Err(ApiError::bad_request("`name` must not be empty"));
    }
    let mut attributes = Map::new();
    attributes.insert("name".to_owned(), Value::String(name));
    attributes.insert(
        "query".to_owned(),
        serde_json::to_value(&body.query).unwrap_or_else(|_| json!({})),
    );
    if let Some(interval) = body
        .refresh_interval
        .as_deref()
        .map(str::trim)
        .filter(|i| !i.is_empty())
    {
        attributes.insert(
            "refresh_interval".to_owned(),
            Value::String(interval.to_owned()),
        );
    }

    let mut response = state
        .db
        .inner()
        .query(
            "LET $o = type::record('object', rand::ulid()); CREATE $o CONTENT $content; SELECT * FROM $o;",
        )
        .bind(("content", json!({
            "type": SAVED_SEARCH_TYPE,
            "attributes": Value::Object(attributes),
            "status": "active",
            "version": 1,
            "updated_by": claims.sub.clone(),
        })))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(2)?;
    let object = rows
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::internal("created saved search was not returned"))?;
    let object_id = object
        .get("id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &claims.sub,
            action: "saved_search_created",
            object_id: Some(&object_id),
            from: None,
            to: None,
            metadata: None,
        },
    )
    .await;
    Ok((StatusCode::CREATED, Json(object)))
}

/// Updates a saved search.
#[utoipa::path(
    patch,
    path = "/saved-searches/{id}",
    request_body = UpdateSavedSearch,
    responses((status = 200, description = "Updated", body = Value))
)]
pub async fn update_saved_search(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
    Json(body): Json<UpdateSavedSearch>,
) -> Result<Json<Value>, ApiError> {
    if !claims.is_admin() {
        return Err(ApiError::forbidden("requires admin role"));
    }
    let key = parse_key(&id, "object")?;
    let current = fetch_object(&state.db, key)
        .await?
        .ok_or_else(|| ApiError::not_found(format!("saved search `{id}` not found")))?;
    if current.get("type").and_then(Value::as_str) != Some(SAVED_SEARCH_TYPE) {
        return Err(ApiError::bad_request("object is not a saved search"));
    }
    let mut attributes = current
        .get("attributes")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    if let Some(name) = body.name {
        let name = name.trim().to_owned();
        if name.is_empty() {
            return Err(ApiError::bad_request("`name` must not be empty"));
        }
        attributes.insert("name".to_owned(), Value::String(name));
    }
    if let Some(query) = body.query {
        attributes.insert(
            "query".to_owned(),
            serde_json::to_value(&query).unwrap_or_else(|_| json!({})),
        );
    }
    if let Some(interval) = body.refresh_interval {
        match interval.as_deref().map(str::trim) {
            Some(i) if !i.is_empty() => {
                attributes.insert("refresh_interval".to_owned(), Value::String(i.to_owned()));
            }
            _ => {
                attributes.remove("refresh_interval");
            }
        }
    }
    let mut response = state
        .db
        .inner()
        .query(
            "LET $o = type::record('object', $key); UPDATE $o SET attributes = $attributes, updated_at = time::now(); SELECT * FROM $o;",
        )
        .bind(("key", key))
        .bind(("attributes", Value::Object(attributes)))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(2)?;
    let object = rows
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::not_found(format!("saved search `{id}` not found")))?;
    let object_id = object
        .get("id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &claims.sub,
            action: "saved_search_updated",
            object_id: Some(&object_id),
            from: None,
            to: None,
            metadata: None,
        },
    )
    .await;
    Ok(Json(object))
}

/// Deletes a saved search (soft delete); rejects when a collection references it.
#[utoipa::path(
    delete,
    path = "/saved-searches/{id}",
    responses((status = 204, description = "Deleted"))
)]
pub async fn delete_saved_search(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<StatusCode, ApiError> {
    if !claims.is_admin() {
        return Err(ApiError::forbidden("requires admin role"));
    }
    let key = parse_key(&id, "object")?;
    let current = fetch_object(&state.db, key)
        .await?
        .ok_or_else(|| ApiError::not_found(format!("saved search `{id}` not found")))?;
    if current.get("type").and_then(Value::as_str) != Some(SAVED_SEARCH_TYPE) {
        return Err(ApiError::bad_request("object is not a saved search"));
    }
    let object_id = current
        .get("id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    let referenced: Vec<Value> = state
        .db
        .inner()
        .query("SELECT id FROM object WHERE type = $t AND attributes.saved_search_id = $sid AND deleted_at IS NONE LIMIT 1")
        .bind(("t", COLLECTION_TYPE))
        .bind(("sid", object_id.clone()))
        .await?
        .check()?
        .take(0)?;
    if !referenced.is_empty() {
        return Err(ApiError::conflict(
            "saved search is referenced by a collection",
        ));
    }
    state
        .db
        .inner()
        .query("UPDATE object SET deleted_at = time::now(), deleted_by = $user WHERE id = type::record('object', $key);")
        .bind(("key", key))
        .bind(("user", claims.sub.clone()))
        .await?
        .check()?;
    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &claims.sub,
            action: "saved_search_deleted",
            object_id: Some(&object_id),
            from: None,
            to: None,
            metadata: None,
        },
    )
    .await;
    Ok(StatusCode::NO_CONTENT)
}

/// Executes a saved search and returns the matching object ids.
#[utoipa::path(
    post,
    path = "/saved-searches/{id}/execute",
    responses((status = 200, description = "Matching object ids", body = Value))
)]
pub async fn execute_saved_search(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(id): Path<String>,
) -> Result<Json<Value>, ApiError> {
    api_key_or_role(&headers, &state.config, ROLE_EDITOR)?;
    let key = parse_key(&id, "object")?;
    let object = fetch_object(&state.db, key)
        .await?
        .ok_or_else(|| ApiError::not_found(format!("saved search `{id}` not found")))?;
    if object.get("type").and_then(Value::as_str) != Some(SAVED_SEARCH_TYPE) {
        return Err(ApiError::bad_request("object is not a saved search"));
    }
    let query: SavedSearchQuery = object
        .get("attributes")
        .and_then(|a| a.get("query"))
        .map(|q| serde_json::from_value(q.clone()))
        .transpose()
        .map_err(|e| ApiError::bad_request(format!("invalid stored query: {e}")))?
        .unwrap_or_default();
    let ids = execute_query(&state.db, &query).await?;
    Ok(Json(json!({ "count": ids.len(), "ids": ids })))
}

// ---------------------------------------------------------------------------
// Collections
// ---------------------------------------------------------------------------

/// Adds `member_count` to each collection row.
async fn with_member_counts(db: &Db, rows: &mut [Value]) -> Result<(), ApiError> {
    for row in rows.iter_mut() {
        let object_id = row
            .get("id")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_owned();
        let count: Vec<Value> = db
            .inner()
            .query(
                "SELECT count() AS c FROM reference WHERE out = type::record('object', $key) AND definition_id = $def GROUP ALL;",
            )
            .bind(("key", object_id.rsplit(':').next().unwrap_or(&object_id)))
            .bind(("def", COLLECTION_MEMBER_DEF))
            .await?
            .check()?
            .take(0)?;
        let count = count
            .first()
            .and_then(|row| row.get("c"))
            .and_then(Value::as_u64)
            .unwrap_or(0);
        row["member_count"] = json!(count);
    }
    Ok(())
}

/// Lists all collections with their member counts.
#[utoipa::path(
    get,
    path = "/collections",
    params(ListParams),
    responses((status = 200, description = "Collections", body = Vec<Value>))
)]
pub async fn list_collections(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Query(params): Query<ListParams>,
) -> Result<Json<Vec<Value>>, ApiError> {
    if !claims.is_editor() {
        return Err(ApiError::forbidden("requires editor role"));
    }
    let limit = params.limit.clamp(1, 1000) as i64;
    let start = params.start as i64;
    let mut response = state
        .db
        .inner()
        .query(
            "SELECT * FROM object WHERE type = $t AND deleted_at IS NONE ORDER BY created_at DESC LIMIT $limit START $start",
        )
        .bind(("t", COLLECTION_TYPE))
        .bind(("limit", limit))
        .bind(("start", start))
        .await?
        .check()?;
    let mut rows: Vec<Value> = response.take(0)?;
    with_member_counts(&state.db, &mut rows).await?;
    Ok(Json(rows))
}

/// Gets a single collection with its member count.
#[utoipa::path(
    get,
    path = "/collections/{id}",
    responses((status = 200, description = "Collection", body = Value))
)]
pub async fn get_collection(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<Json<Value>, ApiError> {
    if !claims.is_editor() {
        return Err(ApiError::forbidden("requires editor role"));
    }
    let key = parse_key(&id, "object")?;
    let object = fetch_object(&state.db, key)
        .await?
        .ok_or_else(|| ApiError::not_found(format!("collection `{id}` not found")))?;
    if object.get("type").and_then(Value::as_str) != Some(COLLECTION_TYPE) {
        return Err(ApiError::bad_request("object is not a collection"));
    }
    let mut rows = vec![object];
    with_member_counts(&state.db, &mut rows).await?;
    Ok(Json(rows.remove(0)))
}

/// Synchronizes the `collection_refresh` job definition for a collection.
///
/// When `refresh_interval` is set, the definition is created/updated so the
/// scheduler fires a `collection_refresh` job; when cleared, the definition is
/// removed. The job definition is named `collection_refresh:<key>`.
async fn sync_collection_job(
    db: &Db,
    collection_id: &str,
    refresh_interval: Option<&str>,
) -> Result<(), ApiError> {
    let key = collection_id.rsplit(':').next().unwrap_or(collection_id);
    let job_name = format!("collection_refresh:{key}");
    let existing: Vec<Value> = db
        .inner()
        .query("SELECT id FROM job_definition WHERE name = $name LIMIT 1")
        .bind(("name", job_name.clone()))
        .await?
        .check()?
        .take(0)?;
    match refresh_interval.map(str::trim).filter(|i| !i.is_empty()) {
        Some(interval) => {
            let content = json!({
                "name": job_name,
                "type": "collection_refresh",
                "schedule": interval,
                "parameters": { "collection_id": collection_id },
                "enabled": true,
            });
            if existing.is_empty() {
                db.inner()
                    .query("CREATE job_definition CONTENT $content;")
                    .bind(("content", content))
                    .await?
                    .check()?;
            } else {
                db.inner()
                    .query("UPDATE job_definition SET schedule = $s, parameters = $p, enabled = true WHERE name = $name;")
                    .bind(("s", interval))
                    .bind(("p", json!({ "collection_id": collection_id })))
                    .bind(("name", job_name))
                    .await?
                    .check()?;
            }
        }
        None => {
            if !existing.is_empty() {
                db.inner()
                    .query("DELETE job_definition WHERE name = $name;")
                    .bind(("name", job_name))
                    .await?
                    .check()?;
            }
        }
    }
    Ok(())
}

async fn ensure_saved_search(db: &Db, saved_search_id: &str) -> Result<String, ApiError> {
    let object = fetch_object(db, saved_search_id).await?.ok_or_else(|| {
        ApiError::bad_request("`saved_search_id` does not reference a saved search")
    })?;
    if object.get("type").and_then(Value::as_str) != Some(SAVED_SEARCH_TYPE) {
        return Err(ApiError::bad_request(
            "`saved_search_id` does not reference a saved search",
        ));
    }
    Ok(object
        .get("id")
        .and_then(Value::as_str)
        .unwrap_or(saved_search_id)
        .to_owned())
}

/// Creates a collection.
#[utoipa::path(
    post,
    path = "/collections",
    request_body = CreateCollection,
    responses((status = 201, description = "Created", body = Value))
)]
pub async fn create_collection(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Json(body): Json<CreateCollection>,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    if !claims.is_admin() {
        return Err(ApiError::forbidden("requires admin role"));
    }
    let name = body.name.trim().to_owned();
    if name.is_empty() {
        return Err(ApiError::bad_request("`name` must not be empty"));
    }
    let saved_search_id = ensure_saved_search(&state.db, &body.saved_search_id).await?;
    let mut attributes = Map::new();
    attributes.insert("name".to_owned(), Value::String(name));
    attributes.insert("saved_search_id".to_owned(), Value::String(saved_search_id));
    if let Some(interval) = body
        .refresh_interval
        .as_deref()
        .map(str::trim)
        .filter(|i| !i.is_empty())
    {
        attributes.insert(
            "refresh_interval".to_owned(),
            Value::String(interval.to_owned()),
        );
    }

    let mut response = state
        .db
        .inner()
        .query(
            "LET $o = type::record('object', rand::ulid()); CREATE $o CONTENT $content; SELECT * FROM $o;",
        )
        .bind(("content", json!({
            "type": COLLECTION_TYPE,
            "attributes": Value::Object(attributes),
            "status": "active",
            "version": 1,
            "updated_by": claims.sub.clone(),
        })))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(2)?;
    let object = rows
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::internal("created collection was not returned"))?;
    let object_id = object
        .get("id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    let refresh = object
        .get("attributes")
        .and_then(|a| a.get("refresh_interval"))
        .and_then(Value::as_str)
        .map(str::to_owned);
    sync_collection_job(&state.db, &object_id, refresh.as_deref()).await?;
    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &claims.sub,
            action: "collection_created",
            object_id: Some(&object_id),
            from: None,
            to: None,
            metadata: None,
        },
    )
    .await;
    Ok((StatusCode::CREATED, Json(object)))
}

/// Updates a collection.
#[utoipa::path(
    patch,
    path = "/collections/{id}",
    request_body = UpdateCollection,
    responses((status = 200, description = "Updated", body = Value))
)]
pub async fn update_collection(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
    Json(body): Json<UpdateCollection>,
) -> Result<Json<Value>, ApiError> {
    if !claims.is_admin() {
        return Err(ApiError::forbidden("requires admin role"));
    }
    let key = parse_key(&id, "object")?;
    let current = fetch_object(&state.db, key)
        .await?
        .ok_or_else(|| ApiError::not_found(format!("collection `{id}` not found")))?;
    if current.get("type").and_then(Value::as_str) != Some(COLLECTION_TYPE) {
        return Err(ApiError::bad_request("object is not a collection"));
    }
    let mut attributes = current
        .get("attributes")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    if let Some(name) = body.name {
        let name = name.trim().to_owned();
        if name.is_empty() {
            return Err(ApiError::bad_request("`name` must not be empty"));
        }
        attributes.insert("name".to_owned(), Value::String(name));
    }
    if let Some(saved_search_id) = body.saved_search_id {
        let resolved = ensure_saved_search(&state.db, &saved_search_id).await?;
        attributes.insert("saved_search_id".to_owned(), Value::String(resolved));
    }
    if let Some(interval) = body.refresh_interval {
        match interval.as_deref().map(str::trim) {
            Some(i) if !i.is_empty() => {
                attributes.insert("refresh_interval".to_owned(), Value::String(i.to_owned()));
            }
            _ => {
                attributes.remove("refresh_interval");
            }
        }
    }
    let mut response = state
        .db
        .inner()
        .query(
            "LET $o = type::record('object', $key); UPDATE $o SET attributes = $attributes, updated_at = time::now(); SELECT * FROM $o;",
        )
        .bind(("key", key))
        .bind(("attributes", Value::Object(attributes)))
        .await?
        .check()?;
    let rows: Vec<Value> = response.take(2)?;
    let object = rows
        .into_iter()
        .next()
        .ok_or_else(|| ApiError::not_found(format!("collection `{id}` not found")))?;
    let object_id = object
        .get("id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    let refresh = object
        .get("attributes")
        .and_then(|a| a.get("refresh_interval"))
        .and_then(Value::as_str)
        .map(str::to_owned);
    sync_collection_job(&state.db, &object_id, refresh.as_deref()).await?;
    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &claims.sub,
            action: "collection_updated",
            object_id: Some(&object_id),
            from: None,
            to: None,
            metadata: None,
        },
    )
    .await;
    Ok(Json(object))
}

/// Deletes a collection (soft delete) and removes its members + refresh job.
#[utoipa::path(
    delete,
    path = "/collections/{id}",
    responses((status = 204, description = "Deleted"))
)]
pub async fn delete_collection(
    AuthUser(claims): AuthUser,
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<StatusCode, ApiError> {
    if !claims.is_admin() {
        return Err(ApiError::forbidden("requires admin role"));
    }
    let key = parse_key(&id, "object")?;
    let current = fetch_object(&state.db, key)
        .await?
        .ok_or_else(|| ApiError::not_found(format!("collection `{id}` not found")))?;
    if current.get("type").and_then(Value::as_str) != Some(COLLECTION_TYPE) {
        return Err(ApiError::bad_request("object is not a collection"));
    }
    let object_id = current
        .get("id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    sync_collection_job(&state.db, &object_id, None).await?;
    state
        .db
        .inner()
        .query(
            "BEGIN TRANSACTION; DELETE reference WHERE out = type::record('object', $key) AND definition_id = $def; UPDATE object SET deleted_at = time::now(), deleted_by = $user WHERE id = type::record('object', $key); COMMIT TRANSACTION;",
        )
        .bind(("key", key))
        .bind(("def", COLLECTION_MEMBER_DEF))
        .bind(("user", claims.sub.clone()))
        .await?
        .check()?;
    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &claims.sub,
            action: "collection_deleted",
            object_id: Some(&object_id),
            from: None,
            to: None,
            metadata: None,
        },
    )
    .await;
    Ok(StatusCode::NO_CONTENT)
}

/// Re-runs the saved search and replaces the collection members atomically.
#[utoipa::path(
    post,
    path = "/collections/{id}/refresh",
    responses((status = 200, description = "Refresh summary", body = Value))
)]
pub async fn refresh_collection(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(id): Path<String>,
) -> Result<Json<Value>, ApiError> {
    let _user_id = crate::auth::api_key_or_role(&headers, &state.config, crate::auth::ROLE_EDITOR)?;
    let key = parse_key(&id, "object")?;
    let collection = fetch_object(&state.db, key)
        .await?
        .ok_or_else(|| ApiError::not_found(format!("collection `{id}` not found")))?;
    if collection.get("type").and_then(Value::as_str) != Some(COLLECTION_TYPE) {
        return Err(ApiError::bad_request("object is not a collection"));
    }
    let collection_id = collection
        .get("id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    let saved_search_id = collection
        .get("attributes")
        .and_then(|a| a.get("saved_search_id"))
        .and_then(Value::as_str)
        .map(str::to_owned)
        .ok_or_else(|| ApiError::bad_request("collection has no `saved_search_id`"))?;
    let saved_search = fetch_object(&state.db, &saved_search_id)
        .await?
        .ok_or_else(|| ApiError::bad_request("referenced saved search not found"))?;
    let query: SavedSearchQuery = saved_search
        .get("attributes")
        .and_then(|a| a.get("query"))
        .map(|q| serde_json::from_value(q.clone()))
        .transpose()
        .map_err(|e| ApiError::bad_request(format!("invalid stored query: {e}")))?
        .unwrap_or_default();
    let ids = execute_query(&state.db, &query).await?;

    let member_keys: Vec<String> = ids
        .iter()
        .map(|id| id.rsplit(':').next().unwrap_or(id).to_owned())
        .collect();
    let members: Vec<Value> = member_keys
        .iter()
        .enumerate()
        .map(|(i, key)| json!({ "key": key, "rank": i + 1 }))
        .collect();

    let mut sql = String::from(
        "BEGIN TRANSACTION; LET $c = type::record('object', $key); DELETE reference WHERE out = $c AND definition_id = $def;",
    );
    if !members.is_empty() {
        sql.push_str(
            " FOR $item IN $members { LET $m = type::record('object', $item.key); RELATE $m->reference->$c SET definition_id = $def, type = 'collection_member', attributes = { rank: $item.rank, added_at: time::now() }; };",
        );
    }
    sql.push_str(
        " UPDATE $c SET attributes.last_refreshed_at = time::now(), attributes.last_result_count = $count, updated_at = time::now(); COMMIT TRANSACTION;",
    );

    let mut query = state
        .db
        .inner()
        .query(sql)
        .bind(("key", key))
        .bind(("def", COLLECTION_MEMBER_DEF))
        .bind(("count", ids.len() as i64));
    if !members.is_empty() {
        query = query.bind(("members", members));
    }
    query.await?.check()?;

    audit::record_best_effort(
        &state.db,
        AuditEntry {
            user_id: &_user_id,
            action: "collection_refreshed",
            object_id: Some(&collection_id),
            from: None,
            to: None,
            metadata: Some(json!({ "count": ids.len() })),
        },
    )
    .await;

    Ok(Json(
        json!({ "count": ids.len(), "collection_id": collection_id }),
    ))
}

/// Lists the members of a collection (with `rank` and the resolved object).
#[utoipa::path(
    get,
    path = "/collections/{id}/members",
    params(ListParams),
    responses((status = 200, description = "Members", body = Vec<Value>))
)]
pub async fn list_collection_members(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(id): Path<String>,
    Query(params): Query<ListParams>,
) -> Result<Json<Vec<Value>>, ApiError> {
    api_key_or_role(&headers, &state.config, ROLE_EDITOR)?;
    let key = parse_key(&id, "object")?;
    let collection = fetch_object(&state.db, key)
        .await?
        .ok_or_else(|| ApiError::not_found(format!("collection `{id}` not found")))?;
    if collection.get("type").and_then(Value::as_str) != Some(COLLECTION_TYPE) {
        return Err(ApiError::bad_request("object is not a collection"));
    }
    let limit = params.limit.clamp(1, 1000) as i64;
    let start = params.start as i64;
    let mut response = state
        .db
        .inner()
        .query(
            "SELECT * FROM reference WHERE out = type::record('object', $key) AND definition_id = $def ORDER BY attributes.rank ASC LIMIT $limit START $start;",
        )
        .bind(("key", key))
        .bind(("def", COLLECTION_MEMBER_DEF))
        .bind(("limit", limit))
        .bind(("start", start))
        .await?
        .check()?;
    let edges: Vec<Value> = response.take(0)?;
    let mut members = Vec::new();
    for edge in edges {
        let member_id = edge
            .get("in")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_owned();
        let Some(member) = fetch_object(&state.db, &member_id).await? else {
            continue;
        };
        let obj_type = member
            .get("type")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_owned();
        let label = member
            .get("attributes")
            .and_then(|a| a.get("name"))
            .and_then(|n| {
                n.as_str().map(str::to_owned).or_else(|| {
                    n.as_object()
                        .and_then(|l| l.get("en").and_then(Value::as_str).map(str::to_owned))
                })
            })
            .unwrap_or_else(|| obj_type.clone());
        members.push(json!({
            "id": member_id,
            "rank": edge.get("attributes").and_then(|a| a.get("rank")).and_then(Value::as_u64).unwrap_or(0),
            "added_at": edge.get("attributes").and_then(|a| a.get("added_at")).and_then(Value::as_str).unwrap_or(""),
            "type": obj_type,
            "label": label,
            "status": member.get("status").and_then(Value::as_str).unwrap_or(""),
        }));
    }
    Ok(Json(members))
}
