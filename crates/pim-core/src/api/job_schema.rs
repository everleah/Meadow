//! The parameter contract of a job definition (`job_definition.parameters`).
//!
//! **Why this module exists:** a job definition stores free-form JSON, and until
//! now only the *consumers* knew what that JSON has to contain — the worker
//! handlers (`meadow/worker/*.py`) and the core paths that queue the runs. The
//! admin form therefore offered a bare JSON textarea, and an AI assistant had no
//! source of truth to build a definition from. This module declares the contract
//! where the job types are already declared (`api::jobs::VALID_JOB_TYPES`), so
//! the form, the assistant and the review of an MCF document read the same list.
//!
//! **Coverage rule** (mirrors `connector_schema.rs`): every declared field is
//! read by a consumer and every read field is declared —
//! * producers in the core: `api::exports::create_export`, `api::imports`,
//!   `api::rules::{recompute_rule, load_inherited_computed_rules}`,
//!   `api::objects::bulk_objects`, `api::saved_searches`, `api::config`,
//!   `api::connectors::test_connector`
//! * consumers in the worker: `meadow/worker/{exports,imports,business_rules,
//!   bulk,collections,config_import,connector_test}.py`
//!
//! **Enforced on the write path:** `create_job_definition` /
//! `update_job_definition` reject a definition whose **declared** fields are
//! missing or malformed (`400`) — the same rule `POST /connectors/validate` and
//! `POST /export-profiles/validate` apply, only that here the write itself checks.
//! An **unknown** key is not a rejection: it is stored as-is and reported, because
//! a stored definition may carry a parameter a newer worker understands. The report
//! is the `notices` list of `POST /jobs/validate` and a `job`-category `WARN` log
//! entry. `POST /jobs/validate` therefore answers with the write path's own verdict
//! (`job_schema::problems_for_write`, the same function the write calls).

use serde::Deserialize;
use serde_json::{Value, json};
use utoipa::ToSchema;

use axum::Json;
use axum::extract::State;
use axum::http::HeaderMap;

use crate::app::AppState;
use crate::error::ApiError;
use crate::scheduler;

/// The shape of one parameter value.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Shape {
    /// A string.
    Text,
    /// A number (`integer` for whole numbers).
    Number,
    Integer,
    /// A boolean.
    Flag,
    /// An array of strings.
    TextList,
    /// A free-form object.
    Object,
}

impl Shape {
    /// The name used in the schema document and in the error messages.
    fn as_str(self) -> &'static str {
        match self {
            Shape::Text => "string",
            Shape::Number => "number",
            Shape::Integer => "integer",
            Shape::Flag => "boolean",
            Shape::TextList => "string_list",
            Shape::Object => "object",
        }
    }

    /// Whether a JSON value matches this shape.
    fn accepts(self, value: &Value) -> bool {
        match self {
            Shape::Text => value.is_string(),
            Shape::Number => value.is_number(),
            Shape::Integer => value.is_i64() || value.is_u64(),
            Shape::Flag => value.is_boolean(),
            Shape::TextList => value
                .as_array()
                .is_some_and(|items| items.iter().all(Value::is_string)),
            // `null` is not an object: a missing key is the way to leave it out.
            Shape::Object => value.is_object(),
        }
    }
}

/// One parameter of a job type (or of one bulk operation).
#[derive(Debug, Clone, Copy)]
pub struct Parameter {
    /// Key inside `parameters` (top level, not dotted).
    pub key: &'static str,
    pub shape: Shape,
    /// Whether a run fails without it.
    pub required: bool,
    /// One sentence for the form and the prompt.
    pub description: &'static str,
    /// Allowed values, when the field is closed.
    pub allowed: &'static [&'static str],
}

impl Parameter {
    fn to_json(self) -> Value {
        let mut entry = json!({
            "key": self.key,
            "shape": self.shape.as_str(),
            "required": self.required,
            "description": self.description,
        });
        if !self.allowed.is_empty() {
            entry["allowed"] = json!(self.allowed);
        }
        entry
    }
}

/// One bulk operation with its own parameters (`parameters.parameters`).
#[derive(Debug, Clone, Copy)]
pub struct Operation {
    /// Value of `parameters.op`.
    pub op: &'static str,
    /// What the operation does.
    pub description: &'static str,
    /// Keys this operation reads inside `parameters.parameters`.
    pub parameters: &'static [Parameter],
}

/// One job type with its parameters.
#[derive(Debug, Clone, Copy)]
pub struct JobTypeSpec {
    /// `job_definition.type` (mirrors `VALID_JOB_TYPES`).
    pub job_type: &'static str,
    /// What a run of this type does.
    pub description: &'static str,
    /// Keys this type reads inside `parameters`.
    pub parameters: &'static [Parameter],
    /// Sub-parameters of `bulk_operation` (empty for every other type).
    pub operations: &'static [Operation],
}

const SEND_FOR_APPROVAL: Parameter = Parameter {
    key: "send_for_approval",
    shape: Shape::Flag,
    required: false,
    description: "Push the selection through the approval workflow after the operation",
    allowed: &[],
};

const BULK_OPERATIONS: &[Operation] = &[
    Operation {
        op: "set_attribute",
        description: "Set one attribute on every selected object",
        parameters: &[
            Parameter {
                key: "attribute",
                shape: Shape::Text,
                required: true,
                description: "Attribute name",
                allowed: &[],
            },
            Parameter {
                key: "value",
                shape: Shape::Object,
                required: false,
                description: "New value (a string, number, array or language map)",
                allowed: &[],
            },
        ],
    },
    Operation {
        op: "clear_attribute",
        description: "Clear one attribute on every selected object",
        parameters: &[Parameter {
            key: "attribute",
            shape: Shape::Text,
            required: true,
            description: "Attribute name",
            allowed: &[],
        }],
    },
    Operation {
        op: "replace_text",
        description: "Find & replace inside one attribute (or all text attributes)",
        parameters: &[
            Parameter {
                key: "search",
                shape: Shape::Text,
                required: true,
                description: "Literal text to find",
                allowed: &[],
            },
            Parameter {
                key: "replace",
                shape: Shape::Text,
                required: false,
                description: "Replacement (empty deletes the match)",
                allowed: &[],
            },
            Parameter {
                key: "case_sensitive",
                shape: Shape::Flag,
                required: false,
                description: "Match case (default: off)",
                allowed: &[],
            },
            Parameter {
                key: "attribute",
                shape: Shape::Text,
                required: false,
                description: "Limit the replacement to one attribute",
                allowed: &[],
            },
        ],
    },
    Operation {
        op: "add_relation",
        description: "Create one relation from every selected object to a target",
        parameters: &[
            Parameter {
                key: "definition_id",
                shape: Shape::Text,
                required: true,
                description: "Relation definition (`relation_definition:<key>`)",
                allowed: &[],
            },
            Parameter {
                key: "target",
                shape: Shape::Text,
                required: true,
                description: "Target object id (`object:<key>`)",
                allowed: &[],
            },
            Parameter {
                key: "role",
                shape: Shape::Text,
                required: false,
                description: "Edge role, when the definition allows several",
                allowed: &[],
            },
        ],
    },
    Operation {
        op: "remove_relation",
        description: "Delete the relation from every selected object to a target",
        parameters: &[
            Parameter {
                key: "definition_id",
                shape: Shape::Text,
                required: true,
                description: "Relation definition (`relation_definition:<key>`)",
                allowed: &[],
            },
            Parameter {
                key: "target",
                shape: Shape::Text,
                required: true,
                description: "Target object id (`object:<key>`)",
                allowed: &[],
            },
        ],
    },
    Operation {
        op: "change_status",
        description: "Set the global object status",
        parameters: &[Parameter {
            key: "status",
            shape: Shape::Text,
            required: true,
            description: "Target status (`draft`, `in_review`, `approved`, `published`, …)",
            allowed: &[],
        }],
    },
    Operation {
        op: "send_for_approval",
        description: "Move the selection into the review state",
        parameters: &[Parameter {
            key: "approval_status",
            shape: Shape::Text,
            required: false,
            description: "Review state to enter (default `in_review`)",
            allowed: &[],
        }],
    },
    Operation {
        op: "add_category",
        description: "Assign a taxonomy node (staffelbar over `definition_id`)",
        parameters: &[
            Parameter {
                key: "category",
                shape: Shape::Text,
                required: true,
                description: "Taxonomy node id (`object:<key>`)",
                allowed: &[],
            },
            Parameter {
                key: "definition_id",
                shape: Shape::Text,
                required: false,
                description: "Categorisation role (default: the primary category relation)",
                allowed: &[],
            },
        ],
    },
    Operation {
        op: "remove_category",
        description: "Remove a taxonomy node assignment",
        parameters: &[
            Parameter {
                key: "category",
                shape: Shape::Text,
                required: true,
                description: "Taxonomy node id (`object:<key>`)",
                allowed: &[],
            },
            Parameter {
                key: "definition_id",
                shape: Shape::Text,
                required: false,
                description: "Categorisation role (default: the primary category relation)",
                allowed: &[],
            },
        ],
    },
    Operation {
        op: "change_parent",
        description: "Move the selection under another composition parent",
        parameters: &[Parameter {
            key: "parent",
            shape: Shape::Text,
            required: true,
            description: "New parent object id (`object:<key>`)",
            allowed: &[],
        }],
    },
    Operation {
        op: "delete",
        description: "Delete the selected objects (their releases go with them)",
        parameters: &[],
    },
    Operation {
        op: "release_for_channel",
        description: "Release the selection for one channel",
        parameters: &[
            Parameter {
                key: "channel",
                shape: Shape::Text,
                required: true,
                description: "Channel code",
                allowed: &[],
            },
            Parameter {
                key: "release_status",
                shape: Shape::Text,
                required: false,
                description: "Channel state to set (default `published`)",
                allowed: &[],
            },
        ],
    },
    Operation {
        op: "withdraw_from_channel",
        description: "Withdraw the selection from one channel",
        parameters: &[Parameter {
            key: "channel",
            shape: Shape::Text,
            required: true,
            description: "Channel code",
            allowed: &[],
        }],
    },
    Operation {
        op: "consolidate_inheritance",
        description: "Promote values all children agree on to the parent",
        parameters: &[
            Parameter {
                key: "dry_run",
                shape: Shape::Flag,
                required: false,
                description: "Only report what would move",
                allowed: &[],
            },
            Parameter {
                key: "min_children",
                shape: Shape::Integer,
                required: false,
                description: "Minimum number of agreeing children (default 2)",
                allowed: &[],
            },
            Parameter {
                key: "attributes",
                shape: Shape::TextList,
                required: false,
                description: "Limit the consolidation to these attributes",
                allowed: &[],
            },
        ],
    },
];

/// Every job type with its parameters — the contract document behind
/// `GET /jobs/schema` and the AI prompt for a job definition.
pub fn specs() -> Vec<JobTypeSpec> {
    vec![
        JobTypeSpec {
            job_type: "outbound_export",
            description: "Export objects; either structured (`source`) or a legacy \
                          type/status filter",
            parameters: &[
                Parameter {
                    key: "source",
                    shape: Shape::Object,
                    required: false,
                    description: "Structured selection: `kind` (`all`, `selection`, \
                                  `saved_search`, `collection`, `category`), plus `id`, \
                                  `object_ids`, `object_type`, `category`, `channel`, \
                                  `release_status` — or `filter`/{`limit`} for a query",
                    allowed: &[],
                },
                Parameter {
                    key: "format",
                    shape: Shape::Text,
                    required: false,
                    description: "Export format (default `json`)",
                    allowed: &["csv", "xlsx", "json", "bmecat"],
                },
                Parameter {
                    key: "fields",
                    shape: Shape::TextList,
                    required: false,
                    description: "Attribute names to export; empty means the object's own",
                    allowed: &[],
                },
                Parameter {
                    key: "locales",
                    shape: Shape::TextList,
                    required: false,
                    description: "Language codes to resolve; empty means the working language",
                    allowed: &[],
                },
                Parameter {
                    key: "options",
                    shape: Shape::Object,
                    required: false,
                    description: "Format switches (`header`, `separator`, `resolution`, \
                                  `include_ancestors`, `resolve_inheritance`, `email_to`, \
                                  `include_system`)",
                    allowed: &[],
                },
                Parameter {
                    key: "bmecat",
                    shape: Shape::Object,
                    required: false,
                    description: "BMEcat dialect and catalog header (`version`, \
                                  `schema_variant`, `catalog_id`, `supplier`, …)",
                    allowed: &[],
                },
                Parameter {
                    key: "filter",
                    shape: Shape::Object,
                    required: false,
                    description: "Legacy simple export: `{type, status}` (no `source`)",
                    allowed: &[],
                },
                Parameter {
                    key: "limit",
                    shape: Shape::Integer,
                    required: false,
                    description: "Legacy simple export: object limit (max 1000)",
                    allowed: &[],
                },
            ],
            operations: &[],
        },
        JobTypeSpec {
            job_type: "inbound_import",
            description: "Import a stored MEF document (parents first)",
            parameters: &[
                Parameter {
                    key: "document_id",
                    shape: Shape::Text,
                    required: true,
                    description: "Stored import document (`import_document:<key>`)",
                    allowed: &[],
                },
                Parameter {
                    key: "match_by_key",
                    shape: Shape::Text,
                    required: false,
                    description: "Attribute name to match existing objects by (upsert)",
                    allowed: &[],
                },
                Parameter {
                    key: "match_by_id",
                    shape: Shape::Flag,
                    required: false,
                    description: "Match by the document's object ids (default: off)",
                    allowed: &[],
                },
                Parameter {
                    key: "emit_events",
                    shape: Shape::Flag,
                    required: false,
                    description: "Emit CRUD events for the writes (default: on)",
                    allowed: &[],
                },
                Parameter {
                    key: "source",
                    shape: Shape::Text,
                    required: false,
                    description: "Source marker written on the imported objects",
                    allowed: &[],
                },
            ],
            operations: &[],
        },
        JobTypeSpec {
            job_type: "business_rule",
            description: "Run one rule over a set of objects (recompute/computed cascade)",
            parameters: &[
                Parameter {
                    key: "rule_id",
                    shape: Shape::Text,
                    required: false,
                    description: "Rule to load from the registry (`business_rule:<key>`)",
                    allowed: &[],
                },
                Parameter {
                    key: "rule",
                    shape: Shape::Object,
                    required: false,
                    description: "Inline rule (`{name, type, script}`) instead of `rule_id`",
                    allowed: &[],
                },
                Parameter {
                    key: "applies_to",
                    shape: Shape::TextList,
                    required: false,
                    description: "Object types the inline rule applies to",
                    allowed: &[],
                },
                Parameter {
                    key: "object_ids",
                    shape: Shape::TextList,
                    required: true,
                    description: "Objects to evaluate (`object:<key>` each); the selection comes \
                                  from the object list and may stay empty here",
                    allowed: &[],
                },
                Parameter {
                    key: "locale",
                    shape: Shape::Text,
                    required: false,
                    description: "Working language of the evaluation",
                    allowed: &[],
                },
            ],
            operations: &[],
        },
        JobTypeSpec {
            job_type: "bulk_operation",
            description: "Run one bulk operation over a selection (`op` plus its parameters)",
            parameters: &[
                Parameter {
                    key: "op",
                    shape: Shape::Text,
                    required: true,
                    description: "Operation to run (see `operations`)",
                    allowed: &[],
                },
                Parameter {
                    key: "object_ids",
                    shape: Shape::TextList,
                    required: true,
                    description: "Objects to change (`object:<key>` each); an interactive run \
                                  builds this from the object list, so a stored definition may \
                                  keep it empty",
                    allowed: &[],
                },
                Parameter {
                    key: "parameters",
                    shape: Shape::Object,
                    required: false,
                    description: "Parameters of the chosen `op` (see `operations`)",
                    allowed: &[],
                },
                SEND_FOR_APPROVAL,
            ],
            operations: BULK_OPERATIONS,
        },
        JobTypeSpec {
            job_type: "collection_refresh",
            description: "Recompute the members of one collection from its filter",
            parameters: &[Parameter {
                key: "collection_id",
                shape: Shape::Text,
                required: true,
                description: "Collection (`object:<key>`)",
                allowed: &[],
            }],
            operations: &[],
        },
        JobTypeSpec {
            job_type: "config_import",
            description: "Import a stored MCF document (model configuration)",
            parameters: &[
                Parameter {
                    key: "document_id",
                    shape: Shape::Text,
                    required: true,
                    description: "Stored MCF document (`config_document:<key>`)",
                    allowed: &[],
                },
                Parameter {
                    key: "mode",
                    shape: Shape::Text,
                    required: false,
                    description: "How the sections are applied",
                    allowed: &["merge", "replace"],
                },
            ],
            operations: &[],
        },
        JobTypeSpec {
            job_type: "connector_test",
            description: "Run one connector once and report its result (`POST /connectors/{id}/test`)",
            parameters: &[
                Parameter {
                    key: "connector_type",
                    shape: Shape::Text,
                    required: true,
                    description: "Connector type whose schema the parameters follow",
                    allowed: &[],
                },
                Parameter {
                    key: "parameters",
                    shape: Shape::Object,
                    required: true,
                    description: "The connector's parameters (see `GET /connector-types`)",
                    allowed: &[],
                },
                Parameter {
                    key: "connector_id",
                    shape: Shape::Text,
                    required: false,
                    description: "Saved connector this test came from",
                    allowed: &[],
                },
                Parameter {
                    key: "name",
                    shape: Shape::Text,
                    required: false,
                    description: "Connector name (for the report)",
                    allowed: &[],
                },
            ],
            operations: &[],
        },
    ]
}

/// The schema document (`GET /jobs/schema`).
pub fn schema_document() -> Value {
    let types: Vec<Value> = specs()
        .into_iter()
        .map(|spec| {
            let mut entry = json!({
                "type": spec.job_type,
                "description": spec.description,
                "parameters": spec
                    .parameters
                    .iter()
                    .map(|parameter| parameter.to_json())
                    .collect::<Vec<Value>>(),
            });
            if !spec.operations.is_empty() {
                entry["operations"] = json!(
                    spec.operations
                        .iter()
                        .map(|operation| {
                            json!({
                                "op": operation.op,
                                "description": operation.description,
                                "parameters": operation
                                    .parameters
                                    .iter()
                                    .map(|parameter| parameter.to_json())
                                    .collect::<Vec<Value>>(),
                            })
                        })
                        .collect::<Vec<Value>>()
                );
            }
            entry
        })
        .collect();
    json!({
        "job_types": types,
        "schedule": "Cron expression with six fields (`second minute hour day-of-month \
                     month day-of-week`); empty means \"only on demand\"",
    })
}

/// Allowed values of a field, for a closed enum (empty when the field is open).
fn allowed_of(parameters: &[Parameter], key: &str) -> &'static [&'static str] {
    parameters
        .iter()
        .find(|parameter| parameter.key == key)
        .map(|parameter| parameter.allowed)
        .unwrap_or(&[])
}

/// What a contract problem means for the write path.
///
/// A **declared** field that is missing or malformed leaves the definition
/// unrunnable, so the write path rejects it. A key the contract does not know is
/// kept: the definition may carry parameters a newer worker understands.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Severity {
    Error,
    Notice,
}

/// One contract problem, tagged with what the write path does about it.
struct Problem {
    severity: Severity,
    message: String,
}

impl Problem {
    fn error(message: String) -> Self {
        Self {
            severity: Severity::Error,
            message,
        }
    }

    fn notice(message: String) -> Self {
        Self {
            severity: Severity::Notice,
            message,
        }
    }
}

/// Splits the contract problems into blocking errors and reports.
///
/// This is the write path's check (`POST`/`PATCH /jobs`): a non-empty error list
/// means `400`, while the notices are stored and reported. `POST /jobs/validate`
/// answers with exactly these two lists, so its verdict is the save's verdict.
pub fn problems_for_write(job_type: &str, parameters: &Value) -> (Vec<String>, Vec<String>) {
    let mut errors: Vec<String> = Vec::new();
    let mut notices: Vec<String> = Vec::new();
    for problem in check(job_type, parameters) {
        match problem.severity {
            Severity::Error => errors.push(problem.message),
            Severity::Notice => notices.push(problem.message),
        }
    }
    (errors, notices)
}

fn check(job_type: &str, parameters: &Value) -> Vec<Problem> {
    let Some(spec) = specs().into_iter().find(|spec| spec.job_type == job_type) else {
        return vec![Problem::error(format!("unknown job type `{job_type}`"))];
    };
    let object = match parameters {
        Value::Null => {
            let missing: Vec<&str> = spec
                .parameters
                .iter()
                .filter(|parameter| parameter.required)
                .map(|parameter| parameter.key)
                .collect();
            return if missing.is_empty() {
                Vec::new()
            } else {
                vec![Problem::error(format!(
                    "`parameters` must be an object with {}",
                    missing
                        .iter()
                        .map(|key| format!("`{key}`"))
                        .collect::<Vec<String>>()
                        .join(", ")
                ))]
            };
        }
        Value::Object(map) => map,
        other => {
            return vec![Problem::error(format!(
                "`parameters` must be an object, got {}",
                if other.is_array() {
                    "an array"
                } else if other.is_string() {
                    "a string"
                } else {
                    "a scalar"
                }
            ))];
        }
    };

    let mut problems: Vec<Problem> = Vec::new();
    for key in object.keys() {
        if !spec.parameters.iter().any(|parameter| parameter.key == key) {
            let known: Vec<&str> = spec
                .parameters
                .iter()
                .map(|parameter| parameter.key)
                .collect();
            problems.push(Problem::notice(format!(
                "unknown parameter `{key}` for `{job_type}` (known: {})",
                known.join(", ")
            )));
        }
    }
    for parameter in spec.parameters {
        match object.get(parameter.key) {
            None | Some(Value::Null) => {
                if parameter.required {
                    problems.push(Problem::error(format!(
                        "`parameters.{}` is required",
                        parameter.key
                    )));
                }
            }
            Some(value) if !parameter.shape.accepts(value) => {
                problems.push(Problem::error(format!(
                    "`parameters.{}` must be a {}",
                    parameter.key,
                    parameter.shape.as_str()
                )));
            }
            Some(value) => {
                let allowed = parameter.allowed;
                if !allowed.is_empty()
                    && value.as_str().is_some_and(|text| !allowed.contains(&text))
                {
                    problems.push(Problem::error(format!(
                        "`parameters.{}` must be one of {}",
                        parameter.key,
                        allowed.join(", ")
                    )));
                }
            }
        }
    }

    // `bulk_operation` names its operation in `op` and puts the operation's own
    // parameters next to it under `parameters.parameters`.
    if job_type == "bulk_operation"
        && let Some(op) = object.get("op").and_then(Value::as_str)
    {
        match BULK_OPERATIONS.iter().find(|operation| operation.op == op) {
            None => {
                let known: Vec<&str> = BULK_OPERATIONS.iter().map(|entry| entry.op).collect();
                problems.push(Problem::error(format!(
                    "unknown `parameters.op` `{op}` (known: {})",
                    known.join(", ")
                )));
            }
            Some(operation) => {
                let inner = object
                    .get("parameters")
                    .and_then(Value::as_object)
                    .cloned()
                    .unwrap_or_default();
                for key in inner.keys() {
                    let known = operation
                        .parameters
                        .iter()
                        .any(|parameter| parameter.key == key)
                        || (key == "send_for_approval"
                            && allowed_of(&[SEND_FOR_APPROVAL], key).is_empty());
                    if !known {
                        problems.push(Problem::notice(format!(
                            "unknown parameter `parameters.{key}` for `{op}`"
                        )));
                    }
                }
                for parameter in operation.parameters {
                    match inner.get(parameter.key) {
                        None | Some(Value::Null) => {
                            if parameter.required {
                                problems.push(Problem::error(format!(
                                    "`parameters.parameters.{}` is required for `{op}`",
                                    parameter.key
                                )));
                            }
                        }
                        Some(value) if !parameter.shape.accepts(value) => {
                            problems.push(Problem::error(format!(
                                "`parameters.parameters.{}` must be a {}",
                                parameter.key,
                                parameter.shape.as_str()
                            )));
                        }
                        Some(_) => {}
                    }
                }
            }
        }
    }

    problems
}

/// Payload for `POST /jobs/validate`: a definition to check without saving.
#[derive(Debug, Deserialize, ToSchema)]
pub struct ValidateJobDefinition {
    /// `job_definition.type` (`outbound_export`, `bulk_operation`, …).
    #[serde(rename = "type")]
    pub job_type: String,
    /// Cron expression (6 fields, seconds first); omitted = manual only.
    #[serde(default)]
    pub schedule: Option<String>,
    /// The `parameters` object of the definition.
    #[serde(default)]
    pub parameters: Value,
}

/// The parameter contract of every job type — read-only metadata.
///
/// The contract is derived from the producers and the worker consumers, so a
/// caller (the job form, the AI assistant) can build a definition without
/// guessing. Read-only, so `editor` (and the worker's API key) may read it.
#[utoipa::path(
    get,
    path = "/jobs/schema",
    responses(
        (status = 200, description = "Job types with their parameters", body = Value),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Forbidden", body = Value)
    )
)]
pub async fn job_schema(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    crate::auth::require_permission(&state, &headers, "job.read")?;
    Ok(Json(schema_document()))
}

/// Checks a definition against the contract — **without saving** (editor or API key).
///
/// **The write path's own check:** it calls `problems_for_write`, the function
/// `POST`/`PATCH /jobs` check with, and the same cron parser the scheduler registers
/// with. `ok` is therefore the save's verdict, and `notices` names the keys the
/// contract does not know — the write keeps them (see the module docs).
#[utoipa::path(
    post,
    path = "/jobs/validate",
    request_body = ValidateJobDefinition,
    responses(
        (status = 200, description = "Validation result", body = Value),
        (status = 400, description = "Invalid payload", body = Value),
        (status = 401, description = "Unauthorized", body = Value),
        (status = 403, description = "Forbidden", body = Value)
    )
)]
pub async fn validate_job_definition(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<ValidateJobDefinition>,
) -> Result<Json<Value>, ApiError> {
    crate::auth::require_permission(&state, &headers, "job.read")?;
    let (mut errors, notices) = problems_for_write(&body.job_type, &body.parameters);
    // An empty schedule means "only on demand" — the same as omitting it.
    if let Some(schedule) = body.schedule.as_deref().map(str::trim)
        && !schedule.is_empty()
        && let Err(error) = scheduler::validate_schedule(schedule)
    {
        errors.push(error.message().to_owned());
    }
    Ok(Json(json!({
        "ok": errors.is_empty(),
        "errors": errors,
        "notices": notices,
    })))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Every message a caller shows a human or a model — errors and notices.
    ///
    /// The write path splits them (`problems_for_write`); the tests assert against
    /// this combined list the way the older single-list check did.
    fn messages(job_type: &str, parameters: &Value) -> Vec<String> {
        let (mut errors, notices) = problems_for_write(job_type, parameters);
        errors.extend(notices);
        errors
    }

    #[test]
    fn a_broken_declared_field_blocks_the_save() {
        // The write path's errors: a required declaration that is missing.
        let (errors, notices) = problems_for_write("collection_refresh", &json!({}));
        assert!(notices.is_empty(), "{notices:?}");
        assert!(
            errors
                .iter()
                .any(|error| error == "`parameters.collection_id` is required"),
            "{errors:?}"
        );
    }

    #[test]
    fn an_unknown_key_is_a_notice_not_an_error() {
        // … an unknown key is not: the write keeps it and reports it.
        let (errors, notices) = problems_for_write(
            "collection_refresh",
            &json!({ "collection_id": "collection:abc", "nope": 1 }),
        );
        assert!(errors.is_empty(), "{errors:?}");
        assert_eq!(notices.len(), 1, "{notices:?}");
        assert!(notices[0].starts_with("unknown parameter `nope`"));
    }

    #[test]
    fn an_unknown_bulk_operation_blocks_the_save() {
        // The operation name decides what runs, so it is a declared field.
        let (errors, notices) = problems_for_write(
            "bulk_operation",
            &json!({ "op": "frobnicate", "object_ids": ["object:1"] }),
        );
        assert!(
            errors
                .iter()
                .any(|error| error.starts_with("unknown `parameters.op`")),
            "{errors:?}"
        );
        assert!(notices.is_empty(), "{notices:?}");
    }

    #[test]
    fn an_unknown_bulk_parameter_is_a_notice() {
        let (errors, notices) = problems_for_write(
            "bulk_operation",
            &json!({
                "op": "change_status",
                "object_ids": [],
                "parameters": { "status": "published", "nope": 2 },
            }),
        );
        assert!(errors.is_empty(), "{errors:?}");
        assert_eq!(
            notices,
            vec!["unknown parameter `parameters.nope` for `change_status`".to_owned()]
        );
    }

    #[test]
    fn every_declared_type_is_a_known_job_type() {
        // The schema must not invent a type the queue would reject.
        for spec in specs() {
            assert!(
                crate::api::jobs::is_known_job_type(spec.job_type),
                "`{}` is not a known job type",
                spec.job_type
            );
            assert!(!spec.description.is_empty());
            let mut keys: Vec<&str> = spec
                .parameters
                .iter()
                .map(|parameter| parameter.key)
                .collect();
            for parameter in spec.parameters {
                assert!(!parameter.description.is_empty(), "{}", parameter.key);
            }
            let declared = keys.len();
            keys.sort_unstable();
            keys.dedup();
            assert_eq!(declared, keys.len(), "duplicate key in `{}`", spec.job_type);
            for operation in spec.operations {
                assert!(!operation.description.is_empty(), "{}", operation.op);
            }
        }
    }

    #[test]
    fn the_contract_covers_every_job_type_without_gaps() {
        // One entry per type the queue accepts: a type without a contract would
        // leave the form and the assistant guessing again.
        let types: Vec<&str> = specs().iter().map(|spec| spec.job_type).collect();
        for job_type in crate::api::jobs::VALID_JOB_TYPES {
            assert!(types.contains(&job_type), "`{job_type}` has no contract");
        }
    }

    #[test]
    fn the_document_carries_the_operations_of_bulk_operation() {
        let document = schema_document();
        let types = document["job_types"].as_array().expect("job_types");
        assert_eq!(types.len(), specs().len());
        let bulk = types
            .iter()
            .find(|entry| entry["type"] == "bulk_operation")
            .expect("bulk_operation entry");
        let operations = bulk["operations"].as_array().expect("operations");
        assert!(operations.len() >= 14);
        let set_attribute = operations
            .iter()
            .find(|entry| entry["op"] == "set_attribute")
            .expect("set_attribute");
        let keys: Vec<&str> = set_attribute["parameters"]
            .as_array()
            .expect("parameters")
            .iter()
            .filter_map(|entry| entry["key"].as_str())
            .collect();
        assert_eq!(keys, vec!["attribute", "value"]);
        // The schedule field explains the six-field cron the scheduler needs.
        assert!(
            document["schedule"]
                .as_str()
                .unwrap_or_default()
                .contains("six")
        );
    }

    #[test]
    fn validation_reports_unknown_missing_and_mistyped_fields() {
        let errors = messages(
            "inbound_import",
            &json!({ "document_id": 42, "nope": true, "match_by_id": "yes" }),
        );
        assert!(
            errors
                .iter()
                .any(|error| error.contains("unknown parameter `nope`"))
        );
        assert!(
            errors
                .iter()
                .any(|error| error.contains("`parameters.document_id` must be a string"))
        );
        assert!(
            errors
                .iter()
                .any(|error| error.contains("`parameters.match_by_id` must be a boolean"))
        );

        assert!(
            messages(
                "inbound_import",
                &json!({ "document_id": "import_document:1" })
            )
            .is_empty()
        );
        assert!(
            messages("inbound_import", &json!({}))
                .iter()
                .any(|error| error.contains("`parameters.document_id` is required"))
        );
        // `null` counts as missing, not as a value.
        assert!(
            messages("inbound_import", &json!({ "document_id": Value::Null }))
                .iter()
                .any(|error| error.contains("is required"))
        );
        assert!(
            messages("nope", &json!({}))
                .iter()
                .any(|e| e.contains("unknown job type"))
        );
        // No parameters at all is fine for a type without required fields.
        assert!(messages("outbound_export", &Value::Null).is_empty());
        assert!(
            messages(
                "config_import",
                &json!({ "document_id": "d", "mode": "nope" })
            )
            .iter()
            .any(|error| error.contains("must be one of merge, replace"))
        );
    }

    #[test]
    fn a_bulk_operation_is_checked_against_its_own_parameter_set() {
        let good = json!({
            "op": "replace_text",
            "object_ids": ["object:1"],
            "parameters": { "search": "alt", "replace": "neu", "case_sensitive": false },
        });
        assert!(
            messages("bulk_operation", &good).is_empty(),
            "{:?}",
            messages("bulk_operation", &good)
        );

        let missing = json!({ "op": "replace_text", "object_ids": ["object:1"], "parameters": {} });
        assert!(messages("bulk_operation", &missing).iter().any(|error| {
            error.contains("`parameters.parameters.search` is required for `replace_text`")
        }));

        let wrong_op = json!({ "op": "frobnicate", "object_ids": ["object:1"] });
        assert!(
            messages("bulk_operation", &wrong_op)
                .iter()
                .any(|error| error.contains("unknown `parameters.op` `frobnicate`"))
        );

        let unknown_inner = json!({
            "op": "delete",
            "object_ids": ["object:1"],
            "parameters": { "nope": 1 },
        });
        assert!(
            messages("bulk_operation", &unknown_inner)
                .iter()
                .any(|error| error.contains("unknown parameter `parameters.nope` for `delete`"))
        );
        // An operation without parameters accepts an empty object.
        assert!(
            messages(
                "bulk_operation",
                &json!({ "op": "delete", "object_ids": ["object:1"] })
            )
            .is_empty()
        );
        // `send_for_approval` is allowed next to every operation except the ones
        // that are a workflow transition themselves — the worker checks that.
        let with_approval = json!({
            "op": "set_attribute",
            "object_ids": ["object:1"],
            "parameters": { "attribute": "name", "send_for_approval": true },
        });
        assert!(messages("bulk_operation", &with_approval).is_empty());
    }
}
