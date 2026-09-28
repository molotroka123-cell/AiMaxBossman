<#
Enable / disable the supervised 24/7 learning mode as a per-user Scheduled Task.
PROVIDED, NOT INSTALLED: the owner (or lead) runs it after the readiness gate says READY.

  # check first (must print LEARNING_247=READY)
  powershell -ExecutionPolicy Bypass -File tools\owner_journeys\learning_247_enable.ps1 -Check
  # install (refuses unless the gate is READY)
  powershell -ExecutionPolicy Bypass -File tools\owner_journeys\learning_247_enable.ps1 -Install
  # remove
  powershell -ExecutionPolicy Bypass -File tools\owner_journeys\learning_247_enable.ps1 -Uninstall
  # stop at any time without uninstalling: create the STOP file (or press the owner STOP in Bossman)
  New-Item -ItemType File "$StateDir\STOP"

The task runs at user logon with below-normal priority, one instance only, local
Ollama model only, and watches the owner's Computer-Use STOP file.
#>
param(
    [switch]$Check,
    [switch]$Install,
    [switch]$Uninstall,
    [string]$Repo = (Resolve-Path "$PSScriptRoot\..\..").Path,
    [string]$StateDir = "$env:USERPROFILE\Bossman\learning247",
    [string]$OwnerDataRoot = "$env:LOCALAPPDATA\Bossman\CommandCenter",
    [string]$Python = (Get-Command python).Source,
    [string]$TaskName = "Bossman Learning 24-7 (supervised)"
)
$ErrorActionPreference = "Stop"
$env:PYTHONPATH = "$Repo\command-center;$Repo\bossman-core;$Repo"

function Test-Ready {
    & $Python "$Repo\tools\owner_journeys\learning_247_readiness.py" --state-dir $StateDir
    return ($LASTEXITCODE -eq 0)
}

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output "removed: $TaskName"
    exit 0
}
if ($Check) { if (Test-Ready) { exit 0 } else { exit 1 } }
if ($Install) {
    if (-not (Test-Ready)) { Write-Error "readiness gate is NOT_READY; refusing to install"; exit 1 }
    $argLine = "-c `"import os,sys;os.environ['PYTHONPATH']=r'$Repo\command-center;$Repo\bossman-core;$Repo';" +
            "sys.path[:0]=os.environ['PYTHONPATH'].split(';');import runpy;sys.argv=['learning_supervisor'," +
            "'--state-dir',r'$StateDir','--owner-data-root',r'$OwnerDataRoot'];" +
            "runpy.run_path(r'$Repo\tools\owner_journeys\learning_supervisor.py',run_name='__main__')`""
    $action = New-ScheduledTaskAction -Execute $Python -Argument $argLine -WorkingDirectory $Repo
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $settings = New-ScheduledTaskSettingsSet -Priority 7 -MultipleInstances IgnoreNew `
        -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5) -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
        -Description "Bossman supervised learning (local models only; PAUSE/STOP aware; no outbound actions)" | Out-Null
    Write-Output "installed: $TaskName (starts at next logon; Start-ScheduledTask -TaskName '$TaskName' to start now)"
    exit 0
}
Write-Output "use -Check, -Install or -Uninstall"
