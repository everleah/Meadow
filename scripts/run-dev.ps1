# Meadow PIM — start local development infrastructure (SurrealDB, NATS, MinIO, Redis).
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot

Push-Location $Root
try {
    docker compose -f docker-compose.dev.yml up -d
    docker compose -f docker-compose.dev.yml ps
} finally {
    Pop-Location
}
