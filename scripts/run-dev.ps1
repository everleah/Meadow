# Meadow PIM — start local development infrastructure (SurrealDB, NATS, MinIO, Redis).
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot

Push-Location $Root
try {
    docker compose -f docker-compose.dev.yml up -d
    docker compose -f docker-compose.dev.yml ps
    Write-Host ""
    Write-Host "[run-dev] Reminder: the core and the worker need the SAME LOG_INGEST_API_KEY"
    Write-Host "[run-dev] (machine access to the core; without it every worker write gets 401)."
    Write-Host '[run-dev]   $env:LOG_INGEST_API_KEY = "dev-machine-key"   # before core AND worker'
} finally {
    Pop-Location
}
