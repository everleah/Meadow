//! Baseline benchmark for graph traversal and value resolution (Phase 3).
//!
//! Measures the two hot read paths against the local SurrealDB:
//! 1. multilingual resolution (`language::resolve_attributes`)
//! 2. inheritance resolution (`inheritance::resolve_inheritance`), plain and
//!    combined with locale resolution.
//!
//! Run from the workspace root:
//!   cargo run --release -p pim-core --example bench

use std::time::Instant;

use serde_json::{Map, Value};

use pim_core::error::ApiError;
use pim_core::{cache::MetaCache, config::Config, db::Db, inheritance, language, schema, seed};

const DEPTH: usize = 5;
const ITERATIONS: usize = 100;

#[tokio::main]
async fn main() -> Result<(), ApiError> {
    let config = Config::load();
    let db = Db::connect(&config).await?;
    schema::apply(&db).await?;
    seed::apply(&db).await?;

    // Benchmark attribute definitions (idempotent, fixed record keys).
    db.inner()
        .query(
            r#"
            UPSERT attribute_definition:bench_brand CONTENT {
                name: 'bench_brand', label: {}, type: 'text',
                inheritable: true, is_key: false,
                applicable_types: [], required_types: [],
                computed: false, dependencies: [], computed_reads_inherited: false,
                multilingual: false, version: 1,
                created_at: time::now(), updated_at: time::now()
            };
            UPSERT attribute_definition:bench_title CONTENT {
                name: 'bench_title', label: {}, type: 'text',
                inheritable: false, is_key: false,
                applicable_types: [], required_types: [],
                computed: false, dependencies: [], computed_reads_inherited: false,
                multilingual: true, version: 1,
                created_at: time::now(), updated_at: time::now()
            };
            "#,
        )
        .await?
        .check()?;

    // Build a composition chain of `DEPTH` objects.
    // object_0 holds `bench_brand`; object_4 holds a multilingual `bench_title`.
    let title = serde_json::json!({
        "root": "Produkt",
        "en": "Product",
        "en-GB": "Product (UK)",
        "de": "Produkt",
    });
    let mut parent = "object_root".to_owned();
    let mut deep_key = String::new();
    let mut deep_attrs = Map::new();
    for level in 0..DEPTH {
        let content = if level == 0 {
            serde_json::json!({ "type": "bench", "attributes": { "bench_brand": "Acme" }, "status": "draft", "version": 1 })
        } else if level == DEPTH - 1 {
            serde_json::json!({ "type": "bench", "attributes": { "bench_title": title.clone() }, "status": "draft", "version": 1 })
        } else {
            serde_json::json!({ "type": "bench", "attributes": {}, "status": "draft", "version": 1 })
        };
        let sql = "
            LET $obj = type::record('object', rand::ulid());
            LET $parent_rec = type::record('object', $parent);
            CREATE $obj CONTENT $content;
            RELATE $obj->reference->$parent_rec SET definition_id = 'rel_depends_on', type = 'depends_on', attributes = {};
            SELECT * FROM $obj;
        ";
        let mut response = db
            .inner()
            .query(sql)
            .bind(("content", content))
            .bind(("parent", parent.clone()))
            .await?
            .check()?;
        let rows: Vec<Value> = response.take(2)?;
        let obj = rows.into_iter().next().expect("created benchmark object");
        let id = obj
            .get("id")
            .and_then(Value::as_str)
            .expect("benchmark object id");
        parent = id
            .split_once(':')
            .map(|(_, key)| key.to_owned())
            .expect("benchmark object key");
        if level == DEPTH - 1 {
            deep_key = id
                .split_once(':')
                .map(|(_, key)| key.to_owned())
                .expect("benchmark object key");
            deep_attrs = obj
                .get("attributes")
                .and_then(Value::as_object)
                .cloned()
                .expect("benchmark object attributes");
        }
    }

    // Capture the multilingual object's attributes (object_0's bench_title is
    // not present there, so reuse the deep object's title for the language test).
    let language_attrs = deep_attrs.clone();
    let deep_key_clone = deep_key.clone();
    let cache = MetaCache::default();

    // --- language resolution ---
    let start = Instant::now();
    for _ in 0..ITERATIONS {
        let mut attrs = language_attrs.clone();
        language::resolve_attributes(&cache, &db, "en-US", &mut attrs).await?;
    }
    let language_ms = start.elapsed().as_secs_f64() * 1000.0 / ITERATIONS as f64;
    println!("language resolve      : {language_ms:.3} ms/op");

    // --- inheritance (no locale) ---
    let start = Instant::now();
    for _ in 0..ITERATIONS {
        let mut attrs = deep_attrs.clone();
        inheritance::resolve_inheritance(&cache, &db, &deep_key_clone, None, &mut attrs).await?;
    }
    let inherit_ms = start.elapsed().as_secs_f64() * 1000.0 / ITERATIONS as f64;
    println!("inheritance (no locale): {inherit_ms:.3} ms/op");

    // --- inheritance + locale ---
    let start = Instant::now();
    for _ in 0..ITERATIONS {
        let mut attrs = deep_attrs.clone();
        inheritance::resolve_inheritance(&cache, &db, &deep_key_clone, Some("en-US"), &mut attrs)
            .await?;
    }
    let combined_ms = start.elapsed().as_secs_f64() * 1000.0 / ITERATIONS as f64;
    println!("inheritance + locale  : {combined_ms:.3} ms/op");

    println!("(depth = {DEPTH}, iterations = {ITERATIONS})");
    Ok(())
}
