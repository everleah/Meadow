# Meadow PIM — restore the local SurrealDB from a `backups/*.surql` export.
# Usage:  .\scripts\restore-db.ps1 [-File .\backups\meadow-20260101-120000.surql]
param(
    [Parameter(Mandatory = $true)]
    [string]$File
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$AbsFile = if (Test-Path $File) { (Resolve-Path $File).Path } else { Join-Path $Root $File }
if (-not (Test-Path $AbsFile)) {
    Write-Host "Backup file not found: $AbsFile" -ForegroundColor Red
    exit 1
}

# Copy the backup into the container, then import it (the image has no shell, so the surrealdb
# binary is invoked directly and the file path is passed as a positional argument).
$Container = docker compose -f docker-compose.dev.yml ps -q surrealdb
if (-not $Container) { throw "SurrealDB container is not running (start it with docker compose up -d)" }

Push-Location $Root
try {
    docker cp $AbsFile "$($Container):/tmp/meadow-restore.surql"
    if ($LASTEXITCODE -ne 0) { throw "docker cp failed (exit $LASTEXITCODE)" }

    docker exec $Container /surreal import `
        --namespace meadow --database meadow `
        --endpoint http://localhost:8000 --username root --password root `
        /tmp/meadow-restore.surql
    if ($LASTEXITCODE -ne 0) { throw "surreal import failed (exit $LASTEXITCODE)" }

    Write-Host "Restored from $AbsFile"
} catch {
    Write-Host "Restore failed: $_" -ForegroundColor Red
    exit 1
} finally {
    Pop-Location
}
