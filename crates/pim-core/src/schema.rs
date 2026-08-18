//! SurrealDB schema: tables, fields, and indexes (Phase 1).
//!
//! Statements are idempotent (`IF NOT EXISTS`) and applied at startup. The
//! field contracts follow `plan/detail/datenmodell.md` and its sibling
//! documents. Schema changes during development require recreating the local
//! development database (`docker compose down -v`) until migrations exist.

use crate::db::Db;

/// Schema statements, applied idempotently at startup.
pub const SCHEMA: &str = r#"
-- Generic object: all business entities share this table.
DEFINE TABLE IF NOT EXISTS object;
DEFINE FIELD IF NOT EXISTS type ON TABLE object TYPE string;
DEFINE FIELD IF NOT EXISTS attributes ON TABLE object TYPE object;
DEFINE FIELD IF NOT EXISTS status ON TABLE object TYPE string;
DEFINE FIELD IF NOT EXISTS version ON TABLE object TYPE int;
DEFINE FIELD IF NOT EXISTS created_at ON TABLE object TYPE datetime;
DEFINE FIELD IF NOT EXISTS updated_at ON TABLE object TYPE datetime;
DEFINE FIELD IF NOT EXISTS updated_by ON TABLE object TYPE option<string>;
DEFINE INDEX IF NOT EXISTS idx_object_type ON TABLE object FIELDS type;
DEFINE INDEX IF NOT EXISTS idx_object_status ON TABLE object FIELDS status;
DEFINE INDEX IF NOT EXISTS idx_object_created_at ON TABLE object FIELDS created_at;
DEFINE INDEX IF NOT EXISTS idx_object_updated_at ON TABLE object FIELDS updated_at;

-- Attribute definitions: the metadata schema (product template per type).
DEFINE TABLE IF NOT EXISTS attribute_definition;
DEFINE FIELD IF NOT EXISTS name ON TABLE attribute_definition TYPE string;
DEFINE FIELD IF NOT EXISTS label ON TABLE attribute_definition TYPE object;
DEFINE FIELD IF NOT EXISTS type ON TABLE attribute_definition TYPE string;
DEFINE FIELD IF NOT EXISTS group_id ON TABLE attribute_definition TYPE option<string>;
DEFINE FIELD IF NOT EXISTS inheritable ON TABLE attribute_definition TYPE bool;
DEFINE FIELD IF NOT EXISTS is_key ON TABLE attribute_definition TYPE bool;
DEFINE FIELD IF NOT EXISTS applicable_types ON TABLE attribute_definition TYPE array;
DEFINE FIELD IF NOT EXISTS required_types ON TABLE attribute_definition TYPE array;
DEFINE FIELD IF NOT EXISTS computed ON TABLE attribute_definition TYPE bool;
DEFINE FIELD IF NOT EXISTS computed_rule ON TABLE attribute_definition TYPE option<string>;
DEFINE FIELD IF NOT EXISTS dependencies ON TABLE attribute_definition TYPE array;
DEFINE FIELD IF NOT EXISTS computed_reads_inherited ON TABLE attribute_definition TYPE bool;
DEFINE FIELD IF NOT EXISTS multilingual ON TABLE attribute_definition TYPE bool;
DEFINE FIELD IF NOT EXISTS unit_id ON TABLE attribute_definition TYPE option<string>;
DEFINE FIELD IF NOT EXISTS display_unit_id ON TABLE attribute_definition TYPE option<string>;
DEFINE FIELD IF NOT EXISTS constraints ON TABLE attribute_definition TYPE option<object>;
DEFINE FIELD IF NOT EXISTS default_value ON TABLE attribute_definition;
DEFINE FIELD IF NOT EXISTS description ON TABLE attribute_definition TYPE option<string>;
DEFINE FIELD IF NOT EXISTS version ON TABLE attribute_definition TYPE int;
DEFINE FIELD IF NOT EXISTS created_at ON TABLE attribute_definition TYPE datetime;
DEFINE FIELD IF NOT EXISTS updated_at ON TABLE attribute_definition TYPE datetime;
DEFINE FIELD IF NOT EXISTS updated_by ON TABLE attribute_definition TYPE option<string>;
DEFINE INDEX IF NOT EXISTS idx_attr_def_name ON TABLE attribute_definition FIELDS name UNIQUE;
DEFINE INDEX IF NOT EXISTS idx_attr_def_group ON TABLE attribute_definition FIELDS group_id;

-- Attribute groups: UI grouping for attribute definitions.
DEFINE TABLE IF NOT EXISTS attribute_group;
DEFINE FIELD IF NOT EXISTS name ON TABLE attribute_group TYPE string;
DEFINE FIELD IF NOT EXISTS label ON TABLE attribute_group TYPE object;
DEFINE FIELD IF NOT EXISTS position ON TABLE attribute_group TYPE int;
DEFINE INDEX IF NOT EXISTS idx_attr_group_name ON TABLE attribute_group FIELDS name UNIQUE;
DEFINE INDEX IF NOT EXISTS idx_attr_group_position ON TABLE attribute_group FIELDS position;

-- References: graph edges between objects (native SurrealDB RELATION table).
-- `in` = source object (plan: `from`), `out` = target object (plan: `to`).
DEFINE TABLE IF NOT EXISTS reference TYPE RELATION;
DEFINE FIELD IF NOT EXISTS definition_id ON TABLE reference TYPE string;
DEFINE FIELD IF NOT EXISTS type ON TABLE reference TYPE string;
DEFINE FIELD IF NOT EXISTS attributes ON TABLE reference TYPE object;
DEFINE INDEX IF NOT EXISTS idx_ref_definition ON TABLE reference FIELDS definition_id;
DEFINE INDEX IF NOT EXISTS idx_ref_in ON TABLE reference FIELDS in;
DEFINE INDEX IF NOT EXISTS idx_ref_out ON TABLE reference FIELDS out;
DEFINE INDEX IF NOT EXISTS idx_ref_in_def ON TABLE reference FIELDS in, definition_id;
DEFINE INDEX IF NOT EXISTS idx_ref_out_def ON TABLE reference FIELDS out, definition_id;

-- Relation definitions: composition vs. aggregation semantics.
DEFINE TABLE IF NOT EXISTS relation_definition;
DEFINE FIELD IF NOT EXISTS name ON TABLE relation_definition TYPE string;
DEFINE FIELD IF NOT EXISTS kind ON TABLE relation_definition TYPE string;
DEFINE FIELD IF NOT EXISTS source_types ON TABLE relation_definition TYPE array;
DEFINE FIELD IF NOT EXISTS target_types ON TABLE relation_definition TYPE array;
DEFINE FIELD IF NOT EXISTS label ON TABLE relation_definition TYPE object;
DEFINE FIELD IF NOT EXISTS system ON TABLE relation_definition TYPE bool;
DEFINE FIELD IF NOT EXISTS created_at ON TABLE relation_definition TYPE datetime;
DEFINE FIELD IF NOT EXISTS updated_at ON TABLE relation_definition TYPE datetime;
DEFINE INDEX IF NOT EXISTS idx_rel_def_name ON TABLE relation_definition FIELDS name UNIQUE;

-- Language tree: hierarchy for multilingual attribute resolution.
DEFINE TABLE IF NOT EXISTS language_tree;
DEFINE FIELD IF NOT EXISTS code ON TABLE language_tree TYPE string;
DEFINE FIELD IF NOT EXISTS parent ON TABLE language_tree TYPE option<string>;
DEFINE FIELD IF NOT EXISTS label ON TABLE language_tree TYPE object;
DEFINE FIELD IF NOT EXISTS fallback_order ON TABLE language_tree TYPE array;
DEFINE INDEX IF NOT EXISTS idx_lang_code ON TABLE language_tree FIELDS code UNIQUE;
DEFINE INDEX IF NOT EXISTS idx_lang_parent ON TABLE language_tree FIELDS parent;

-- Business rules: Python scripts (validated in a later phase).
DEFINE TABLE IF NOT EXISTS business_rule;
DEFINE FIELD IF NOT EXISTS name ON TABLE business_rule TYPE string;
DEFINE FIELD IF NOT EXISTS type ON TABLE business_rule TYPE string;
DEFINE FIELD IF NOT EXISTS applies_to ON TABLE business_rule TYPE array;
DEFINE FIELD IF NOT EXISTS script ON TABLE business_rule TYPE string;
DEFINE FIELD IF NOT EXISTS state ON TABLE business_rule TYPE string;
DEFINE FIELD IF NOT EXISTS version ON TABLE business_rule TYPE int;
DEFINE FIELD IF NOT EXISTS created_at ON TABLE business_rule TYPE datetime;
DEFINE FIELD IF NOT EXISTS updated_at ON TABLE business_rule TYPE datetime;
DEFINE FIELD IF NOT EXISTS updated_by ON TABLE business_rule TYPE option<string>;
DEFINE INDEX IF NOT EXISTS idx_rule_name ON TABLE business_rule FIELDS name UNIQUE;
DEFINE INDEX IF NOT EXISTS idx_rule_type ON TABLE business_rule FIELDS type;
DEFINE INDEX IF NOT EXISTS idx_rule_state ON TABLE business_rule FIELDS state;

-- Units: measurement-unit catalog (SI + display + custom units).
DEFINE TABLE IF NOT EXISTS unit;
DEFINE FIELD IF NOT EXISTS symbol ON TABLE unit TYPE string;
DEFINE FIELD IF NOT EXISTS dimension ON TABLE unit TYPE string;
DEFINE FIELD IF NOT EXISTS canonical_unit_id ON TABLE unit TYPE option<string>;
DEFINE FIELD IF NOT EXISTS factor ON TABLE unit TYPE float;
DEFINE FIELD IF NOT EXISTS offset ON TABLE unit TYPE float;
DEFINE FIELD IF NOT EXISTS label ON TABLE unit TYPE object;
DEFINE FIELD IF NOT EXISTS system ON TABLE unit TYPE string;
DEFINE FIELD IF NOT EXISTS readonly ON TABLE unit TYPE bool;
DEFINE FIELD IF NOT EXISTS created_at ON TABLE unit TYPE datetime;
DEFINE FIELD IF NOT EXISTS updated_at ON TABLE unit TYPE datetime;
DEFINE INDEX IF NOT EXISTS idx_unit_symbol ON TABLE unit FIELDS symbol UNIQUE;
DEFINE INDEX IF NOT EXISTS idx_unit_dimension ON TABLE unit FIELDS dimension;

-- Users: technical table, separate from business objects.
DEFINE TABLE IF NOT EXISTS user;
DEFINE FIELD IF NOT EXISTS username ON TABLE user TYPE string;
DEFINE FIELD IF NOT EXISTS email ON TABLE user TYPE string;
DEFINE FIELD IF NOT EXISTS password_hash ON TABLE user TYPE string;
DEFINE FIELD IF NOT EXISTS auth_provider ON TABLE user TYPE string;
DEFINE FIELD IF NOT EXISTS external_sub ON TABLE user TYPE option<string>;
DEFINE FIELD IF NOT EXISTS roles ON TABLE user TYPE array;
DEFINE FIELD IF NOT EXISTS status ON TABLE user TYPE string;
DEFINE FIELD IF NOT EXISTS token_version ON TABLE user TYPE int;
DEFINE FIELD IF NOT EXISTS locale ON TABLE user TYPE string;
DEFINE FIELD IF NOT EXISTS created_at ON TABLE user TYPE datetime;
DEFINE FIELD IF NOT EXISTS updated_at ON TABLE user TYPE datetime;
DEFINE INDEX IF NOT EXISTS idx_user_username ON TABLE user FIELDS username UNIQUE;
DEFINE INDEX IF NOT EXISTS idx_user_email ON TABLE user FIELDS email UNIQUE;

-- Groups: RBAC group permissions and ABAC scopes.
DEFINE TABLE IF NOT EXISTS group;
DEFINE FIELD IF NOT EXISTS name ON TABLE group TYPE string;
DEFINE FIELD IF NOT EXISTS grant_permissions ON TABLE group TYPE array;
DEFINE FIELD IF NOT EXISTS deny_permissions ON TABLE group TYPE array;
DEFINE FIELD IF NOT EXISTS scope ON TABLE group TYPE object;
DEFINE FIELD IF NOT EXISTS branding ON TABLE group TYPE object;
DEFINE INDEX IF NOT EXISTS idx_group_name ON TABLE group FIELDS name UNIQUE;
"#;

/// Applies the schema idempotently.
pub async fn apply(db: &Db) -> Result<(), surrealdb::Error> {
    db.inner().query(SCHEMA).await?.check().map(|_| ())
}
