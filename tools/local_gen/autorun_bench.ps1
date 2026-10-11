# autorun_bench.ps1 - one-shot bench run after the owner's reboot/logon (scheduled task BossmanLocalGenBench).
# Runs: preflight -> smoke (TI2V-5B, 9 frames, 4 steps) -> matrix 480p, logs to autorun.log, then deletes its own task.
# Non-admin user task; GPU use only here, one process at a time (the harness enforces it).
$ErrorActionPreference = "Continue"
$repo = "C:\Users\asd\Bossman\wt-unified-20261010"
$py = "C:\Users\asd\AppData\Local\Programs\Python\Python312\python.exe"
$bench = Join-Path $repo "tools\local_gen\bench_local_gen.py"
$outDir = "C:\Users\asd\Videos\Bossman-Genjutsu\local-gen-bench-20261011"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$log = Join-Path $outDir "autorun.log"
function Log($m) { "$(Get-Date -Format s) $m" | Tee-Object -FilePath $log -Append }
Log "=== BossmanLocalGenBench start (user task, no admin) ==="
try {
    Set-Location $repo
    Log "git HEAD $(git rev-parse --short HEAD)"
    & $py $bench preflight 2>&1 | Tee-Object -FilePath $log -Append
    if ($LASTEXITCODE -ne 0) { Log "preflight BLOCKED -> waiting 10 min once, then retry"; Start-Sleep 600; & $py $bench preflight 2>&1 | Tee-Object -FilePath $log -Append }
    if ($LASTEXITCODE -ne 0) { Log "preflight still BLOCKED -> abort (no GPU run)"; throw "preflight" }
    Log "--- smoke: wan22-ti2v-5b-q8 t2v 480p 9 frames 4 steps (not a gate result)"
    & $py $bench run --candidate wan22-ti2v-5b-q8 --task t2v --rung 480p --frames 9 --steps 4 --cap 600 2>&1 | Tee-Object -FilePath $log -Append
    $smoke = Join-Path $outDir "480p\wan22-ti2v-5b-q8\t2v\run.json"
    $st = if (Test-Path $smoke) { (Get-Content $smoke | ConvertFrom-Json).status } else { "NO_RUN_JSON" }
    Log "smoke status: $st"
    if ($st -eq "DEVICE_LOST") { Log "device lost on smoke -> STOP, no matrix (owner rule: no blind retry)"; throw "device lost" }
    Log "--- matrix 480p"
    & $py $bench matrix --rung 480p 2>&1 | Tee-Object -FilePath $log -Append
    Log "matrix exit $LASTEXITCODE"
    & $py $bench report 2>&1 | Tee-Object -FilePath $log -Append
} catch {
    Log "stopped: $_"
} finally {
    Log "deleting task BossmanLocalGenBench"
    schtasks /Delete /TN BossmanLocalGenBench /F 2>&1 | Tee-Object -FilePath $log -Append
    Log "=== end ==="
}
