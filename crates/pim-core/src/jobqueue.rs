//! NATS JetStream job queue (Phase 5).
//!
//! The core creates a `job_run` record and then publishes a job message to the
//! `JOBS` stream (subject `jobs.<type>`). The stream uses `WorkQueue` retention
//! (a message is removed once a worker acknowledges it) and a short `max_age`,
//! because the durable job status lives in `job_run`, not in NATS.
//!
//! Dead-lettered jobs (after the consumer's `max_deliver` is exhausted) are
//! published by the worker to the `JOBS_DLQ` stream (`jobs.dlq.<type>`,
//! `Limits` retention, manual cleanup). The core monitors this stream and
//! marks the corresponding `job_run` as finally `failed`.

use std::time::Duration;

use bytes::Bytes;
use futures_util::StreamExt;
use serde_json::Value;

use crate::api::jobs::fail_run;
use crate::config::Config;
use crate::db::Db;
use crate::error::ApiError;

/// Name of the JetStream stream that carries job messages.
pub const JOBS_STREAM: &str = "JOBS";
/// Subject pattern of the job stream.
pub const JOBS_SUBJECT: &str = "jobs.*";
/// Name of the JetStream stream that carries dead-lettered job messages.
pub const DLQ_STREAM: &str = "JOBS_DLQ";
/// Subject pattern of the dead-letter stream.
pub const DLQ_SUBJECT: &str = "jobs.dlq.*";
/// Subscription subject for the core's DLQ monitor.
pub const DLQ_MONITOR_SUBJECT: &str = "jobs.dlq.>";

/// Ensures the `JOBS` stream exists and publishes a job message for `job_type`.
///
/// The payload is published to `jobs.<job_type>` and must contain at least the
/// run id and job definition id so the worker can correlate the message with
/// its `job_run` record.
pub async fn publish_job(
    nats_url: &str,
    job_type: &str,
    max_age_minutes: u64,
    payload: Value,
) -> Result<(), ApiError> {
    let client = async_nats::connect(nats_url)
        .await
        .map_err(|error| ApiError::unavailable(format!("job queue connect failed: {error}")))?;
    let jetstream = async_nats::jetstream::new(client);

    let _stream = jetstream
        .get_or_create_stream(async_nats::jetstream::stream::Config {
            name: JOBS_STREAM.to_owned(),
            subjects: vec![JOBS_SUBJECT.to_owned()],
            retention: async_nats::jetstream::stream::RetentionPolicy::WorkQueue,
            max_age: Duration::from_secs(max_age_minutes * 60),
            ..Default::default()
        })
        .await
        .map_err(|error| ApiError::unavailable(format!("job stream setup failed: {error}")))?;

    let bytes: Bytes = serde_json::to_vec(&payload)
        .map_err(|error| ApiError::internal(format!("failed to encode job message: {error}")))?
        .into();

    let subject = format!("jobs.{job_type}");
    jetstream
        .publish(subject, bytes)
        .await
        .map_err(|error| ApiError::unavailable(format!("job publish failed: {error}")))?
        .await
        .map_err(|error| ApiError::unavailable(format!("job publish ack failed: {error}")))?;

    Ok(())
}

/// Ensures the `JOBS_DLQ` stream exists (`Limits` retention, manual cleanup).
pub async fn ensure_dlq_stream(nats_url: &str) -> Result<(), ApiError> {
    let client = async_nats::connect(nats_url)
        .await
        .map_err(|error| ApiError::unavailable(format!("DLQ connect failed: {error}")))?;
    let jetstream = async_nats::jetstream::new(client);
    jetstream
        .get_or_create_stream(async_nats::jetstream::stream::Config {
            name: DLQ_STREAM.to_owned(),
            subjects: vec![DLQ_SUBJECT.to_owned()],
            retention: async_nats::jetstream::stream::RetentionPolicy::Limits,
            ..Default::default()
        })
        .await
        .map_err(|error| ApiError::unavailable(format!("DLQ stream setup failed: {error}")))?;
    Ok(())
}

/// Marks the `job_run` referenced by a dead-letter message as finally failed.
async fn handle_dlq_message(db: &Db, message: &async_nats::Message) -> Result<(), ApiError> {
    let payload: Value = serde_json::from_slice(&message.payload)
        .map_err(|error| ApiError::bad_request(format!("invalid DLQ payload: {error}")))?;
    let Some(run_id) = payload.get("run_id").and_then(Value::as_str) else {
        tracing::warn!(subject = %message.subject, "DLQ message without run_id");
        return Ok(());
    };
    let error = payload
        .get("error")
        .and_then(Value::as_str)
        .unwrap_or("unknown error");
    let num_delivered = payload
        .get("num_delivered")
        .and_then(Value::as_u64)
        .unwrap_or(0);
    let composed = format!("dead lettered after {num_delivered} attempts: {error}");
    fail_run(db, run_id, &composed).await;
    tracing::warn!(%run_id, "job run dead-lettered");
    Ok(())
}

/// Monitors the dead-letter stream and marks the affected job runs as failed.
///
/// Runs forever; intended to be spawned as a background task at startup.
pub async fn monitor_dlq(db: Db, config: Config) {
    if let Err(error) = ensure_dlq_stream(&config.jobs_nats_url).await {
        tracing::error!(error = %error.message(), "failed to ensure DLQ stream");
        return;
    }
    let client = match async_nats::connect(&config.jobs_nats_url).await {
        Ok(client) => client,
        Err(error) => {
            tracing::error!(%error, "DLQ monitor failed to connect");
            return;
        }
    };
    let mut subscriber = match client.subscribe(DLQ_MONITOR_SUBJECT).await {
        Ok(subscriber) => subscriber,
        Err(error) => {
            tracing::error!(%error, "DLQ monitor failed to subscribe");
            return;
        }
    };
    tracing::info!("DLQ monitor subscribed to {DLQ_MONITOR_SUBJECT}");
    while let Some(message) = subscriber.next().await {
        if let Err(error) = handle_dlq_message(&db, &message).await {
            tracing::warn!(error = %error.message(), "DLQ message handling failed");
        }
    }
}
