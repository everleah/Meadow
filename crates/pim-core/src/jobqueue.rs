//! NATS JetStream job queue (Phase 5).
//!
//! The core creates a `job_run` record and then publishes a job message to the
//! `JOBS` stream (subject `jobs.<type>`). The stream uses `WorkQueue` retention
//! (a message is removed once a worker acknowledges it) and a short `max_age`,
//! because the durable job status lives in `job_run`, not in NATS.

use std::time::Duration;

use bytes::Bytes;
use serde_json::Value;

use crate::error::ApiError;

/// Name of the JetStream stream that carries job messages.
pub const JOBS_STREAM: &str = "JOBS";
/// Subject pattern of the job stream.
pub const JOBS_SUBJECT: &str = "jobs.*";

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
