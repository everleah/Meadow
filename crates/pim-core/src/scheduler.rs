//! Background scheduler (Phase 5, step 5).
//!
//! Keeps a `tokio_cron_scheduler` in sync with the scheduled `job_definition`
//! records: every `SCHEDULER_INTERVAL_S` seconds the sync loop loads all
//! enabled definitions that carry a schedule, registers/updates/removes the
//! matching cron jobs, and each job creates a `job_run` (`trigger: schedule`)
//! and publishes it to NATS JetStream when it fires. Cron expressions use the
//! 6-field seconds-first format (`sec min hour day month dow`).

use std::collections::{HashMap, HashSet};
use std::time::Duration;

use serde_json::Value;
use tokio_cron_scheduler::{Job, JobScheduler};
use uuid::Uuid;

use crate::api::jobs::queue_run;
use crate::config::Config;
use crate::db::Db;
use crate::error::ApiError;

/// Extracts a schedulable definition from a `job_definition` row.
///
/// Returns `(id, type, schedule, parameters)` when the row has a non-empty
/// `schedule`; `None` otherwise.
fn extract_definition(row: &Value) -> Option<(String, String, String, Value)> {
    let id = row.get("id").and_then(Value::as_str)?.to_owned();
    let job_type = row.get("type").and_then(Value::as_str)?.to_owned();
    let schedule = row.get("schedule").and_then(Value::as_str)?.to_owned();
    if schedule.trim().is_empty() {
        return None;
    }
    let parameters = row
        .get("parameters")
        .cloned()
        .unwrap_or_else(|| serde_json::json!({}));
    Some((id, job_type, schedule, parameters))
}

/// Loads all enabled, scheduled job definitions.
async fn load_scheduled_definitions(
    db: &Db,
) -> Result<Vec<(String, String, String, Value)>, ApiError> {
    let sql = "SELECT id, type, schedule, parameters FROM job_definition WHERE enabled = true";
    let mut response = db.inner().query(sql).await?.check()?;
    let rows: Vec<Value> = response.take(0)?;
    Ok(rows.iter().filter_map(extract_definition).collect())
}

/// Registers a cron job for a definition and returns its scheduler guid.
async fn register_job(
    sched: &JobScheduler,
    db: &Db,
    config: &Config,
    definition_id: &str,
    job_type: &str,
    schedule: &str,
    parameters: Value,
) -> Result<Uuid, ApiError> {
    let closure_db = db.clone();
    let closure_config = config.clone();
    let closure_definition_id = definition_id.to_owned();
    let closure_job_type = job_type.to_owned();

    let job = Job::new_async(schedule, move |_uuid, _lock| {
        // Clone per invocation so the closure stays `FnMut` across cron ticks.
        let db = closure_db.clone();
        let config = closure_config.clone();
        let definition_id = closure_definition_id.clone();
        let job_type = closure_job_type.clone();
        let parameters = parameters.clone();
        Box::pin(async move {
            if let Err(error) = queue_run(
                &db,
                &config,
                Some(&definition_id),
                &job_type,
                parameters,
                "schedule",
                "system",
            )
            .await
            {
                tracing::warn!(
                    error = %error.message(),
                    %definition_id,
                    "scheduled job failed to queue"
                );
            }
        })
    })
    .map_err(|error| ApiError::internal(format!("invalid cron schedule `{schedule}`: {error}")))?;

    let guid = job.guid();
    sched
        .add(job)
        .await
        .map_err(|error| ApiError::internal(format!("failed to register cron job: {error}")))?;
    Ok(guid)
}

/// Reconciles the registered cron jobs with the current definitions.
async fn sync(
    sched: &JobScheduler,
    db: &Db,
    config: &Config,
    registered: &mut HashMap<String, (Uuid, String)>,
) -> Result<(), ApiError> {
    let definitions = load_scheduled_definitions(db).await?;
    let mut seen: HashSet<String> = HashSet::new();

    for (id, job_type, schedule, parameters) in definitions {
        seen.insert(id.clone());
        let unchanged = registered
            .get(&id)
            .is_some_and(|(_, old_schedule)| old_schedule == &schedule);
        if unchanged {
            continue;
        }

        // Schedule changed or newly scheduled: remove the stale job, re-add.
        if let Some((guid, _)) = registered.remove(&id) {
            sched.remove(&guid).await.map_err(|error| {
                ApiError::internal(format!("failed to remove cron job: {error}"))
            })?;
        }
        let guid = register_job(sched, db, config, &id, &job_type, &schedule, parameters).await?;
        registered.insert(id, (guid, schedule));
    }

    // Drop jobs for definitions that were removed or no longer scheduled.
    let stale: Vec<String> = registered
        .keys()
        .filter(|id| !seen.contains(*id))
        .cloned()
        .collect();
    for id in stale {
        if let Some((guid, _)) = registered.remove(&id) {
            sched.remove(&guid).await.map_err(|error| {
                ApiError::internal(format!("failed to remove cron job: {error}"))
            })?;
        }
    }
    Ok(())
}

/// Runs the scheduler forever, re-syncing cron jobs from the database.
pub async fn run(db: Db, config: Config) {
    let sched = match JobScheduler::new().await {
        Ok(sched) => sched,
        Err(error) => {
            tracing::error!(%error, "scheduler failed to initialize");
            return;
        }
    };
    if let Err(error) = sched.start().await {
        tracing::error!(%error, "scheduler failed to start");
        return;
    }
    tracing::info!(
        interval_seconds = config.scheduler_interval_seconds,
        "scheduler started"
    );

    let interval = Duration::from_secs(config.scheduler_interval_seconds);
    let mut registered: HashMap<String, (Uuid, String)> = HashMap::new();
    loop {
        if let Err(error) = sync(&sched, &db, &config, &mut registered).await {
            tracing::warn!(error = %error.message(), "scheduler sync failed");
        }
        tokio::time::sleep(interval).await;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn extract_definition_requires_nonempty_schedule() {
        let row = json!({
            "id": "job_definition:a",
            "type": "business_rule",
            "schedule": "0 0 3 * * *",
            "parameters": {"rule": "x"}
        });
        let definition = extract_definition(&row).unwrap();
        assert_eq!(definition.0, "job_definition:a");
        assert_eq!(definition.1, "business_rule");
        assert_eq!(definition.2, "0 0 3 * * *");

        assert!(
            extract_definition(&json!({"id": "job_definition:b", "type": "business_rule"}))
                .is_none()
        );
        assert!(
            extract_definition(
                &json!({"id": "job_definition:c", "type": "business_rule", "schedule": "  "})
            )
            .is_none()
        );
    }
}
