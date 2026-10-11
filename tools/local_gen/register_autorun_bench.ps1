# register_autorun_bench.ps1 - registers the USER scheduled task BossmanLocalGenBench (no admin rights needed):
# trigger ONLOGON for user asd, 3 min delay, runs autorun_bench.ps1 once; the script deletes the task itself.
# Owner/coordinator runs this ONLY when the reboot is about to happen; it does not start anything now.
param([switch]$Remove)
$tn = "BossmanLocalGenBench"
if ($Remove) { schtasks /Delete /TN $tn /F; exit $LASTEXITCODE }
$script = "C:\Users\asd\Bossman\wt-unified-20261010\tools\local_gen\autorun_bench.ps1"
if (-not (Test-Path $script)) { throw "missing $script" }
$tr = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`""
schtasks /Create /TN $tn /SC ONLOGON /RU "$env:USERDOMAIN\$env:USERNAME" /DELAY 0003:00 /TR $tr /F
if ($LASTEXITCODE -ne 0) { throw "schtasks /Create failed ($LASTEXITCODE)" }
schtasks /Query /TN $tn /V /FO LIST | Select-String -Pattern "TaskName|Status|Schedule Type|Task To Run|Run As User|Delay"
Write-Host "[autorun] registered: runs once ~3 min after the next logon of $env:USERNAME, log -> Videos\Bossman-Genjutsu\local-gen-bench-20261011\autorun.log"
