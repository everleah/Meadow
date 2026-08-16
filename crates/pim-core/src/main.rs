//! Meadow PIM core service.
//!
//! Headless REST API for object management, search, language-tree and
//! value inheritance, jobs, and the event system. The architecture is
//! documented in `plan/meadow-pim-architektur.md`.

use axum::{Json, Router, routing::get};
use serde_json::{Value, json};

/// Liveness/readiness endpoint used by Docker health checks.
async fn health() -> Json<Value> {
    Json(json!({ "status": "ok" }))
}

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt().init();

    let app = Router::new().route("/health", get(health));

    let addr = "0.0.0.0:8080";
    let listener = tokio::net::TcpListener::bind(addr)
        .await
        .expect("failed to bind TCP listener");
    tracing::info!("pim-core listening on {addr}");

    axum::serve(listener, app).await.expect("server error");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn health_returns_ok() {
        let Json(value) = health().await;
        assert_eq!(value, json!({ "status": "ok" }));
    }
}
