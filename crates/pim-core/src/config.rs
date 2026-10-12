//! Runtime configuration, resolved from environment variables.

/// Runtime configuration for the core service.
#[derive(Debug, Clone)]
pub struct Config {
    /// Address the HTTP server binds to.
    pub bind_addr: String,
    /// SurrealDB WebSocket URL.
    pub surrealdb_url: String,
    /// SurrealDB namespace.
    pub surrealdb_namespace: String,
    /// SurrealDB database.
    pub surrealdb_database: String,
    /// SurrealDB root user.
    pub surrealdb_user: String,
    /// SurrealDB root password.
    pub surrealdb_pass: String,
    /// Secret used to sign JWTs (HS256).
    pub jwt_secret: String,
    /// Access-token lifetime in seconds.
    pub jwt_ttl_seconds: u64,
    /// API key for machine-to-machine log ingestion (`POST /system/logs`).
    pub log_ingest_api_key: Option<String>,
    /// NATS URL for the synchronous rule-evaluation request/reply.
    pub rules_nats_url: String,
    /// NATS URL for publishing job messages to JetStream.
    pub jobs_nats_url: String,
    /// Retention window for the `JOBS` stream, in minutes.
    pub jobs_stream_max_age_minutes: u64,
    /// NATS URL for the event relay (publishes CRUD events to JetStream).
    pub events_nats_url: String,
    /// Retention window for the `EVENTS` stream, in days.
    pub events_stream_max_age_days: u64,
    /// Maximum bytes of the `EVENTS` stream.
    pub events_stream_max_bytes: i64,
    /// Event-relay poll interval, in seconds.
    pub event_relay_interval_seconds: u64,
    /// NATS subject for rule evaluation.
    pub rules_subject: String,
    /// NATS subject for rule-script compile validation.
    pub rules_compile_subject: String,
    /// NATS subject for AI generation (context building + model call in the worker).
    pub ai_generate_subject: String,
    /// NATS subject for the AI connection test (`POST /ai/test`).
    pub ai_test_subject: String,
    /// Timeout for a rule-evaluation request, in milliseconds.
    pub rules_timeout_ms: u64,
    /// Degrade mode: on transport failure, allow the write and skip rules.
    pub rules_degrade: bool,
    /// Consecutive rule failures before the circuit breaker disables a rule.
    pub rules_breaker_threshold: u64,
    /// Scheduler sync interval, in seconds (how often job definitions are
    /// reconciled with the cron scheduler).
    pub scheduler_interval_seconds: u64,
    /// Log-retention cleanup interval, in seconds.
    pub log_cleanup_interval_seconds: u64,
    /// Maximum request body size in bytes (0 = axum default). File upload
    /// endpoints override this with their own larger limit.
    pub max_body_bytes: usize,
    /// Per-IP request rate limit (requests per minute). 0 = disabled.
    pub rate_limit_per_minute: u64,
}

impl Config {
    /// Loads configuration from environment variables, applying defaults.
    pub fn load() -> Self {
        Self {
            bind_addr: env_or("PIM_CORE_ADDR", "0.0.0.0:8080"),
            surrealdb_url: env_or("SURREALDB_URL", "127.0.0.1:8000"),
            surrealdb_namespace: env_or("SURREALDB_NAMESPACE", "meadow"),
            surrealdb_database: env_or("SURREALDB_DATABASE", "meadow"),
            surrealdb_user: env_or("SURREALDB_USER", "root"),
            surrealdb_pass: env_or("SURREALDB_PASS", "root"),
            jwt_secret: jwt_secret(),
            jwt_ttl_seconds: env_or_parse("JWT_TTL_SECONDS", 3600),
            log_ingest_api_key: std::env::var("LOG_INGEST_API_KEY")
                .ok()
                .filter(|value| !value.is_empty()),
            rules_nats_url: env_or("RULES_NATS_URL", "nats://localhost:4222"),
            jobs_nats_url: env_or("JOBS_NATS_URL", "nats://localhost:4222"),
            jobs_stream_max_age_minutes: env_or_parse("JOBS_STREAM_MAX_AGE_MINUTES", 1440),
            events_nats_url: env_or("EVENTS_NATS_URL", "nats://localhost:4222"),
            events_stream_max_age_days: env_or_parse("EVENTS_STREAM_MAX_AGE_DAYS", 30),
            events_stream_max_bytes: env_or_parse("EVENTS_STREAM_MAX_BYTES", 1_073_741_824i64),
            event_relay_interval_seconds: env_or_parse("EVENT_RELAY_INTERVAL_S", 5),
            rules_subject: env_or("RULES_SUBJECT", "rules.evaluate"),
            rules_compile_subject: env_or("RULES_COMPILE_SUBJECT", "rules.compile"),
            ai_generate_subject: env_or("AI_GENERATE_SUBJECT", "ai.generate"),
            ai_test_subject: env_or("AI_TEST_SUBJECT", "ai.test"),
            rules_timeout_ms: env_or_parse("RULES_TIMEOUT_MS", 2000),
            rules_degrade: env_or_bool("RULES_DEGRADE", false),
            rules_breaker_threshold: env_or_parse("RULES_BREAKER_THRESHOLD", 3),
            scheduler_interval_seconds: env_or_parse("SCHEDULER_INTERVAL_S", 60),
            log_cleanup_interval_seconds: env_or_parse("LOG_CLEANUP_INTERVAL_S", 3600),
            max_body_bytes: env_or_parse("PIM_MAX_BODY_BYTES", 10_485_760usize),
            rate_limit_per_minute: env_or_parse("PIM_RATE_LIMIT_PER_MINUTE", 0u64),
        }
    }
}

/// Minimum length of a signing secret. The template's placeholder and the
/// defaults that shipped before this check are all far shorter, so a single
/// rule rejects both without spelling them out.
const MIN_SECRET_LENGTH: usize = 32;

/// Accepts a configured secret only when it is long enough to be one.
fn usable_secret(raw: Option<&str>) -> Option<String> {
    let value = raw?.trim();
    if value.len() < MIN_SECRET_LENGTH {
        return None;
    }
    Some(value.to_owned())
}

/// `JWT_SECRET` signs every access token (HS256). There is deliberately no
/// default: a public fallback would let anyone mint a `superadmin` token.
fn jwt_secret() -> String {
    match usable_secret(std::env::var("JWT_SECRET").ok().as_deref()) {
        Some(secret) => secret,
        None => panic!(
            "JWT_SECRET must be set to at least {MIN_SECRET_LENGTH} characters; the core refuses \
             to start with a guessable signing key - generate one with `openssl rand -hex 32`"
        ),
    }
}

fn env_or(key: &str, default: &str) -> String {
    std::env::var(key).unwrap_or_else(|_| default.to_owned())
}

fn env_or_parse<T: std::str::FromStr>(key: &str, default: T) -> T {
    std::env::var(key)
        .ok()
        .and_then(|value| value.parse().ok())
        .unwrap_or(default)
}

fn env_or_bool(key: &str, default: bool) -> bool {
    std::env::var(key)
        .ok()
        .map(|value| matches!(value.as_str(), "1" | "true" | "TRUE" | "yes" | "YES"))
        .unwrap_or(default)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_missing_or_short_secret_is_rejected() {
        assert_eq!(usable_secret(None), None);
        assert_eq!(usable_secret(Some("")), None);
        assert_eq!(usable_secret(Some("   ")), None);
        assert_eq!(usable_secret(Some("too-short-to-sign-anything")), None);
    }

    #[test]
    fn a_long_secret_is_kept_without_surrounding_whitespace() {
        let secret = "x".repeat(MIN_SECRET_LENGTH);
        assert_eq!(usable_secret(Some(&format!("  {secret}  "))), Some(secret));
    }
}
