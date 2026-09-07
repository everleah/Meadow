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
DEFINE FIELD OVERWRITE created_at ON TABLE object TYPE datetime DEFAULT time::now();
DEFINE FIELD OVERWRITE updated_at ON TABLE object TYPE datetime DEFAULT time::now();
DEFINE FIELD IF NOT EXISTS updated_by ON TABLE object TYPE option<string>;
DEFINE FIELD IF NOT EXISTS source_id ON TABLE object TYPE option<string>;
DEFINE FIELD IF NOT EXISTS source ON TABLE object TYPE option<string>;
DEFINE INDEX IF NOT EXISTS idx_object_type ON TABLE object FIELDS type;
DEFINE INDEX IF NOT EXISTS idx_object_status ON TABLE object FIELDS status;
DEFINE INDEX IF NOT EXISTS idx_object_created_at ON TABLE object FIELDS created_at;
DEFINE INDEX IF NOT EXISTS idx_object_updated_at ON TABLE object FIELDS updated_at;
DEFINE INDEX IF NOT EXISTS idx_object_source ON TABLE object FIELDS source, source_id;

-- Attribute definitions: the metadata schema (product template per type).
DEFINE TABLE IF NOT EXISTS attribute_definition;
DEFINE FIELD IF NOT EXISTS name ON TABLE attribute_definition TYPE string;
DEFINE FIELD IF NOT EXISTS label ON TABLE attribute_definition TYPE object;
DEFINE FIELD IF NOT EXISTS type ON TABLE attribute_definition TYPE string;
-- `group_ids` is `option<array>` (not a bare `array`): records created before
-- group enforcement may lack the field; the code guards with `group_ids ?? []`.
DEFINE FIELD OVERWRITE group_ids ON TABLE attribute_definition TYPE option<array>;
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
DEFINE FIELD OVERWRITE created_at ON TABLE attribute_definition TYPE datetime DEFAULT time::now();
DEFINE FIELD OVERWRITE updated_at ON TABLE attribute_definition TYPE datetime DEFAULT time::now();
DEFINE FIELD IF NOT EXISTS updated_by ON TABLE attribute_definition TYPE option<string>;
DEFINE INDEX IF NOT EXISTS idx_attr_def_name ON TABLE attribute_definition FIELDS name UNIQUE;
DEFINE INDEX IF NOT EXISTS idx_attr_def_group ON TABLE attribute_definition FIELDS group_ids;

-- Attribute groups: UI grouping for attribute definitions.
DEFINE TABLE IF NOT EXISTS attribute_group;
DEFINE FIELD IF NOT EXISTS name ON TABLE attribute_group TYPE string;
DEFINE FIELD IF NOT EXISTS label ON TABLE attribute_group TYPE object;
DEFINE FIELD IF NOT EXISTS position ON TABLE attribute_group TYPE int;
DEFINE FIELD IF NOT EXISTS applicable_types ON TABLE attribute_group TYPE array;
DEFINE INDEX IF NOT EXISTS idx_attr_group_name ON TABLE attribute_group FIELDS name UNIQUE;
DEFINE INDEX IF NOT EXISTS idx_attr_group_position ON TABLE attribute_group FIELDS position;

-- Object types: the managed registry of business object types (STIBO STEP /
-- Akeneo family model). Every object belongs to exactly one type; a type may
-- declare `key_attributes` (the unique + mandatory business-key attribute names
-- for that type; empty = no key) and an optional `label_attribute` (the
-- attribute used as the display name). Attribute definitions associate with
-- types via their `applicable_types`/`required_types` arrays.
DEFINE TABLE IF NOT EXISTS object_type;
DEFINE FIELD IF NOT EXISTS name ON TABLE object_type TYPE string;
DEFINE FIELD IF NOT EXISTS label ON TABLE object_type TYPE object;
DEFINE FIELD IF NOT EXISTS description ON TABLE object_type TYPE option<string>;
DEFINE FIELD IF NOT EXISTS key_attributes ON TABLE object_type TYPE array;
DEFINE FIELD IF NOT EXISTS label_attribute ON TABLE object_type TYPE option<string>;
DEFINE FIELD IF NOT EXISTS system ON TABLE object_type TYPE bool DEFAULT false;
DEFINE FIELD OVERWRITE created_at ON TABLE object_type TYPE datetime DEFAULT time::now();
DEFINE FIELD OVERWRITE updated_at ON TABLE object_type TYPE datetime DEFAULT time::now();
DEFINE FIELD IF NOT EXISTS updated_by ON TABLE object_type TYPE option<string>;
DEFINE INDEX IF NOT EXISTS idx_object_type_name ON TABLE object_type FIELDS name UNIQUE;

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
DEFINE FIELD OVERWRITE created_at ON TABLE relation_definition TYPE datetime DEFAULT time::now();
DEFINE FIELD OVERWRITE updated_at ON TABLE relation_definition TYPE datetime DEFAULT time::now();
DEFINE INDEX IF NOT EXISTS idx_rel_def_name ON TABLE relation_definition FIELDS name UNIQUE;

-- Language tree: hierarchy for multilingual attribute resolution.
DEFINE TABLE IF NOT EXISTS language_tree;
DEFINE FIELD IF NOT EXISTS code ON TABLE language_tree TYPE string;
DEFINE FIELD IF NOT EXISTS parent ON TABLE language_tree TYPE option<string>;
DEFINE FIELD IF NOT EXISTS label ON TABLE language_tree TYPE object;
DEFINE FIELD IF NOT EXISTS fallback_order ON TABLE language_tree TYPE array;
DEFINE FIELD IF NOT EXISTS active ON TABLE language_tree TYPE bool DEFAULT true;
DEFINE FIELD IF NOT EXISTS is_ui ON TABLE language_tree TYPE bool DEFAULT false;
DEFINE INDEX IF NOT EXISTS idx_lang_code ON TABLE language_tree FIELDS code UNIQUE;
DEFINE INDEX IF NOT EXISTS idx_lang_parent ON TABLE language_tree FIELDS parent;

-- Business rules: Python scripts (validated in a later phase).
DEFINE TABLE IF NOT EXISTS business_rule;
DEFINE FIELD IF NOT EXISTS name ON TABLE business_rule TYPE string;
DEFINE FIELD IF NOT EXISTS description ON TABLE business_rule TYPE option<string>;
DEFINE FIELD IF NOT EXISTS type ON TABLE business_rule TYPE string;
DEFINE FIELD IF NOT EXISTS applies_to ON TABLE business_rule TYPE array;
DEFINE FIELD IF NOT EXISTS script ON TABLE business_rule TYPE string;
DEFINE FIELD IF NOT EXISTS state ON TABLE business_rule TYPE string;
DEFINE FIELD IF NOT EXISTS version ON TABLE business_rule TYPE int;
DEFINE FIELD IF NOT EXISTS failure_count ON TABLE business_rule TYPE int DEFAULT 0;
DEFINE FIELD OVERWRITE created_at ON TABLE business_rule TYPE datetime DEFAULT time::now();
DEFINE FIELD OVERWRITE updated_at ON TABLE business_rule TYPE datetime DEFAULT time::now();
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
DEFINE FIELD OVERWRITE created_at ON TABLE unit TYPE datetime DEFAULT time::now();
DEFINE FIELD OVERWRITE updated_at ON TABLE unit TYPE datetime DEFAULT time::now();
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
DEFINE FIELD OVERWRITE created_at ON TABLE user TYPE datetime DEFAULT time::now();
DEFINE FIELD OVERWRITE updated_at ON TABLE user TYPE datetime DEFAULT time::now();
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

-- Audit log: durable, domain-level change history (plan section 13).
-- `from`/`to` carry optional old/new values; the `timestamp` defaults to the
-- write time in UTC.
DEFINE TABLE IF NOT EXISTS audit_log;
DEFINE FIELD IF NOT EXISTS object_id ON TABLE audit_log TYPE option<string>;
DEFINE FIELD IF NOT EXISTS user_id ON TABLE audit_log TYPE option<string>;
DEFINE FIELD IF NOT EXISTS action ON TABLE audit_log TYPE string;
DEFINE FIELD IF NOT EXISTS metadata ON TABLE audit_log;
DEFINE FIELD OVERWRITE timestamp ON TABLE audit_log TYPE datetime DEFAULT time::now();
DEFINE INDEX IF NOT EXISTS idx_audit_object ON TABLE audit_log FIELDS object_id;
DEFINE INDEX IF NOT EXISTS idx_audit_user ON TABLE audit_log FIELDS user_id;
DEFINE INDEX IF NOT EXISTS idx_audit_action ON TABLE audit_log FIELDS action;
DEFINE INDEX IF NOT EXISTS idx_audit_timestamp ON TABLE audit_log FIELDS timestamp;

-- System logs: curated operational entries (append-only, flüchtig).
DEFINE TABLE IF NOT EXISTS log_entry;
DEFINE FIELD IF NOT EXISTS service ON TABLE log_entry TYPE string;
DEFINE FIELD IF NOT EXISTS level ON TABLE log_entry TYPE string;
DEFINE FIELD IF NOT EXISTS category ON TABLE log_entry TYPE string;
DEFINE FIELD IF NOT EXISTS message ON TABLE log_entry TYPE string;
DEFINE FIELD IF NOT EXISTS trace_id ON TABLE log_entry TYPE option<string>;
DEFINE FIELD IF NOT EXISTS context ON TABLE log_entry;
DEFINE FIELD OVERWRITE created_at ON TABLE log_entry TYPE datetime DEFAULT time::now();
DEFINE INDEX IF NOT EXISTS idx_log_service_level_created ON TABLE log_entry FIELDS service, level, created_at;
DEFINE INDEX IF NOT EXISTS idx_log_trace ON TABLE log_entry FIELDS trace_id;

-- System settings: persisted runtime configuration (Default < ENV < DB).
DEFINE TABLE IF NOT EXISTS system_setting;
DEFINE FIELD IF NOT EXISTS key ON TABLE system_setting TYPE string;
DEFINE FIELD IF NOT EXISTS value ON TABLE system_setting;
DEFINE FIELD IF NOT EXISTS updated_by ON TABLE system_setting TYPE option<string>;
DEFINE FIELD OVERWRITE updated_at ON TABLE system_setting TYPE datetime DEFAULT time::now();
DEFINE INDEX IF NOT EXISTS idx_system_setting_key ON TABLE system_setting FIELDS key UNIQUE;

-- Outbox for CRUD events (transactional outbox; Phase 6 relays to NATS
-- JetStream on `events.crud`). Events are written atomically with the CRUD
-- operation and picked up oldest-pending-first by the relay.
DEFINE TABLE IF NOT EXISTS outbox_event;
DEFINE FIELD IF NOT EXISTS event_type ON TABLE outbox_event TYPE string;
DEFINE FIELD IF NOT EXISTS object_type ON TABLE outbox_event TYPE string;
DEFINE FIELD IF NOT EXISTS object_id ON TABLE outbox_event TYPE string;
DEFINE FIELD IF NOT EXISTS changes ON TABLE outbox_event TYPE object;
DEFINE FIELD IF NOT EXISTS metadata ON TABLE outbox_event TYPE object;
DEFINE FIELD IF NOT EXISTS status ON TABLE outbox_event TYPE string;
DEFINE FIELD OVERWRITE created_at ON TABLE outbox_event TYPE datetime DEFAULT time::now();
DEFINE FIELD IF NOT EXISTS published_at ON TABLE outbox_event TYPE option<datetime>;
DEFINE INDEX IF NOT EXISTS idx_outbox_status_created ON TABLE outbox_event FIELDS status, created_at;

-- Jobs: definitions of recurring/manual tasks and their run history.
-- The scheduler (tokio-cron-scheduler) triggers `schedule` definitions; the
-- worker executes them and reports status back over REST.
DEFINE TABLE IF NOT EXISTS job_definition;
DEFINE FIELD IF NOT EXISTS name ON TABLE job_definition TYPE string;
DEFINE FIELD IF NOT EXISTS type ON TABLE job_definition TYPE string;
DEFINE FIELD IF NOT EXISTS schedule ON TABLE job_definition TYPE option<string>;
DEFINE FIELD IF NOT EXISTS parameters ON TABLE job_definition TYPE object;
DEFINE FIELD IF NOT EXISTS enabled ON TABLE job_definition TYPE bool;
DEFINE FIELD OVERWRITE created_at ON TABLE job_definition TYPE datetime DEFAULT time::now();
DEFINE FIELD OVERWRITE updated_at ON TABLE job_definition TYPE datetime DEFAULT time::now();
DEFINE FIELD IF NOT EXISTS updated_by ON TABLE job_definition TYPE option<string>;
DEFINE INDEX IF NOT EXISTS idx_job_def_name ON TABLE job_definition FIELDS name UNIQUE;
DEFINE INDEX IF NOT EXISTS idx_job_def_type ON TABLE job_definition FIELDS type;

DEFINE TABLE IF NOT EXISTS job_run;
DEFINE FIELD OVERWRITE job_definition_id ON TABLE job_run TYPE option<string>;
DEFINE FIELD IF NOT EXISTS trigger ON TABLE job_run TYPE string;
DEFINE FIELD IF NOT EXISTS status ON TABLE job_run TYPE string;
DEFINE FIELD IF NOT EXISTS attempt ON TABLE job_run TYPE int;
DEFINE FIELD IF NOT EXISTS started_at ON TABLE job_run TYPE option<datetime>;
DEFINE FIELD IF NOT EXISTS finished_at ON TABLE job_run TYPE option<datetime>;
DEFINE FIELD IF NOT EXISTS result ON TABLE job_run;
DEFINE FIELD IF NOT EXISTS error ON TABLE job_run TYPE option<string>;
DEFINE FIELD IF NOT EXISTS worker_id ON TABLE job_run TYPE option<string>;
DEFINE FIELD OVERWRITE created_at ON TABLE job_run TYPE datetime DEFAULT time::now();
DEFINE FIELD OVERWRITE updated_at ON TABLE job_run TYPE datetime DEFAULT time::now();
DEFINE INDEX IF NOT EXISTS idx_job_run_definition ON TABLE job_run FIELDS job_definition_id;
DEFINE INDEX IF NOT EXISTS idx_job_run_status ON TABLE job_run FIELDS status;
DEFINE INDEX IF NOT EXISTS idx_job_run_created ON TABLE job_run FIELDS created_at;

-- Connectors: persisted inbound/outbound connector configuration. Env
-- variables seed the initial value; the database is the durable source of
-- truth managed via the API/admin UI (Phase 6).
DEFINE TABLE IF NOT EXISTS connector;
DEFINE FIELD IF NOT EXISTS name ON TABLE connector TYPE string;
DEFINE FIELD IF NOT EXISTS type ON TABLE connector TYPE string;
DEFINE FIELD IF NOT EXISTS enabled ON TABLE connector TYPE bool;
DEFINE FIELD IF NOT EXISTS parameters ON TABLE connector TYPE object;
DEFINE FIELD OVERWRITE created_at ON TABLE connector TYPE datetime DEFAULT time::now();
DEFINE FIELD OVERWRITE updated_at ON TABLE connector TYPE datetime DEFAULT time::now();
DEFINE FIELD IF NOT EXISTS updated_by ON TABLE connector TYPE option<string>;
DEFINE INDEX IF NOT EXISTS idx_connector_name ON TABLE connector FIELDS name UNIQUE;
DEFINE INDEX IF NOT EXISTS idx_connector_type ON TABLE connector FIELDS type;

-- Import documents: raw MEF documents submitted via `POST /imports`. The
-- core stores the document and queues an `inbound_import` job; the worker
-- fetches it via `GET /imports/{document_id}` and performs the import.
DEFINE TABLE IF NOT EXISTS import_document;
DEFINE FIELD IF NOT EXISTS document ON TABLE import_document TYPE object;
DEFINE FIELD IF NOT EXISTS format_version ON TABLE import_document TYPE option<string>;
DEFINE FIELD IF NOT EXISTS source ON TABLE import_document TYPE option<string>;
DEFINE FIELD OVERWRITE created_at ON TABLE import_document TYPE datetime DEFAULT time::now();
DEFINE FIELD IF NOT EXISTS created_by ON TABLE import_document TYPE option<string>;
DEFINE INDEX IF NOT EXISTS idx_import_document_created ON TABLE import_document FIELDS created_at;

-- Full-text search: one shared analyzer plus an index over the flexible
-- `attributes` object. Per-field indexes can be added for hot fields later.
DEFINE ANALYZER IF NOT EXISTS meadow_std TOKENIZERS blank,class FILTERS lowercase;
DEFINE INDEX IF NOT EXISTS idx_object_attributes_fulltext ON TABLE object FIELDS attributes FULLTEXT ANALYZER meadow_std BM25;
"#;

/// Applies the schema idempotently.
pub async fn apply(db: &Db) -> Result<(), surrealdb::Error> {
    db.inner().query(SCHEMA).await?.check().map(|_| ())
}
