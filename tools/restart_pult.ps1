# Restart the owner's mobile pult on :8840 from THIS checkout (owner audit 01.11: same-source rule as restart_backend.ps1).
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$conn = Get-NetTCPConnection -LocalPort 8840 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($conn) { Stop-Process -Id $conn.OwningProcess -Force; Start-Sleep -Seconds 2 }
$env:PYTHONUTF8 = "1"
$env:PYTHONPATH = "$(Join-Path $repo 'apps\bossman-mobile');$repo;$(Join-Path $repo 'command-center');$(Join-Path $repo 'bossman-core')"
Start-Process python -ArgumentList '-X','utf8','-m','bossmobile','serve','--port','8840' -WorkingDirectory (Join-Path $repo 'apps\bossman-mobile') -WindowStyle Hidden
for ($i = 0; $i -lt 20; $i++) {
  Start-Sleep -Seconds 1
  try { $r = Invoke-WebRequest http://127.0.0.1:8840/m/ -UseBasicParsing -TimeoutSec 3; Write-Output "pult $($r.StatusCode)"; exit 0 } catch { }
}
throw "pult did not come back on :8840"
