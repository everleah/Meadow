//! Temporary probe: verify cleanup kept recent entries and removed old ones.

use pim_core::config::Config;
use pim_core::db::Db;

#[tokio::main]
async fn main() {
    let config = Config::load();
    let db = Db::connect(&config).await.expect("connect to SurrealDB");

    let recent_kept = count(&db, "recent").await;
    let old_remaining = count(&db, "old").await;

    println!("recent_kept={recent_kept} old_remaining={old_remaining}");
    assert_eq!(recent_kept, 1, "the recent entry must be kept");
    assert_eq!(old_remaining, 0, "the old entry must be deleted");
}

async fn count(db: &Db, message: &str) -> u64 {
    let sql =
        "SELECT count() FROM log_entry WHERE message = $message AND service = 'probe' GROUP ALL;";
    let mut response = db
        .inner()
        .query(sql)
        .bind(("message", message.to_owned()))
        .await
        .expect("count")
        .check()
        .unwrap();
    let rows: Vec<serde_json::Value> = response.take(0).unwrap();
    rows.first()
        .and_then(|row| row.get("count"))
        .and_then(|v| v.as_u64().or_else(|| v.as_i64().map(|n| n as u64)))
        .unwrap_or(0)
}
