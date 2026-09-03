//! HTTP application: routes, state, and the OpenAPI document.

use axum::{
    Json, Router,
    extract::DefaultBodyLimit,
    routing::{get, post, put},
};
use utoipa::OpenApi as _;
use utoipa::openapi::OpenApi;

use crate::cache::MetaCache;
use crate::rules::RulesClient;
use crate::{api, config::Config, db::Db};

/// Shared application state.
#[derive(Clone)]
pub struct AppState {
    /// Handle to the SurrealDB instance.
    pub db: Db,
    /// Runtime configuration.
    pub config: Config,
    /// In-memory cache for slow-changing metadata.
    pub cache: std::sync::Arc<MetaCache>,
    /// Synchronous rule-evaluation client (NATS request/reply).
    pub rules: RulesClient,
}

/// Builds the application router with the given state.
pub fn router(state: AppState) -> Router {
    Router::new()
        .route("/health", get(api::health::health))
        .route("/api-docs/openapi.json", get(openapi_json))
        .route("/auth/login", post(api::auth::login))
        .route("/auth/me", get(api::auth::me))
        .route(
            "/auth/impersonate/{user_id}",
            post(api::auth::impersonate_user),
        )
        .route(
            "/objects",
            get(api::objects::list_objects).post(api::objects::create_object),
        )
        .route("/objects/by-key", get(api::keys::get_by_key))
        .route("/objects/by-source", get(api::objects::get_by_source))
        .route("/objects/picker", get(api::objects::picker))
        .route(
            "/objects/batch-update",
            post(api::objects::batch_update_objects),
        )
        .route("/objects/bulk", post(api::objects::bulk_objects))
        .route("/objects/tree", get(api::references::get_object_tree))
        .route(
            "/objects/{id}",
            get(api::objects::get_object)
                .patch(api::objects::update_object)
                .delete(api::objects::delete_object),
        )
        .route(
            "/objects/{id}/computed",
            axum::routing::patch(api::objects::update_computed),
        )
        .route("/references", post(api::references::create_reference))
        .route(
            "/references/{id}",
            axum::routing::delete(api::references::delete_reference),
        )
        .route(
            "/relation-definitions",
            get(api::references::list_relation_definitions),
        )
        .route("/search", post(api::search::search))
        .route(
            "/rules",
            get(api::rules::list_rules).post(api::rules::create_rule),
        )
        .route(
            "/rules/{id}",
            get(api::rules::get_rule).patch(api::rules::update_rule),
        )
        .route("/rules/{id}/activate", post(api::rules::activate_rule))
        .route("/rules/{id}/disable", post(api::rules::disable_rule))
        .route("/rules/{id}/dry-run", post(api::rules::dry_run_rule))
        .route("/rules/{id}/recompute", post(api::rules::recompute_rule))
        .route(
            "/objects/{id}/references",
            get(api::references::list_object_references),
        )
        .route(
            "/objects/{id}/children",
            get(api::references::list_object_children),
        )
        .route(
            "/objects/{id}/parent",
            axum::routing::patch(api::references::reparent_object),
        )
        .route(
            "/attribute-definitions",
            get(api::attribute_definitions::list_attribute_definitions)
                .post(api::attribute_definitions::create_attribute_definition),
        )
        .route(
            "/attribute-definitions/{id}",
            get(api::attribute_definitions::get_attribute_definition)
                .patch(api::attribute_definitions::update_attribute_definition)
                .delete(api::attribute_definitions::delete_attribute_definition),
        )
        .route(
            "/attribute-groups",
            get(api::attribute_groups::list_attribute_groups)
                .post(api::attribute_groups::create_attribute_group),
        )
        .route(
            "/attribute-groups/{id}",
            get(api::attribute_groups::get_attribute_group)
                .patch(api::attribute_groups::update_attribute_group)
                .delete(api::attribute_groups::delete_attribute_group),
        )
        .route(
            "/users",
            get(api::users::list_users).post(api::users::create_user),
        )
        .route(
            "/users/{id}",
            get(api::users::get_user)
                .patch(api::users::update_user)
                .delete(api::users::delete_user),
        )
        .route(
            "/groups",
            get(api::groups::list_groups).post(api::groups::create_group),
        )
        .route(
            "/groups/{id}",
            get(api::groups::get_group)
                .patch(api::groups::update_group)
                .delete(api::groups::delete_group),
        )
        .route(
            "/system/logs",
            get(api::system::list_logs).post(api::system::ingest_logs),
        )
        .route("/system/settings", get(api::system::list_settings))
        .route(
            "/system/settings/{key}",
            put(api::system::put_setting).delete(api::system::delete_setting),
        )
        .route(
            "/jobs",
            get(api::jobs::list_job_definitions).post(api::jobs::create_job_definition),
        )
        .route("/jobs/runs", get(api::jobs::list_job_runs))
        .route(
            "/jobs/runs/{run_id}",
            axum::routing::patch(api::jobs::update_job_run),
        )
        .route(
            "/jobs/{id}",
            get(api::jobs::get_job_definition).patch(api::jobs::update_job_definition),
        )
        .route("/jobs/{id}/runs", get(api::jobs::list_definition_runs))
        .route("/jobs/{id}/run", post(api::jobs::run_job))
        .route(
            "/connectors",
            get(api::connectors::list_connectors).post(api::connectors::create_connector),
        )
        .route(
            "/connectors/{id}",
            get(api::connectors::get_connector)
                .patch(api::connectors::update_connector)
                .delete(api::connectors::delete_connector),
        )
        .route(
            "/imports",
            post(api::imports::create_import).layer(DefaultBodyLimit::max(100 * 1024 * 1024)),
        )
        .route(
            "/imports/{document_id}",
            get(api::imports::get_import_document),
        )
        .route(
            "/imports/{document_id}/complete",
            post(api::imports::complete_import),
        )
        .route("/units", get(api::units::list_units))
        .route("/units/convert", get(api::units::convert))
        .route("/languages", get(api::languages::list_languages))
        .with_state(state)
}

/// Serves the generated OpenAPI 3 document.
async fn openapi_json() -> Json<OpenApi> {
    Json(api::ApiDoc::openapi())
}
