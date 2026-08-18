//! REST API handlers and the OpenAPI document.

pub mod attribute_definitions;
pub mod health;
pub mod objects;

use utoipa::OpenApi;

/// Aggregates the OpenAPI document for the whole public API.
#[derive(OpenApi)]
#[openapi(
    paths(
        health::health,
        objects::create_object,
        objects::get_object,
        objects::list_objects,
        objects::update_object,
        attribute_definitions::create_attribute_definition,
        attribute_definitions::get_attribute_definition,
        attribute_definitions::list_attribute_definitions,
        attribute_definitions::update_attribute_definition,
    ),
    components(schemas(
        objects::CreateObject,
        objects::UpdateObject,
        objects::ListObjects,
        attribute_definitions::CreateAttributeDefinition,
        attribute_definitions::UpdateAttributeDefinition,
        attribute_definitions::ListAttributeDefinitions,
    ))
)]
pub struct ApiDoc;
