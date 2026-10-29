# Restart the owner's Command Center on :8801 from THIS checkout, on the owner's working DB.
# Gotcha 17.10: without BCC_DATA_DIR a source checkout silently starts on an empty command-center\data DB.
# Used by tools\long_horizon_run.py to interrupt a running task for real. Run only when no Direct Gen job is active.
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$env:BCC_DATA_DIR = Join-Path $env:LOCALAPPDATA "Bossman\CommandCenter"
$env:PYTHONUTF8 = "1"
$conn = Get-NetTCPConnection -LocalPort 8801 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($conn) { Stop-Process -Id $conn.OwningProcess -Force; Start-Sleep -Seconds 3 }
$log = Join-Path $env:TEMP "bossman-8801.log"
Start-Process -FilePath (Get-Command python).Source `
  -ArgumentList "-m", "uvicorn", "bcc.app:create_app", "--factory", "--host", "127.0.0.1", "--port", "8801" `
  -WorkingDirectory (Join-Path $repo "command-center") -WindowStyle Hidden `
  -RedirectStandardOutput $log -RedirectStandardError "$log.err"
for ($i = 0; $i -lt 60; $i++) {
  Start-Sleep -Seconds 3
  try { $r = Invoke-RestMethod http://127.0.0.1:8801/health/live -TimeoutSec 3; Write-Output "$($r.status) $($r.build_sha_short) $($r.source_identity)"; exit 0 } catch { }
}
throw "backend did not come back on :8801"
