param([string]$Id = "tournament-full-20261026")
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $repo
$env:EVAL500_TOURNAMENT_ID = $Id; $env:EVAL500_PER_CATEGORY = "3"; $env:EVAL500_SCENARIO_BUDGET_S = "240"; $env:PYTHONIOENCODING = "utf-8"
$arms = $env:EVAL500_ARMS
python -X utf8 -u tools/eval500/tournament.py judge --loop *>> "D:\Bossman\eval\runs\$Id\judge.log"
