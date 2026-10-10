# OBSERVE stage of the North Star cycles: Bossman's own test pipeline on the BASE commit of the lab clone.
# Output: <Lab>\scan\junit.xml + scan.log. Failing tests = the defect candidates (the harness author chooses none).
param([string]$Lab = 'C:\Users\asd\Bossman\ns-lab-20261010', [int]$Workers = 4)
$repo = "$Lab\repo"; $out = "$Lab\scan"
New-Item -ItemType Directory -Force $out, "$out\tmp" | Out-Null
$env:TEMP = "$out\tmp"; $env:TMP = "$out\tmp"
$env:PYTHONUTF8 = '1'; $env:PYTHONIOENCODING = 'utf-8'; $env:LOCAL_ONLY = '1'
$env:BCC_DATA_DIR = "$out\data"; Remove-Item Env:BCC_PORT -ErrorAction SilentlyContinue
$args = @('-m', 'pytest', 'command-center/tests', '-q', '-p', 'no:cacheprovider', '--tb=short', '-n', "$Workers",
          '--maxfail=200', "--junitxml=$out\junit.xml",
          '--ignore-glob=*ux_soak*', '--ignore-glob=*desktop*', '--ignore-glob=*browser*', '--ignore-glob=*playwright*')
$p = Start-Process -FilePath "$Lab\venv\Scripts\python.exe" -ArgumentList $args -WorkingDirectory $repo `
    -RedirectStandardOutput "$out\scan.log" -RedirectStandardError "$out\scan.err.log" -WindowStyle Hidden -PassThru
$p.PriorityClass = 'BelowNormal'
$p.Id | Set-Content "$out\scan.pid"
"scan started pid=$($p.Id) at $((Get-Date).ToUniversalTime().ToString('u'))"
