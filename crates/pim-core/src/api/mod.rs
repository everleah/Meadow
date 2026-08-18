//! REST API handlers and the OpenAPI document.

pub mod health;

use utoipa::OpenApi;

/// Aggregates the OpenAPI document for the whole public API.
#[derive(OpenApi)]
#[openapi(paths(health::health))]
pub struct ApiDoc;
