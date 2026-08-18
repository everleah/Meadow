//! `pim-core` binary entry point.

#[tokio::main]
async fn main() {
    pim_core::run().await;
}
