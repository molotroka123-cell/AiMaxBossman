<#
.SYNOPSIS
  Starts Bossman Mobile (iPhone PWA gateway) on 127.0.0.1:8840 and, with -Tailscale, publishes it INSIDE the
  owner's tailnet with `tailscale serve` (never funnel: nothing is exposed to the internet).

.EXAMPLE
  .\scripts\Start-BossmanMobile.ps1                 # loopback only
  .\scripts\Start-BossmanMobile.ps1 -Tailscale      # + https://<pc>.<tailnet>.ts.net/m/ (needs HTTPS certificates on)
  .\scripts\Start-BossmanMobile.ps1 -Tailscale -Http  # fallback without certificates: http://<pc>.<tailnet>.ts.net/m/
#>
[CmdletBinding()]
param(
  [int]$Port = 8840,
  [string]$BossmanUrl = $(if ($env:BOSSMAN_URL) { $env:BOSSMAN_URL } else { 'http://127.0.0.1:8801' }),
  [switch]$Tailscale,
  [switch]$Http,
  [switch]$ShowPairing
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$app = Join-Path $root 'apps\bossman-mobile'
$dataDir = Join-Path $env:USERPROFILE '.bossman-mobile'
New-Item -ItemType Directory -Force $dataDir | Out-Null

$listening = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($listening) {
  Write-Host "Bossman Mobile already listening on 127.0.0.1:$Port (PID $($listening[0].OwningProcess))"
} else {
  $env:BOSSMAN_URL = $BossmanUrl
  $py = (Get-Command python -ErrorAction Stop).Source
  $p = Start-Process -FilePath $py -ArgumentList @('-m', 'bossmobile', 'serve', '--port', "$Port", '--host', '127.0.0.1') `
        -WorkingDirectory $app -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $dataDir 'server.out.log') -RedirectStandardError (Join-Path $dataDir 'server.err.log')
  $ok = $false
  foreach ($i in 1..30) {
    Start-Sleep -Milliseconds 500
    try { if ((Invoke-RestMethod "http://127.0.0.1:$Port/m/api/health" -TimeoutSec 2).ok) { $ok = $true; break } } catch { }
  }
  if (-not $ok) { throw "Bossman Mobile did not start; see $dataDir\server.err.log" }
  Write-Host "Bossman Mobile started: http://127.0.0.1:$Port/m/  (PID $($p.Id))"
}

$publicBase = "http://127.0.0.1:$Port"
if ($Tailscale) {
  $ts = Get-Command tailscale -ErrorAction SilentlyContinue
  if (-not $ts) { throw 'tailscale CLI not found: install Tailscale for Windows and log in first' }
  $status = & tailscale status --json | ConvertFrom-Json
  $dns = ($status.Self.DNSName).TrimEnd('.')
  if ($Http) {
    # Inside the tailnet traffic is WireGuard-encrypted end to end; the PWA service worker still needs HTTPS.
    & tailscale serve --bg --http=80 "http://127.0.0.1:$Port"
    $publicBase = "http://$dns"
  } else {
    if (-not $status.CertDomains) {
      Write-Warning 'HTTPS certificates are not enabled on this tailnet (CertDomains is empty). Enable "HTTPS Certificates" in the Tailscale admin console (DNS page) or rerun with -Http.'
    }
    & tailscale serve --bg --https=443 "http://127.0.0.1:$Port"
    $publicBase = "https://$dns"
  }
  if ($LASTEXITCODE -ne 0) { throw "tailscale serve failed (exit $LASTEXITCODE)" }
  Write-Host "Tailnet only (serve, never funnel): $publicBase/m/"
  Write-Host 'Stop publishing:  tailscale serve reset'
}

Push-Location $app
try {
  if ($ShowPairing) {
    & python -m bossmobile pairing --url $publicBase
  } else {
    Write-Host "Pairing token: $dataDir\tokens.json  (or rerun with -ShowPairing to print a one-tap pairing link)"
  }
} finally { Pop-Location }
