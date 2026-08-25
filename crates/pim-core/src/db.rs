//! SurrealDB connection.

use std::sync::Arc;

use surrealdb::Surreal;
use surrealdb::engine::remote::ws::{Client, Ws};
use surrealdb::opt::auth::Root;

use crate::config::Config;

/// Handle to the SurrealDB instance.
///
/// The `Surreal<Client>` is shared behind an `Arc` so every clone of `Db`
/// (one per HTTP request via `AppState`) reuses the **same** session. Cloning
/// `Surreal<Client>` directly would create a fresh session whose namespace/
/// database replay is asynchronous, which intermittently yields
/// "Specify a namespace to use" under concurrent requests.
#[derive(Debug, Clone)]
pub struct Db {
    inner: Arc<Surreal<Client>>,
}

impl Db {
    /// Connects to SurrealDB, signs in as root, and selects namespace/database.
    pub async fn connect(config: &Config) -> Result<Self, surrealdb::Error> {
        let inner = Surreal::new::<Ws>(config.surrealdb_url.as_str()).await?;
        inner
            .signin(Root {
                username: config.surrealdb_user.clone(),
                password: config.surrealdb_pass.clone(),
            })
            .await?;

        // Idempotently ensure the namespace and database exist. The identifiers
        // come from trusted runtime configuration, not user input.
        inner
            .query(format!(
                "DEFINE NAMESPACE IF NOT EXISTS {ns}; USE NS {ns}; DEFINE DATABASE IF NOT EXISTS {db};",
                ns = config.surrealdb_namespace,
                db = config.surrealdb_database,
            ))
            .await?
            .check()?;

        inner
            .use_ns(config.surrealdb_namespace.as_str())
            .use_db(config.surrealdb_database.as_str())
            .await?;
        Ok(Self {
            inner: Arc::new(inner),
        })
    }

    /// Returns the underlying SurrealDB client.
    pub fn inner(&self) -> &Surreal<Client> {
        self.inner.as_ref()
    }
}
