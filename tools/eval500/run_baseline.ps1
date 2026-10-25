# EVAL500 long run (detached): runner -> judge (repeated until every transcript has a verdict) -> report.
# Start detached, NOT inside an agent shell:
#   Start-Process powershell -WindowStyle Hidden -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File',
#     'C:\Users\asd\Bossman\wt-final-20261023\tools\eval500\run_baseline.ps1','-RunId','baseline-cc7d1e42'
# Resume = start it again with the same RunId (state file D:\Bossman\eval\runs\<RunId>\state.json).
# Pause = create D:\Bossman\eval\PAUSE (the runner yields between scenarios; it also yields to any owner task).
param(
    [string]$RunId = "baseline-cc7d1e42",
    [int]$Workers = 1,
    [string]$Parts = "dev,holdout",
    [string]$Out = "docs/audits/2026-10-25-eval500-baseline",
    [switch]$SkipRun
)
$ErrorActionPreference = "Continue"
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $repo
$dir = "D:\Bossman\eval\runs\$RunId"
New-Item -ItemType Directory -Force $dir | Out-Null
$log = Join-Path $dir "run.log"
$env:PYTHONIOENCODING = "utf-8"
function Log($m) { Add-Content -Path $log -Value ("[{0}] {1}" -f (Get-Date -Format s), $m) -Encoding utf8 }

Log "start run=$RunId parts=$Parts workers=$Workers pid=$PID repo=$repo"
if (-not $SkipRun) {
    python -X utf8 tools/eval500/runner.py run --run-id $RunId --parts $Parts --workers $Workers *>> $log
    Log "runner exit=$LASTEXITCODE"
}
for ($i = 1; $i -le 6; $i++) {
    python -X utf8 tools/eval500/judge.py --run-id $RunId --parts $Parts *>> $log
    Log "judge pass $i exit=$LASTEXITCODE"
    $left = python -X utf8 -c "import json,sys; sys.path.insert(0,'tools'); from eval500 import judge,schema,runner; n=0
for part in '$Parts'.split(','):
    sc = schema.load_dev() if part=='dev' else schema.load_holdout('judge')
    p = judge.verdicts_path('$RunId', part); v = json.loads(p.read_text(encoding='utf-8'))['verdicts'] if p.is_file() else {}
    n += sum(1 for s in sc if runner.transcript_path('$RunId', part, s['id']).is_file() and not (v.get(s['id']) or {}).get('verdict'))
print(n)"
    Log "unjudged=$left"
    if ("$left".Trim() -eq "0") { break }
    Start-Sleep -Seconds 600
}
python -X utf8 tools/eval500/report.py baseline --run-id $RunId --parts $Parts --out $Out *>> $log
Log "report exit=$LASTEXITCODE; done"
