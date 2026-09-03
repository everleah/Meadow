//! REST API handlers and the OpenAPI document.

pub mod attribute_definitions;
pub mod attribute_groups;
pub mod auth;
pub mod connectors;
pub mod groups;
pub mod health;
pub mod imports;
pub mod jobs;
pub mod keys;
pub mod languages;
pub mod objects;
pub mod references;
pub mod rules;
pub mod search;
pub mod system;
pub mod units;
pub mod users;

use utoipa::OpenApi;

/// Aggregates the OpenAPI document for the whole public API.
#[derive(OpenApi)]
#[openapi(
    paths(
        auth::login,
        auth::me,
        auth::impersonate_user,
        health::health,
        jobs::create_job_definition,
        jobs::get_job_definition,
        jobs::list_job_definitions,
        jobs::update_job_definition,
        jobs::list_definition_runs,
        jobs::list_job_runs,
        jobs::update_job_run,
        jobs::run_job,
        connectors::create_connector,
        connectors::get_connector,
        connectors::list_connectors,
        connectors::update_connector,
        connectors::delete_connector,
        imports::create_import,
        imports::get_import_document,
        imports::complete_import,
        objects::create_object,
        objects::get_object,
        objects::list_objects,
        objects::update_object,
        objects::delete_object,
        objects::update_computed,
        objects::batch_update_objects,
        objects::bulk_objects,
        keys::get_by_key,
        languages::list_languages,
        objects::get_by_source,
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
        rules::recompute_rule,
        attribute_definitions::create_attribute_definition,
        attribute_definitions::get_attribute_definition,
        attribute_definitions::list_attribute_definitions,
        attribute_definitions::update_attribute_definition,
        attribute_definitions::delete_attribute_definition,
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
        units::list_units,
        units::convert,
    ),
    components(schemas(
        auth::LoginRequest,
        auth::LoginResponse,
        auth::MeResponse,
        crate::auth::Impersonator,
        connectors::CreateConnector,
        connectors::UpdateConnector,
        connectors::ListConnectors,
        jobs::CreateJobDefinition,
        jobs::UpdateJobDefinition,
        jobs::ListJobs,
        jobs::ListJobRuns,
        jobs::UpdateJobRun,
        imports::ImportParams,
        imports::CompleteImport,
        objects::CreateObject,
        objects::UpdateObject,
        objects::UpdateComputed,
        objects::BatchUpdateItem,
        objects::BatchUpdateResult,
        objects::ListObjects,
        objects::GetObjectQuery,
        objects::WriteObjectQuery,
        keys::ByKeyParams,
        objects::BySourceParams,
        references::CreateReference,
        references::ReparentObject,
        rules::CreateRule,
        rules::UpdateRule,
        rules::ListRules,
        rules::DryRunRule,
        rules::DryRunObject,
        rules::RecomputeRule,
        attribute_definitions::CreateAttributeDefinition,
        attribute_definitions::UpdateAttributeDefinition,
        attribute_definitions::ListAttributeDefinitions,
        attribute_definitions::DeleteAttributeDefinition,
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
        system::SettingsQuery,
        search::SearchRequest,
        units::ListUnits,
        units::ConvertQuery,
    ))
)]
pub struct ApiDoc;
