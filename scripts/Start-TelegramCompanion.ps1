<#
.SYNOPSIS
  Start (or diagnose / set up) the Bossman Telegram Companion on Windows.

.DESCRIPTION
  Thin wrapper around the existing `bcc.telegram_companion` module. It adds no
  new bot logic and no new authority:
    * checks that the local models and Command Center answer (read-only GETs);
    * optionally loads TG_COMPANION_* secrets from a LOCAL, untracked env file
      into THIS process only (never written anywhere, never printed);
    * runs the companion with the installed Bossman runtime, or with source
      from a checkout (-RepoRoot) when the installed build is older.

  The recommended way to store the bot token is the encrypted setup
  (-Mode setup). The env file is an alternative for owners who prefer it.

.EXAMPLE
  .\Start-TelegramCompanion.ps1 -Mode setup
  .\Start-TelegramCompanion.ps1 -Mode diagnose -RepoRoot C:\Users\asd\Bossman\wt-telegram
  .\Start-TelegramCompanion.ps1 -RepoRoot C:\Users\asd\Bossman\wt-telegram
#>
param(
    [ValidateSet('run', 'diagnose', 'setup', 'setup-console', 'unlock-delegation')]
    [string]$Mode = 'run',
    # Python that has Bossman's dependencies (httpx, cryptography). Default: installed product runtime.
    [string]$Python = '',
    # Optional source checkout; its command-center and bossman-core are put first on sys.path.
    [string]$RepoRoot = '',
    [string]$ConfigPath = (Join-Path $env:LOCALAPPDATA 'Bossman\telegram-companion\config.json'),
    [string]$EnvFile = (Join-Path $env:LOCALAPPDATA 'Bossman\telegram-companion\companion.env'),
    [switch]$SkipChecks
)
$ErrorActionPreference = 'Stop'

function Find-Python {
    if ($Python) { return $Python }
    $apps = Join-Path $env:USERPROFILE 'Bossman\app'
    if (Test-Path $apps) {
        $candidate = Get-ChildItem $apps -Directory -Filter 'BOSSMAN-Windows-x64-*' |
            Sort-Object LastWriteTime -Descending |
            ForEach-Object { Join-Path $_.FullName 'runtime\python.exe' } |
            Where-Object { Test-Path $_ } | Select-Object -First 1
        if ($candidate) { return $candidate }
    }
    throw 'Bossman runtime python not found; pass -Python <path to python.exe>.'
}

function Test-InsideGitTree([string]$Path) {
    $dir = Split-Path -Parent ([IO.Path]::GetFullPath($Path))
    while ($dir) {
        if (Test-Path (Join-Path $dir '.git')) { return $true }
        $parent = Split-Path -Parent $dir
        if ($parent -eq $dir) { break }
        $dir = $parent
    }
    return $false
}

function Import-CompanionEnv([string]$Path) {
    if (-not (Test-Path $Path)) { return }
    if (Test-InsideGitTree $Path) {
        throw "Refusing secrets file inside a git work tree: $Path. Keep it under %LOCALAPPDATA%."
    }
    $allowed = 'TG_COMPANION_BOT_TOKEN', 'TG_COMPANION_CORE_TOKEN', 'TG_COMPANION_LOCAL_TOKEN',
               'TG_COMPANION_CLOUD_TOKEN', 'TG_COMPANION_PROXY'
    $loaded = @()
    foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {
        $t = $line.Trim()
        if (-not $t -or $t.StartsWith('#')) { continue }
        $name, $value = $t -split '=', 2
        $name = $name.Trim()
        if ($allowed -notcontains $name) { Write-Warning "Ignoring unsupported key in env file: $name"; continue }
        $value = "$value".Trim().Trim('"')
        if ($value) {
            [Environment]::SetEnvironmentVariable($name, $value, 'Process')
            $loaded += $name
        }
    }
    # Names only; values are never printed.
    if ($loaded) { Write-Host ("Loaded from local env file: " + ($loaded -join ', ')) }
}

function Test-Get([string]$Label, [string]$Url) {
    try {
        $r = Invoke-RestMethod -Uri $Url -TimeoutSec 5 -Method Get
        Write-Host ("[OK]   {0}: {1}" -f $Label, $Url)
        return $r
    } catch {
        Write-Warning ("{0} is not answering at {1}" -f $Label, $Url)
        return $null
    }
}

function Invoke-Preflight {
    if (-not (Test-Path $ConfigPath)) {
        Write-Warning "No companion config yet ($ConfigPath). Run: -Mode setup"
        return
    }
    $cfg = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($route in @(@('MAIN', $cfg.local_url, $cfg.local_model), @('FAST', $cfg.fast_url, $cfg.fast_model))) {
        $label, $url, $model = $route
        if (-not $url) { continue }
        $models = Test-Get "$label model server" "$url/models"
        if ($models -and $model) {
            $ids = @($models.data | ForEach-Object { $_.id })
            if ($ids -notcontains $model) {
                Write-Warning "$label config model id is not what the server serves. Copy one of these exactly into config.json:"
                $ids | ForEach-Object { Write-Host "         $_" }
            }
        }
    }
    if ($cfg.core_url) { [void](Test-Get 'Bossman Command Center' "$($cfg.core_url)/health/live") }
}

$py = Find-Python
Write-Host "Python: $py"
Import-CompanionEnv $EnvFile
if (-not $SkipChecks -and $Mode -in 'run', 'diagnose') { Invoke-Preflight }

$modeArgs = @{ 'run' = @(); 'diagnose' = @('--diagnose'); 'setup' = @('--setup'); 'setup-console' = @('--setup-console'); 'unlock-delegation' = @('--unlock-delegation') }[$Mode]
$cliArgs = @('--config', $ConfigPath) + $modeArgs

if ($RepoRoot) {
    $cc = Join-Path $RepoRoot 'command-center'
    $core = Join-Path $RepoRoot 'bossman-core'
    if (-not (Test-Path (Join-Path $cc 'bcc\telegram_companion\__main__.py'))) { throw "Not a Bossman checkout: $RepoRoot" }
    # -I keeps user site/PYTHONPATH out; the checkout is added explicitly and only for this process.
    $boot = 'import sys; sys.path[:0] = sys.argv[1:3]; from bcc.telegram_companion.__main__ import main; raise SystemExit(main(sys.argv[3:]))'
    & $py -I -c $boot $cc $core @cliArgs
} else {
    & $py -I -m bcc.telegram_companion @cliArgs
}
exit $LASTEXITCODE
