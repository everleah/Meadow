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
use crate::{ai::AiClient, api, config::Config, db::Db};

/// Shared application state.
#[derive(Clone)]
pub struct AppState {
    /// Handle to the SurrealDB instance.
    pub db: Db,
    /// Runtime configuration (environment-derived, startup-bound).
    pub config: Config,
    /// In-memory cache for slow-changing metadata.
    pub cache: std::sync::Arc<MetaCache>,
    /// Per-key locks around the business-key check and the write that follows it.
    pub key_locks: std::sync::Arc<crate::key_lock::KeyLocks>,
    /// Synchronous rule-evaluation client (NATS request/reply).
    pub rules: RulesClient,
    /// AI-assistance client (NATS request/reply; configuration + secret handling).
    pub ai: AiClient,
    /// Resolved runtime settings snapshot (default < env < db). Refreshed on
    /// settings writes so consumers (e.g. the rule client) see updates at
    /// runtime without a restart.
    pub settings: std::sync::Arc<std::sync::RwLock<serde_json::Map<String, serde_json::Value>>>,
}

/// Builds the application router with the given state.
pub fn router(state: AppState) -> Router {
    let body_limit = state.config.max_body_bytes;
    let limiter = std::sync::Arc::new(crate::rate_limit::RateLimiter::new(
        state.config.rate_limit_per_minute,
    ));
    // The renewal layer needs the state after the routes took it, and it has to wrap every
    // route: any handler that raises `auth_version` ends the caller's own session too.
    let renewal_state = state.clone();

    Router::new()
        .route("/health", get(api::health::health))
        .route("/api-docs/openapi.json", get(openapi_json))
        .route("/auth/login", post(api::auth::login))
        .route("/auth/me", get(api::auth::me))
        .route("/auth/password", post(api::auth::change_password))
        .route("/config/export", get(api::config::export_config))
        // A complete MCF document (the whole model, ETIM included) is far larger
        // than the default body limit: the demo instance exports 15 MB, so a
        // restore would fail with `413` before reaching the importer.
        .route(
            "/config/import",
            post(api::config::import_config).layer(DefaultBodyLimit::max(100 * 1024 * 1024)),
        )
        .route("/exports", post(api::exports::create_export))
        .route("/exports/{run_id}/file", get(api::exports::get_export_file))
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
        .route("/objects/stats", get(api::objects::object_stats))
        .route("/objects/count", get(api::objects::count_objects))
        .route(
            "/objects/attribute-usage",
            get(api::objects::attribute_usage),
        )
        .route(
            "/objects/composition-types",
            get(api::objects::composition_types),
        )
        .route("/objects/picker", get(api::objects::picker))
        .route(
            "/objects/batch-update",
            post(api::objects::batch_update_objects),
        )
        .route(
            "/objects/batch-delete",
            post(api::objects::batch_delete_objects),
        )
        .route(
            "/objects/batch-upsert",
            post(api::objects::batch_upsert_objects),
        )
        .route("/objects/bulk", post(api::objects::bulk_objects))
        .route("/objects/tree", get(api::references::get_object_tree))
        .route(
            "/object-types",
            get(api::object_types::list_object_types).post(api::object_types::create_object_type),
        )
        .route(
            "/object-types/{name}",
            get(api::object_types::get_object_type)
                .patch(api::object_types::update_object_type)
                .delete(api::object_types::delete_object_type),
        )
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
        .route(
            "/channels",
            get(api::channels::list_channels).post(api::channels::create_channel),
        )
        .route(
            "/classification-standards",
            get(api::classification_standards::list_classification_standards)
                .post(api::classification_standards::create_classification_standard),
        )
        .route(
            "/classification-standards/{id}",
            get(api::classification_standards::get_classification_standard)
                .patch(api::classification_standards::update_classification_standard)
                .delete(api::classification_standards::delete_classification_standard),
        )
        .route(
            "/export-profiles",
            get(api::export_profiles::list_export_profiles)
                .post(api::export_profiles::create_export_profile),
        )
        .route(
            "/export-profiles/schema",
            get(api::export_profiles::export_profile_schema),
        )
        .route(
            "/export-profiles/validate",
            post(api::export_profiles::validate_export_profile),
        )
        .route(
            "/export-profiles/{id}",
            get(api::export_profiles::get_export_profile)
                .patch(api::export_profiles::update_export_profile)
                .delete(api::export_profiles::delete_export_profile),
        )
        .route(
            "/channels/{id}",
            get(api::channels::get_channel)
                .patch(api::channels::update_channel)
                .delete(api::channels::delete_channel),
        )
        .route(
            "/objects/{id}/releases",
            get(api::channels::list_object_releases).put(api::channels::set_object_release),
        )
        .route(
            "/objects/{id}/releases/{channel}",
            axum::routing::delete(api::channels::delete_object_release),
        )
        .route(
            "/objects/{id}/category-attributes",
            get(api::category_attributes::list_object_category_attributes),
        )
        .route(
            "/categories/{id}/attributes",
            get(api::category_attributes::list_category_attributes)
                .post(api::category_attributes::create_category_attribute),
        )
        .route(
            "/categories/{id}/attributes/{attribute}",
            axum::routing::patch(api::category_attributes::update_category_attribute)
                .delete(api::category_attributes::delete_category_attribute),
        )
        .route("/references", post(api::references::create_reference))
        .route(
            "/references/batch",
            post(api::references::batch_create_references),
        )
        .route("/references/query", post(api::references::query_references))
        .route(
            "/references/{id}",
            axum::routing::patch(api::references::update_reference)
                .delete(api::references::delete_reference),
        )
        .route(
            "/relation-definitions",
            get(api::references::list_relation_definitions)
                .post(api::references::create_relation_definition),
        )
        .route(
            "/relation-definitions/{id}",
            axum::routing::patch(api::references::update_relation_definition)
                .delete(api::references::delete_relation_definition),
        )
        .route("/search", post(api::search::search))
        .route(
            "/rules",
            get(api::rules::list_rules).post(api::rules::create_rule),
        )
        .route(
            "/rules/{id}",
            get(api::rules::get_rule)
                .patch(api::rules::update_rule)
                .delete(api::rules::delete_rule),
        )
        .route("/rules/{id}/activate", post(api::rules::activate_rule))
        .route("/rules/{id}/disable", post(api::rules::disable_rule))
        .route("/rules/{id}/dry-run", post(api::rules::dry_run_rule))
        .route("/rules/{id}/test-batch", post(api::rules::test_batch_rule))
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
            "/objects/{id}/ancestors",
            get(api::references::list_object_ancestors),
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
            "/attribute-definitions/vocabulary",
            get(api::attribute_definitions::list_attribute_vocabulary),
        )
        .route(
            "/attribute-definitions/{id}",
            get(api::attribute_definitions::get_attribute_definition)
                .patch(api::attribute_definitions::update_attribute_definition)
                .delete(api::attribute_definitions::delete_attribute_definition),
        )
        .route(
            "/attribute-definitions/{id}/enum-usage",
            get(api::attribute_definitions::enum_value_usage),
        )
        .route(
            "/attribute-definitions/{id}/type-change",
            axum::routing::post(api::attribute_definitions::change_attribute_type),
        )
        .route(
            "/enum-sets",
            get(api::enum_sets::list_enum_sets).post(api::enum_sets::create_enum_set),
        )
        .route(
            "/enum-sets/{id}",
            get(api::enum_sets::get_enum_set)
                .patch(api::enum_sets::update_enum_set)
                .delete(api::enum_sets::delete_enum_set),
        )
        .route(
            "/enum-options",
            get(api::enum_sets::list_enum_options).post(api::enum_sets::create_enum_option),
        )
        .route(
            "/enum-options/{id}",
            axum::routing::patch(api::enum_sets::update_enum_option)
                .delete(api::enum_sets::delete_enum_option),
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
        .route("/groups/preview-scope", post(api::groups::preview_scope))
        .route(
            "/groups/{id}",
            get(api::groups::get_group)
                .patch(api::groups::update_group)
                .delete(api::groups::delete_group),
        )
        .route("/groups/{id}/members", post(api::groups::add_group_member))
        .route(
            "/groups/{id}/members/{user_id}",
            axum::routing::delete(api::groups::remove_group_member),
        )
        .route("/groups/{id}/children", post(api::groups::add_group_child))
        .route(
            "/groups/{id}/children/{child_id}",
            axum::routing::delete(api::groups::remove_group_child),
        )
        .route("/memberships", get(api::groups::list_memberships))
        .route("/permissions", get(api::permissions::permission_catalog))
        .route(
            "/system/logs",
            get(api::system::list_logs).post(api::system::ingest_logs),
        )
        .route("/system/settings", get(api::system::list_settings))
        .route(
            "/system/settings/{key}",
            put(api::system::put_setting).delete(api::system::delete_setting),
        )
        .route("/ai/generate", post(api::ai::generate))
        .route("/ai/test", post(api::ai::test))
        .route(
            "/jobs",
            get(api::jobs::list_job_definitions).post(api::jobs::create_job_definition),
        )
        .route("/jobs/runs", get(api::jobs::list_job_runs))
        .route(
            "/jobs/runs/{run_id}",
            axum::routing::patch(api::jobs::update_job_run).delete(api::jobs::delete_job_run),
        )
        // The static paths come before `/jobs/{id}`: otherwise a request for
        // `/jobs/schema` would hit the `{id}` match and answer `405`.
        .route("/jobs/schema", get(api::job_schema::job_schema))
        .route(
            "/jobs/validate",
            post(api::job_schema::validate_job_definition),
        )
        .route(
            "/jobs/{id}",
            get(api::jobs::get_job_definition)
                .patch(api::jobs::update_job_definition)
                .delete(api::jobs::delete_job_definition),
        )
        .route("/jobs/{id}/runs", get(api::jobs::list_definition_runs))
        .route("/jobs/{id}/run", post(api::jobs::run_job))
        .route(
            "/connectors",
            get(api::connectors::list_connectors).post(api::connectors::create_connector),
        )
        .route(
            "/connector-types",
            get(api::connectors::list_connector_types),
        )
        .route(
            "/connectors/{id}",
            get(api::connectors::get_connector)
                .patch(api::connectors::update_connector)
                .delete(api::connectors::delete_connector),
        )
        .route(
            "/connectors/{id}/test",
            post(api::connectors::test_connector),
        )
        .route(
            "/connectors/validate",
            post(api::connectors::validate_connector),
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
        .route(
            "/units",
            get(api::units::list_units).post(api::units::create_unit),
        )
        .route(
            "/units/{id}",
            axum::routing::patch(api::units::update_unit).delete(api::units::delete_unit),
        )
        .route("/units/convert", get(api::units::convert))
        .route(
            "/saved-searches",
            get(api::saved_searches::list_saved_searches)
                .post(api::saved_searches::create_saved_search),
        )
        .route(
            "/saved-searches/{id}",
            get(api::saved_searches::get_saved_search)
                .patch(api::saved_searches::update_saved_search)
                .delete(api::saved_searches::delete_saved_search),
        )
        .route(
            "/saved-searches/{id}/execute",
            post(api::saved_searches::execute_saved_search),
        )
        .route(
            "/saved-searches/preview",
            post(api::saved_searches::preview_saved_search),
        )
        .route(
            "/search-fields",
            get(api::saved_searches::list_search_fields),
        )
        .route(
            "/collections",
            get(api::saved_searches::list_collections).post(api::saved_searches::create_collection),
        )
        .route(
            "/collections/{id}",
            get(api::saved_searches::get_collection)
                .patch(api::saved_searches::update_collection)
                .delete(api::saved_searches::delete_collection),
        )
        .route(
            "/collections/{id}/refresh",
            post(api::saved_searches::refresh_collection),
        )
        .route(
            "/collections/{id}/members",
            get(api::saved_searches::list_collection_members)
                .post(api::saved_searches::add_collection_members),
        )
        .route(
            "/languages",
            get(api::languages::list_languages).post(api::languages::create_language),
        )
        .route(
            "/languages/{code}",
            axum::routing::patch(api::languages::update_language)
                .delete(api::languages::delete_language),
        )
        .with_state(state)
        .layer(DefaultBodyLimit::max(body_limit))
        .layer(axum::middleware::from_fn_with_state(
            renewal_state,
            api::auth::renew_after_access_change,
        ))
        .layer(axum::middleware::from_fn_with_state(
            limiter,
            crate::rate_limit::middleware,
        ))
}

/// Serves the generated OpenAPI 3 document.
async fn openapi_json() -> Json<OpenApi> {
    Json(api::ApiDoc::openapi())
}
