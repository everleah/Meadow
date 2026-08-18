//! HTTP application: routes, state, and the OpenAPI document.

use axum::{
    Json, Router,
    routing::{get, post},
};
use utoipa::OpenApi as _;
use utoipa::openapi::OpenApi;

use crate::{api, config::Config, db::Db};

/// Shared application state.
#[derive(Clone)]
pub struct AppState {
    /// Handle to the SurrealDB instance.
    pub db: Db,
    /// Runtime configuration.
    pub config: Config,
}

/// Builds the application router with the given state.
pub fn router(state: AppState) -> Router {
    Router::new()
        .route("/health", get(api::health::health))
        .route("/api-docs/openapi.json", get(openapi_json))
        .route("/auth/login", post(api::auth::login))
        .route("/auth/me", get(api::auth::me))
        .route(
            "/objects",
            get(api::objects::list_objects).post(api::objects::create_object),
        )
        .route(
            "/objects/{id}",
            get(api::objects::get_object).patch(api::objects::update_object),
        )
        .route(
            "/attribute-definitions",
            get(api::attribute_definitions::list_attribute_definitions)
                .post(api::attribute_definitions::create_attribute_definition),
        )
        .route(
            "/attribute-definitions/{id}",
            get(api::attribute_definitions::get_attribute_definition)
                .patch(api::attribute_definitions::update_attribute_definition),
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
        .with_state(state)
}

/// Serves the generated OpenAPI 3 document.
async fn openapi_json() -> Json<OpenApi> {
    Json(api::ApiDoc::openapi())
}
