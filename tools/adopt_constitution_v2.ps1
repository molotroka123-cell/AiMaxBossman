<#
.SYNOPSIS
  OWNER-ONLY: adopt Bossman Constitution v2 (independent verifiers instead of Claude+Codex) and pin it.

.DESCRIPTION
  Run this yourself in your OWN interactive PowerShell window. It refuses to run non-interactively, inside an
  agent session (Claude Code / Codex / Bossman worker / CI) or with redirected input/output.

  What it does, in order:
    1. finds the constitution file the runtime of the chosen build actually hashes
       (asks that build's own Python for default_constitution_path()),
    2. shows a unified diff live-vs-draft,
    3. asks you to type the confirmation word ADOPT-V2,
    4. backs the live file up next to itself (<file>.bak-<timestamp>),
    5. replaces ONLY that one file with the v2 draft (byte-identical copy, hash verified),
    6. launches the existing `bossman autonomy constitution pin` flow: YOU type the first 12 hex characters of the
       SHA-256 shown there. This script never writes the pin file and never types the confirmation for you.

  It does not push, merge, restart anything or touch any other file.

.PARAMETER Build
  Root of the build to adopt in: either an installed bundle (has runtime\python.exe, e.g.
  C:\Users\asd\Bossman\app\BOSSMAN-Windows-x64-94e40cd17822) or a source checkout (has command-center\bcc\autonomy).
  Default: the checkout this script lives in. Run the script once per build the loop will run from.

.PARAMETER Draft
  The v2 draft. Default: docs\constitution\proposals\BOSSMAN_CONSTITUTION_v2_DRAFT.md of this checkout.

.PARAMETER NoPin
  Replace the file but do not start the pin flow (the loop stays BLOCKED until you pin).
#>
[CmdletBinding()]
param(
    [string]$Build = '',
    [string]$Draft = '',
    [switch]$NoPin
)
$ErrorActionPreference = 'Stop'
$Word = 'ADOPT-V2'
$AgentMarkers = @('CLAUDECODE', 'CLAUDE_CODE_ENTRYPOINT', 'CLAUDE_CODE_SSE_PORT', 'CODEX_SANDBOX',
    'CODEX_SANDBOX_NETWORK_DISABLED', 'CODEX_THREAD_ID', 'BOSSMAN_AGENT_SESSION', 'BOSSMAN_AUTONOMY_WORKER',
    'BOSSMAN_RAVE_AGENT', 'CI', 'GITHUB_ACTIONS')

function Stop-Refuse([string]$why) {
    Write-Host "REFUSED: $why" -ForegroundColor Red
    exit 5
}

# ---- 0. interactive human only -------------------------------------------------------------------------------
if (-not [Environment]::UserInteractive) { Stop-Refuse 'not an interactive session' }
if ([Console]::IsInputRedirected -or [Console]::IsOutputRedirected) {
    Stop-Refuse 'stdin/stdout are redirected: run this in your own PowerShell window, not through an agent or a pipe'
}
$set = @($AgentMarkers | Where-Object { [Environment]::GetEnvironmentVariable($_) })
if ($set.Count -gt 0) { Stop-Refuse ("agent/automation session markers are set: " + ($set -join ', ')) }

# ---- 1. locate the draft and the build ------------------------------------------------------------------------
$repo = Split-Path -Parent $PSScriptRoot
if (-not $Build) { $Build = $repo }
if (-not $Draft) { $Draft = Join-Path $repo 'docs\constitution\proposals\BOSSMAN_CONSTITUTION_v2_DRAFT.md' }
if (-not (Test-Path -LiteralPath $Draft -PathType Leaf)) { Stop-Refuse "draft not found: $Draft" }
$Draft = (Resolve-Path -LiteralPath $Draft).Path
$Build = (Resolve-Path -LiteralPath $Build).Path

$draftText = [IO.File]::ReadAllText($Draft)
$rule4 = ($draftText -split "`n" | Where-Object { $_ -match '^\s*4\.\s' } | Select-Object -First 1)
if (-not $rule4 -or $rule4 -notmatch [regex]::Escape('independent verification of the same immutable SHA') -or
    $rule4 -match '(?i)both Claude and Codex') {
    Stop-Refuse 'the draft does not declare the v2 independent-verification rule in rule 4'
}

$installPy = Join-Path $Build 'runtime\python.exe'
$srcPkg = Join-Path $Build 'command-center\bcc\autonomy\constitution.py'
if (Test-Path -LiteralPath $installPy) {
    $kind = 'installed bundle'
    $py = $installPy
    $env:PYTHONPATH = ''
    $pinArgs = @('-m', 'bossman.cli', 'autonomy', 'constitution', 'pin')
    $workDir = $Build
} elseif (Test-Path -LiteralPath $srcPkg) {
    $kind = 'source checkout'
    $pyCmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pyCmd) { Stop-Refuse 'python not found on PATH (needed to ask the checkout where its constitution is)' }
    $py = $pyCmd.Source
    $env:PYTHONPATH = (Join-Path $Build 'bossman-core') + ';' + (Join-Path $Build 'command-center') + ';' + $Build
    $pinArgs = @('-m', 'bossman.cli', 'autonomy', 'constitution', 'pin')
    $workDir = $Build
} else {
    Stop-Refuse "not a Bossman build (no runtime\python.exe and no command-center\bcc\autonomy): $Build"
}
$env:PYTHONUTF8 = '1'

# The file the runtime hashes = default_constitution_path() of THAT build's own code.
$probe = 'from bcc.autonomy import constitution as c; print(c.default_constitution_path())'
$target = (& $py -c $probe | Select-Object -Last 1)
if ($LASTEXITCODE -ne 0 -or -not $target) { Stop-Refuse "could not ask the build for its constitution path ($py)" }
$target = $target.Trim()
$pinPath = & $py -c 'from bcc.autonomy import constitution as c; print(c.default_pin_path())'
$pinPath = ($pinPath | Select-Object -Last 1).Trim()

Write-Host ''
Write-Host "Build:       $Build ($kind)"
Write-Host "Live file:   $target"
Write-Host "Draft:       $Draft"
Write-Host "Pin file:    $pinPath   (written only by the bossman pin command, never by this script)"
if (-not (Test-Path -LiteralPath $target -PathType Leaf)) {
    Write-Host 'NOTE: the live file does not exist in this build yet (the loop could not hash anything); it will be created.' -ForegroundColor Yellow
}

# ---- 2. diff ------------------------------------------------------------------------------------------------
Write-Host ''
Write-Host '=== diff: live -> draft ===' -ForegroundColor Cyan
$git = Get-Command git -ErrorAction SilentlyContinue
if ((Test-Path -LiteralPath $target) -and $git) {
    & git --no-pager diff --no-index --no-color --unified=2 -- $target $Draft
} elseif (Test-Path -LiteralPath $target) {
    Compare-Object (Get-Content -LiteralPath $target) (Get-Content -LiteralPath $Draft) | Format-Table -AutoSize
} else {
    Write-Host '(no live file: the whole draft is new)'
}
Write-Host '=== end of diff ===' -ForegroundColor Cyan
Write-Host ''
Write-Host 'Unchanged on purpose: the "Always the user''s decision" list, the owner pin outside the repository,'
Write-Host 'levels L0-L4, 25 clean cycles + your confirmation for promotion, STOP, release tiers (you press Release).'
Write-Host ''

# ---- 3. typed confirmation ------------------------------------------------------------------------------------
$answer = Read-Host "Type $Word to replace the live constitution with the v2 draft (anything else cancels)"
if ($answer -cne $Word) { Write-Host 'Cancelled. Nothing was changed.'; exit 0 }

# ---- 4/5. backup and replace ONLY that file --------------------------------------------------------------------
$backup = ''
if (Test-Path -LiteralPath $target -PathType Leaf) {
    $backup = "$target.bak-" + (Get-Date -Format 'yyyyMMdd-HHmmss')
    Copy-Item -LiteralPath $target -Destination $backup -ErrorAction Stop
    Write-Host "Backup:      $backup"
} else {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
}
Copy-Item -LiteralPath $Draft -Destination $target -Force -ErrorAction Stop
$h1 = (Get-FileHash -LiteralPath $Draft -Algorithm SHA256).Hash
$h2 = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash
if ($h1 -ne $h2) { Stop-Refuse "copy verification failed ($h1 vs $h2); restore: $backup" }
Write-Host "Replaced:    $target"
Write-Host "SHA-256:     $($h2.ToLower())"

if ($NoPin) {
    Write-Host 'NoPin: the loop stays BLOCKED until you run `bossman autonomy constitution pin` yourself.' -ForegroundColor Yellow
    exit 0
}

# ---- 6. the existing owner pin flow (you type the 12 hex characters) ---------------------------------------------
Write-Host ''
Write-Host '=== pin (you type the first 12 characters of the SHA-256 shown below) ===' -ForegroundColor Cyan
Push-Location $workDir
try {
    & $py @pinArgs
    $code = $LASTEXITCODE
} finally {
    Pop-Location
}
if ($code -ne 0) {
    Write-Host "Pin did not complete (exit $code): the loop stays BLOCKED." -ForegroundColor Yellow
    if ($backup) {
        $r = Read-Host 'Restore the previous constitution from the backup now? Type RESTORE to restore, anything else keeps v2 (unpinned)'
        if ($r -ceq 'RESTORE') {
            Copy-Item -LiteralPath $backup -Destination $target -Force
            Write-Host "Restored $target from $backup"
        }
    }
    exit $code
}
Write-Host ''
Write-Host '=== status ===' -ForegroundColor Cyan
Push-Location $workDir
try { & $py -m bossman.cli autonomy constitution status } finally { Pop-Location }
Write-Host ''
Write-Host 'Done. Restart nothing by hand: the next autonomy cycle reads the pin and applies the v2 approver policy.'
