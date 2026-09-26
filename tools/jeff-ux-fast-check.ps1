# Fast deterministic Jeff UX preflight for coding agents.
# Purpose: catch cheap failures before spending model/context budget.
[CmdletBinding()]
param(
    [switch]$Browser,
    [switch]$Neighbours
)

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
Set-Location $Repo

$Python = Join-Path $Repo ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { $Python = "python" }

function Run-Step([string]$Name, [scriptblock]$Action) {
    Write-Host "==> $Name" -ForegroundColor Cyan
    & $Action
    if ($LASTEXITCODE -ne 0) {
        Write-Host "FAIL=$Name" -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

Run-Step "Python compile" {
    & $Python -m py_compile command-center/bcc/jeff_desktop.py tools/jeff_ux_packet.py
}

if (Get-Command node -ErrorAction SilentlyContinue) {
    Run-Step "Jeff JS syntax" { & node --check command-center/ui/jeff.js }
} else {
    Write-Host "SKIP=node unavailable" -ForegroundColor Yellow
}

Run-Step "Static authority + packet tests" {
    & $Python -m pytest tests/test_jeff_ux_packet.py command-center/tests/test_jeff_ux_isolation.py -q --tb=short --maxfail=1
}

Run-Step "Compact context packet" { & $Python tools/jeff_ux_packet.py }

if ($Browser) {
    Run-Step "Jeff browser smoke" {
        & $Python -m pytest command-center/tests/test_jeff_ux_browser.py -q --tb=short --maxfail=1
    }
}

if ($Neighbours) {
    Run-Step "Affected Bossman neighbours" {
        & $Python -m pytest command-center/tests/test_pit_foundation.py command-center/tests/test_pit_runtime.py command-center/tests/test_pit_cli.py command-center/tests/test_desktop_build_identity.py command-center/tests/test_ux2_desktop.py -q --tb=short --maxfail=1
    }
}

Write-Host "JEFF_UX_FAST_CHECK=PASS" -ForegroundColor Green
