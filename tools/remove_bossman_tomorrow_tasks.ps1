<#
.SYNOPSIS
  Removes every BossmanTomorrow-* scheduled task (and nothing else). Safe to run twice.
.DESCRIPTION
  Does not touch BossmanOne-*, evidence under handoff\tomorrow-20261008, keys, roots or the running Bossman.
  A step that is running right now is stopped by creating handoff\tomorrow-20261008\STOP (the step kills its own
  process tree); remove that file afterwards if you want to run steps by hand again.
#>
$ErrorActionPreference = 'Stop'
$tasks = Get-ScheduledTask -TaskName 'BossmanTomorrow-*' -ErrorAction SilentlyContinue
foreach ($t in $tasks) {
  if ($t.State -eq 'Running') { Stop-ScheduledTask -TaskName $t.TaskName }
  Unregister-ScheduledTask -TaskName $t.TaskName -Confirm:$false
  "removed $($t.TaskName)"
}
if (-not $tasks) { 'no BossmanTomorrow-* tasks registered' }
