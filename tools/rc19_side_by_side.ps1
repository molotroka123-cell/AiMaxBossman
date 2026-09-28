<#
.SYNOPSIS
  Side-by-side install, start/stop/restart and rollback check of ONE Bossman Windows build,
  without touching the owner's data or the installs that are already there.

.DESCRIPTION
  Mechanical steps for the RC19 release (docs/owner/RC19_RELEASE_PROCEDURE.md):

    Build          clean clone of -Sha, hash-pinned build tools, locked bundle build
    Install        extract the archive next to (never over) the existing installs
    Start          start the bundled backend (runtime\python.exe -I -m bcc) on -Port
    Status         /health/live, build SHA, the single process that listens on -Port
    Stop           clean stop (console Ctrl+C -> uvicorn graceful shutdown), forced only as fallback
    Restart        Stop + Start, then prove the data written before the restart is still there
    RollbackCheck  the previous installs are untouched: launcher + runtime + MANIFEST present
    Rehearse       Install (if needed) -> Start -> write marker -> Stop -> Start -> marker? -> Stop -> RollbackCheck
    Shortcuts      write "Bossman <Label>" (window) and "Bossman <Label> CMD" (terminal) shortcuts for ONE
                   install, ONE -Port and ONE -DataDir into -ShortcutDir (default: the evidence folder;
                   putting them on the Desktop is a separate, deliberate decision). Both go through
                   small wrappers in <InstallRoot>\rc-launchers that set BCC_DATA_DIR/BCC_PORT, because a
                   .lnk cannot carry environment variables. Existing shortcuts are never modified: a
                   file of the same name that this script did not write is a refusal.
    ShortcutTest   open both shortcuts through explorer.exe in both orders and prove that exactly one
                   backend (one PID on -Port, build -Sha) serves the window and the terminal
    StopRC         stop every process that runs from THIS RC install (window, terminal, backend)

  Refusals (exit code 2), by construction and not by convention:
    * the data dir is, contains, or lies inside %LOCALAPPDATA%\Bossman\CommandCenter (owner data);
    * something already listens on -Port when Start is requested;
    * the data dir is already served by a backend on another port or build (<data>\backend.lock held,
      holder named by <data>\backend.json, or bcc.app exit code 5); the same port and build is
      reported as ALREADY RUNNING and exits 0;
    * the install folder exists and holds a DIFFERENT build than -Sha;
    * the archive's SHA-256 differs from -ArchiveSha256 when one is given;
    * the backend reports a build SHA different from -Sha.

  Works with Windows PowerShell 5.1 and PowerShell 7.

.EXAMPLE
  pwsh -File tools\rc19_side_by_side.ps1 -Action Rehearse -Sha 762e96d2c46bdf3ede17045a46114fbd3edf2c7b `
       -Archive "$env:USERPROFILE\Bossman\rc19-build\762e96d2\dist\BOSSMAN-Windows-x64-762e96d2c46b.zip" `
       -DataDir "$env:USERPROFILE\Bossman\rc19-data\a-rehearsal" -Port 8831
  (-Root defaults to %USERPROFILE%\Bossman; -InstallRoot to <Root>\rc19-install\<sha8>.)
#>
[CmdletBinding()]
param(
  [ValidateSet('Build', 'Install', 'Start', 'Status', 'Stop', 'Restart', 'RollbackCheck', 'Rehearse',
               'Shortcuts', 'ShortcutTest', 'StopRC')]
  [string]$Action = 'Rehearse',
  [string]$ShortcutDir = '',
  [string]$Label = '1.9 RC',
  [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-f]{40}$')][string]$Sha,
  [ValidateRange(1024, 65535)][int]$Port = 8831,
  [string]$DataDir = '',
  [string]$Archive = '',
  [string]$ArchiveSha256 = '',
  [string]$Root = '',
  [string]$InstallRoot = '',
  [string]$BuildRoot = '',
  [string]$PreviousInstalls = '',
  [string]$SourceRepo = '',
  [string]$BuildPython = '',
  [int]$StartTimeoutSec = 180
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2
$short = $Sha.Substring(0, 8)
if (-not $Root) { $Root = Join-Path $env:USERPROFILE 'Bossman' }
if (-not $ShortcutDir) { $ShortcutDir = Join-Path $Root 'evidence\rc19\a\shortcuts' }
$bundleName = "BOSSMAN-Windows-x64-$($Sha.Substring(0, 12))"
if (-not $InstallRoot) { $InstallRoot = Join-Path $Root "rc19-install\$short" }
if (-not $BuildRoot) { $BuildRoot = Join-Path $Root "rc19-build\$short" }
if (-not $PreviousInstalls) { $PreviousInstalls = Join-Path $Root 'app' }
if (-not $Archive) { $Archive = Join-Path $BuildRoot "dist\$bundleName.zip" }
$Home_ = Join-Path $InstallRoot $bundleName
$Python = Join-Path $Home_ 'runtime\python.exe'

function Say([string]$m) { Write-Host "[rc19] $m" }
function Refuse([string]$m) { Write-Host "[rc19] REFUSED: $m" -ForegroundColor Red; exit 2 }
function Fail([string]$m) { Write-Host "[rc19] FAIL: $m" -ForegroundColor Red; exit 1 }

function Full([string]$p) {
  return [IO.Path]::GetFullPath($p).TrimEnd('\')
}

function Assert-SafeDataDir {
  if (-not $DataDir) { Refuse '-DataDir is required (a fresh folder for this rehearsal)' }
  $local = $env:LOCALAPPDATA
  if (-not $local) { $local = Join-Path $env:USERPROFILE 'AppData\Local' }
  $owner = Full (Join-Path $local 'Bossman\CommandCenter')
  $d = Full $DataDir
  $cmp = [StringComparison]::OrdinalIgnoreCase
  if ($d.Equals($owner, $cmp) -or $d.StartsWith($owner + '\', $cmp) -or $owner.StartsWith($d + '\', $cmp)) {
    Refuse "data dir $d is, contains or lies inside the owner data root $owner"
  }
  # A junction or symlink could point back into the owner root: resolve every existing segment.
  $probe = $d
  while ($probe) {
    if (Test-Path -LiteralPath $probe) {
      $item = Get-Item -LiteralPath $probe -Force
      if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        Refuse "data dir path segment $probe is a junction/symlink; use a plain folder"
      }
    }
    $parent = Split-Path -Parent $probe
    if ($parent -eq $probe) { break }
    $probe = $parent
  }
}

function Listeners([int]$p) {
  $c = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue
  if (-not $c) { return @() }
  return @($c | Select-Object -ExpandProperty OwningProcess -Unique)
}

function Pid-File { return Join-Path $DataDir "_rc19\backend-$Port.pid" }
function Log-Dir { return Join-Path $DataDir '_rc19' }

function Health {
  try {
    return Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health/live" -TimeoutSec 5
  } catch { return $null }
}

function Token {
  $t = Join-Path $DataDir 'token'
  if (Test-Path -LiteralPath $t) { return (Get-Content -LiteralPath $t -Raw).Trim() }
  return $null
}

function Do-Build {
  $src = Join-Path $BuildRoot 'src'
  $venv = Join-Path $BuildRoot 'venv'
  if (-not $SourceRepo) { $SourceRepo = (git rev-parse --show-toplevel) }
  if (-not $BuildPython) { $BuildPython = 'py' }
  New-Item -ItemType Directory -Force $BuildRoot | Out-Null
  if (-not (Test-Path $src)) {
    git -c core.autocrlf=false clone --no-checkout $SourceRepo $src
    if ($LASTEXITCODE) { Fail 'clone failed' }
    git -C $src config core.autocrlf false
    git -C $src config core.eol lf
    git -C $src checkout --detach $Sha
    if ($LASTEXITCODE) { Fail "checkout $Sha failed (is the commit in $SourceRepo?)" }
  }
  if ((git -C $src rev-parse HEAD) -ne $Sha) { Fail "clean clone is not at $Sha" }
  if (git -C $src status --porcelain) { Fail 'clean clone is dirty' }
  if (-not (Test-Path (Join-Path $venv 'Scripts\python.exe'))) {
    if ($BuildPython -eq 'py') { py -3.12 -m venv $venv } else { & $BuildPython -m venv $venv }
    if ($LASTEXITCODE) { Fail 'venv creation failed (Python 3.12 required by the lock)' }
  }
  $vpy = Join-Path $venv 'Scripts\python.exe'
  & $vpy -m pip install --disable-pip-version-check --require-hashes --requirement (Join-Path $src 'tools\windows_bundle_build_tools.txt')
  if ($LASTEXITCODE) { Fail 'pinned build tools did not install' }
  $env:PYTHONUTF8 = '1'
  Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
  Push-Location $src
  try {
    $sw = [Diagnostics.Stopwatch]::StartNew()
    & $vpy tools/app_icons.py --check
    if ($LASTEXITCODE) { Fail 'icon check failed' }
    & $vpy tools/build_windows_bundle.py --out (Join-Path $BuildRoot 'dist') --zip --profile release
    if ($LASTEXITCODE) { Fail "bundle build exit $LASTEXITCODE" }
    Say "build seconds: $([int]$sw.Elapsed.TotalSeconds)"
  } finally { Pop-Location }
  $h = (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLower()
  Say "archive: $Archive"
  Say "bytes:   $((Get-Item -LiteralPath $Archive).Length)"
  Say "sha256:  $h"
}

function Do-Install {
  if (-not (Test-Path -LiteralPath $Archive)) { Fail "archive not found: $Archive" }
  $h = (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLower()
  Say "archive sha256 $h"
  if ($ArchiveSha256 -and $h -ne $ArchiveSha256.ToLower()) { Refuse "archive sha256 $h != expected $ArchiveSha256" }
  $manifestPath = Join-Path $Home_ 'MANIFEST.json'
  if (Test-Path -LiteralPath $manifestPath) {
    $m = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    if ($m.source_sha -ne $Sha) { Refuse "$Home_ already holds build $($m.source_sha)" }
    Say "already installed: $Home_ (source_sha $($m.source_sha))"
    return
  }
  if ((Test-Path -LiteralPath $InstallRoot) -and (Get-ChildItem -LiteralPath $InstallRoot -Force | Select-Object -First 1)) {
    Refuse "install root $InstallRoot is not empty and has no $bundleName"
  }
  $prev = Full $PreviousInstalls
  if ((Full $InstallRoot).StartsWith($prev, [StringComparison]::OrdinalIgnoreCase)) {
    Refuse "install root must be outside the existing installs folder $prev"
  }
  New-Item -ItemType Directory -Force $InstallRoot | Out-Null
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  $sw = [Diagnostics.Stopwatch]::StartNew()
  [IO.Compression.ZipFile]::ExtractToDirectory($Archive, $InstallRoot)
  Say "extracted in $([int]$sw.Elapsed.TotalSeconds) s to $Home_"
  $m = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
  if ($m.source_sha -ne $Sha) { Fail "MANIFEST source_sha $($m.source_sha) != $Sha" }
  # Every shipped file against SHA256SUMS (the archive's own statement of its bytes).
  $bad = 0; $n = 0
  foreach ($line in Get-Content -LiteralPath (Join-Path $Home_ 'SHA256SUMS')) {
    $parts = $line -split '  ', 2
    if ($parts.Count -ne 2) { continue }
    $n++
    $f = Join-Path $Home_ ($parts[1] -replace '/', '\')
    if (-not (Test-Path -LiteralPath $f) -or (Get-FileHash -LiteralPath $f -Algorithm SHA256).Hash.ToLower() -ne $parts[0]) { $bad++ }
  }
  if ($bad) { Fail "$bad of $n shipped files differ from SHA256SUMS" }
  Say "SHA256SUMS verified: $n files"
}

function Holder {
  # One backend per data root (bcc/backend_lock.py): <data>\backend.lock is held with an OS
  # byte-range lock by the serving process and <data>\backend.json names it. The info is
  # trusted only while the lock is really held: if we can take the byte lock ourselves,
  # nobody serves this data dir and backend.json is stale.
  $lockPath = Join-Path $DataDir 'backend.lock'
  if (-not (Test-Path -LiteralPath $lockPath)) { return $null }
  $fs = $null
  try {
    $fs = [IO.File]::Open($lockPath, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::ReadWrite)
    try { $fs.Lock(0, 1); $fs.Unlock(0, 1); return $null } catch [IO.IOException] { }
  } catch { return $null } finally { if ($fs) { $fs.Dispose() } }
  $info = Join-Path $DataDir 'backend.json'
  if (-not (Test-Path -LiteralPath $info)) { return @{ pid = '?'; port = '?'; build_sha = '?' } }
  try { return (Get-Content -LiteralPath $info -Raw | ConvertFrom-Json) } catch { return @{ pid = '?'; port = '?'; build_sha = '?' } }
}

function Report-Holder($h, [string]$why) {
  Say ("ALREADY RUNNING ({0}): data dir {1} is served by pid {2} on {3}:{4}, build {5}, kind {6}" -f $why,
       (Full $DataDir), $h.pid, $(if ($h.host) { $h.host } else { '127.0.0.1' }), $h.port, $h.build_sha,
       $(if ($h.kind) { $h.kind } else { '?' }))
  if ("$($h.port)" -eq "$Port" -and "$($h.build_sha)" -eq $Sha) {
    Say 'same port and same build: nothing to start, attach to it'
    exit 0
  }
  Refuse "the data dir is held by another backend (port $($h.port), build $($h.build_sha)); stop it first or use another -DataDir"
}

function Do-Start {
  Assert-SafeDataDir
  if (-not (Test-Path -LiteralPath $Python)) { Fail "bundled runtime missing: $Python (run -Action Install)" }
  $held = Holder
  if ($held) { Report-Holder $held 'backend.lock is held' }
  $busy = @(Listeners $Port)
  if ($busy.Count) { Refuse "port $Port already has a listener (PID $($busy -join ','))" }
  New-Item -ItemType Directory -Force (Log-Dir) | Out-Null
  $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
  $out = Join-Path (Log-Dir) "backend-$Port-$stamp.out.log"
  $err = Join-Path (Log-Dir) "backend-$Port-$stamp.err.log"
  # Owner data isolation: BCC_DATA_DIR for the backend, BOSSMAN_DATA_DIR for the PIT/terminal
  # helpers that read that name. The runtime is started in isolated mode (-I); PYTHONUTF8 is an
  # environment variable that -I ignores, so UTF-8 mode is requested with -X utf8.
  $saved = @{}
  foreach ($k in 'BCC_DATA_DIR', 'BOSSMAN_DATA_DIR', 'PLAYWRIGHT_BROWSERS_PATH', 'PATH', 'BCC_PORT', 'BCC_HOST') {
    $saved[$k] = [Environment]::GetEnvironmentVariable($k, 'Process')
  }
  try {
    $env:BCC_DATA_DIR = (Full $DataDir)
    $env:BOSSMAN_DATA_DIR = (Full $DataDir)
    $env:PLAYWRIGHT_BROWSERS_PATH = (Join-Path $Home_ 'browser')
    $env:PATH = (Join-Path $Home_ 'runtime\Scripts') + ';' + (Join-Path $Home_ 'media') + ';' + $saved['PATH']
    Remove-Item Env:BCC_PORT, Env:BCC_HOST -ErrorAction SilentlyContinue
    # The backend gets its OWN hidden console (CREATE_NEW_CONSOLE): that is what lets Stop deliver
    # a real Ctrl+C to it, and only to it, instead of killing the process.
    $launcher = @'
import subprocess, sys
python, home, port, out, err = sys.argv[1:6]
si = subprocess.STARTUPINFO()
si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
si.wShowWindow = 0
with open(out, "ab") as o, open(err, "ab") as e:
    p = subprocess.Popen([python, "-I", "-X", "utf8", "-m", "bcc", "--host", "127.0.0.1", "--port", port],
                         cwd=home, stdin=subprocess.DEVNULL, stdout=o, stderr=e,
                         creationflags=subprocess.CREATE_NEW_CONSOLE, startupinfo=si)
print(p.pid)
'@
    $lp = Join-Path (Log-Dir) 'launch_backend.py'
    Set-Content -LiteralPath $lp -Value $launcher -Encoding ascii
    $backendPid = [int](& $Python -I $lp $Python $Home_ "$Port" $out $err)
    if ($LASTEXITCODE) { Fail "launcher exit $LASTEXITCODE" }
  } finally {
    foreach ($k in $saved.Keys) { [Environment]::SetEnvironmentVariable($k, $saved[$k], 'Process') }
  }
  $p = Get-Process -Id $backendPid -ErrorAction SilentlyContinue
  if ($p) { $null = $p.Handle }   # keep a handle so ExitCode is readable after exit
  if (-not $p) {
    $held = Holder
    if ($held) { Report-Holder $held 'backend exited at start' }
    Get-Content -LiteralPath $err -Tail 30; Fail "backend PID $backendPid exited immediately"
  }
  Set-Content -LiteralPath (Pid-File) -Value $backendPid -Encoding ascii
  Say "started PID $backendPid on 127.0.0.1:$Port (logs $out)"
  $deadline = (Get-Date).AddSeconds($StartTimeoutSec)
  $h = $null
  while ((Get-Date) -lt $deadline) {
    if ($p.HasExited) {
      if ($p.ExitCode -eq 5) {
        # bcc.app: "a backend already serves this data root" (bcc/backend_lock.py)
        $held = Holder
        if ($held) { Report-Holder $held 'backend exit code 5' }
        Refuse 'backend exit code 5 (data root already served) but no live holder in backend.json'
      }
      Get-Content -LiteralPath $err -Tail 30; Fail "backend PID $backendPid exited with code $($p.ExitCode)"
    }
    $h = Health
    if ($h) { break }
    Start-Sleep -Milliseconds 500
  }
  if (-not $h) { Fail "no /health/live within $StartTimeoutSec s" }
  Do-Status | Out-Null
}

function Do-Status {
  $h = Health
  $listen = @(Listeners $Port)
  $pidRec = $null
  if ($DataDir -and (Test-Path -LiteralPath (Pid-File))) { $pidRec = [int](Get-Content -LiteralPath (Pid-File)) }
  if (-not $h) { Say "port $Port : no /health/live; listeners: $($listen -join ',')"; return $false }
  $procs = @($listen | ForEach-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue })
  $paths = @($procs | ForEach-Object { $_.Path })
  Say "health/live: status=$($h.status) build_sha=$($h.build_sha) source_identity=$($h.source_identity)"
  Say "listeners on $Port : PID $($listen -join ',') path $($paths -join ' | ') (pid file: $pidRec)"
  if ($listen.Count -ne 1) { Fail "expected exactly one listening process on $Port, found $($listen.Count)" }
  if ($pidRec -and $listen[0] -ne $pidRec) { Fail "listener PID $($listen[0]) is not the started backend $pidRec" }
  if ($paths[0] -and -not ((Full $paths[0]).StartsWith((Full $Home_), [StringComparison]::OrdinalIgnoreCase))) {
    Fail "listener is not the bundled runtime of $bundleName : $($paths[0])"
  }
  if ($h.build_sha -and $h.build_sha -ne $Sha) { Fail "backend reports build $($h.build_sha), expected $Sha" }
  return $true
}

function Do-Stop {
  $listen = @(Listeners $Port)
  $pidRec = $null
  if (Test-Path -LiteralPath (Pid-File)) { $pidRec = [int](Get-Content -LiteralPath (Pid-File)) }
  if (-not $pidRec) { Say 'no pid file: nothing started by this script'; return }
  $proc = Get-Process -Id $pidRec -ErrorAction SilentlyContinue
  if (-not $proc) { Say "PID $pidRec already gone"; Remove-Item -LiteralPath (Pid-File); return }
  if (-not (Full $proc.Path).StartsWith((Full $Home_), [StringComparison]::OrdinalIgnoreCase)) {
    Refuse "PID $pidRec is $($proc.Path), not this bundle's runtime: not stopping it"
  }
  # Clean stop: deliver Ctrl+C to the backend's own console (uvicorn shuts down gracefully).
  # Done from a separate bundled-python process so this shell's console is never detached.
  $ctrl = @'
import ctypes, sys
k = ctypes.WinDLL("kernel32", use_last_error=True)
pid = int(sys.argv[1])
k.FreeConsole()
if not k.AttachConsole(pid):
    sys.exit(3)
k.SetConsoleCtrlHandler(None, True)
ok = k.GenerateConsoleCtrlEvent(0, 0)
sys.exit(0 if ok else 4)
'@
  $tmp = Join-Path (Log-Dir) 'ctrl_c.py'
  Set-Content -LiteralPath $tmp -Value $ctrl -Encoding ascii
  $c = Start-Process -FilePath $Python -ArgumentList @('-I', $tmp, "$pidRec") -PassThru -Wait -WindowStyle Hidden
  $how = 'graceful'
  if (-not $proc.WaitForExit(30000)) {
    $how = 'FORCED (graceful stop did not finish in 30 s)'
    Stop-Process -Id $pidRec -Force
    $proc.WaitForExit(10000) | Out-Null
  }
  Remove-Item -LiteralPath (Pid-File)
  $left = @(Listeners $Port)
  Say "stopped PID $pidRec ($how; ctrl-c helper exit $($c.ExitCode)); listeners left on $Port : $($left.Count)"
  if ($left.Count) { Fail "port $Port still has a listener after stop" }
}

function Marker-Write {
  $t = Token
  if (-not $t) { Say 'no token file yet; marker skipped'; return $null }
  $name = "rc19-rehearsal-marker-$(Get-Date -Format yyyyMMddHHmmss)"
  $kinds = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/providers/kinds" -Headers @{ 'X-BCC-Token' = $t }
  $kind = @($kinds)[0]
  $body = @{ name = $name; kind = $kind; base_url = 'http://127.0.0.1:9' } | ConvertTo-Json
  Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$Port/api/providers" -Headers @{ 'X-BCC-Token' = $t } `
    -ContentType 'application/json' -Body $body | Out-Null
  Say "persistence marker written: provider '$name' (kind $kind, dead local URL, no network)"
  return @{ name = $name; token = $t }
}

function Marker-Check($marker) {
  if (-not $marker) { return }
  $t = Token
  if ($t -ne $marker.token) { Fail 'the access token changed across the restart' }
  $names = @(Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/providers" -Headers @{ 'X-BCC-Token' = $t } | ForEach-Object { $_.name })
  if ($names -notcontains $marker.name) { Fail "marker $($marker.name) missing after restart" }
  Say "restart persistence: marker and token survived the restart"
}

function Do-RollbackCheck {
  if (-not (Test-Path -LiteralPath $PreviousInstalls)) { Say "no previous installs folder $PreviousInstalls"; return }
  $ok = 0
  foreach ($d in Get-ChildItem -LiteralPath $PreviousInstalls -Directory | Where-Object Name -like 'BOSSMAN-Windows-x64-*') {
    $launch = Join-Path $d.FullName 'Start-Bossman.cmd'
    $rt = Join-Path $d.FullName 'runtime\python.exe'
    $mf = Join-Path $d.FullName 'MANIFEST.json'
    $sha = ''
    if (Test-Path -LiteralPath $mf) { $sha = (Get-Content -LiteralPath $mf -Raw | ConvertFrom-Json).source_sha }
    $state = if ((Test-Path -LiteralPath $launch) -and (Test-Path -LiteralPath $rt) -and $sha) { 'READY' } else { 'INCOMPLETE' }
    if ($state -eq 'READY') { $ok++ }
    Say ("rollback target {0}: {1} launcher={2} runtime={3} source_sha={4} last_write={5:yyyy-MM-dd HH:mm:ss} (local)" -f $d.Name, $state,
         (Test-Path -LiteralPath $launch), (Test-Path -LiteralPath $rt), $sha, $d.LastWriteTime)
  }
  Say "rollback targets ready: $ok (not started: they would open the owner's data)"
}

function Launcher-Dir { return Join-Path $InstallRoot 'rc-launchers' }

function Write-Wrapper([string]$name, [string[]]$tail) {
  # Plain ASCII .cmd with CRLF; every path is quoted. The environment is set HERE because a
  # .lnk cannot carry environment variables, and it must be the same for both entry points.
  $d = Full $DataDir
  $lines = @(
    '@echo off',
    'setlocal',
    "rem Generated by tools/rc19_side_by_side.ps1 for $Sha - one install, one port, one data dir.",
    "set ""BOSSMAN_HOME=$Home_\""",
    "set ""BCC_DATA_DIR=$d""",
    "set ""BOSSMAN_DATA_DIR=$d""",
    "set ""BCC_PORT=$Port""",
    'set "BCC_HOST=127.0.0.1"',
    'set "BOSSMAN_URL="',
    'call "%BOSSMAN_HOME%app-support\_env.cmd"',
    'if errorlevel 1 exit /b 1'
  ) + $tail
  foreach ($l in $lines) { if ($l -match '[^\x20-\x7E]') { Refuse "non-ASCII path in wrapper line: $l" } }
  $path = Join-Path (Launcher-Dir) $name
  [IO.File]::WriteAllText($path, ($lines -join "`r`n") + "`r`n", [Text.Encoding]::ASCII)
  return $path
}

function Do-Shortcuts {
  Assert-SafeDataDir
  # Collisions first: a shortcut of the same name that this script did not write (for example the
  # owner's own) is never overwritten, whatever else is or is not installed.
  $wsCheck = New-Object -ComObject WScript.Shell
  foreach ($n in @("Bossman $Label.lnk", "Bossman $Label CMD.lnk")) {
    $existing = Join-Path $ShortcutDir $n
    if ((Test-Path -LiteralPath $existing) -and
        -not $wsCheck.CreateShortcut($existing).Description.StartsWith('rc19-side-by-side ')) {
      Refuse "$existing exists and was not written by this script: not touching it"
    }
  }
  if (-not (Test-Path -LiteralPath $Python)) { Fail "bundled runtime missing: $Python (run -Action Install)" }
  $manifest = Get-Content -LiteralPath (Join-Path $Home_ 'MANIFEST.json') -Raw | ConvertFrom-Json
  if ($manifest.source_sha -ne $Sha) { Refuse "$Home_ holds $($manifest.source_sha), not $Sha" }
  New-Item -ItemType Directory -Force (Launcher-Dir), $ShortcutDir, $DataDir | Out-Null
  $ux = Write-Wrapper 'Bossman-RC-Window.cmd' @(
    "start """" ""%BOSSMAN_HOME%runtime\pythonw.exe"" -m bcc.desktop --host 127.0.0.1 --port $Port --no-show-token",
    'exit /b 0')
  $cli = Write-Wrapper 'Bossman-RC-Terminal.cmd' @(
    'call "%BOSSMAN_HOME%Bossman-Terminal.cmd" %*',
    'exit /b %ERRORLEVEL%')
  $marker = "rc19-side-by-side $Sha port=$Port data=$(Full $DataDir)"
  $ws = New-Object -ComObject WScript.Shell
  $icon = (Join-Path $Home_ 'icons\bossman.ico') + ',0'
  $made = @()
  foreach ($spec in @(
      @{ name = "Bossman $Label.lnk"; wrapper = $ux; style = 7; what = 'window' },
      @{ name = "Bossman $Label CMD.lnk"; wrapper = $cli; style = 1; what = 'terminal' })) {
    $lnkPath = Join-Path $ShortcutDir $spec.name
    if (Test-Path -LiteralPath $lnkPath) {
      $old = $ws.CreateShortcut($lnkPath)
      if (-not $old.Description.StartsWith('rc19-side-by-side ')) {
        Refuse "$lnkPath exists and was not written by this script: not touching it"
      }
    }
    $l = $ws.CreateShortcut($lnkPath)
    $l.TargetPath = $env:ComSpec
    $l.Arguments = "/c """"$($spec.wrapper)"""""
    $l.WorkingDirectory = $Home_
    $l.WindowStyle = $spec.style
    $l.IconLocation = $icon
    $l.Description = "$marker ($($spec.what))"
    $l.Save()
    $made += $lnkPath
    Say "shortcut: $lnkPath -> $($spec.wrapper)"
  }
  return $made
}

function RC-Processes {
  $homeFull = Full $Home_
  $lDir = Full (Launcher-Dir)
  # The window may be the system Edge/Chrome (bcc.desktop picks a browser); it is ours only when
  # its profile lives in THIS data dir (--user-data-dir=<DataDir>\desktop-profile).
  $dDir = Full $DataDir
  return @(Get-CimInstance Win32_Process | Where-Object {
      ($_.ExecutablePath -and $_.ExecutablePath.StartsWith($homeFull, [StringComparison]::OrdinalIgnoreCase)) -or
      ($_.CommandLine -and $_.CommandLine.IndexOf($lDir, [StringComparison]::OrdinalIgnoreCase) -ge 0) -or
      ($_.CommandLine -and $_.CommandLine.IndexOf($dDir + '\desktop-profile', [StringComparison]::OrdinalIgnoreCase) -ge 0) })
}

function Do-StopRC {
  $procs = RC-Processes
  foreach ($p in $procs) {
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
  }
  Start-Sleep -Seconds 2
  $left = @(RC-Processes)
  Say "StopRC: stopped $($procs.Count) process(es) of this RC install; left: $($left.Count); listeners on $Port : $(@(Listeners $Port).Count)"
  if ($left.Count) { Fail "processes of this RC install still running: $($left.ProcessId -join ',')" }
}

function Wait-Backend([int]$seconds) {
  $deadline = (Get-Date).AddSeconds($seconds)
  while ((Get-Date) -lt $deadline) {
    $h = Health
    if ($h) { return $h }
    Start-Sleep -Milliseconds 500
  }
  return $null
}

function One-Backend([string]$phase) {
  $h = Health
  if (-not $h) { Fail "$phase : no /health/live on $Port" }
  $listen = @(Listeners $Port)
  if ($listen.Count -ne 1) { Fail "$phase : $($listen.Count) listeners on $Port (expected exactly one)" }
  $bp = Get-CimInstance Win32_Process -Filter "ProcessId=$($listen[0])"
  if (-not $bp.ExecutablePath.StartsWith((Full $Home_), [StringComparison]::OrdinalIgnoreCase)) {
    Fail "$phase : listener $($listen[0]) is not this install: $($bp.ExecutablePath)"
  }
  if ($h.build_sha -ne $Sha) { Fail "$phase : backend build $($h.build_sha) != $Sha" }
  $servers = @(RC-Processes | Where-Object { $_.CommandLine -match '-m bcc(\.app|\.desktop)?(\s|$)' -and $_.Name -like 'python*' })
  $clients = @(Get-NetTCPConnection -RemotePort $Port -State Established -ErrorAction SilentlyContinue |
               Select-Object -ExpandProperty OwningProcess -Unique |
               ForEach-Object { (Get-Process -Id $_ -ErrorAction SilentlyContinue).ProcessName } | Sort-Object -Unique)
  Say ("{0}: ONE backend PID {1} ({2}) port {3} build {4}; bcc processes of this install: {5}; clients connected: {6}" -f
       $phase, $listen[0], ($bp.CommandLine -replace '\s+', ' ').Substring(0, [Math]::Min(110, $bp.CommandLine.Length)),
       $Port, $h.build_sha, (($servers | ForEach-Object { "$($_.ProcessId):$($_.Name)" }) -join ','), ($clients -join ','))
  return $listen[0]
}

function Do-ShortcutTest {
  Assert-SafeDataDir
  if (@(Listeners $Port).Count) { Refuse "port $Port already has a listener" }
  if (@(RC-Processes).Count) { Refuse 'processes of this RC install are already running (use -Action StopRC)' }
  $made = @(Do-Shortcuts)
  $ux, $cli = $made[0], $made[1]
  foreach ($order in @(@($ux, $cli), @($cli, $ux))) {
    $first = Split-Path -Leaf $order[0]; $second = Split-Path -Leaf $order[1]
    Say "--- order: '$first' then '$second' (both opened through explorer.exe)"
    Start-Process explorer.exe -ArgumentList "`"$($order[0])`""
    if ($order[0] -eq $cli) {
      # With no backend the terminal asks "Запустить Bossman сейчас? [Y/n]" and waits for the
      # owner. The test answers exactly like the owner pressing Enter (the default Y): an Enter
      # key event written into THAT console's input, nothing else.
      $chat = $null
      $deadline = (Get-Date).AddSeconds(60)
      while (-not $chat -and (Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 1
        $chat = @(RC-Processes | Where-Object { $_.CommandLine -match 'bossman\.cli\s+chat' }) | Select-Object -First 1
      }
      if (-not $chat) { Fail "the terminal (bossman.cli chat) did not start from '$first'" }
      Start-Sleep -Seconds 6
      if (-not (Health)) {
        $enter = @'
import ctypes, sys
from ctypes import wintypes
k = ctypes.WinDLL("kernel32", use_last_error=True)
class KEY(ctypes.Structure):
    _fields_ = [("bKeyDown", wintypes.BOOL), ("wRepeatCount", wintypes.WORD), ("wVirtualKeyCode", wintypes.WORD),
                ("wVirtualScanCode", wintypes.WORD), ("uChar", wintypes.WCHAR), ("dwControlKeyState", wintypes.DWORD)]
class REC(ctypes.Structure):
    _fields_ = [("EventType", wintypes.WORD), ("Event", KEY)]
k.FreeConsole()
if not k.AttachConsole(int(sys.argv[1])):
    sys.exit(3)
k.CreateFileW.restype = wintypes.HANDLE
h = k.CreateFileW("CONIN$", 0xC0000000, 3, None, 3, 0, None)
recs = (REC * 2)(REC(1, KEY(True, 1, 0x0D, 0x1C, "\r", 0)), REC(1, KEY(False, 1, 0x0D, 0x1C, "\r", 0)))
n = wintypes.DWORD()
ok = k.WriteConsoleInputW(h, recs, 2, ctypes.byref(n))
sys.exit(0 if ok and n.value == 2 else 4)
'@
        $ep = Join-Path (Launcher-Dir) 'answer_enter.py'
        Set-Content -LiteralPath $ep -Value $enter -Encoding ascii
        $c = Start-Process -FilePath $Python -ArgumentList @('-I', $ep, "$($chat.ProcessId)") -PassThru -Wait -WindowStyle Hidden
        Say "terminal asked to start Bossman; answered Enter (default Y) in its console (helper exit $($c.ExitCode))"
      }
    }
    if (-not (Wait-Backend 120)) { Fail "no backend on $Port after opening '$first'" }
    Start-Sleep -Seconds 8
    $pid1 = One-Backend "after '$first'"
    Start-Process explorer.exe -ArgumentList "`"$($order[1])`""
    Start-Sleep -Seconds 25
    $pid2 = One-Backend "after '$second'"
    if ($pid1 -ne $pid2) { Fail "the second entry point replaced the backend ($pid1 -> $pid2)" }
    $termLog = Join-Path $DataDir 'terminal-backend.log'
    Say "terminal-backend.log (written only when the terminal starts its own backend): $(Test-Path -LiteralPath $termLog)"
    Do-StopRC
  }
  Say "SHORTCUT TEST PASS: both entry points of $bundleName share one backend on $Port with data $DataDir"
}

switch ($Action) {
  'Shortcuts' { Do-Shortcuts | Out-Null }
  'ShortcutTest' { Do-ShortcutTest }
  'StopRC' { Do-StopRC }
  'Build' { Do-Build }
  'Install' { Do-Install }
  'Start' { Do-Start }
  'Status' { if (-not (Do-Status)) { exit 1 } }
  'Stop' { Assert-SafeDataDir; Do-Stop }
  'Restart' { Assert-SafeDataDir; Do-Stop; Do-Start }
  'RollbackCheck' { Do-RollbackCheck }
  'Rehearse' {
    Assert-SafeDataDir
    if ((Test-Path -LiteralPath $DataDir) -and (Get-ChildItem -LiteralPath $DataDir -Force | Where-Object Name -ne '_rc19' | Select-Object -First 1)) {
      Say "note: data dir $DataDir is not empty (a previous rehearsal); restart persistence is still checked"
    }
    Do-Install
    Do-Start
    $marker = Marker-Write
    Do-Stop
    Do-Start
    Marker-Check $marker
    Do-Stop
    Do-RollbackCheck
    Say "REHEARSAL PASS: $bundleName on port $Port, data $DataDir"
  }
}
exit 0
