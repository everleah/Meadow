//! REST API handlers and the OpenAPI document.

pub mod attribute_definitions;
pub mod attribute_groups;
pub mod auth;
pub mod groups;
pub mod health;
pub mod keys;
pub mod objects;
pub mod references;
pub mod rules;
pub mod search;
pub mod system;
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
        keys::get_by_key,
        references::create_reference,
        references::list_object_references,
        references::list_object_children,
        references::reparent_object,
        rules::create_rule,
        rules::get_rule,
        rules::list_rules,
        rules::update_rule,
        rules::activate_rule,
        rules::disable_rule,
        rules::dry_run_rule,
        attribute_definitions::create_attribute_definition,
        attribute_definitions::get_attribute_definition,
        attribute_definitions::list_attribute_definitions,
        attribute_definitions::update_attribute_definition,
        attribute_groups::create_attribute_group,
        attribute_groups::list_attribute_groups,
        attribute_groups::get_attribute_group,
        attribute_groups::update_attribute_group,
        attribute_groups::delete_attribute_group,
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
        system::ingest_logs,
        system::list_logs,
        system::list_settings,
        system::put_setting,
        system::delete_setting,
        search::search,
    ),
    components(schemas(
        auth::LoginRequest,
        auth::LoginResponse,
        auth::MeResponse,
        objects::CreateObject,
        objects::UpdateObject,
        objects::ListObjects,
        objects::GetObjectQuery,
        keys::ByKeyParams,
        references::CreateReference,
        references::ReparentObject,
        rules::CreateRule,
        rules::UpdateRule,
        rules::ListRules,
        rules::DryRunRule,
        rules::DryRunObject,
        attribute_definitions::CreateAttributeDefinition,
        attribute_definitions::UpdateAttributeDefinition,
        attribute_definitions::ListAttributeDefinitions,
        attribute_groups::CreateAttributeGroup,
        attribute_groups::UpdateAttributeGroup,
        attribute_groups::ListAttributeGroups,
        users::CreateUser,
        users::UpdateUser,
        users::ListUsers,
        groups::CreateGroup,
        groups::UpdateGroup,
        groups::ListGroups,
        system::IngestLogEntry,
        system::ListLogs,
        system::PutSetting,
        search::SearchRequest,
    ))
)]
pub struct ApiDoc;
