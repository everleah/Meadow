//! Meadow PIM core service.
//!
//! Headless REST API for object management, search, language-tree and
//! value inheritance, jobs, and the event system. The architecture is
//! documented in `plan/meadow-pim-architektur.md`.

pub mod api;
pub mod app;
pub mod attribute_definition_types;
pub mod attribute_values;
pub mod audit;
pub mod auth;
pub mod cache;
pub mod completeness;
pub mod config;
pub mod db;
pub mod error;
pub mod event;
pub mod inheritance;
pub mod jobqueue;
pub mod language;
pub mod log_cleanup;
pub mod rate_limit;
pub mod rules;
pub mod scheduler;
pub mod schema;
pub mod seed;

/// Starts the `pim-core` HTTP server.
pub async fn run() {
    tracing_subscriber::fmt().init();

    let config = config::Config::load();
    let db = db::Db::connect(&config)
        .await
        .expect("failed to connect to SurrealDB");
    schema::apply(&db)
        .await
        .expect("failed to apply database schema");
    seed::apply(&db).await.expect("failed to seed system data");
    seed::ensure_default_admin(&db)
        .await
        .expect("failed to seed default admin user");

    let listener = tokio::net::TcpListener::bind(&config.bind_addr)
        .await
        .expect("failed to bind TCP listener");
    tracing::info!("pim-core listening on {}", config.bind_addr);

    let settings = std::sync::Arc::new(std::sync::RwLock::new(
        crate::api::system::initial_settings(),
    ));
    let state = app::AppState {
        db,
        cache: std::sync::Arc::new(cache::MetaCache::default()),
        rules: rules::RulesClient::from_config(&config, settings.clone()),
        config,
        settings,
    };
    // Load DB overrides into the settings snapshot so runtime consumers (the
    // rule client) honour persisted settings immediately.
    if let Err(error) = crate::api::system::refresh_settings_snapshot(&state).await {
        tracing::warn!(?error, "failed to load runtime settings snapshot");
    }

    // Start the background scheduler (cron jobs from scheduled definitions).
    tokio::spawn(scheduler::run(state.db.clone(), state.config.clone()));
    // Start the dead-letter monitor (marks dead-lettered job runs as failed).
    tokio::spawn(jobqueue::monitor_dlq(
        state.db.clone(),
        state.config.clone(),
    ));
    // Start the log-retention cleanup.
    tokio::spawn(log_cleanup::run(state.db.clone(), state.config.clone()));
    // Start the event relay (publishes outbox events to NATS JetStream).
    tokio::spawn(event::run_relay(state.db.clone(), state.config.clone()));

    axum::serve(
        listener,
        app::router(state).into_make_service_with_connect_info::<std::net::SocketAddr>(),
    )
    .await
    .expect("server error");
}
