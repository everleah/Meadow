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
-- Binds an attribute to a classification standard's feature code, e.g.
-- `{ "etim": "EF000123" }`. The class-feature resolution reads the value from
-- the bound attribute, so an **own** attribute can feed a standard feature
-- without being renamed (`plan/detail/bmecat-export.md` §11.7).
DEFINE FIELD IF NOT EXISTS standard_codes ON TABLE attribute_definition TYPE option<object>;
-- Marks an attribute as **reference data** of a classification standard (set by
-- the standard import): it belongs to the standard's vocabulary, not to the
-- customer's own attribute set, and is hidden wherever attributes are *chosen*
-- (product form, export field selection, bulk maintenance) unless it already
-- carries a value. "Adopting" it in the attribute catalog clears the flag.
DEFINE FIELD IF NOT EXISTS reference_data ON TABLE attribute_definition TYPE bool;
-- Reference to a reusable enum_set (`enum_set:<key>`); replaces
-- `constraints.enum_values` for enum attributes (Phase 9, planned).
DEFINE FIELD IF NOT EXISTS enum_set_id ON TABLE attribute_definition TYPE option<string>;
DEFINE FIELD IF NOT EXISTS default_value ON TABLE attribute_definition;
DEFINE FIELD IF NOT EXISTS description ON TABLE attribute_definition TYPE option<string>;
DEFINE FIELD IF NOT EXISTS version ON TABLE attribute_definition TYPE int;
DEFINE FIELD OVERWRITE created_at ON TABLE attribute_definition TYPE datetime DEFAULT time::now();
DEFINE FIELD OVERWRITE updated_at ON TABLE attribute_definition TYPE datetime DEFAULT time::now();
DEFINE FIELD IF NOT EXISTS updated_by ON TABLE attribute_definition TYPE option<string>;
DEFINE INDEX IF NOT EXISTS idx_attr_def_name ON TABLE attribute_definition FIELDS name UNIQUE;
DEFINE INDEX IF NOT EXISTS idx_attr_def_group ON TABLE attribute_definition FIELDS group_ids;

-- Enum sets: reusable, multilingual option lists (Reference-Data style).
DEFINE TABLE IF NOT EXISTS enum_set;
DEFINE FIELD IF NOT EXISTS name ON TABLE enum_set TYPE string;
DEFINE FIELD IF NOT EXISTS label ON TABLE enum_set TYPE object;
DEFINE FIELD IF NOT EXISTS options ON TABLE enum_set TYPE array;
DEFINE FIELD IF NOT EXISTS position ON TABLE enum_set TYPE int;
DEFINE FIELD OVERWRITE created_at ON TABLE enum_set TYPE datetime DEFAULT time::now();
DEFINE FIELD OVERWRITE updated_at ON TABLE enum_set TYPE datetime DEFAULT time::now();
DEFINE INDEX IF NOT EXISTS idx_enum_set_name ON TABLE enum_set FIELDS name UNIQUE;

-- Enum options: a stable code + multilingual labels, referenced by enum sets.
DEFINE TABLE IF NOT EXISTS enum_option;
DEFINE FIELD IF NOT EXISTS code ON TABLE enum_option TYPE string;
DEFINE FIELD IF NOT EXISTS labels ON TABLE enum_option TYPE object;
DEFINE FIELD IF NOT EXISTS position ON TABLE enum_option TYPE int;
DEFINE FIELD IF NOT EXISTS active ON TABLE enum_option TYPE bool;
DEFINE FIELD OVERWRITE created_at ON TABLE enum_option TYPE datetime DEFAULT time::now();
DEFINE FIELD OVERWRITE updated_at ON TABLE enum_option TYPE datetime DEFAULT time::now();
DEFINE INDEX IF NOT EXISTS idx_enum_option_code ON TABLE enum_option FIELDS code UNIQUE;

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
DEFINE FIELD IF NOT EXISTS taxonomy ON TABLE object_type TYPE bool DEFAULT false;
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
DEFINE FIELD IF NOT EXISTS role ON TABLE relation_definition TYPE option<string>;
DEFINE FIELD IF NOT EXISTS source_types ON TABLE relation_definition TYPE array;
DEFINE FIELD IF NOT EXISTS target_types ON TABLE relation_definition TYPE array;
DEFINE FIELD IF NOT EXISTS label ON TABLE relation_definition TYPE object;
DEFINE FIELD IF NOT EXISTS attributes ON TABLE relation_definition TYPE array;
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

-- Channels: output channels (webshop, print catalog, marketplace) as a managed
-- configuration catalog. A channel is referenced by its stable `code` (not by
-- the record id) so that configuration documents (MCF) and filters stay
-- portable. See `plan/detail/workflow-freigaben.md`.
DEFINE TABLE IF NOT EXISTS channel;
DEFINE FIELD IF NOT EXISTS code ON TABLE channel TYPE string;
DEFINE FIELD IF NOT EXISTS label ON TABLE channel TYPE object;
DEFINE FIELD IF NOT EXISTS description ON TABLE channel TYPE option<string>;
DEFINE FIELD IF NOT EXISTS active ON TABLE channel TYPE bool DEFAULT true;
DEFINE FIELD IF NOT EXISTS position ON TABLE channel TYPE int;
DEFINE FIELD OVERWRITE created_at ON TABLE channel TYPE datetime DEFAULT time::now();
DEFINE FIELD OVERWRITE updated_at ON TABLE channel TYPE datetime DEFAULT time::now();
DEFINE FIELD IF NOT EXISTS updated_by ON TABLE channel TYPE option<string>;
DEFINE INDEX IF NOT EXISTS idx_channel_code ON TABLE channel FIELDS code UNIQUE;
DEFINE INDEX IF NOT EXISTS idx_channel_position ON TABLE channel FIELDS position;

-- Releases: the channel-specific approval state of an object. One row per
-- (object, channel) carries the channel state (`status`, same vocabulary as the
-- object status); the row's existence means "the channel is assessed for this
-- object", `status = 'published'` means "released for that channel".
-- `object_id` is a native record so the `?released_for=` filter is a subquery.
DEFINE TABLE IF NOT EXISTS release;
DEFINE FIELD IF NOT EXISTS object_id ON TABLE release TYPE record<object>;
DEFINE FIELD IF NOT EXISTS channel ON TABLE release TYPE string;
DEFINE FIELD IF NOT EXISTS status ON TABLE release TYPE string;
DEFINE FIELD IF NOT EXISTS released_at ON TABLE release TYPE option<datetime>;
DEFINE FIELD IF NOT EXISTS released_by ON TABLE release TYPE option<string>;
DEFINE FIELD OVERWRITE created_at ON TABLE release TYPE datetime DEFAULT time::now();
DEFINE FIELD OVERWRITE updated_at ON TABLE release TYPE datetime DEFAULT time::now();
DEFINE FIELD IF NOT EXISTS updated_by ON TABLE release TYPE option<string>;
DEFINE INDEX IF NOT EXISTS idx_release_object_channel ON TABLE release FIELDS object_id, channel UNIQUE;
DEFINE INDEX IF NOT EXISTS idx_release_channel_status ON TABLE release FIELDS channel, status;

-- Class features (Stage B): the attribute definitions a taxonomy node ("class")
-- requires or offers as a feature. One row per (node, attribute). `category_id`
-- is a native record so "which features does this class have?" is one indexed
-- query; `attribute` is the attribute definition's **name** (configuration stays
-- portable, like the channel `code`). `required` marks the mandatory features of
-- the class; features of an ancestor class apply to its descendants too.
-- See `plan/detail/kategorisierung.md` ("Klassifizierungsstandards").
DEFINE TABLE IF NOT EXISTS category_attribute;
DEFINE FIELD IF NOT EXISTS category_id ON TABLE category_attribute TYPE record<object>;
DEFINE FIELD IF NOT EXISTS attribute ON TABLE category_attribute TYPE string;
DEFINE FIELD IF NOT EXISTS required ON TABLE category_attribute TYPE bool DEFAULT false;
DEFINE FIELD IF NOT EXISTS position ON TABLE category_attribute TYPE int DEFAULT 0;
DEFINE FIELD OVERWRITE created_at ON TABLE category_attribute TYPE datetime DEFAULT time::now();
DEFINE FIELD OVERWRITE updated_at ON TABLE category_attribute TYPE datetime DEFAULT time::now();
DEFINE FIELD IF NOT EXISTS updated_by ON TABLE category_attribute TYPE option<string>;
DEFINE INDEX IF NOT EXISTS idx_category_attribute_unique ON TABLE category_attribute FIELDS category_id, attribute UNIQUE;
DEFINE INDEX IF NOT EXISTS idx_category_attribute_name ON TABLE category_attribute FIELDS attribute;

-- Classification standards: the registry of the external classification
-- standards (ETIM, ECLASS, …) this catalogue was loaded with. The class nodes,
-- their features and the feature attribute definitions arrive through a standard
-- bundle (MCF + MEF, `plan/detail/bmecat-export.md` §11.9); this table records
-- **which standard in which version, from which source, over which node type** —
-- the BMEcat export names `REFERENCE_FEATURE_SYSTEM_NAME` (`ETIM-10.0`) from
-- here instead of trusting a free-text profile field, and the MCF section
-- `classification_standards` makes the record portable. One row per
-- (code, version); several versions may be registered (update path), the newest
-- per code is `current`. See `plan/detail/kategorisierung.md`.
DEFINE TABLE IF NOT EXISTS classification_standard;
DEFINE FIELD IF NOT EXISTS code ON TABLE classification_standard TYPE string;
DEFINE FIELD IF NOT EXISTS version ON TABLE classification_standard TYPE string;
DEFINE FIELD IF NOT EXISTS name ON TABLE classification_standard TYPE option<string>;
DEFINE FIELD IF NOT EXISTS source ON TABLE classification_standard TYPE option<string>;
DEFINE FIELD IF NOT EXISTS node_type ON TABLE classification_standard TYPE option<string>;
DEFINE FIELD IF NOT EXISTS notes ON TABLE classification_standard TYPE option<string>;
DEFINE FIELD OVERWRITE created_at ON TABLE classification_standard TYPE datetime DEFAULT time::now();
DEFINE FIELD OVERWRITE updated_at ON TABLE classification_standard TYPE datetime DEFAULT time::now();
DEFINE FIELD IF NOT EXISTS updated_by ON TABLE classification_standard TYPE option<string>;
DEFINE INDEX IF NOT EXISTS idx_standard_code_version ON TABLE classification_standard FIELDS code, version UNIQUE;
DEFINE INDEX IF NOT EXISTS idx_standard_node_type ON TABLE classification_standard FIELDS node_type;

-- Users: technical table, separate from business objects.
DEFINE TABLE IF NOT EXISTS user;
DEFINE FIELD IF NOT EXISTS username ON TABLE user TYPE string;
DEFINE FIELD IF NOT EXISTS email ON TABLE user TYPE string;
DEFINE FIELD IF NOT EXISTS password_hash ON TABLE user TYPE string;
-- Set when the user changes their own password; stays empty for seeded/admin-set
-- passwords so the UI can hint that the password was never chosen by the user.
DEFINE FIELD IF NOT EXISTS password_changed_at ON TABLE user TYPE option<datetime>;
-- Whether the user must set their own password (banner + hint in the UI).
-- Absent/NONE counts as `true`: only an admin who vouches for the password
-- (unchecked box in /users) or the user's own change clears the requirement.
DEFINE FIELD IF NOT EXISTS must_change_password ON TABLE user TYPE bool DEFAULT true;
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
-- Document kind: `mef` (Meadow Exchange Format, default) or `mcf`
-- (Meadow Configuration Format). The worker dispatches on it.
DEFINE FIELD IF NOT EXISTS kind ON TABLE import_document TYPE option<string> DEFAULT "mef";
DEFINE FIELD OVERWRITE created_at ON TABLE import_document TYPE datetime DEFAULT time::now();
DEFINE FIELD IF NOT EXISTS created_by ON TABLE import_document TYPE option<string>;
DEFINE INDEX IF NOT EXISTS idx_import_document_created ON TABLE import_document FIELDS created_at;

-- Export profiles: the saved configurations of the export wizard
-- (`plan/detail/bmecat-export.md` §5). A profile is the `ExportRequest`
-- **without `source`** — format, field list, locales, options and the `bmecat`
-- block — so the *how* of an export survives a catalogue migration (the MCF
-- section `export_profiles` carries it, `konfiguration-export-import.md`). The
-- record key is derived from `name` (`Meadow BMEcat 1.2` → `meadow-bmecat-1-2`),
-- so a round trip into an empty catalogue rebuilds the same keys.
DEFINE TABLE IF NOT EXISTS export_profile;
DEFINE FIELD IF NOT EXISTS name ON TABLE export_profile TYPE string;
DEFINE FIELD IF NOT EXISTS format ON TABLE export_profile TYPE string;
DEFINE FIELD IF NOT EXISTS notes ON TABLE export_profile TYPE option<string>;
DEFINE FIELD IF NOT EXISTS parameters ON TABLE export_profile TYPE object DEFAULT {};
DEFINE FIELD OVERWRITE created_at ON TABLE export_profile TYPE datetime DEFAULT time::now();
DEFINE FIELD OVERWRITE updated_at ON TABLE export_profile TYPE datetime DEFAULT time::now();
DEFINE FIELD IF NOT EXISTS created_by ON TABLE export_profile TYPE option<string>;
DEFINE FIELD IF NOT EXISTS updated_by ON TABLE export_profile TYPE option<string>;
DEFINE INDEX IF NOT EXISTS idx_export_profile_name ON TABLE export_profile FIELDS name UNIQUE;
DEFINE INDEX IF NOT EXISTS idx_export_profile_format ON TABLE export_profile FIELDS format;

-- Full-text search: one shared analyzer plus an index over the flexible
-- `attributes` object. Per-field indexes can be added for hot fields later.
DEFINE ANALYZER IF NOT EXISTS meadow_std TOKENIZERS blank,class FILTERS lowercase;
DEFINE INDEX IF NOT EXISTS idx_object_attributes_fulltext ON TABLE object FIELDS attributes FULLTEXT ANALYZER meadow_std BM25;
"#;

/// Applies the schema idempotently.
pub async fn apply(db: &Db) -> Result<(), surrealdb::Error> {
    db.inner().query(SCHEMA).await?.check().map(|_| ())
}
