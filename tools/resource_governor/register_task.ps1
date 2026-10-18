# Registers the logon task BossmanOne-6-Governor (user-level, no admin). Idempotent: replaces an existing task.
# The task runs the governor from this checkout with the owner's Python 3.12 (pythonw: no console window).
# Live from the first start (dry_run=false in governor.json); `-DryRun` registers it with --dry-run instead.
param([switch]$DryRun, [switch]$Disabled)
$ErrorActionPreference = 'Stop'
$py     = 'C:\Users\asd\AppData\Local\Programs\Python\Python312\pythonw.exe'
$repo   = 'C:\Users\asd\Bossman\wt-checkpoint-20261015'
$script = Join-Path $repo 'tools\resource_governor\governor.py'
$arg    = "-X utf8 `"$script`" run" + $(if ($DryRun) { ' --dry-run' } else { '' })
$action = New-ScheduledTaskAction -Execute $py -Argument $arg -WorkingDirectory $repo
$trig   = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$set    = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
            -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1)
$prin   = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName 'BossmanOne-6-Governor' -Action $action -Trigger $trig -Settings $set -Principal $prin `
    -Description 'Bossman resource governor: warm model files into RAM when idle; unload Ollama and handle Jeff while a generation runs.' -Force | Out-Null
if ($Disabled) { Disable-ScheduledTask -TaskName 'BossmanOne-6-Governor' | Out-Null }
Get-ScheduledTask -TaskName 'BossmanOne-6-Governor' | Select-Object TaskName, State
