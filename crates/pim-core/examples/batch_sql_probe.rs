//! Temporary probe: which RELATE form creates a *proper* (traversable) edge?
//!
//!   cargo run -p pim-core --example batch_sql_probe

use serde_json::json;

use pim_core::config::Config;
use pim_core::db::Db;
use pim_core::error::ApiError;

fn shorten(text: &str) -> String {
    text.replace('\n', " ").chars().take(140).collect()
}

async fn try_form(db: &Db, label: &str, sql: &str, bindings: Vec<(&str, serde_json::Value)>) {
    let mut query = db.inner().query(sql);
    for (name, value) in bindings {
        query = query.bind((name.to_owned(), value));
    }
    println!("\n=== {label} ===");
    match query.await {
        Ok(response) => match response.check() {
            Ok(_) => println!("  write: OK"),
            Err(error) => println!("  write: ERR {}", shorten(&error.to_string())),
        },
        Err(error) => println!("  write: ERR {}", shorten(&error.to_string())),
    }
    // Traverse the edge: only a proper relation answers this.
    if let Ok(mut verify) = db
        .inner()
        .query("SELECT type::string(id) AS id FROM object:01JPROBE000000000000BATCH->reference->object;")
        .await
    {
        let children: Vec<serde_json::Value> = verify.take(0).unwrap_or_default();
        println!("  traversal sees {} child(ren)", children.len());
    }
    if let Ok(mut edges) = db
        .inner()
        .query("SELECT type::string(in) AS i FROM reference WHERE in = object:01JPROBE000000000000BATCH;")
        .await
    {
        let rows: Vec<serde_json::Value> = edges.take(0).unwrap_or_default();
        println!("  edge rows: {}", rows.len());
    }
}

async fn reset(db: &Db, create: &serde_json::Value) -> Result<(), ApiError> {
    db.inner()
        .query(
            "DELETE reference WHERE in = object:01JPROBE000000000000BATCH; \
             DELETE object:01JPROBE000000000000BATCH;",
        )
        .await?
        .check()?;
    db.inner()
        .query("FOR $c IN $creates { CREATE type::record('object', $c.id) CONTENT $c.content; };")
        .bind(("creates", create.clone()))
        .await?
        .check()?;
    Ok(())
}

#[tokio::main]
async fn main() -> Result<(), ApiError> {
    let config = Config::load();
    let db = Db::connect(&config).await?;
    let ulid = "01JPROBE000000000000BATCH";
    let create = json!([{
        "id": ulid,
        "content": {
            "type": "product",
            "attributes": {"name": "Batch probe", "sku": "batch-probe-sku"},
            "status": "draft",
            "updated_by": "user:admin",
            "source": "upsert-probe",
            "source_id": "probe-1",
            "version": 1
        }
    }]);

    // A. inline literal record id
    reset(&db, &create).await?;
    try_form(
        &db,
        "A: inline literal RELATE",
        "RELATE object:01JPROBE000000000000BATCH->reference->object:object_root \
         SET definition_id = 'rel_depends_on', type = 'depends_on', attributes = {};",
        vec![],
    )
    .await;

    // B. bound record id used as an array element
    reset(&db, &create).await?;
    try_form(
        &db,
        "B: RELATE over a bound record",
        "FOR $c IN $creates { RELATE $c.rec->reference->object:object_root \
         SET definition_id = 'rel_depends_on', type = 'depends_on', attributes = {}; };",
        vec![(
            "creates",
            json!([{"id": ulid, "rec": format!("object:{ulid}")}]),
        )],
    )
    .await;

    // C. two statements per object inside the loop body
    reset(&db, &create).await?;
    try_form(
        &db,
        "C: FOR body with LET then RELATE",
        "FOR $c IN $creates { LET $o = type::record('object', $c.id); \
         RELATE $o->reference->object:object_root \
         SET definition_id = 'rel_depends_on', type = 'depends_on', attributes = {}; };",
        vec![("creates", create.clone())],
    )
    .await;

    // Clean up.
    db.inner()
        .query(
            "DELETE reference WHERE in = object:01JPROBE000000000000BATCH; \
             DELETE object:01JPROBE000000000000BATCH;",
        )
        .await?
        .check()?;
    println!("\ncleaned up");
    Ok(())
}
