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
