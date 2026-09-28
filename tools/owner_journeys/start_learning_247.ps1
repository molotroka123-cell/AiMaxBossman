<#
Bossman learning 24/7 - single entry point (autostart at logon, manual start, pause, stop).

  # start (background, below-normal priority, goal reports to the owner control bot "Pult")
  powershell -ExecutionPolicy Bypass -File tools\owner_journeys\start_learning_247.ps1
  # Task Scheduler action (keeps the task "Running"; register AFTER the Bossman backend task)
  powershell -ExecutionPolicy Bypass -WindowStyle Hidden -File tools\owner_journeys\start_learning_247.ps1 -Foreground
  # control
  ... start_learning_247.ps1 -Status | -Pause | -Resume | -Stop

-Stop writes <StateDir>\STOP: the loop exits within a minute and does NOT start again at the
next logon until -Resume (which removes STOP and PAUSE). /stop or /pause in the Pult bot
(or STOP in Bossman) halts learning too; "Continue" (/resume) resumes it.
Claude/Anthropic is never used; cloud = verified :free models, then at most the capped
paid model from <StateDir>\ladder.json (daily_cap_usd, default 0.50).
#>
param(
    [string]$Repo = (Resolve-Path "$PSScriptRoot\..\..").Path,
    [string]$StateDir = "$env:USERPROFILE\Bossman\learning247",
    [string]$OwnerDataRoot = "$env:LOCALAPPDATA\Bossman\CommandCenter",
    [string]$Python = "",
    [string]$KeyFile = "",
    [string]$BackendUrl = "",
    [int]$WaitBackendS = 300,
    [double]$MaxHours = 0,
    [ValidateSet("telegram", "off")][string]$Report = "telegram",
    [switch]$Foreground,
    [switch]$Status,
    [switch]$Pause,
    [switch]$Resume,
    [switch]$Stop
)
$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force $StateDir | Out-Null

if ($Stop)   { Set-Content -Path "$StateDir\STOP" -Value "owner stop $(Get-Date -Format o)"; Write-Output "STOP set: $StateDir\STOP"; exit 0 }
if ($Pause)  { Set-Content -Path "$StateDir\PAUSE" -Value "owner pause $(Get-Date -Format o)"; Write-Output "PAUSE set"; exit 0 }
if ($Resume) { Remove-Item "$StateDir\PAUSE", "$StateDir\STOP" -ErrorAction SilentlyContinue; Write-Output "PAUSE/STOP removed"; exit 0 }

function Get-SupervisorPid {
    try {
        $st = Get-Content "$StateDir\state.json" -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($st.pid -and (Get-Process -Id $st.pid -ErrorAction SilentlyContinue) -and
            $st.mode -notin @("STOPPED", "EXITED")) { return [int]$st.pid }
    } catch { }
    return $null
}

if ($Status) {
    $alive = Get-SupervisorPid
    $mode = try { (Get-Content "$StateDir\state.json" -Raw -Encoding UTF8 | ConvertFrom-Json).mode } catch { "none" }
    Write-Output "learning247: pid=$alive mode=$mode stop=$(Test-Path "$StateDir\STOP") pause=$(Test-Path "$StateDir\PAUSE")"
    exit 0
}
if (Test-Path "$StateDir\STOP") {
    Write-Output "STOP file present ($StateDir\STOP): the owner stopped learning. Run with -Resume to allow it again."
    exit 0
}
$running = Get-SupervisorPid
if ($running) { Write-Output "already running (pid $running)"; exit 0 }

if (-not $Python) {
    foreach ($cand in @("$Repo\runtime\python.exe", "$Repo\..\runtime\python.exe")) {
        if (Test-Path $cand) { $Python = (Resolve-Path $cand).Path; break }
    }
    if (-not $Python) { $Python = (Get-Command python -ErrorAction Stop).Source }
}
if ($KeyFile) {
    # the ladder reads OPENROUTER_API_KEY from this env-file at call time; the key itself is never copied
    $env:LEARNING247_KEY_FILE = $KeyFile
    $env:LEARNING247_STATE = $StateDir
    & $Python -c "import json,os,pathlib;p=pathlib.Path(os.environ['LEARNING247_STATE'],'ladder.json');d=json.loads(p.read_text(encoding='utf-8')) if p.is_file() else {};d['key_file']=os.environ['LEARNING247_KEY_FILE'];p.write_text(json.dumps(d,indent=2),encoding='utf-8')" 2>&1 | Out-Null
}
if ($BackendUrl) {
    # after the backend: wait until it answers (401 = up), but learning never depends on it
    $deadline = (Get-Date).AddSeconds($WaitBackendS)
    while ((Get-Date) -lt $deadline) {
        try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 "$BackendUrl/api/health" | Out-Null; break }
        catch { if ($_.Exception.Response) { break }; Start-Sleep -Seconds 10 }
    }
}

$env:PYTHONPATH = "$Repo\command-center;$Repo\bossman-core;$Repo"
$env:PYTHONIOENCODING = "utf-8"
$args2 = @("$Repo\tools\owner_journeys\learning_supervisor.py", "--state-dir", $StateDir,
           "--owner-data-root", $OwnerDataRoot, "--report", $Report)
if ($MaxHours -gt 0) { $args2 += @("--max-hours", "$MaxHours") }
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$log = "$StateDir\supervisor-$stamp.log"
if ($Foreground) {
    [System.Diagnostics.Process]::GetCurrentProcess().PriorityClass = "BelowNormal"
    & $Python @args2 *>> $log
    exit $LASTEXITCODE
}
$quoted = $args2 | ForEach-Object { '"' + $_ + '"' }
$p = Start-Process -FilePath $Python -ArgumentList $quoted -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $log -RedirectStandardError "$StateDir\supervisor-$stamp.err"
try { $p.PriorityClass = "BelowNormal" } catch { }
Write-Output "started pid $($p.Id); log $log"
