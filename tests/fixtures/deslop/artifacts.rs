//! Intentionally sloppy Rust sample for the Deslop scan.
//!
//! tests/test_deslop.py copies this file to a neutral directory and scans it with
//! `stopslop --no-config`, because most rules skip paths that look like test code;
//! tests/fixtures/deslop/ itself is excluded from the repository's own scan (see
//! stopslop.toml). Nothing here is part of a crate target - the file is never
//! compiled, it only has to parse.
//!
//! Rules this file proves (verified 2026-09-18):
//!
//! - SLOP001 elision comment
//! - SLOP002 leaked chat preamble
//! - SLOP008 stub-only body (`todo!()`)
//! - SLOP009 placeholder credential
//! - SLOP039 pass-through wrapper

// Certainly! Here's the updated implementation of the slug helper.

pub const API_KEY: &str = "YOUR_API_KEY";

pub fn slugify(input: &str) -> String {
    // ... rest of the function unchanged
}

pub fn reindex_article(article_id: &str) {
    todo!()
}

pub fn name_of(product: &Product) -> String {
    product_name(product)
}

pub fn product_name(product: &Product) -> String {
    let name = product.name.clone();
    // clone the name
    name
}
