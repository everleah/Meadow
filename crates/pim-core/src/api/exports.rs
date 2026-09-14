//! UI-guided data export endpoints (Phase 9).
//!
//! `POST /exports` accepts a **structured** export request (source, fields,
//! format, locales, options) and queues an `outbound_export` job; the worker
//! resolves the source, builds tabular columns/rows (or a MEF document for
//! JSON) and stores the serializer-ready output in `job_run.result`
//! (`csv` text / `xlsx_base64` / `document`). `GET /exports/{run_id}/file`
//! reads that result and returns it as a downloadable attachment.
//!
//! `format: "bmecat"` is the BMEcat/ETIM catalogue export: the worker builds one
//! **XML** document (`result.xml`, plus the report) keyed by the `bmecat` profile
//! block of the request and validates it against the XSD when the schema is
//! available (`plan/detail/bmecat-export.md` §8 V1).

use axum::Json;
use axum::body::Body;
use axum::extract::{Path, Query, State};
use axum::http::{HeaderMap, HeaderValue, StatusCode, header};
use axum::response::Response;
use base64::Engine;
use serde::Deserialize;
use serde_json::{Value, json};
use utoipa::{IntoParams, ToSchema};

use crate::api::jobs::queue_run;
use crate::app::AppState;
use crate::auth::{ROLE_EDITOR, api_key_or_role};
use crate::error::ApiError;

/// The object source for an export.
#[derive(Debug, Clone, Deserialize, serde::Serialize, ToSchema)]
pub struct ExportSource {
    /// `collection` | `type` | `category` | `saved_search` | `selection` | `channel`.
    pub kind: String,
    /// Collection or saved-search id (for `collection`/`saved_search`).
    #[serde(default)]
    pub id: Option<String>,
    /// Object type (for `type`).
    #[serde(default, rename = "type")]
    pub object_type: Option<String>,
    /// Category id (for `category`).
    #[serde(default)]
    pub category: Option<String>,
    /// Object ids (for `selection`).
    #[serde(default)]
    pub object_ids: Option<Vec<String>>,
    /// Channel code (for `channel` = "released for that channel"); empty in the
    /// request falls back to the `channel` of the `json_export` profile.
    #[serde(default)]
    pub channel: Option<String>,
    /// State a release must have for `channel` (default `published`).
    #[serde(default)]
    pub release_status: Option<String>,
}

/// Serialization options.
#[derive(Debug, Clone, Deserialize, serde::Serialize, ToSchema)]
pub struct ExportOptions {
    #[serde(default = "default_true")]
    pub header: bool,
    #[serde(default = "default_separator")]
    pub separator: String,
    #[serde(default)]
    pub include_system: bool,
    /// E-mail recipients; when set (and SMTP is configured) the worker sends
    /// the finished export as an attachment.
    #[serde(default)]
    pub email_to: Option<Vec<String>>,
}

impl Default for ExportOptions {
    /// Mirrors the serde defaults (used when a request omits `options`).
    fn default() -> Self {
        Self {
            header: true,
            separator: default_separator(),
            include_system: false,
            email_to: None,
        }
    }
}

/// Profile metadata of a **BMEcat** export (`format: "bmecat"`).
///
/// The document header is a business statement (who delivers to whom, which
/// catalogue in which language), so it is passed with the request. The values are
/// written verbatim — the core does not invent a supplier name. `transaction`
/// picks the ETIM transaction: `new_productdata` (default, price-less) or
/// `new_catalog` (catalogue delivery with the mandatory order details, prices and
/// UDX block — `plan/detail/bmecat-export.md` §11.11).
#[derive(Debug, Clone, Default, Deserialize, serde::Serialize, ToSchema)]
pub struct BmecatProfile {
    /// `CATALOG_ID` (e.g. `MEADOW-2026`).
    #[serde(default)]
    pub catalog_id: Option<String>,
    /// `CATALOG_NAME`.
    #[serde(default)]
    pub catalog_name: Option<String>,
    /// `CATALOG_VERSION` (default `1.0`).
    #[serde(default)]
    pub catalog_version: Option<String>,
    /// Catalogue language (`eng`/`ger`/…, BMEcat codes); the export is one file
    /// per language (§10 Punkt 3).
    #[serde(default)]
    pub language: Option<String>,
    /// `CURRENCY` (ISO-4217, default `EUR`).
    #[serde(default)]
    pub currency: Option<String>,
    /// `SUPPLIER_NAME` (mandatory in the ETIM profile).
    #[serde(default)]
    pub supplier: Option<String>,
    /// `BUYER_NAME` (the ETIM profile requires a `BUYER` block, and it precedes
    /// `SUPPLIER`).
    #[serde(default)]
    pub buyer: Option<String>,
    /// Attribute whose value becomes `SUPPLIER_PID` (default `code`, then `sku`;
    /// the schema limits the value to 32 characters).
    #[serde(default)]
    pub pid_attribute: Option<String>,
    /// `REFERENCE_FEATURE_SYSTEM_NAME` version part **fallback** (default `10.0`).
    ///
    /// The version usually comes from the **installed** classification standard of
    /// the class nodes (`classification_standard`, `GET /classification-standards`);
    /// this field only applies to a class whose node type has no registered
    /// standard (see `plan/detail/kategorisierung.md`).
    #[serde(default)]
    pub etim_version: Option<String>,
    /// `new_productdata` (default, no prices) or `new_catalog` (prices, order
    /// details and a UDX block per product — all three are **mandatory** in that
    /// transaction, so a product without a price is skipped and reported).
    #[serde(default)]
    pub transaction: Option<String>,
    /// `ORDER_UNIT` — a **BMEcat unit code** (UN/ECE Rec 20, e.g. `C62` = piece),
    /// not a Meadow unit symbol; only used by `new_catalog` (default `C62`).
    #[serde(default)]
    pub order_unit: Option<String>,
    /// `CONTENT_UNIT` (BMEcat unit code, default: `order_unit`).
    #[serde(default)]
    pub content_unit: Option<String>,
    /// Attribute whose value becomes `PRICE_AMOUNT` (required by `new_catalog`;
    /// a quantified number contributes its number).
    #[serde(default)]
    pub price_attribute: Option<String>,
    /// `price_type` of `PRODUCT_PRICE`: `net_list` (default) | `net_customer` | `nrp`.
    #[serde(default)]
    pub price_type: Option<String>,
    /// BMEcat version: `2005` (default, the ETIM profile) or `1.2` (legacy
    /// delivery, `T_NEW_CATALOG` with `ARTICLE` — verified against the 1.2 DTD/XSD).
    /// BMEcat 1.2 has no price-less transaction, so `price_attribute` is required.
    #[serde(default)]
    pub version: Option<String>,
    /// Schema variant for **1.2**: `dtd` (default, Rev. A) or `xsd`. A 1.2 document
    /// satisfies only one of them (the namespace is `#FIXED` in both artefacts).
    #[serde(default)]
    pub schema_variant: Option<String>,
}

/// Structured export request for `POST /exports`.
#[derive(Debug, Deserialize, serde::Serialize, ToSchema)]
pub struct ExportRequest {
    pub source: ExportSource,
    /// Field/column names; empty => the `json_export` connector profile, else
    /// the worker derives them from the objects.
    #[serde(default)]
    pub fields: Option<Vec<String>>,
    /// `csv` | `xlsx` | `json`; unset => the `json_export` connector profile.
    #[serde(default)]
    pub format: Option<String>,
    /// Locales for multilingual columns (`<attr>_<code>`); empty => the
    /// `json_export` connector profile, else the default locale.
    #[serde(default)]
    pub locales: Option<Vec<String>>,
    #[serde(default)]
    pub options: Option<ExportOptions>,
    /// Profile of a BMEcat export; only used with `format: "bmecat"`.
    #[serde(default)]
    pub bmecat: Option<BmecatProfile>,
}

/// Query params for `GET /exports/{run_id}/file`.
#[derive(Debug, Deserialize, IntoParams, ToSchema)]
#[into_params(parameter_in = Query)]
pub struct ExportFileQuery {
    /// Override the stored format: `csv` | `xlsx` | `json`.
    #[serde(default)]
    pub format: Option<String>,
}

fn default_format() -> String {
    "json".to_owned()
}

fn default_true() -> bool {
    true
}

fn default_separator() -> String {
    ",".to_owned()
}

fn strip_prefix<'a>(id: &'a str, table: &str) -> &'a str {
    let prefix = format!("{table}:");
    id.strip_prefix(&prefix).unwrap_or(id)
}

/// Whether the requested format's inline payload is present in the result.
fn inline_present(result: &Value, format: &str) -> bool {
    match format {
        "csv" => result.get("csv").and_then(Value::as_str).is_some(),
        "xlsx" => result.get("xlsx_base64").and_then(Value::as_str).is_some(),
        "json" => result.get("document").is_some(),
        "bmecat" => result.get("xml").and_then(Value::as_str).is_some(),
        _ => false,
    }
}

/// Creates and queues an `outbound_export` job from a structured request.
#[utoipa::path(
    post,
    path = "/exports",
    request_body = ExportRequest,
    responses(
        (status = 202, description = "Export job queued", body = Value),
        (status = 400, description = "Invalid request", body = Value),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Forbidden", body = Value)
    )
)]
pub async fn create_export(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(req): Json<ExportRequest>,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    let user_id = api_key_or_role(&headers, &state.config, ROLE_EDITOR)?;

    let mut source = req.source.clone();
    let kind = source.kind.as_str();
    if !matches!(
        kind,
        "collection" | "type" | "category" | "saved_search" | "selection" | "channel"
    ) {
        return Err(ApiError::bad_request(format!(
            "unsupported source kind `{kind}`"
        )));
    }

    // Defaults of the `json_export` connector profile (empty without one):
    // explicit request fields win, the profile fills the rest.
    let defaults = crate::api::connectors::type_defaults(
        &state.db,
        crate::api::connectors::CONNECTOR_TYPE_JSON_EXPORT,
    )
    .await?;
    let format = req
        .format
        .clone()
        .filter(|value| !value.trim().is_empty())
        .or_else(|| crate::api::connectors::default_string(&defaults, "format"))
        .unwrap_or_else(default_format);
    if !matches!(format.as_str(), "csv" | "xlsx" | "json" | "bmecat") {
        return Err(ApiError::bad_request(format!(
            "unsupported format `{format}` (expected csv | xlsx | json | bmecat)"
        )));
    }
    let fields = req
        .fields
        .clone()
        .filter(|values| !values.is_empty())
        .or_else(|| crate::api::connectors::default_string_list(&defaults, "fields"));
    let locales = req
        .locales
        .clone()
        .filter(|values| !values.is_empty())
        .or_else(|| crate::api::connectors::default_string_list(&defaults, "locales"));
    let mut options = req.options.clone().unwrap_or_default();
    if options.email_to.is_none() {
        options.email_to =
            crate::api::connectors::default_string_list(&defaults, "options.email_to");
    }
    // An export profile can be bound to a channel: a `channel` source without a
    // code takes the profile's channel.
    if kind == "channel"
        && source
            .channel
            .as_deref()
            .map(str::trim)
            .unwrap_or_default()
            .is_empty()
    {
        source.channel = crate::api::connectors::default_string(&defaults, "channel")
            .filter(|value| !value.trim().is_empty());
    }
    match kind {
        "collection" | "saved_search" if source.id.is_none() => {
            return Err(ApiError::bad_request(format!(
                "source kind `{kind}` requires an `id`"
            )));
        }
        "type" if source.object_type.is_none() => {
            return Err(ApiError::bad_request(
                "source kind `type` requires a `type`",
            ));
        }
        "category" if source.category.is_none() => {
            return Err(ApiError::bad_request(
                "source kind `category` requires a `category`",
            ));
        }
        "channel" if source.channel.is_none() => {
            return Err(ApiError::bad_request(
                "source kind `channel` requires a `channel`",
            ));
        }
        "selection" if source.object_ids.as_deref().unwrap_or_default().is_empty() => {
            return Err(ApiError::bad_request(
                "source kind `selection` requires `object_ids`",
            ));
        }
        _ => {}
    }

    // A channel source is validated against the catalog: the filter is by code.
    if kind == "channel"
        && let Some(channel) = source.channel.clone()
    {
        crate::api::channels::find_channel(&state.db, &channel)
            .await?
            .ok_or_else(|| ApiError::not_found(format!("channel `{channel}` not found")))?;
        source.channel = Some(crate::api::channels::channel_code(&channel));
    }

    let parameters = json!({
        "source": source,
        "fields": fields,
        "format": format,
        "locales": locales,
        "options": options,
        "bmecat": req.bmecat,
    });
    let (_, run) = queue_run(
        &state.db,
        &state.config,
        None,
        "outbound_export",
        parameters,
        "manual",
        &user_id,
    )
    .await?;
    let run_id = run
        .get("id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    Ok((StatusCode::ACCEPTED, Json(json!({ "job_run_id": run_id }))))
}

/// Loads a single job run record.
async fn fetch_run(db: &crate::db::Db, key: &str) -> Result<Option<Value>, ApiError> {
    let sql = "
        LET $r = type::record('job_run', $key);
        SELECT * FROM $r;
    ";
    let mut response = db.inner().query(sql).bind(("key", key)).await?.check()?;
    let rows: Vec<Value> = response.take(1)?;
    Ok(rows.into_iter().next())
}

/// Renders the job-run export result as a downloadable file.
#[utoipa::path(
    get,
    path = "/exports/{run_id}/file",
    params(ExportFileQuery),
    responses(
        (status = 200, description = "Export file"),
        (status = 302, description = "Redirect to the object-storage download URL"),
        (status = 400, description = "No result / wrong format"),
        (status = 404, description = "Not found")
    )
)]
pub async fn get_export_file(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(run_id): Path<String>,
    Query(params): Query<ExportFileQuery>,
) -> Result<Response, ApiError> {
    api_key_or_role(&headers, &state.config, ROLE_EDITOR)?;
    let key = strip_prefix(&run_id, "job_run").to_owned();
    let Some(run) = fetch_run(&state.db, &key).await? else {
        return Err(ApiError::not_found(format!("job run `{run_id}` not found")));
    };
    if run.get("status").and_then(Value::as_str) != Some("success") {
        return Err(ApiError::bad_request("export is not finished yet"));
    }
    let Some(result) = run.get("result") else {
        return Err(ApiError::bad_request("job run has no export result"));
    };
    let stored_format = result
        .get("format")
        .and_then(Value::as_str)
        .unwrap_or("json");
    let format = params.format.as_deref().unwrap_or(stored_format);
    if !matches!(format, "csv" | "xlsx" | "json" | "bmecat") {
        return Err(ApiError::bad_request(format!(
            "unsupported format `{format}`"
        )));
    }

    // Large results are archived in object storage and their inline copy is
    // dropped: redirect the client to the presigned download URL instead.
    if !inline_present(result, format) {
        if let Some(url) = result
            .get("archive")
            .and_then(|archive| archive.get("url"))
            .and_then(Value::as_str)
        {
            return Response::builder()
                .status(StatusCode::FOUND)
                .header(header::LOCATION, url)
                .body(Body::empty())
                .map_err(|e| ApiError::internal(format!("build redirect: {e}")));
        }
        return Err(ApiError::bad_request(format!("no {format} result")));
    }

    let (content_type, filename, body): (&str, String, Vec<u8>) = match format {
        "csv" => {
            let csv = result
                .get("csv")
                .and_then(Value::as_str)
                .ok_or_else(|| ApiError::bad_request("no csv result"))?;
            (
                "text/csv; charset=utf-8",
                "meadow-export.csv".to_owned(),
                csv.as_bytes().to_vec(),
            )
        }
        "xlsx" => {
            let b64 = result
                .get("xlsx_base64")
                .and_then(Value::as_str)
                .ok_or_else(|| ApiError::bad_request("no xlsx result"))?;
            let decoded = base64::engine::general_purpose::STANDARD
                .decode(b64)
                .map_err(|_| ApiError::bad_request("corrupt xlsx payload"))?;
            (
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "meadow-export.xlsx".to_owned(),
                decoded,
            )
        }
        "json" => {
            let document = result.get("document").cloned().unwrap_or(Value::Null);
            (
                "application/json",
                "meadow-export.json".to_owned(),
                serde_json::to_vec(&document)
                    .map_err(|e| ApiError::internal(format!("serialize json: {e}")))?,
            )
        }
        "bmecat" => {
            let xml = result
                .get("xml")
                .and_then(Value::as_str)
                .ok_or_else(|| ApiError::bad_request("no bmecat result"))?;
            (
                "application/xml; charset=utf-8",
                "meadow-export-bmecat.xml".to_owned(),
                xml.as_bytes().to_vec(),
            )
        }
        other => {
            return Err(ApiError::bad_request(format!(
                "unsupported format `{other}`"
            )));
        }
    };

    let mut response = Response::builder()
        .status(StatusCode::OK)
        .header(header::CONTENT_TYPE, content_type)
        .header(
            header::CONTENT_DISPOSITION,
            format!("attachment; filename=\"{filename}\""),
        )
        .body(Body::from(body))
        .map_err(|e| ApiError::internal(format!("build response: {e}")))?;
    response.headers_mut().insert(
        header::ACCESS_CONTROL_EXPOSE_HEADERS,
        HeaderValue::from_static("Content-Disposition"),
    );
    Ok(response)
}
