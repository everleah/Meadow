#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Runs every verification gate of Meadow PIM (the same set as CI).

.DESCRIPTION
    Mirrors scripts/check.sh, which Forgejo Actions runs (see
    .forgejo/workflows/ci.yml). Keep both files in sync when a gate changes.

    The Rust core must not be running: on Windows a running `pim-core.exe`
    locks `target\debug\pim-core.exe` while relinking.

.EXAMPLE
    ./scripts/check.ps1
    ./scripts/check.ps1 -Selection rust
#>
param(
    [ValidateSet('all', 'rust', 'python')]
    [string]$Selection = 'all'
)

$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')

function Invoke-Gate {
    param(
        [Parameter(Mandatory)][string]$Label,
        [Parameter(Mandatory)][string[]]$Command
    )
    Write-Host ''
    Write-Host "==> $Label" -ForegroundColor Cyan
    # Native tools write diagnostics to stderr (cargo/rustc do); with
    # $ErrorActionPreference = 'Stop' Windows PowerShell would treat that as a
    # terminating error, so the exit code is inspected explicitly instead.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Command[0] $Command[1..($Command.Length - 1)]
        $code = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previous
    }
    if ($code -ne 0) {
        throw "Gate failed: $Label (exit code $code)"
    }
}

$rustGates = @(
    @{ Label = 'cargo fmt --check'; Command = @('cargo', 'fmt', '--check') }
    @{ Label = 'cargo clippy --all-targets -- -D warnings'; Command = @('cargo', 'clippy', '--all-targets', '--', '-D', 'warnings') }
    @{ Label = 'cargo test'; Command = @('cargo', 'test') }
)

$pythonGates = @(
    @{ Label = 'uv sync'; Command = @('uv', 'sync', '--quiet') }
    @{ Label = 'ruff check'; Command = @('uv', 'run', 'ruff', 'check', 'meadow', 'tests') }
    @{ Label = 'ruff format --check'; Command = @('uv', 'run', 'ruff', 'format', '--check', 'meadow', 'tests') }
    @{ Label = 'pytest'; Command = @('uv', 'run', 'pytest', '-q') }
)

$gates = @()
if ($Selection -in @('all', 'rust')) { $gates += $rustGates }
if ($Selection -in @('all', 'python')) { $gates += $pythonGates }

try {
    foreach ($gate in $gates) {
        Invoke-Gate -Label $gate.Label -Command $gate.Command
    }
}
catch {
    Write-Host ''
    Write-Host $_ -ForegroundColor Red
    exit 1
}

Write-Host ''
Write-Host 'All checks passed.' -ForegroundColor Green
exit 0
