#!/usr/bin/env bash
# Runs every verification gate of Meadow PIM (same set as the CI pipeline).
#
#   bash scripts/check.sh            # all gates
#   bash scripts/check.sh rust       # only the Rust gates
#   bash scripts/check.sh python     # only the Python gates
#
# Requires: cargo/rustfmt/clippy, uv (creates the project venv from uv.lock).
# The Rust core must not be running while `cargo build`/`cargo test` relink on
# Windows (the running `pim-core.exe` locks the binary).
set -euo pipefail

cd "$(dirname "$0")/.."

selection="${1:-all}"

step() {
    printf '\n\033[1m==> %s\033[0m\n' "$1"
}

run_rust() {
    step "cargo fmt --check"
    cargo fmt --check

    step "cargo clippy --all-targets -- -D warnings"
    cargo clippy --all-targets -- -D warnings

    step "cargo test"
    cargo test
}

run_python() {
    step "uv sync"
    uv sync --quiet

    step "ruff check"
    uv run ruff check meadow tests scripts

    step "ruff format --check"
    uv run ruff format --check meadow tests scripts

    step "pytest"
    uv run pytest -q
}

case "$selection" in
    rust) run_rust ;;
    python) run_python ;;
    all)
        run_rust
        run_python
        ;;
    *)
        printf 'Unknown gate selection: %s (use rust, python or all)\n' "$selection" >&2
        exit 2
        ;;
esac

printf '\n\033[1;32mAll checks passed.\033[0m\n'
