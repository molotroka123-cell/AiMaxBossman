# Registers the two FX collection tasks (BossmanFX-AM 09:00, BossmanFX-PM 21:00 local time). Touches NO other task.
#   powershell -File apps\war-terminal\tools\register_fx_tasks.ps1            # install runtime copy + (re)register both tasks
#   powershell -File apps\war-terminal\tools\register_fx_tasks.ps1 -Remove    # delete only these two tasks
# The tasks run from a runtime copy of the Python package in %LOCALAPPDATA%\Bossman\CommandCenter\fx\runtime, so they keep working when
# a git worktree is deleted; run this script again after the code changes. Current-user logon session, StartWhenAvailable, battery allowed.
param([switch]$Remove)

$names = @{ AM = 'BossmanFX-AM'; PM = 'BossmanFX-PM' }
if ($Remove) {
    foreach ($n in $names.Values) { Unregister-ScheduledTask -TaskName $n -Confirm:$false -ErrorAction SilentlyContinue }
    'removed'; return
}
$appRoot = Split-Path $PSScriptRoot -Parent                      # apps/war-terminal
$rt = Join-Path $env:LOCALAPPDATA 'Bossman\CommandCenter\fx\runtime'
New-Item -ItemType Directory -Force $rt | Out-Null
if (Test-Path (Join-Path $rt 'warterminal')) { Remove-Item -Recurse -Force (Join-Path $rt 'warterminal') }
New-Item -ItemType Directory -Force (Join-Path $rt 'warterminal') | Out-Null
Get-ChildItem (Join-Path $appRoot 'warterminal') -Filter *.py | Copy-Item -Destination (Join-Path $rt 'warterminal')
Copy-Item (Join-Path $appRoot 'warterminal\connectors') (Join-Path $rt 'warterminal\connectors') -Recurse -Exclude '__pycache__'
Copy-Item (Join-Path $PSScriptRoot 'fx_daily.ps1') $rt -Force
$script = Join-Path $rt 'fx_daily.ps1'

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2)
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
foreach ($slot in 'AM', 'PM') {
    $at = if ($slot -eq 'AM') { '09:00' } else { '21:00' }
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$script`" -Slot $slot"
    Register-ScheduledTask -TaskName $names[$slot] -Action $action -Trigger (New-ScheduledTaskTrigger -Daily -At $at) -Settings $settings -Principal $principal `
        -Description "Bossman FX tracker ($slot): CNB + ECB daily rates; skips while a job is loading or RAM < 20 GB" -Force | Out-Null
}
Get-ScheduledTask -TaskName $names.Values | Select-Object TaskName, State | Format-Table -AutoSize
