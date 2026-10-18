# Daily FX collection (CNB + ECB public files, one free-model cross-check). Run by the tasks BossmanFX-AM (09:00) and BossmanFX-PM (21:00).
# The owner's phone generations have ABSOLUTE priority: the Python side first looks at GET http://127.0.0.1:8801/api/direct-gen/jobs and,
# while a job is loading (or free RAM < 20 GB; a job already generating does not block), waits 20 minutes and looks again (at most 3 retries), then skips this slot.
# No browser, no Ollama, no GPU: two small HTTP GETs and (optionally) one free cloud-model call through Bossman. Runs at BelowNormal priority.
param([ValidateSet('AM', 'PM')][string]$Slot = 'AM')

$ErrorActionPreference = 'Continue'
[System.Diagnostics.Process]::GetCurrentProcess().PriorityClass = 'BelowNormal'
$root = if (Test-Path (Join-Path $PSScriptRoot 'warterminal')) { $PSScriptRoot } else { Split-Path $PSScriptRoot -Parent }   # runtime copy, or apps/war-terminal in the repo
$py = 'C:\Users\asd\AppData\Local\Programs\Python\Python312\python.exe'
if (-not (Test-Path $py)) { $py = 'python' }
$logs = Join-Path $env:LOCALAPPDATA 'Bossman\CommandCenter\fx\logs'
New-Item -ItemType Directory -Force $logs | Out-Null
$log = Join-Path $logs ("fx-{0}-{1}.log" -f (Get-Date -Format 'yyyyMMdd'), $Slot)
"[{0}] slot={1} root={2}" -f (Get-Date -Format 'o'), $Slot, $root | Add-Content -Encoding utf8 $log
$env:PYTHONUTF8 = '1'
Push-Location $root
try {
    & $py -X utf8 -m warterminal fx collect --wait-idle 2>&1 | Add-Content -Encoding utf8 $log
    "[{0}] exit={1}" -f (Get-Date -Format 'o'), $LASTEXITCODE | Add-Content -Encoding utf8 $log
    exit $LASTEXITCODE
}
finally { Pop-Location }
