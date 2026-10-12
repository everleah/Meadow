<#
.SYNOPSIS
    Builds (and optionally pushes) the Meadow PIM service images.

.DESCRIPTION
    Every image is tagged twice: with the immutable tag (the short git SHA by
    default, `-dirty` appended when the worktree is not clean) and with `latest`.
    Pin the immutable tag in `.env.vps` on the server - that is what makes a
    deploy reproducible and a rollback a one-line change.

    The images are built here on the workstation and only pulled by the server
    (see plan/detail/betrieb-deployment.md §5). Registry credentials come from
    `docker login`.

    Bash twin: scripts/build-images.sh.

.PARAMETER Push
    Pushes both tags of all three images to the registry.

.PARAMETER NoCache
    Rebuilds every layer from scratch (`docker build --no-cache`).

.PARAMETER Tag
    Immutable tag to use instead of the git short SHA.

.PARAMETER Registry
    Registry host and path (e.g. registry.example.com/meadow). The workstation
    has no `.env.vps`, so this is how the value gets in - otherwise it is read
    from the environment or from `.env.vps`.

.EXAMPLE
    powershell -File scripts/build-images.ps1 -Registry registry.example.com/meadow -Push
#>
[CmdletBinding()]
param(
    [switch]$Push,
    [switch]$NoCache,
    [string]$Tag,
    [string]$Registry
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Push-Location (Join-Path $PSScriptRoot '..')
try {
    if (-not (Test-Path 'docker-compose.vps.yml')) {
        throw "Run this from the repository (docker-compose.vps.yml not found in $(Get-Location))"
    }

    # The registry lives in .env.vps, like every other deployment setting. Only
    # that single key is read, so a value with a '#' in it is never mangled.
    $registry = $Registry
    if (-not $registry) { $registry = $env:PIM_REGISTRY }
    if (-not $registry -and (Test-Path '.env.vps')) {
        foreach ($line in Get-Content '.env.vps') {
            if ($line -match '^\s*PIM_REGISTRY\s*=\s*(.+?)\s*$') {
                $registry = $Matches[1].Trim('"')
                break
            }
        }
    }
    if (-not $registry) {
        throw 'No registry: pass -Registry <host/path>, set $env:PIM_REGISTRY or provide .env.vps'
    }

    if (-not $Tag) {
        $Tag = (git rev-parse --short HEAD).Trim()
        if ($LASTEXITCODE -ne 0) { throw 'git rev-parse failed - is this a git worktree?' }
        if (git status --porcelain) { $Tag = "$Tag-dirty" }
    }

    Write-Host "==> Registry $registry, tag $Tag (plus latest)"
    $services = @('core', 'frontend', 'worker')
    foreach ($service in $services) {
        $image = "$registry/pim-$service"
        Write-Host "==> Building $image`:$Tag"
        $buildArgs = @('build')
        if ($NoCache) { $buildArgs += '--no-cache' }
        $buildArgs += @('-f', "docker/Dockerfile.$service", '-t', "${image}:${Tag}", '-t', "${image}:latest", '.')
        & docker @buildArgs
        if ($LASTEXITCODE -ne 0) { throw "docker build failed for $image" }
    }

    if ($Push) {
        Write-Host "==> Pushing (docker login $registry must have happened)"
        foreach ($service in $services) {
            $image = "$registry/pim-$service"
            & docker push "${image}:${Tag}"
            if ($LASTEXITCODE -ne 0) { throw "docker push failed for ${image}:${Tag}" }
            & docker push "${image}:latest"
            if ($LASTEXITCODE -ne 0) { throw "docker push failed for ${image}:latest" }
        }
        Write-Host '==> Pin this tag on the server (.env.vps):'
        Write-Host "    PIM_CORE_TAG=$Tag"
        Write-Host "    PIM_FRONTEND_TAG=$Tag"
        Write-Host "    PIM_WORKER_TAG=$Tag"
    }
    else {
        Write-Host "==> Images built locally; pass -Push to push them to $registry"
    }
}
finally {
    Pop-Location
}
