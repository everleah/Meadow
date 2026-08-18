//! Health endpoint used by Docker health checks.

use axum::Json;
use serde::Serialize;
use utoipa::ToSchema;

/// Liveness/readiness response.
#[derive(Debug, Serialize, ToSchema)]
pub struct HealthResponse {
    /// Always `"ok"` while the service is reachable.
    pub status: String,
}

/// Liveness/readiness endpoint used by Docker health checks.
#[utoipa::path(
    get,
    path = "/health",
    responses(
        (status = 200, description = "Service is reachable", body = HealthResponse)
    )
)]
pub async fn health() -> Json<HealthResponse> {
    Json(HealthResponse {
        status: "ok".to_owned(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn health_returns_ok() {
        let Json(response) = health().await;
        assert_eq!(response.status, "ok");
    }
}
