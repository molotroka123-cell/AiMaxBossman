# Owner 18.10: self-improvement orchestra, FREE ONLY. One hidden BELOW_NORMAL background process.
#   start : powershell -File tools\Start-FreeOrchestra.ps1 [-Cycles 6] [-Hours 4] [-ClientSrc <checkout at the running server's SHA>]
#   stop  : POST /api/autonomy/stop (or `bossman autonomy stop`)  -> the orchestra kills its cycle, cancels the task and exits;
#           clear with /api/autonomy/resume. Hard stop: file docs\evidence\limits-out-orchestra\STOP_ORCHESTRA.
# It pauses by itself while any direct-gen job is queued/loading/generating. Preflight only: python tools\limits_out_orchestra.py --dry
param([int]$Cycles = 6, [double]$Hours = 4, [string]$ClientSrc = '')
$root = Split-Path -Parent $PSScriptRoot
$py = 'C:\Users\asd\AppData\Local\Programs\Python\Python312\python.exe'
$a = @('-X', 'utf8', (Join-Path $root 'tools\limits_out_orchestra.py'), '--max-cycles', $Cycles, '--max-hours', $Hours, '--send')
if ($ClientSrc) { $a += @('--client-src', $ClientSrc) }
$logs = Join-Path $env:LOCALAPPDATA 'Bossman\CommandCenter\one-bossman'
New-Item -ItemType Directory -Force $logs | Out-Null
$log = Join-Path $logs ("free-orchestra-{0}.log" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
$env:PYTHONDONTWRITEBYTECODE = '1'
$p = Start-Process -FilePath $py -ArgumentList $a -WorkingDirectory $root -RedirectStandardOutput $log -RedirectStandardError "$log.err" `
    -WindowStyle Hidden -PassThru
try { $p.PriorityClass = 'BelowNormal' } catch {}
"started pid $($p.Id), log $log"
