# Registers the Windows Scheduled Task that runs one bounded niche-research tick every 30 minutes.
# NOT run automatically: the coordinator registers it after review.
#
#   pwsh -File niche_research\install_task.ps1 [-Python C:\path\python.exe] [-DataDir D:\...\niche-research] [-WhatIf]
#
# The task runs `python -m niche_research tick` from the repo root. A tick does bounded work and exits; after day 28
# the tick writes reports\final.md and marks the run stopped, so later ticks are no-ops. MultipleInstances=IgnoreNew
# keeps ticks from overlapping (the DB tick lease is a second guard). Remove with:
#   Unregister-ScheduledTask -TaskName BossmanNicheResearchTick -Confirm:$false
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$Python = (Get-Command python -ErrorAction SilentlyContinue).Source,
    [string]$DataDir = "",
    [string]$TaskName = "BossmanNicheResearchTick"
)
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
if (-not $Python) { throw "python not found; pass -Python" }

$envPart = ""
$logDir = Join-Path $env:LOCALAPPDATA "Bossman\niche-research"
if ($DataDir) { $envPart = "`$env:NICHE_RESEARCH_DIR='$DataDir'; "; $logDir = $DataDir }
New-Item -ItemType Directory -Force $logDir | Out-Null
$cmd = "${envPart}Set-Location '$repo'; & '$Python' -m niche_research tick *>> '$logDir\last_tick.log'"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -Command `"$cmd`"" -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) `
    -RepetitionInterval (New-TimeSpan -Minutes 30) -RepetitionDuration (New-TimeSpan -Days 29)
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 28) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

if ($PSCmdlet.ShouldProcess($TaskName, "Register-ScheduledTask (every 30 min, 29 days)")) {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
        -Principal $principal -Description "Bossman 28-day niche research: one bounded tick (no sending/publishing)" -Force
    Write-Host "Registered $TaskName. First run in ~2 minutes. Start the run first: python -m niche_research start"
}
