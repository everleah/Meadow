#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Runs the deliberate AI-slop scan of Meadow PIM.

.DESCRIPTION
    Companion to scripts/deslop.sh; keep both files in sync (same contract as
    scripts/check.ps1 and scripts/check.sh). Rationale and workflow:
    plan/detail/ai-code-quality.md.

    This is a quality signal, not a verdict. It never replaces the gates in
    scripts/check.ps1, it never proves runtime correctness, and it never rewrites
    code on its own: the Python deep dive only reports. Repairs stay deliberate,
    one category at a time, with the focused tests re-run afterwards.

    Reports land in .tmp/deslop/ (gitignored). The committed baseline in
    .stopslop-baseline.json keeps known findings out of the way, so only new
    findings fail the run; -Strict ignores the baseline and shows the complete
    current state instead.

    Findings of the lower tier are reported but do not fail the run - that is the
    upstream default, and stopslop 0.5.1 cannot change it (no `--fail-on-tier`).

.EXAMPLE
    ./scripts/deslop.ps1
    ./scripts/deslop.ps1 -Selection staged
    ./scripts/deslop.ps1 -Selection changed -Strict
    ./scripts/deslop.ps1 -FixPlan
#>
param(
    [ValidateSet('all', 'changed', 'staged')]
    [string]$Selection = 'all',
    [switch]$Strict,
    [switch]$FixPlan
)

$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')

$StopslopVersion = '0.5.1'
$SlopDetectorVersion = '4.0'
$ReportDir = '.tmp/deslop'

if (-not (Get-Command stopslop -ErrorAction SilentlyContinue)) {
    Write-Host 'stopslop is missing. Install the pinned version:' -ForegroundColor Red
    Write-Host "  cargo install stopslop --version $StopslopVersion --locked"
    exit 2
}

$installed = (& stopslop --version) -replace '^stopslop\s+', ''
if ($installed -ne $StopslopVersion) {
    Write-Host "warning: stopslop $installed is installed, the repository pins $StopslopVersion" -ForegroundColor Yellow
}

# stopslop 0.5.1 has no git-aware selection (`--staged`/`--changed` are documented
# upstream but not in this release), so the file list comes from git. An empty
# selection exits clean instead of scanning the whole tree. As upstream, untracked
# files are not part of `changed`.
if ($Selection -eq 'all') {
    $scanArgs = @('.')
}
else {
    $diffFilter = if ($Selection -eq 'staged') { '--cached' } else { 'HEAD' }
    $scanArgs = @(git diff --name-only --diff-filter=ACMR $diffFilter)
    if ($scanArgs.Count -eq 0) {
        Write-Host "Nothing to scan for selection '$Selection'."
        exit 0
    }
}

New-Item -ItemType Directory -Force -Path $ReportDir | Out-Null
$jsonReport = Join-Path $ReportDir "stopslop-$Selection.json"

# The baseline keeps the accepted findings of the repository out of the way;
# -Strict shows the complete current state instead.
$baselineArgs = @()
if ($Strict) {
    $emptyBaseline = Join-Path $ReportDir 'empty-baseline.json'
    [System.IO.File]::WriteAllText($emptyBaseline, '{"accepted": {}}', (New-Object System.Text.UTF8Encoding($false)))
    $baselineArgs = @("--baseline=$emptyBaseline")
}

Write-Host ''
Write-Host "==> stopslop ($Selection)" -ForegroundColor Cyan

# Native tools write diagnostics to stderr (stopslop's update notice, the deep
# dive's progress lines). With $ErrorActionPreference = 'Stop' PowerShell would turn
# that into a terminating error, so the exit codes are inspected explicitly - the
# same pattern as scripts/check.ps1.
$previous = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
try {
    $json = & stopslop @($scanArgs + $baselineArgs + @('--format', 'json'))
    $jsonStatus = $LASTEXITCODE

    if ($json) {
        # Written as UTF-8 without BOM: PowerShell 5.1 redirection would write UTF-16
        # and produce a file that JSON consumers cannot read.
        [System.IO.File]::WriteAllText(
            $jsonReport,
            ($json -join [Environment]::NewLine),
            (New-Object System.Text.UTF8Encoding($false))
        )
        Write-Host "report: $jsonReport"
    }

    & stopslop @($scanArgs + $baselineArgs)
    $status = [Math]::Max($jsonStatus, $LASTEXITCODE)

    if ($FixPlan) {
        Write-Host ''
        Write-Host "==> Python deep dive (ai-slop-detector $SlopDetectorVersion, report only)" -ForegroundColor Cyan
        if (Get-Command uv -ErrorAction SilentlyContinue) {
            # The Python we ship plus our tooling. `tests` is excluded by the tool
            # itself, and scratch files under `.tmp/` are not part of the project,
            # so they must not show up in a quality report.
            foreach ($deepPath in @('meadow', 'scripts')) {
                & uv run slop-detector $deepPath --no-color
                $deepReport = Join-Path $ReportDir "slop-detector-$deepPath.json"
                & uv run slop-detector $deepPath --json --output $deepReport | Out-Null
                Write-Host "report: $deepReport"
            }
        }
        else {
            Write-Host 'uv is missing, so the Python deep dive is skipped (uv sync, then retry)' -ForegroundColor Yellow
        }
        Write-Host 'Repairs are deliberate: one category, focused tests, then scripts/check.ps1 python.'
    }
}
finally {
    $ErrorActionPreference = $previous
}

if ($status -ne 0) {
    Write-Host ''
    Write-Host 'Findings were reported. Investigate before suppressing, and repair per' -ForegroundColor Red
    Write-Host 'category - details in plan/detail/ai-code-quality.md.' -ForegroundColor Red
}

exit $status
