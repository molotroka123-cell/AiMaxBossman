<#
.SYNOPSIS
  Registers the BossmanTomorrow-* one-shot tasks for 2026-10-08 (the only clock Bossman does not have itself).
.DESCRIPTION
  Each task runs `python tools\bossman_tomorrow.py <step>` which respects STOP, the $1/day cap, the serial heavy lock
  and Bossman's own queue (see that file). Current user, "run only when user is logged on", Limited, BELOW_NORMAL
  priority (Task Scheduler priority 7), one instance, hidden window. Idempotent: -Force replaces a same-named task.
  Cancel everything:  pwsh -File tools\remove_bossman_tomorrow_tasks.ps1
  Cancel one:         Unregister-ScheduledTask -TaskName BossmanTomorrow-2-Replay -Confirm:$false
  Emergency stop:     create C:\Users\asd\Bossman\handoff\tomorrow-20261008\STOP  (or the usual Bossman STOP)
  Does NOT touch BossmanOne-* tasks, owner keys, roots, the constitution pin, or any running Bossman.
#>
[CmdletBinding()]
param(
  [string]$Python = (Get-Command python).Source,
  [string]$Repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path,
  [datetime]$Day = [datetime]'2026-10-08'
)
$ErrorActionPreference = 'Stop'
$plan = @(
  @{ n = 'BossmanTomorrow-1-SelfRepair';  step = 'selfrepair'; at = '01:00'; hours = 2.75 },
  @{ n = 'BossmanTomorrow-2-Replay';      step = 'replay';     at = '03:30'; hours = 1.6 },
  @{ n = 'BossmanTomorrow-3-UxSweep';     step = 'ux';         at = '05:05'; hours = 1.1 },
  @{ n = 'BossmanTomorrow-4-SitePublish'; step = 'publish';    at = '06:15'; hours = 0.6 },
  @{ n = 'BossmanTomorrow-5-Dossier';     step = 'dossier';    at = '06:45'; hours = 0.3 },
  @{ n = 'BossmanTomorrow-6-Report';      step = 'report';     at = '08:00'; hours = 0.2 }
)
$tool = Join-Path $Repo 'tools\bossman_tomorrow.py'
if (-not (Test-Path $tool)) { throw "missing $tool" }
foreach ($p in $plan) {
  $when = $Day.Date + [timespan]::Parse($p.at)
  $arg = "-NoProfile -WindowStyle Hidden -Command `"& '$Python' -X utf8 '$tool' $($p.step)`""
  $act = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arg -WorkingDirectory $Repo
  $trg = New-ScheduledTaskTrigger -Once -At $when
  $set = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -Priority 7 `
         -ExecutionTimeLimit (New-TimeSpan -Hours $p.hours) -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries
  $pr = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
  Register-ScheduledTask -TaskName $p.n -Action $act -Trigger $trg -Settings $set -Principal $pr -Force `
    -Description "Bossman day plan 2026-10-08, step $($p.step); cancel: tools\remove_bossman_tomorrow_tasks.ps1" | Out-Null
  '{0,-32} {1:yyyy-MM-dd HH:mm}  limit {2} h' -f $p.n, $when, $p.hours
}
