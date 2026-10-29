# EVAL500 FULL tournament (detached): 3 hardest dev scenarios per category (42), no total time limit, 240 s per scenario for all arms.
#   Start-Process powershell -WindowStyle Hidden -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File', '<this file>'
# Resume = start again (state.json per arm keeps finished scenarios). Judge runs as its own process (see run_judge_full.ps1).
param([string]$Arms = "strata,qwen,gemma", [string]$Pack = "iq3_s", [string]$Id = "tournament-full-20261026")
$ErrorActionPreference = "Continue"
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $repo
$env:EVAL500_TOURNAMENT_ID = $Id
$env:EVAL500_PER_CATEGORY = "3"
$env:EVAL500_SCENARIO_BUDGET_S = "240"
$env:EVAL500_TOTAL_BUDGET_S = "864000"
$env:EVAL500_ARMS = $Arms
$env:EVAL500_STRATA_PACK = $Pack
$env:PYTHONIOENCODING = "utf-8"
$dir = "D:\Bossman\eval\runs\$Id"
New-Item -ItemType Directory -Force $dir | Out-Null
$log = Join-Path $dir "run.log"
Add-Content $log ("[{0}] start arms={1} pack={2} pid={3}" -f (Get-Date -Format s), $Arms, $Pack, $PID)
python -X utf8 -u tools/eval500/tournament.py all *>> $log
Add-Content $log ("[{0}] all exit={1}" -f (Get-Date -Format s), $LASTEXITCODE)
