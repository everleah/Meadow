//! Simple per-IP rate limiting middleware (fixed window).
//!
//! Enabled via `PIM_RATE_LIMIT_PER_MINUTE` (requests per minute, 0 = disabled).
//! Uses in-memory per-IP counters, suitable for a single-instance deployment.
//! A disabled limiter short-circuits without locking.

use std::collections::HashMap;
use std::net::{IpAddr, SocketAddr};
use std::sync::Arc;
use std::time::{Duration, Instant};

use axum::extract::{ConnectInfo, Request, State};
use axum::http::StatusCode;
use axum::middleware::Next;
use axum::response::{IntoResponse, Response};
use tokio::sync::Mutex;

/// In-memory fixed-window rate limiter, keyed by client IP.
#[derive(Clone)]
pub struct RateLimiter {
    limit: u64,
    inner: Arc<Mutex<HashMap<IpAddr, (Instant, u64)>>>,
}

impl RateLimiter {
    /// Builds a limiter for `limit_per_minute` requests per IP (0 = disabled).
    pub fn new(limit_per_minute: u64) -> Self {
        Self {
            limit: limit_per_minute,
            inner: Arc::new(Mutex::new(HashMap::new())),
        }
    }

    async fn allow(&self, ip: IpAddr) -> bool {
        if self.limit == 0 {
            return true;
        }
        let now = Instant::now();
        let mut map = self.inner.lock().await;
        let entry = map.entry(ip).or_insert((now, 0));
        if now.duration_since(entry.0) >= Duration::from_secs(60) {
            *entry = (now, 1);
            return true;
        }
        entry.1 += 1;
        entry.1 <= self.limit
    }
}

/// Axum middleware that enforces the rate limit when enabled.
pub async fn middleware(
    State(limiter): State<Arc<RateLimiter>>,
    req: Request,
    next: Next,
) -> Response {
    let ip = req
        .extensions()
        .get::<ConnectInfo<SocketAddr>>()
        .map(|info| info.0.ip());
    if let Some(ip) = ip
        && !limiter.allow(ip).await
    {
        return (StatusCode::TOO_MANY_REQUESTS, "rate limit exceeded").into_response();
    }
    next.run(req).await
}
