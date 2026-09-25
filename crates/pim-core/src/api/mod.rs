//! REST API handlers and the OpenAPI document.

pub mod ai;
pub mod attribute_definitions;
pub mod attribute_groups;
pub mod auth;
pub mod category_attributes;
pub mod channels;
pub mod classification_standards;
pub mod config;
pub mod connector_schema;
pub mod connectors;
pub mod enum_sets;
pub mod export_profiles;
pub mod exports;
pub mod groups;
pub mod health;
pub mod imports;
pub mod job_schema;
pub mod jobs;
pub mod keys;
pub mod languages;
pub mod object_types;
pub mod objects;
pub mod permissions;
pub mod references;
pub mod rules;
pub mod saved_searches;
pub mod search;
pub mod search_schema;
pub mod system;
pub mod units;
pub mod users;

use utoipa::OpenApi;

/// Aggregates the OpenAPI document for the whole public API.
#[derive(OpenApi)]
#[openapi(
    paths(
        ai::generate,
        ai::test,
        auth::login,
        auth::me,
        auth::change_password,
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
        job_schema::job_schema,
        job_schema::validate_job_definition,
        config::export_config,
        config::import_config,
        connectors::create_connector,
        connectors::get_connector,
        connectors::list_connectors,
        connectors::list_connector_types,
        connectors::update_connector,
        connectors::delete_connector,
        connectors::test_connector,
        connectors::validate_connector,
        exports::create_export,
        exports::get_export_file,
        imports::create_import,
        imports::get_import_document,
        imports::complete_import,
        object_types::list_object_types,
        object_types::get_object_type,
        object_types::create_object_type,
        object_types::update_object_type,
        object_types::delete_object_type,
        channels::list_channels,
        channels::get_channel,
        channels::create_channel,
        channels::update_channel,
        channels::delete_channel,
        channels::list_object_releases,
        channels::set_object_release,
        channels::delete_object_release,
        classification_standards::list_classification_standards,
        classification_standards::get_classification_standard,
        classification_standards::create_classification_standard,
        classification_standards::update_classification_standard,
        classification_standards::delete_classification_standard,
        export_profiles::list_export_profiles,
        export_profiles::get_export_profile,
        export_profiles::create_export_profile,
        export_profiles::update_export_profile,
        export_profiles::delete_export_profile,
        export_profiles::export_profile_schema,
        export_profiles::validate_export_profile,
        category_attributes::list_category_attributes,
        category_attributes::create_category_attribute,
        category_attributes::update_category_attribute,
        category_attributes::delete_category_attribute,
        category_attributes::list_object_category_attributes,
        objects::create_object,
        objects::get_object,
        objects::list_objects,
        objects::count_objects,
        objects::attribute_usage,
        objects::object_stats,
        objects::composition_types,
        objects::update_object,
        objects::delete_object,
        objects::update_computed,
        objects::batch_update_objects,
        objects::batch_delete_objects,
        objects::batch_upsert_objects,
        objects::bulk_objects,
        keys::get_by_key,
        languages::list_languages,
        objects::get_by_source,
        references::create_reference,
        references::batch_create_references,
        references::query_references,
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
        rules::test_batch_rule,
        rules::recompute_rule,
        saved_searches::list_saved_searches,
        saved_searches::get_saved_search,
        saved_searches::create_saved_search,
        saved_searches::update_saved_search,
        saved_searches::delete_saved_search,
        saved_searches::execute_saved_search,
        saved_searches::preview_saved_search,
        saved_searches::list_search_fields,
        saved_searches::list_collections,
        saved_searches::get_collection,
        saved_searches::create_collection,
        saved_searches::update_collection,
        saved_searches::delete_collection,
        saved_searches::refresh_collection,
        saved_searches::list_collection_members,
        attribute_definitions::create_attribute_definition,
        attribute_definitions::get_attribute_definition,
        attribute_definitions::list_attribute_definitions,
        attribute_definitions::update_attribute_definition,
        attribute_definitions::delete_attribute_definition,
        attribute_definitions::change_attribute_type,
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
        groups::add_group_member,
        groups::remove_group_member,
        groups::add_group_child,
        groups::remove_group_child,
        groups::list_memberships,
        permissions::permission_catalog,
        system::ingest_logs,
        system::list_logs,
        system::list_settings,
        system::put_setting,
        system::delete_setting,
        search::search,
        units::list_units,
        units::convert,
        units::create_unit,
        units::update_unit,
        units::delete_unit,
        languages::create_language,
        languages::update_language,
        languages::delete_language,
    ),
    components(schemas(
        ai::GenerateRequest,
        ai::ColumnSpec,
        ai::TestRequest,
        auth::LoginRequest,
        auth::LoginResponse,
        auth::ChangePasswordRequest,
        auth::MeResponse,
        crate::auth::Impersonator,
        config::ConfigExportQuery,
        config::ConfigImportQuery,
        exports::ExportRequest,
        exports::ExportSource,
        exports::ExportOptions,
        exports::ExportFileQuery,
        saved_searches::CollectionMembersRequest,
        connectors::CreateConnector,
        connectors::UpdateConnector,
        connectors::ListConnectors,
        connectors::ValidateConnector,
        jobs::CreateJobDefinition,
        jobs::UpdateJobDefinition,
        jobs::ListJobs,
        jobs::ListJobRuns,
        jobs::UpdateJobRun,
        job_schema::ValidateJobDefinition,
        imports::ImportParams,
        imports::CompleteImport,
        objects::CreateObject,
        objects::UpdateObject,
        objects::UpdateComputed,
        objects::BatchUpdateItem,
        objects::BatchUpdateResult,
        objects::ListObjects,
        objects::GetObjectQuery,
        objects::ObjectStatsQuery,
        object_types::ListObjectTypes,
        object_types::CreateObjectType,
        object_types::UpdateObjectType,
        channels::ListChannels,
        channels::CreateChannel,
        channels::UpdateChannel,
        channels::SetRelease,
        classification_standards::ListStandards,
        classification_standards::CreateStandard,
        classification_standards::UpdateStandard,
        export_profiles::ListExportProfiles,
        export_profiles::CreateExportProfile,
        export_profiles::UpdateExportProfile,
        export_profiles::ValidateExportProfile,
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
        rules::TestBatch,
        rules::RecomputeRule,
        saved_searches::SavedSearchQuery,
        saved_searches::QueryFilter,
        saved_searches::QueryCondition,
        saved_searches::QueryFulltext,
        saved_searches::QueryReference,
        saved_searches::QuerySort,
        saved_searches::CreateSavedSearch,
        saved_searches::UpdateSavedSearch,
        saved_searches::CreateCollection,
        saved_searches::UpdateCollection,
        saved_searches::ListParams,
        attribute_definitions::CreateAttributeDefinition,
        attribute_definitions::UpdateAttributeDefinition,
        attribute_definitions::ChangeAttributeType,
        attribute_definitions::TypeChangeParams,
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
        units::CreateUnit,
        units::UpdateUnit,
        languages::LanguageNode,
        languages::CreateLanguage,
        languages::UpdateLanguage,
    ))
)]
pub struct ApiDoc;

#[cfg(test)]
mod tests {
    use super::*;

    /// The generated document must **build** and serialise.
    ///
    /// This is a crash guard, not a formality: utoipa inlines a nested type into
    /// its parent's schema, so the self-referential `QueryCondition.conditions`
    /// recursed without bound inside `ApiDoc::openapi()` and killed the whole
    /// process with a stack overflow (`GET /api-docs/openapi.json`, found
    /// 2026-09-21 — the endpoint had never been called). The field carries
    /// `#[schema(no_recursion)]`; without a test, the next recursive type added to
    /// the document would take the process down again instead of failing a gate.
    #[test]
    fn the_openapi_document_builds_and_serialises() {
        let document = ApiDoc::openapi();
        let json = serde_json::to_string(&document).expect("the document must serialise");
        assert!(
            json.len() > 10_000,
            "suspiciously small: {} bytes",
            json.len()
        );
        for path in ["/jobs/schema", "/jobs/validate", "/saved-searches/preview"] {
            assert!(
                document.paths.paths.contains_key(path),
                "`{path}` is missing from the document"
            );
        }
    }
}
