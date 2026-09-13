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

# 4. Standard bundles (optional): classification data lives in resources/standards/
#    (gitignored). A missing or empty folder is fine - the system runs without it.
$Standards = Join-Path $Root "resources\standards"
$Bundles = @()
if (Test-Path $Standards) {
    $Bundles = Get-ChildItem -Path $Standards -Directory |
        Where-Object { Test-Path (Join-Path $_.FullName "manifest.json") }
}
if ($Bundles.Count -eq 0) {
    Write-Host "[setup] no standard bundles in resources/standards - skipping (optional)."
} else {
    $CoreUp = $false
    try {
        $null = Invoke-WebRequest -Uri "http://127.0.0.1:8080/health" -TimeoutSec 3 -UseBasicParsing
        $CoreUp = $true
    } catch {
        $CoreUp = $false
    }
    if ($CoreUp) {
        Push-Location $Root
        try {
            uv run python scripts/install_standard_bundle.py --all
        } finally {
            Pop-Location
        }
    } else {
        Write-Host "[setup] $($Bundles.Count) standard bundle(s) found, but pim-core is not running."
        Write-Host "[setup]   start the core, then: uv run python scripts/install_standard_bundle.py --all"
    }
}
