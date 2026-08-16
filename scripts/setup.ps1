# Meadow PIM — idempotent development environment setup.
# Installs missing prerequisites and syncs dependencies. Safe to re-run.

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot

function Test-Command([string]$Name) {
    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

# 1. uv (Python package manager)
if (-not (Test-Command "uv")) {
    Write-Host "[setup] uv not found - installing via the official installer..."
    irm https://astral.sh/uv/install.ps1 | iex
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}
Write-Host "[setup] uv: $(uv --version)"

# 2. Rust toolchain
if (-not (Test-Command "rustup")) {
    Write-Host "[setup] rustup not found - install Rust from https://rustup.rs and re-run."
    exit 1
}
rustup toolchain install stable --profile default
rustup component add rustfmt clippy
Write-Host "[setup] rustc: $(rustc --version)"

# 3. Python virtual environment + dependencies
Push-Location $Root
try {
    if (-not (Test-Path ".venv")) {
        uv venv --python 3.14
    }
    uv sync
} finally {
    Pop-Location
}

Write-Host "[setup] Done."
