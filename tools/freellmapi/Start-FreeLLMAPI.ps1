# Owner 18.10: FreeLLMAPI local gateway (MIT, audited: docs/audits/2026-10-18-freellmapi/README.md).
# Starts it hidden, BELOW_NORMAL, bound to 127.0.0.1:3001 ONLY, if the port is not already answering.
# No catalog sync (no phone-home), data under apps-local\freellmapi-data. Run by the logon task BossmanOne-7-FreeLLMAPI.
# Never expose with `tailscale serve`; HOST is hard-coded to loopback here on purpose.
$root = 'C:\Users\asd\Bossman\apps-local'
$app  = Join-Path $root 'freellmapi-audit'
$data = Join-Path $root 'freellmapi-data'
$logs = Join-Path $env:LOCALAPPDATA 'Bossman\CommandCenter\one-bossman'
New-Item -ItemType Directory -Force $logs, $data | Out-Null

function Test-Up([int]$port) {
    try { $c = New-Object Net.Sockets.TcpClient; $c.Connect('127.0.0.1', $port); $c.Close(); return $true } catch { return $false }
}
if (Test-Up 3001) { exit 0 }

# portable Node 22 (better-sqlite3 has no prebuilt binary for Node 20 on Windows; no global install, no admin)
$node = Join-Path $root 'node22\node.exe'
$env:HOST = '127.0.0.1'
$env:PORT = '3001'
$env:FREEAPI_DB_PATH = Join-Path $data 'freellmapi.db'
$env:CLIENT_DIST = Join-Path $app 'client\dist'
$env:CATALOG_SYNC_DISABLED = '1'
$env:TRUST_PROXY = 'false'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$log = Join-Path $logs "freellmapi-$stamp.log"
$p = Start-Process -FilePath $node -ArgumentList 'dist/index.js' -WorkingDirectory (Join-Path $app 'server') `
    -RedirectStandardOutput $log -RedirectStandardError "$log.err" -WindowStyle Hidden -PassThru
try { $p.PriorityClass = 'BelowNormal' } catch {}
