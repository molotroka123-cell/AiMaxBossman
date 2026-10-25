param(
    [Parameter(Mandatory=$true)][string]$Settings,
    [Parameter(Mandatory=$true)][string]$OutDir,
    [Parameter(Mandatory=$true)][string]$Tag,
    [double]$KillFreeGB = 3.0,
    [string]$WgpProfile = ''
)
# Detached WanGP runner with RAM guard (free RAM sampled every 10 s, generator killed below $KillFreeGB).
$ErrorActionPreference = 'Continue'
$wgpDir = 'C:\Users\asd\Bossman\media-runtime\WanGP-viggle'
$py = Join-Path $wgpDir 'env_venv\Scripts\python.exe'
$logDir = 'D:\Bossman\work\genjutsu-local-20261025\logs'
New-Item -ItemType Directory -Force $logDir | Out-Null
New-Item -ItemType Directory -Force $OutDir | Out-Null
$out = Join-Path $logDir "$Tag.out.log"
$err = Join-Path $logDir "$Tag.err.log"
$mem = Join-Path $logDir "$Tag.mem.log"
$env:MIOPEN_FIND_MODE = 'FAST'
$env:PYTHONUNBUFFERED = '1'
$env:PYTHONIOENCODING = 'utf-8'
$t0 = Get-Date
"start $($t0.ToString('s')) settings=$Settings" | Out-File $mem -Encoding utf8
$wargs = @('wgp.py','--process',"`"$Settings`"",'--output-dir',"`"$OutDir`"")
if ($WgpProfile -ne '') { $wargs += @('--profile', $WgpProfile) }
$p = Start-Process -FilePath $py -ArgumentList $wargs -WorkingDirectory $wgpDir -RedirectStandardOutput $out -RedirectStandardError $err -PassThru -WindowStyle Hidden
"pid $($p.Id)" | Out-File $mem -Append -Encoding utf8
$minFree = 999.0
$maxWs = 0.0
while (-not $p.HasExited) {
    $os = Get-CimInstance Win32_OperatingSystem
    $free = [math]::Round($os.FreePhysicalMemory / 1MB, 2)
    if ($free -lt $minFree) { $minFree = $free }
    $ws = 0
    try { $ws = [math]::Round((Get-Process -Id $p.Id -ErrorAction Stop).WorkingSet64 / 1GB, 2) } catch {}
    if ($ws -gt $maxWs) { $maxWs = $ws }
    $el = [int]((Get-Date) - $t0).TotalSeconds
    "$el s free=$free GB min=$minFree GB py_ws=$ws GB" | Out-File $mem -Append -Encoding utf8
    if ($free -lt $KillFreeGB) {
        "GUARD KILL: free $free GB < $KillFreeGB GB" | Out-File $mem -Append -Encoding utf8
        Stop-Process -Id $p.Id -Force
        Get-CimInstance Win32_Process -Filter "ParentProcessId=$($p.Id)" | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        break
    }
    $tail = ''
    try { $fs = [IO.File]::Open($out, 'Open', 'Read', 'ReadWrite'); $len = $fs.Length; $fs.Seek([Math]::Max(0, $len - 4000), 'Begin') | Out-Null; $buf = New-Object byte[] 4000; $n = $fs.Read($buf, 0, 4000); $fs.Close(); $tail = [Text.Encoding]::UTF8.GetString($buf, 0, $n) } catch {}
    if ($tail -match 'Queue completed') {
        "queue completed -> stopping lingering wgp process" | Out-File $mem -Append -Encoding utf8
        Start-Sleep -Seconds 15
        Get-CimInstance Win32_Process -Filter "ParentProcessId=$($p.Id)" | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
        break
    }
    Start-Sleep -Seconds 10
}
$p.WaitForExit()
$el = [int]((Get-Date) - $t0).TotalSeconds
"END exit=$($p.ExitCode) elapsed=$el s minFree=$minFree GB maxWs=$maxWs GB" | Out-File $mem -Append -Encoding utf8
