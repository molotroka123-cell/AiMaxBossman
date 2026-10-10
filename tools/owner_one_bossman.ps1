<#
.SYNOPSIS
  Exactly ONE Bossman on the owner's machine: one install, one data root, one backend,
  one poller per Telegram bot, autostart at logon. Re-runnable with every new release ZIP.

.DESCRIPTION
  Actions (Switch runs them all in this order; each can be run alone):

    Snapshot   record what is there now: processes on the data root (command lines), scheduled
               tasks (XML), the three desktop shortcuts, the Jeff/«Пульт» configs and the Jeff voice
               launcher -> <Evidence>\snapshot-<stamp>\ ; writes <Evidence>\rollback.ps1
    Install    extract -Archive into <AppRoot>\BOSSMAN-Windows-x64-<sha12> (never over another build),
               check MANIFEST source_sha and every SHA256SUMS line
    Launchers  <LauncherDir>: owner_one_bossman.py + Bossman-Window/Terminal/Jeff .cmd wrappers
    StopOld    graceful stop of every Jeff/companion/backend process serving -DataDir that is NOT the
               new install (Jeff: its stop.flag; others: Ctrl+C to their console). Refuses to force.
    Backup     cold, verified backup of -DataDir (SQLite backup API + SHA-256 manifest) + verify
    Configure  core_url of Jeff (PIT) and «Пульт» configs -> the one backend; the Jeff voice launcher
               (voice\Start-Jeff-Voice.ps1) delegates to the one install, keeping the voice lines
    Tasks      logon tasks for the current user (Limited, no admin): BossmanOne-1-Backend ->
               BossmanOne-2-Jeff -> BossmanOne-3-Companion -> BossmanOne-4-LearningSupervisor
               (registered DISABLED until agent D delivers the supervisor); disables \BossmanJeff
    Start      run the tasks in order, wait for /health/live of the one backend
    Agents     apply -AgentPlan (JSON) through the backend API, before/after JSON in -Evidence
    Shortcuts  desktop «Bossman», «Bossman CMD», «Bossman Jeff» -> the wrappers (old ones kept)
    Status     one backend (backend.json PID), poller locks, processes on the data root -> JSON

  Never deletes owner files; never force-kills (StopOld reports what did not stop).

.EXAMPLE
  pwsh -File tools\owner_one_bossman.ps1 -Action Switch -Sha 84f5e0acce6a32c348330af39ff4a5874ade98a1 `
    -Archive C:\Users\asd\Bossman\rc19-build\84f5e0ac\dist\BOSSMAN-Windows-x64-84f5e0acce6a.zip `
    -ArchiveSha256 e05bf381e6db19a408ddc36f260ebdf13dbe5a5a79a6814736001397bae19019 `
    -AgentPlan C:\Users\asd\Bossman\evidence\rc19\k\agent-plan.json
#>
[CmdletBinding()]
param(
  [ValidateSet('Switch', 'Snapshot', 'Install', 'Launchers', 'StopOld', 'Backup', 'Configure', 'Tasks',
               'Start', 'Agents', 'Shortcuts', 'Status')]
  [string]$Action = 'Status',
  [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-f]{40}$')][string]$Sha,
  [string]$Archive = '',
  [string]$ArchiveSha256 = '',
  [string]$Root = '',
  [string]$AppRoot = '',
  [string]$LauncherDir = '',
  [string]$DataDir = '',
  [string]$CompanionConfig = '',
  [string]$Evidence = '',
  [string]$BackupRoot = '',
  [string]$AgentPlan = '',
  [string]$SystemPython = '',
  [ValidateRange(1024, 65535)][int]$Port = 8801,
  [ValidateRange(1024, 65535)][int]$JeffPort = 8850,
  [string]$Stamp = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2
if (-not $Root) { $Root = Join-Path $env:USERPROFILE 'Bossman' }
if (-not $AppRoot) { $AppRoot = Join-Path $Root 'app' }
if (-not $LauncherDir) { $LauncherDir = Join-Path $Root 'one-bossman' }
if (-not $DataDir) { $DataDir = Join-Path $env:LOCALAPPDATA 'Bossman\CommandCenter' }
if (-not $CompanionConfig) { $CompanionConfig = Join-Path $env:LOCALAPPDATA 'Bossman\telegram-companion\config.json' }
if (-not $Evidence) { $Evidence = Join-Path $Root 'evidence\rc19\k' }
if (-not $BackupRoot) { $BackupRoot = Join-Path $Root 'backups' }
if (-not $Stamp) { $Stamp = Get-Date -Format 'yyyyMMdd-HHmmss' }
if (-not $SystemPython) {
  $cand = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'
  $SystemPython = if (Test-Path $cand) { $cand } else { (Get-Command python).Source }
}
$bundle = "BOSSMAN-Windows-x64-$($Sha.Substring(0, 12))"
$HomeDir = Join-Path $AppRoot $bundle
$Py = Join-Path $HomeDir 'runtime\python.exe'
$PyW = Join-Path $HomeDir 'runtime\pythonw.exe'
$Tool = Join-Path $LauncherDir 'owner_one_bossman.py'
$SrcTool = Join-Path $PSScriptRoot 'owner_one_bossman.py'
$TaskNames = @('BossmanOne-1-Backend', 'BossmanOne-2-Jeff', 'BossmanOne-3-Companion', 'BossmanOne-4-LearningSupervisor')
$OldTasks = @('BossmanJeff')
$ShortcutNames = @('Bossman', 'Bossman CMD', 'Bossman Jeff')
$Desktop = [Environment]::GetFolderPath('Desktop')
$env:PYTHONIOENCODING = 'utf-8'
New-Item -ItemType Directory -Force $Evidence | Out-Null
$Log = Join-Path $Evidence "switch-$Stamp.log"

function Say([string]$m) { $line = "[one-bossman] $m"; Write-Host $line; Add-Content -LiteralPath $Log -Value $line -Encoding utf8 }
function Fail([string]$m) { Say "FAIL: $m"; exit 1 }
function Tool {
  # A simple function on purpose: $args keeps '--data-dir' style tokens verbatim.
  # The system Python has psutil for 'find'/'stop'; the tool is stdlib-only otherwise.
  $a = @($args | ForEach-Object { "$_" })
  $out = & $SystemPython -I $SrcTool @a 2>&1
  $code = $LASTEXITCODE
  $out | ForEach-Object { Add-Content -LiteralPath $Log -Value "$_" -Encoding utf8 }
  return @{ code = $code; text = ($out -join "`n") }
}

function Do-Snapshot {
  $snap = Join-Path $Evidence "snapshot-$Stamp"
  New-Item -ItemType Directory -Force $snap, (Join-Path $snap 'tasks'), (Join-Path $snap 'shortcuts'), (Join-Path $snap 'files') | Out-Null
  $r = Tool find --data-dir $DataDir
  Set-Content -LiteralPath (Join-Path $snap 'processes-on-data-root.json') -Value $r.text -Encoding utf8
  $state = @()
  foreach ($t in @(Get-ScheduledTask | Where-Object { $_.TaskName -in ($OldTasks + $TaskNames) })) {
    Export-ScheduledTask -TaskName $t.TaskName -TaskPath $t.TaskPath | Set-Content -LiteralPath (Join-Path $snap "tasks\$($t.TaskName).xml") -Encoding unicode
    $state += [pscustomobject]@{ name = $t.TaskName; path = $t.TaskPath; state = "$($t.State)" }
  }
  $state | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $snap 'tasks\state.json') -Encoding utf8
  foreach ($n in $ShortcutNames) {
    $l = Join-Path $Desktop "$n.lnk"
    if (Test-Path -LiteralPath $l) { Copy-Item -LiteralPath $l -Destination (Join-Path $snap "shortcuts\$n.lnk") }
  }
  $files = @{
    'pit-config.json'        = (Join-Path $DataDir 'pit-v1.7\config.json')
    'companion-config.json'  = $CompanionConfig
    'Start-Jeff-Voice.ps1'   = (Join-Path $DataDir 'voice\Start-Jeff-Voice.ps1')
  }
  $map = @{}
  foreach ($k in $files.Keys) {
    if (Test-Path -LiteralPath $files[$k]) { Copy-Item -LiteralPath $files[$k] -Destination (Join-Path $snap "files\$k"); $map[$k] = $files[$k] }
  }
  $map | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $snap 'files\map.json') -Encoding utf8
  Write-Rollback $snap
  Say "snapshot: $snap"
  return $snap
}

function Write-Rollback([string]$snap) {
  # Restores tasks, shortcuts and the three config/launcher files as they were in $snap, stops the
  # one-install processes gracefully. It never touches bcc.db: to go back to the old DB content,
  # restore the cold backup (see backups\owner-data-*\backup.json) with the old build stopped.
  $rb = @"
# Generated by tools/owner_one_bossman.ps1 at $Stamp. Rolls the owner machine back to snapshot:
#   $snap
# Usage:  pwsh -NoProfile -File "$Evidence\rollback.ps1"   (add -StartOld to start the old Jeff task)
param([switch]`$StartOld)
`$ErrorActionPreference = 'Continue'
`$snap = '$snap'
# 1) stop the one-install processes gracefully (Jeff stop.flag, then Ctrl+C), never forced
& '$SystemPython' -I '$Tool' stop --data-dir '$DataDir' --only-home '$HomeDir'
# 2) remove the new logon tasks
foreach (`$t in @('$($TaskNames -join "','")')) { Unregister-ScheduledTask -TaskName `$t -Confirm:`$false -ErrorAction SilentlyContinue }
# 3) old tasks back exactly as exported (state included)
`$state = Get-Content (Join-Path `$snap 'tasks\state.json') -Raw | ConvertFrom-Json
foreach (`$s in @(`$state)) {
  if (-not `$s) { continue }
  `$xml = Join-Path `$snap "tasks\`$(`$s.name).xml"
  if (`$s.name -like 'BossmanOne-*') { continue }
  Register-ScheduledTask -TaskName `$s.name -TaskPath `$s.path -Xml (Get-Content `$xml -Raw) -Force | Out-Null
  if (`$s.state -eq 'Disabled') { Disable-ScheduledTask -TaskName `$s.name -TaskPath `$s.path | Out-Null } else { Enable-ScheduledTask -TaskName `$s.name -TaskPath `$s.path | Out-Null }
}
# 4) shortcuts and files back (the current ones are kept beside with .one-bossman suffix)
Get-ChildItem (Join-Path `$snap 'shortcuts') -Filter *.lnk | ForEach-Object {
  `$dst = Join-Path '$Desktop' `$_.Name
  if (Test-Path -LiteralPath `$dst) { Copy-Item -LiteralPath `$dst -Destination "`$dst.one-bossman" -Force }
  Copy-Item -LiteralPath `$_.FullName -Destination `$dst -Force
}
`$map = Get-Content (Join-Path `$snap 'files\map.json') -Raw | ConvertFrom-Json
foreach (`$p in `$map.PSObject.Properties) {
  if (Test-Path -LiteralPath `$p.Value) { Copy-Item -LiteralPath `$p.Value -Destination "`$(`$p.Value).one-bossman" -Force }
  Copy-Item -LiteralPath (Join-Path `$snap "files\`$(`$p.Name)") -Destination `$p.Value -Force
}
# 5) agent model choices back through the API is only possible while a backend runs; the
#    before-values are in $Evidence\agents-before.json (PATCH /api/agents/<id>).
if (`$StartOld) { Start-ScheduledTask -TaskName 'BossmanJeff' -ErrorAction SilentlyContinue }
Write-Host "rollback done from `$snap. Old backend command line(s): see `$snap\processes-on-data-root.json"
"@
  Set-Content -LiteralPath (Join-Path $Evidence 'rollback.ps1') -Value $rb -Encoding utf8
}

function Do-Install {
  if (Test-Path -LiteralPath (Join-Path $HomeDir 'MANIFEST.json')) {
    $m = Get-Content -LiteralPath (Join-Path $HomeDir 'MANIFEST.json') -Raw | ConvertFrom-Json
    if ($m.source_sha -ne $Sha) { Fail "$HomeDir holds $($m.source_sha), not $Sha" }
    Say "already installed: $HomeDir"
  } else {
    if (-not (Test-Path -LiteralPath $Archive)) { Fail "archive not found: $Archive" }
    $h = (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLower()
    if ($ArchiveSha256 -and $h -ne $ArchiveSha256.ToLower()) { Fail "archive sha256 $h != $ArchiveSha256" }
    if (Test-Path -LiteralPath $HomeDir) { Fail "$HomeDir exists without a MANIFEST.json: not extracting over it" }
    Say "archive sha256 $h; extracting to $AppRoot"
    $tmp = Join-Path $AppRoot "_extract-$Stamp"
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [IO.Compression.ZipFile]::ExtractToDirectory($Archive, $tmp)
    if (-not (Test-Path -LiteralPath (Join-Path $tmp $bundle))) { Fail "archive has no $bundle folder" }
    Move-Item -LiteralPath (Join-Path $tmp $bundle) -Destination $HomeDir
    Remove-Item -LiteralPath $tmp -Recurse -Force
  }
  $m = Get-Content -LiteralPath (Join-Path $HomeDir 'MANIFEST.json') -Raw | ConvertFrom-Json
  if ($m.source_sha -ne $Sha) { Fail "MANIFEST source_sha $($m.source_sha) != $Sha" }
  $bad = 0; $n = 0
  foreach ($line in Get-Content -LiteralPath (Join-Path $HomeDir 'SHA256SUMS')) {
    $parts = $line -split '  ', 2
    if ($parts.Count -ne 2) { continue }
    $n++
    $f = Join-Path $HomeDir ($parts[1] -replace '/', '\')
    if (-not (Test-Path -LiteralPath $f) -or (Get-FileHash -LiteralPath $f -Algorithm SHA256).Hash.ToLower() -ne $parts[0]) { $bad++ }
  }
  if ($bad) { Fail "$bad of $n shipped files differ from SHA256SUMS" }
  Say "install verified: $HomeDir ($n files = SHA256SUMS, source_sha $Sha)"
}

function Write-Cmd([string]$name, [string[]]$tail) {
  $lines = @(
    '@echo off',
    'setlocal',
    "rem Generated by tools/owner_one_bossman.ps1 for $Sha - ONE install, ONE data root, ONE backend.",
    "set ""BOSSMAN_HOME=$HomeDir\""",
    "set ""BCC_DATA_DIR=$DataDir""",
    "set ""BOSSMAN_DATA_DIR=$DataDir""",
    "set ""BCC_PORT=$Port""",
    'set "BCC_HOST=127.0.0.1"',
    'set "BOSSMAN_URL="',
    'call "%BOSSMAN_HOME%app-support\_env.cmd"',
    'if errorlevel 1 exit /b 1',
    # the one backend first (no-op when backend.lock is held): window and terminal only attach
    """%BOSSMAN_HOME%runtime\python.exe"" -I ""$Tool"" launch backend --home ""%BOSSMAN_HOME%."" --data-dir ""%BCC_DATA_DIR%"" --port $Port"
  ) + $tail
  foreach ($l in $lines) { if ($l -match '[^\x20-\x7E]') { Fail "non-ASCII path in wrapper line: $l" } }
  $path = Join-Path $LauncherDir $name
  [IO.File]::WriteAllText($path, ($lines -join "`r`n") + "`r`n", [Text.Encoding]::ASCII)
  return $path
}

function Do-Launchers {
  if (-not (Test-Path -LiteralPath $Py)) { Fail "runtime missing: $Py (Install first)" }
  New-Item -ItemType Directory -Force $LauncherDir | Out-Null
  Copy-Item -LiteralPath $SrcTool -Destination $Tool -Force
  Write-Cmd 'Bossman-Window.cmd' @(
    "start """" ""%BOSSMAN_HOME%runtime\pythonw.exe"" -m bcc.desktop --host 127.0.0.1 --port $Port --no-show-token",
    'exit /b 0') | Out-Null
  Write-Cmd 'Bossman-Terminal.cmd' @(
    'call "%BOSSMAN_HOME%Bossman-Terminal.cmd" %*',
    'exit /b %ERRORLEVEL%') | Out-Null
  $jeff = @(
    '@echo off',
    "rem Generated by tools/owner_one_bossman.ps1 for $Sha - Jeff participant window over the one data root.",
    "start """" ""$PyW"" -I ""$Tool"" launch jeff-window --home ""$HomeDir"" --data-dir ""$DataDir"" --port $JeffPort",
    'exit /b 0')
  [IO.File]::WriteAllText((Join-Path $LauncherDir 'Bossman-Jeff.cmd'), ($jeff -join "`r`n") + "`r`n", [Text.Encoding]::ASCII)
  @{ sha = $Sha; home = $HomeDir; data_dir = $DataDir; port = $Port; jeff_port = $JeffPort; written = $Stamp } |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $LauncherDir 'current.json') -Encoding utf8
  Say "launchers written to $LauncherDir"
}

function Do-StopOld {
  $r = Tool stop --data-dir $DataDir --kinds 'jeff,companion,backend' --except-home $HomeDir
  Say "StopOld result:`n$($r.text)"
  if ($r.code -ne 0) { Fail 'a process did not stop gracefully (not forced). See the log; decide before retrying.' }
}

function Do-Backup {
  $dest = Join-Path $BackupRoot "owner-data-$Stamp"
  $r = Tool backup --src $DataDir --dest $dest
  if ($r.code -ne 0) { Fail "backup failed: $($r.text)" }
  Copy-Item -LiteralPath (Join-Path $dest 'backup.json') -Destination (Join-Path $Evidence "backup-$Stamp.json")
  $v = Tool verify --dir $dest
  if ($v.code -ne 0) { Fail "backup verify failed" }
  $meta = Get-Content -LiteralPath (Join-Path $dest 'backup.json') -Raw | ConvertFrom-Json
  Say "backup: $dest files=$($meta.files) bytes=$($meta.bytes) manifest_sha256=$($meta.manifest_sha256) (verified)"
  # Owner order 07.10: provider keys must not be lost. Copy the keys folder and its DPAPI copies
  # (protected for this Windows user) into a sibling folder, outside the verified data manifest.
  # Names are never printed.
  $keysHome = Join-Path $env:LOCALAPPDATA 'Bossman'
  foreach ($sub in @('keys', 'backups\keys')) {
    $src = Join-Path $keysHome $sub
    if (Test-Path -LiteralPath $src) {
      $to = Join-Path "$dest-keys" ($sub -replace '\\', '-')
      New-Item -ItemType Directory -Force -Path "$dest-keys" | Out-Null
      Copy-Item -LiteralPath $src -Destination $to -Recurse -Force
      Say "backup: copied $sub ($(@(Get-ChildItem -LiteralPath $to -File).Count) file(s))"
    }
  }
}

function Do-Configure {
  $cfgs = @((Join-Path $DataDir 'pit-v1.7\config.json'))
  if (Test-Path -LiteralPath $CompanionConfig) { $cfgs += $CompanionConfig }
  $args_ = @('repoint', '--port', "$Port")
  foreach ($c in $cfgs) { $args_ += @('--config', $c) }
  $r = Tool @args_
  Say "repoint: $($r.text -replace '\s+', ' ')"
  # Jeff voice launcher: keep the owner's voice lines, run Jeff from the one install.
  $voice = Join-Path $DataDir 'voice\Start-Jeff-Voice.ps1'
  if (Test-Path -LiteralPath $voice) {
    $old = Get-Content -LiteralPath $voice
    $keep = @($old | Where-Object { $_ -match '^\s*\$env:BOSSMAN_PIT_TTS_[A-Z_]+\s*=' })
    Copy-Item -LiteralPath $voice -Destination "$voice.before-one-bossman-$Stamp"
    $new = @("`$ErrorActionPreference = 'Stop'",
             "# one-bossman ($Sha): Jeff runs from the ONE install. The voice lines below are the owner's",
             "# choice and are read by the launcher; change the voice here and re-run this script.") + $keep + @(
             "& '$Py' -I '$Tool' launch jeff --home '$HomeDir' --data-dir '$DataDir'",
             'exit $LASTEXITCODE')
    Set-Content -LiteralPath $voice -Value $new -Encoding utf8
    Say "voice launcher now delegates to the one install (kept: $($keep.Count) voice line(s))"
  }
}

function New-OneTask([string]$name, [string]$kindArgs, [int]$delaySec, [bool]$enabled) {
  $user = "$env:USERDOMAIN\$env:USERNAME"
  # --ensure: the same task re-checks every 10 minutes and starts only what is down and not held
  # by `stop --hold` (owner 07.10: Bossman, Jeff and the Пульт must always run).
  $act = New-ScheduledTaskAction -Execute $PyW -Argument "-I `"$Tool`" launch $kindArgs --ensure" -WorkingDirectory $HomeDir
  $trg = New-ScheduledTaskTrigger -AtLogOn -User $user
  if ($delaySec -gt 0) { $trg.Delay = "PT${delaySec}S" }
  $set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
           -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
  $pr = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
  $triggers = @($trg)
  if ($enabled) { $triggers += New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) -RepetitionInterval (New-TimeSpan -Minutes 10) }
  $t = Register-ScheduledTask -TaskName $name -Action $act -Trigger $triggers -Settings $set -Principal $pr `
         -Description "One Bossman ($Sha) - tools/owner_one_bossman.ps1" -Force
  if (-not $enabled) { Disable-ScheduledTask -TaskName $name | Out-Null }
  Say "task $name ($(if ($enabled) { 'enabled' } else { 'DISABLED' }), logon +${delaySec}s): $PyW -I $Tool launch $kindArgs"
}

function Do-Tasks {
  $common = "--home `"$HomeDir`" --data-dir `"$DataDir`""
  New-OneTask $TaskNames[0] "backend $common --port $Port" 0 $true
  New-OneTask $TaskNames[1] "jeff $common --port $Port" 30 $true
  New-OneTask $TaskNames[2] "companion $common --port $Port --companion-config `"$CompanionConfig`"" 45 $true
  New-OneTask $TaskNames[3] "supervisor $common --port $Port" 90 $false
  foreach ($o in $OldTasks) {
    $t = Get-ScheduledTask -TaskName $o -ErrorAction SilentlyContinue
    if ($t -and $t.State -ne 'Disabled') { Disable-ScheduledTask -TaskName $o | Out-Null; Say "old task $o disabled (XML in the snapshot)" }
  }
}

function Health { try { return Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health/live" -TimeoutSec 5 } catch { return $null } }

function Do-Start {
  Start-ScheduledTask -TaskName $TaskNames[0]
  $deadline = (Get-Date).AddSeconds(180); $h = $null
  while (-not $h -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 1; $h = Health }
  if (-not $h) { Fail "no /health/live on $Port after starting $($TaskNames[0])" }
  if ($h.build_sha -ne $Sha) { Fail "backend on $Port is build $($h.build_sha), expected $Sha" }
  Say "backend healthy on $Port build $($h.build_sha)"
  Start-ScheduledTask -TaskName $TaskNames[1]; Start-Sleep -Seconds 12
  Start-ScheduledTask -TaskName $TaskNames[2]; Start-Sleep -Seconds 8
}

function Do-Agents {
  if (-not $AgentPlan) { Say 'no -AgentPlan: agents unchanged'; return }
  $r = Tool agents --port "$Port" --data-dir $DataDir --plan $AgentPlan --evidence $Evidence
  Say "agents: $($r.text -replace '\s+', ' ')"
  if ($r.code -ne 0) { Fail 'agent plan not applied exactly' }
}

function Do-Shortcuts {
  $snapLnk = Join-Path $Evidence "snapshot-$Stamp\shortcuts"
  New-Item -ItemType Directory -Force $snapLnk | Out-Null
  $ws = New-Object -ComObject WScript.Shell
  $icon = (Join-Path $HomeDir 'icons\bossman.ico') + ',0'
  foreach ($spec in @(@{ n = 'Bossman'; w = 'Bossman-Window.cmd'; s = 7 },
                      @{ n = 'Bossman CMD'; w = 'Bossman-Terminal.cmd'; s = 1 },
                      @{ n = 'Bossman Jeff'; w = 'Bossman-Jeff.cmd'; s = 7 })) {
    $lnk = Join-Path $Desktop "$($spec.n).lnk"
    if ((Test-Path -LiteralPath $lnk) -and -not (Test-Path -LiteralPath (Join-Path $snapLnk "$($spec.n).lnk"))) {
      Copy-Item -LiteralPath $lnk -Destination (Join-Path $snapLnk "$($spec.n).lnk")
    }
    $l = $ws.CreateShortcut($lnk)
    $l.TargetPath = $env:ComSpec
    $l.Arguments = "/c """"$(Join-Path $LauncherDir $spec.w)"""""
    $l.WorkingDirectory = $HomeDir
    $l.WindowStyle = $spec.s
    $l.IconLocation = $icon
    $l.Description = "one-bossman $Sha port=$Port data=$DataDir"
    $l.Save()
    Say "shortcut $lnk -> $($spec.w)"
  }
}

function Do-Status {
  $r = Tool status --data-dir $DataDir --companion-config $CompanionConfig
  $f = Tool find --data-dir $DataDir
  $out = Join-Path $Evidence "status-$Stamp.json"
  Set-Content -LiteralPath $out -Value ("{`"status`": $($r.text),`n`"processes`": $($f.text)}") -Encoding utf8
  Say "status -> $out"
  Write-Host $r.text; Write-Host $f.text
}

switch ($Action) {
  'Snapshot' { Do-Snapshot | Out-Null }
  'Install' { Do-Install }
  'Launchers' { Do-Launchers }
  'StopOld' { Do-StopOld }
  'Backup' { Do-Backup }
  'Configure' { Do-Configure }
  'Tasks' { Do-Tasks }
  'Start' { Do-Start }
  'Agents' { Do-Agents }
  'Shortcuts' { Do-Shortcuts }
  'Status' { Do-Status }
  'Switch' {
    Do-Snapshot | Out-Null
    Do-Install
    Do-Launchers
    Do-StopOld
    Do-Backup
    Do-Configure
    Do-Tasks
    Do-Start
    Do-Agents
    Do-Shortcuts
    Do-Status
    Say "SWITCH DONE: $bundle on 127.0.0.1:$Port, data $DataDir. Rollback: $Evidence\rollback.ps1"
  }
}
exit 0
