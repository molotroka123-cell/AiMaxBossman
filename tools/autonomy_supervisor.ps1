<#
.SYNOPSIS
  Launcher of the BOUNDED autonomy supervisor (`bossman autonomy run`) for Windows Task Scheduler or a manual run.

.DESCRIPTION
  This file does NOT register anything and starts nothing by itself at import. Running it performs ONE bounded
  supervisor run: default 1 cycle, at most 2 hours, stops at the owner gate (USER_APPROVAL) and waits for YOU.

  What the script checks BEFORE it starts the supervisor (each failure exits with a code and starts nothing):
    5  the owner has not pinned the constitution (`bossman autonomy constitution pin`, interactive terminal, by hand)
    6  a STOP is set: <data>\computer\STOP (the owner's global STOP) or <data>\autonomy\STOP
    7  another run of this script is already going (named mutex)
    1  a limit is out of range (cycles 1..25, hours > 0 and <= 24: PowerShell parameter validation refuses it)
  The supervisor then checks the same things again on every pass, the daily budget (cycles / turns / usd per day,
  <data>\autonomy\budget.json, default 3 / 30 / 0 USD) and the engineering lease, and writes its heartbeat to
  <data>\autonomy\heartbeat.json. Autonomous apply stays OFF: a candidate is prepared for you as commands, you run them.

  There is no switch here that raises the autonomy level, the budget or the limits, and none must be added.

.NOTES
  Owner registration (manual, only when YOU decide; do it in an ordinary PowerShell of YOUR user, Claude Code and
  Codex must be logged in for that user; "run only when user is logged on"):

    $action  = New-ScheduledTaskAction -Execute 'powershell.exe' `
               -Argument '-NoProfile -ExecutionPolicy Bypass -File "<repo>\tools\autonomy_supervisor.ps1" -MaxCycles 1 -MaxHours 2'
    $trigger = New-ScheduledTaskTrigger -Daily -At 03:00
    $set     = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 3) `
               -StartWhenAvailable
    Register-ScheduledTask -TaskName 'BossmanAutonomySupervisor' -Action $action -Trigger $trigger -Settings $set `
               -Description 'Bounded Bossman autonomy supervisor (1 cycle, stops at the owner gate)'
    # remove it again:  Unregister-ScheduledTask -TaskName 'BossmanAutonomySupervisor' -Confirm:$false
    # emergency stop any time:  bossman autonomy stop   (or the palette / Telegram STOP)

.EXAMPLE
  pwsh -File tools\autonomy_supervisor.ps1 -Dry
  pwsh -File tools\autonomy_supervisor.ps1 -MaxCycles 1 -MaxHours 2 -ClaudeModel haiku
#>
[CmdletBinding()]
param(
  [ValidateRange(1, 25)][int]$MaxCycles = 1,
  [ValidateScript({ $_ -gt 0 -and $_ -le 24 })][double]$MaxHours = 2.0,
  [ValidateRange(0, 3600)][double]$IntervalS = 0,
  [switch]$Dry,
  [string]$Repo = '',
  [string]$DataDir = '',
  [string]$ClaudeModel = '',
  [string]$CodexModel = '',
  [int]$MaxCliTurns = 0,
  [string]$Python = ''
)

$ErrorActionPreference = 'Stop'
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $Repo) { $Repo = (Resolve-Path (Join-Path $ScriptRoot '..')).Path }
if (-not $DataDir) {
  $DataDir = if ($env:BCC_DATA_DIR) { $env:BCC_DATA_DIR } else { Join-Path $env:LOCALAPPDATA 'Bossman\CommandCenter' }
}
$AutonomyDir = Join-Path $DataDir 'autonomy'
$LogDir = Join-Path $AutonomyDir 'supervisor-logs'
$Pin = Join-Path $env:LOCALAPPDATA 'Bossman\autonomy\constitution.sha256'

function Stop-Launch([int]$Code, [string]$Message) {
  Write-Host "autonomy supervisor: $Message"
  exit $Code
}

# --- the owner's pin (only the owner writes it, in an interactive terminal; this script never does)
if (-not (Test-Path -LiteralPath $Pin)) {
  Stop-Launch 5 "the constitution is not pinned ($Pin is missing). Run ``bossman autonomy constitution pin`` yourself."
}

# --- STOP files: the owner's global STOP and the autonomy STOP
foreach ($stopFile in @((Join-Path $DataDir 'computer\STOP'), (Join-Path $AutonomyDir 'STOP'))) {
  if (Test-Path -LiteralPath $stopFile) { Stop-Launch 6 "STOP is set ($stopFile). Clear it where it was set, then run again." }
}

# --- one run at a time
$createdNew = $false
$mutex = New-Object System.Threading.Mutex($true, 'Global\BossmanAutonomySupervisor', [ref]$createdNew)
if (-not $createdNew) { Stop-Launch 7 'another autonomy supervisor run is already going.' }

try {
  New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
  $log = Join-Path $LogDir ("run-{0}.log" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))

  # --- the CLI: the installed `bossman` when present, otherwise this checkout's own code
  if (-not $Python) {
    $venv = Join-Path $Repo 'venv\Scripts\python.exe'
    $Python = if (Test-Path -LiteralPath $venv) { $venv } else { (Get-Command python -ErrorAction Stop).Source }
  }
  $env:PYTHONUTF8 = '1'
  $env:PYTHONPATH = (@((Join-Path $Repo 'command-center'), (Join-Path $Repo 'bossman-core'), $Repo) -join ';')
  $env:BCC_DATA_DIR = $DataDir

  $cli = @('-m', 'bcc.terminal_cli', 'autonomy', 'run', '--max-cycles', $MaxCycles, '--max-hours', $MaxHours,
           '--interval-s', $IntervalS, '--repo', $Repo, '--data-dir', $DataDir)
  if ($Dry) { $cli += '--dry' }
  if ($ClaudeModel) { $cli += @('--claude-model', $ClaudeModel) }
  if ($CodexModel) { $cli += @('--codex-model', $CodexModel) }
  if ($MaxCliTurns -gt 0) { $cli += @('--max-cli-turns', $MaxCliTurns) }

  "{0} start: cycles={1} hours={2} interval={3} dry={4} repo={5}" -f (Get-Date -Format o), $MaxCycles, $MaxHours, $IntervalS, $Dry.IsPresent, $Repo |
    Tee-Object -FilePath $log -Append
  Push-Location (Join-Path $Repo 'command-center')
  try {
    & $Python @cli 2>&1 | Tee-Object -FilePath $log -Append
    $code = $LASTEXITCODE
  } finally {
    Pop-Location
  }
  "{0} exit code: {1} (0 finished/waiting for the owner/idle, 1 errors, 5 blocked/refused, 6 stopped)" -f (Get-Date -Format o), $code |
    Tee-Object -FilePath $log -Append
  exit $code
} finally {
  $mutex.ReleaseMutex()
  $mutex.Dispose()
}
