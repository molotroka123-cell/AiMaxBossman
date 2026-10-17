# Starts the Bossman War & Procurement Terminal on loopback. DEMO by default; -Live enables public sources (owner's choice).
param([switch]$Live, [int]$Port = 8830)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$app = Join-Path $repo 'apps\war-terminal'
Remove-Item Env:CLAUDECODE -ErrorAction SilentlyContinue
$args2 = @('-m', 'warterminal', '--port', $Port)
if ($Live) { $args2 += '--live' }
Push-Location $app
try { & python @args2 } finally { Pop-Location }
