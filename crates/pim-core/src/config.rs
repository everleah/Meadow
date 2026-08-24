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
    /// NATS subject for rule evaluation.
    pub rules_subject: String,
    /// NATS subject for rule-script compile validation.
    pub rules_compile_subject: String,
    /// Timeout for a rule-evaluation request, in milliseconds.
    pub rules_timeout_ms: u64,
    /// Degrade mode: on transport failure, allow the write and skip rules.
    pub rules_degrade: bool,
    /// Consecutive rule failures before the circuit breaker disables a rule.
    pub rules_breaker_threshold: u64,
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
            jwt_secret: env_or("JWT_SECRET", "dev-secret-change-me"),
            jwt_ttl_seconds: env_or_parse("JWT_TTL_SECONDS", 3600),
            log_ingest_api_key: std::env::var("LOG_INGEST_API_KEY")
                .ok()
                .filter(|value| !value.is_empty()),
            rules_nats_url: env_or("RULES_NATS_URL", "nats://localhost:4222"),
            jobs_nats_url: env_or("JOBS_NATS_URL", "nats://localhost:4222"),
            jobs_stream_max_age_minutes: env_or_parse("JOBS_STREAM_MAX_AGE_MINUTES", 1440),
            rules_subject: env_or("RULES_SUBJECT", "rules.evaluate"),
            rules_compile_subject: env_or("RULES_COMPILE_SUBJECT", "rules.compile"),
            rules_timeout_ms: env_or_parse("RULES_TIMEOUT_MS", 2000),
            rules_degrade: env_or_bool("RULES_DEGRADE", false),
            rules_breaker_threshold: env_or_parse("RULES_BREAKER_THRESHOLD", 3),
        }
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
