<#
.SYNOPSIS
  One command: start the owner's own Poker Train on loopback and run a session.
    -Mode bot   : Bossman's bot plays the trainer by itself (screen -> vision -> owner's preflop chart / equity -> clicks).
    -Mode duel  : the owner plays in the opened window; the bot only advises and records its choice next to the owner's.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File apps\poker-vision\tools\Start-PokerTrainerMatch.ps1 -Mode bot -Hands 20 -Headful
  powershell -ExecutionPolicy Bypass -File apps\poker-vision\tools\Start-PokerTrainerMatch.ps1 -Mode duel -Hands 20
.NOTES
  Loopback only (127.0.0.1). The bot can click only inside its own Chromium window with the trainer; nothing else on the desktop.
  Stop: Ctrl+C in this console (or close the window).
#>
param(
  [ValidateSet("bot", "duel")] [string]$Mode = "bot",
  [int]$Hands = 20,
  [switch]$Headful,
  [ValidateSet("cash_nl2", "cash_nl5", "cash_nl10", "cash_nl25")] [string]$Table = "cash_nl10",
  [string]$TrainerDir = "C:\Users\asd\Bossman\apps-local\pokertrain",
  [int]$Port = 3417,
  [string]$Python = "C:\Users\asd\Bossman\venv-verify-20261009\Scripts\python.exe",
  [string]$Out = ""
)
$ErrorActionPreference = "Stop"
$appDir = Split-Path -Parent $PSScriptRoot
if (-not $Out) { $Out = Join-Path $appDir ("evidence\play\" + $Mode + "-" + (Get-Date -Format "yyyyMMdd-HHmmss")) }
if (-not (Test-Path $Python)) { throw "Python not found: $Python (needs playwright + chromium, opencv, numpy, pillow)" }
if (-not (Test-Path (Join-Path $TrainerDir "package.json"))) {
  throw "Poker Train not found in $TrainerDir. Clone it: git clone https://github.com/molotroka123-cell/pokertrain `"$TrainerDir`""
}
$url = "http://127.0.0.1:$Port/"
function Test-Trainer { try { (Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 $url).StatusCode -eq 200 } catch { $false } }
if (-not (Test-Trainer)) {
  if (-not (Test-Path (Join-Path $TrainerDir "node_modules"))) {
    Write-Host "npm install in $TrainerDir (first run only)..."
    Push-Location $TrainerDir; try { npm install --no-audit --no-fund | Out-Host } finally { Pop-Location }
  }
  Write-Host "Starting Poker Train on $url ..."
  Start-Process -FilePath "cmd.exe" -ArgumentList "/c", "npx vite --host 127.0.0.1 --port $Port --strictPort" -WorkingDirectory $TrainerDir -WindowStyle Minimized | Out-Null
  $deadline = (Get-Date).AddSeconds(60)
  while (-not (Test-Trainer)) {
    if ((Get-Date) -gt $deadline) { throw "Poker Train did not answer on $url within 60 s" }
    Start-Sleep -Milliseconds 500
  }
}
$argv = @("-m", "pokervision.play_trainer", $Mode, "--url", $url, "--hands", "$Hands", "--bootstrap", $Table, "--out", $Out)
if ($Headful -or $Mode -eq "duel") { $argv += "--headful" }
Write-Host "Session: $Mode, $Hands hands, table $Table -> $Out"
Push-Location $appDir
try { & $Python @argv } finally { Pop-Location }
Write-Host "Report: $Out\summary.json ; annotated screenshots: $Out\frames"
