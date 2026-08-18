//! REST API handlers and the OpenAPI document.

pub mod attribute_definitions;
pub mod auth;
pub mod groups;
pub mod health;
pub mod objects;
pub mod users;

use utoipa::OpenApi;

/// Aggregates the OpenAPI document for the whole public API.
#[derive(OpenApi)]
#[openapi(
    paths(
        auth::login,
        auth::me,
        health::health,
        objects::create_object,
        objects::get_object,
        objects::list_objects,
        objects::update_object,
        attribute_definitions::create_attribute_definition,
        attribute_definitions::get_attribute_definition,
        attribute_definitions::list_attribute_definitions,
        attribute_definitions::update_attribute_definition,
        users::create_user,
        users::list_users,
        users::get_user,
        users::update_user,
        users::delete_user,
        groups::create_group,
        groups::list_groups,
        groups::get_group,
        groups::update_group,
        groups::delete_group,
    ),
    components(schemas(
        auth::LoginRequest,
        auth::LoginResponse,
        auth::MeResponse,
        objects::CreateObject,
        objects::UpdateObject,
        objects::ListObjects,
        attribute_definitions::CreateAttributeDefinition,
        attribute_definitions::UpdateAttributeDefinition,
        attribute_definitions::ListAttributeDefinitions,
        users::CreateUser,
        users::UpdateUser,
        users::ListUsers,
        groups::CreateGroup,
        groups::UpdateGroup,
        groups::ListGroups,
    ))
)]
pub struct ApiDoc;
