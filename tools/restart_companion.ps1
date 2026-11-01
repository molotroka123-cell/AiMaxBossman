# Restart the owner's Telegram companion («пульт») from THIS checkout via the official start script.
# Owner 01.11: needed to pick up the /key -> backend provider-key fix. Lesson: do NOT set BCC_DATA_DIR for the
# companion (config.load() then looks for companion.env under CommandCenter\ and reports TELEGRAM_NOT_CONFIGURED).
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$py = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
$old = Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and $_.CommandLine -match 'telegram_companion' }
foreach ($p in $old) { Stop-Process -Id $p.ProcessId -Force; Write-Output "stopped $($p.ProcessId)" }
Start-Sleep -Seconds 2
Start-Process powershell -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File',(Join-Path $repo 'scripts\Start-TelegramCompanion.ps1'),'-RepoRoot',"`"$repo`"",'-Python',"`"$py`"",'-SkipChecks' -WindowStyle Hidden
Start-Sleep -Seconds 10
$new = Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and $_.CommandLine -match 'telegram_companion' }
if (-not $new) { throw "companion did not start" }
Write-Output "companion pid $($new.ProcessId) ALIVE"
