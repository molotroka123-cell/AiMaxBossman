param(
    [ValidateSet('default', 'acceptEdits', 'bypassPermissions')]
    [string]$PermissionMode = 'default'
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$expectedBranch = 'feat/bossman-genjutsu-jeff-unified-20261010'
$actualBranch = (& git branch --show-current).Trim()
if ($LASTEXITCODE -ne 0 -or $actualBranch -ne $expectedBranch) {
    throw "Wrong branch: $actualBranch; expected $expectedBranch. No checkout performed."
}
$originHead = & git symbolic-ref refs/remotes/origin/HEAD 2>$null
if ($LASTEXITCODE -ne 0 -or $originHead -ne 'refs/remotes/origin/main') {
    throw 'origin/HEAD must be verified as origin/main before remote launch.'
}
if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
    throw 'Claude CLI is not installed or not on PATH.'
}
& claude remote-control --name Bossman-Owner-PC --spawn=session --permission-mode $PermissionMode
exit $LASTEXITCODE
