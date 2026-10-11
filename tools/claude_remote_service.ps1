# Claude Code Remote Control keeper for the owner's PC (owner request, 2026-10-11).
# Used by the pult (/remote) and by the logon task BossmanClaudeRemote after a reboot.
#   - waits for the network (claude.ai:443) up to -NetworkWaitMinutes;
#   - never starts a second Remote Control with the same session name;
#   - runs tools/start_claude_remote.ps1 (branch + origin/HEAD checks live there);
#   - bounded restarts (-MaxRestarts) with backoff; -Once = a single attempt (pult);
#   - log: %LOCALAPPDATA%\Bossman\remote\claude-remote.log (no secrets are written);
#   - STOP file  %LOCALAPPDATA%\Bossman\remote\STOP      stops the loop,
#     DISABLED   %LOCALAPPDATA%\Bossman\remote\DISABLED  skips autostart entirely.
# No password, token or key is read or stored here.
param(
    [string]$Checkout = (Split-Path -Parent $PSScriptRoot),
    [string]$Name = 'Bossman-Owner-PC',
    [ValidateSet('default', 'acceptEdits', 'bypassPermissions')]
    [string]$PermissionMode = 'default',
    [int]$NetworkWaitMinutes = 10,
    [int]$MaxRestarts = 5,
    [switch]$Once
)
$ErrorActionPreference = 'Continue'
$dir = Join-Path $env:LOCALAPPDATA 'Bossman\remote'
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$log = Join-Path $dir 'claude-remote.log'
function Log([string]$m) { ('{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m) | Out-File -FilePath $log -Append -Encoding utf8 }
function Live {
    Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -and $_.CommandLine -match 'remote-control' -and $_.CommandLine -match [regex]::Escape($Name) -and $_.CommandLine -notmatch '--help'
    }
}
if (Test-Path (Join-Path $dir 'DISABLED')) { Log 'DISABLED file present: not starting'; exit 0 }
Remove-Item -LiteralPath (Join-Path $dir 'STOP') -ErrorAction SilentlyContinue
$starter = Join-Path $Checkout 'tools\start_claude_remote.ps1'
if (-not (Test-Path $starter)) { Log "starter missing: $starter"; exit 2 }
Log "keeper start checkout=$Checkout name=$Name mode=$PermissionMode once=$($Once.IsPresent)"

$deadline = (Get-Date).AddMinutes($NetworkWaitMinutes)
while ($true) {
    $ok = $false
    try { $ok = (Test-NetConnection -ComputerName 'claude.ai' -Port 443 -InformationLevel Quiet -WarningAction SilentlyContinue) } catch { $ok = $false }
    if ($ok) { break }
    if ((Get-Date) -gt $deadline) { Log "network not available after $NetworkWaitMinutes min: giving up"; exit 3 }
    Start-Sleep -Seconds 15
}
Log 'network ok'

$attempt = 0
while ($true) {
    if (Test-Path (Join-Path $dir 'STOP')) { Log 'STOP file: exit'; exit 0 }
    if (Live) { Log "already running: $((Live | Select-Object -First 1).ProcessId); not starting a second one"; exit 0 }
    $attempt++
    Log "attempt ${attempt}: starting remote-control"
    $started = Get-Date
    Set-Location -LiteralPath $Checkout
    & powershell -NoProfile -ExecutionPolicy Bypass -File $starter -PermissionMode $PermissionMode 2>&1 |
        ForEach-Object { $line = "$_"; if ($line -match 'Connected|https://claude\.ai/code/session_|error|fail|Wrong branch|origin/HEAD|not installed') { Log $line } }
    $code = $LASTEXITCODE
    $ranFor = ((Get-Date) - $started).TotalSeconds
    Log "remote-control exited code=$code after ${ranFor}s"
    if ($Once) { exit $code }
    if ($attempt -ge $MaxRestarts) { Log "max restarts ($MaxRestarts) reached: stop"; exit 4 }
    if ($ranFor -gt 600) { $attempt = 0 }   # a long healthy run resets the budget
    Start-Sleep -Seconds ([Math]::Min(300, 30 * $attempt))
}
