# EVAL500 tournament (detached): hard 14 set, arms Strata -> Qwen -> Gemma sequentially, 1-hour budget.
#   Start-Process powershell -WindowStyle Hidden -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File',
#     'C:\Users\asd\Bossman\wt-final-20261023\tools\eval500\run_tournament.ps1'
# The blind judge runs as its own detached process: tournament.py judge --loop (cloud, no GPU).
# Resume = start again (budget.json keeps the original start; per-arm state.json keeps finished scenarios).
$ErrorActionPreference = "Continue"
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $repo
$dir = "D:\Bossman\eval\runs\tournament-hard14-20261025"
New-Item -ItemType Directory -Force $dir | Out-Null
$log = Join-Path $dir "run.log"
$env:PYTHONIOENCODING = "utf-8"
function Log($m) { Add-Content -Path $log -Value ("[{0}] {1}" -f (Get-Date -Format s), $m) -Encoding utf8 }
Log "start pid=$PID repo=$repo"
python -X utf8 -u tools/eval500/tournament.py all *>> $log
Log "all exit=$LASTEXITCODE"
