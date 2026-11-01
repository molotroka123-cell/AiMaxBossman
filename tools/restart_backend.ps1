# Restart the owner's Command Center on :8801 from THIS checkout, on the owner's working DB.
# Gotcha 17.10: without BCC_DATA_DIR a source checkout silently starts on an empty command-center\data DB.
# Used by tools\long_horizon_run.py to interrupt a running task for real. Run only when no Direct Gen job is active.
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$env:BCC_DATA_DIR = Join-Path $env:LOCALAPPDATA "Bossman\CommandCenter"
$env:PYTHONUTF8 = "1"
# Owner audit 01.11: global editable installs of bossman_shared / bossman pointed at ANOTHER checkout, so the backend
# ran a mixed tree. Every sibling package must come from THIS checkout (build_identity reports a mixed tree as UNKNOWN).
$env:PYTHONPATH = "$repo;$(Join-Path $repo 'command-center');$(Join-Path $repo 'bossman-core')"
$conn = Get-NetTCPConnection -LocalPort 8801 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($conn) { Stop-Process -Id $conn.OwningProcess -Force; Start-Sleep -Seconds 3 }
$log = Join-Path $env:TEMP "bossman-8801.log"
$py = (Get-Command python).Source
$workDir = Join-Path $repo "command-center"
$cmdLine = "cmd.exe /c `"set BCC_DATA_DIR=$($env:BCC_DATA_DIR)&& set PYTHONUTF8=1&& set PYTHONPATH=$($env:PYTHONPATH)&& `"$py`" -m uvicorn bcc.app:create_app --factory --host 127.0.0.1 --port 8801 > `"$log`" 2> `"$log.err`"`""
$si = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ ShowWindow = [uint16]0 }
Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
  CommandLine = $cmdLine
  CurrentDirectory = $workDir
  ProcessStartupInformation = $si
} | Out-Null
for ($i = 0; $i -lt 60; $i++) {
  Start-Sleep -Seconds 3
  try { $r = Invoke-RestMethod http://127.0.0.1:8801/health/live -TimeoutSec 3; Write-Output "$($r.status) $($r.build_sha_short) $($r.source_identity)"; exit 0 } catch { }
}
throw "backend did not come back on :8801"
