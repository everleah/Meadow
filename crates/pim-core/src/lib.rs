//! Meadow PIM core service.
//!
//! Headless REST API for object management, search, language-tree and
//! value inheritance, jobs, and the event system. The architecture is
//! documented in `plan/meadow-pim-architektur.md`.

pub mod ai;
pub mod api;
pub mod app;
pub mod attribute_definition_types;
pub mod attribute_values;
pub mod audit;
pub mod auth;
pub mod cache;
pub mod class_features;
pub mod completeness;
pub mod config;
pub mod db;
pub mod error;
pub mod event;
pub mod inheritance;
pub mod jobqueue;
pub mod key_lock;
pub mod language;
pub mod log_cleanup;
pub mod permissions;
pub mod rate_limit;
pub mod rbac;
pub mod rules;
pub mod scheduler;
pub mod schema;
pub mod seed;

/// Starts the `pim-core` HTTP server.
pub async fn run() {
    // `RUST_LOG` selects the level (e.g. `RUST_LOG=info,pim_core::api::objects=debug`
    // for the write-path phase timings); without it the filter stays at `info`.
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info")),
        )
        .init();

    let config = config::Config::load();
    let db = db::Db::connect(&config)
        .await
        .expect("failed to connect to SurrealDB");
    schema::apply(&db)
        .await
        .expect("failed to apply database schema");
    // Business-key uniqueness is checked on every write, so the lookup index has
    // to exist for every declared key attribute (the registry decides which these
    // are, which is why they cannot live in `schema.rs`). Idempotent; the first
    // run on an existing dataset pays the index build once.
    match api::keys::registered_key_attributes(&db).await {
        Ok(names) => {
            if let Err(error) = api::keys::ensure_key_indexes(&db, &names).await {
                tracing::warn!(?error, "failed to define the business-key indexes");
            }
        }
        Err(error) => tracing::warn!(?error, "failed to read the business-key attributes"),
    }
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
        key_locks: std::sync::Arc::new(key_lock::KeyLocks::new()),
        rules: rules::RulesClient::from_config(&config, settings.clone()),
        ai: ai::AiClient::from_config(&config, settings.clone()),
        config,
        settings,
    };
    // The access version decides which issued tokens are still valid. The stored counter
    // is the source of truth, the cache mirrors it for the request path (a database read
    // per request would defeat the point of carrying the permissions in the token). A
    // failure here would leave every session invalid, so it is fatal like the schema and
    // the seed above.
    let auth_version = rbac::auth_version(&state.db)
        .await
        .expect("failed to load the access version");
    state.cache.set_auth_version(auth_version);
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
