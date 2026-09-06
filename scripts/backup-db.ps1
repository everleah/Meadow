# Meadow PIM — export the local SurrealDB into `backups/`.
# Usage:  .\scripts\backup-db.ps1 [-OutDir backups] [-RetentionDays 14]
param(
    [string]$OutDir = "backups",
    [int]$RetentionDays = 14
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$BackupDir = Join-Path $Root $OutDir
New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null

$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$File = Join-Path $BackupDir "meadow-${Stamp}.surql"

# The dev container exposes SurrealDB on loopback:8000 with root/root (see docker-compose.dev.yml).
# Export inside the container to avoid Windows PowerShell's UTF-16 redirection, then copy it out.
$Container = docker compose -f docker-compose.dev.yml ps -q surrealdb
if (-not $Container) { throw "SurrealDB container is not running (start it with docker compose up -d)" }

Push-Location $Root
try {
    docker exec $Container /surreal export `
        --namespace meadow --database meadow `
        --endpoint http://localhost:8000 --username root --password root `
        /tmp/meadow-backup.surql
    if ($LASTEXITCODE -ne 0) { throw "surreal export failed (exit $LASTEXITCODE)" }

    docker cp "$($Container):/tmp/meadow-backup.surql" $File
    if ($LASTEXITCODE -ne 0) { throw "docker cp failed (exit $LASTEXITCODE)" }

    Write-Host "Backup written to $File"
} catch {
    Write-Host "Backup failed: $_" -ForegroundColor Red
    exit 1
} finally {
    Pop-Location
}

# Cleanup old backups beyond the retention window.
Get-ChildItem -Path $BackupDir -Filter "meadow-*.surql" |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-$RetentionDays) } |
    ForEach-Object { Remove-Item $_.FullName -Force }
