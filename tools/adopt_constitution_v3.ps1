<#
.SYNOPSIS
  OWNER-ONLY: adopt Bossman Constitution v3 (one PC operator driven from the phone) and pin it.

.DESCRIPTION
  Run this yourself in your OWN interactive PowerShell window. It refuses to run non-interactively, inside an
  agent session (Claude Code / Codex / Bossman worker / CI) or with redirected input/output.

  Steps, in order:
    1. checks the v3 proposal (required headings and the bossman-policy block),
    2. shows its SHA-256, a short summary of what changes versus v2 and the diff live -> v3,
    3. shows the EFFECTIVE policy of the proposal (`constitution explain`; pins and writes nothing),
    4. asks you to type ADOPT-V3,
    5. backs the live file up to docs\constitution\backup\BOSSMAN_CONSTITUTION_<timestamp>.md,
    6. copies the proposal over the live file byte for byte (hash verified),
    7. starts `bossman autonomy constitution pin`: YOU type the first 12 hex characters of the SHA-256.
  On ANY failure (copy, hash, explain, pin cancelled or failed) the backup is restored and the script says so.
  This script never writes the pin file and never types the confirmation for you. It does not push, merge or
  restart anything and touches no other file.

.PARAMETER Build
  Root of the build to adopt in: an installed bundle (has runtime\python.exe) or a source checkout
  (has command-center\bcc\autonomy). Default: the checkout this script lives in.

.PARAMETER Proposal
  The v3 proposal. Default: docs\constitution\proposals\BOSSMAN_CONSTITUTION_v3_OPERATOR.md of this checkout.

.PARAMETER NoPin
  Replace the file but do not start the pin flow (the loop stays BLOCKED until you pin).
#>
[CmdletBinding()]
param(
    [string]$Build = '',
    [string]$Proposal = '',
    [switch]$NoPin
)
$ErrorActionPreference = 'Stop'
$Word = 'ADOPT-V3'
$AgentMarkers = @('CLAUDECODE', 'CLAUDE_CODE_ENTRYPOINT', 'CLAUDE_CODE_SSE_PORT', 'CODEX_SANDBOX',
    'CODEX_SANDBOX_NETWORK_DISABLED', 'CODEX_THREAD_ID', 'BOSSMAN_AGENT_SESSION', 'BOSSMAN_AUTONOMY_WORKER',
    'BOSSMAN_RAVE_AGENT', 'CI', 'GITHUB_ACTIONS')
$RequiredHeadings = @('## Total control of the PC', '## Wallets',
    '## Providers: the operator calls them itself, by these rules')

function Stop-Refuse([string]$why) {
    Write-Host "ОТКАЗ (REFUSED): $why" -ForegroundColor Red
    exit 5
}

# ---- 0. interactive human only -------------------------------------------------------------------------------
if (-not [Environment]::UserInteractive) { Stop-Refuse 'сессия не интерактивная (not an interactive session)' }
if ([Console]::IsInputRedirected -or [Console]::IsOutputRedirected) {
    Stop-Refuse 'stdin/stdout перенаправлены: запустите скрипт в своем окне PowerShell, не через агента или pipe'
}
$set = @($AgentMarkers | Where-Object { [Environment]::GetEnvironmentVariable($_) })
if ($set.Count -gt 0) { Stop-Refuse ('заданы признаки агента/автоматизации: ' + ($set -join ', ')) }

# ---- 1. locate and check the proposal and the build ---------------------------------------------------------------
$repo = Split-Path -Parent $PSScriptRoot
if (-not $Build) { $Build = $repo }
if (-not $Proposal) { $Proposal = Join-Path $repo 'docs\constitution\proposals\BOSSMAN_CONSTITUTION_v3_OPERATOR.md' }
if (-not (Test-Path -LiteralPath $Proposal -PathType Leaf)) { Stop-Refuse "проект v3 не найден: $Proposal" }
$Proposal = (Resolve-Path -LiteralPath $Proposal).Path
$Build = (Resolve-Path -LiteralPath $Build).Path

$text = [IO.File]::ReadAllText($Proposal)
$lines = $text -split "`r?`n"
foreach ($h in $RequiredHeadings) {
    if (-not ($lines | Where-Object { $_.TrimEnd() -ceq $h })) { Stop-Refuse "в проекте нет обязательного раздела: $h" }
}
if ($text -notmatch '(?m)^```bossman-policy\s*$') { Stop-Refuse 'в проекте нет блока bossman-policy' }
$proposalHash = (Get-FileHash -LiteralPath $Proposal -Algorithm SHA256).Hash.ToLower()

$installPy = Join-Path $Build 'runtime\python.exe'
$srcPkg = Join-Path $Build 'command-center\bcc\autonomy\constitution.py'
if (Test-Path -LiteralPath $installPy) {
    $kind = 'installed bundle'
    $py = $installPy
    $env:PYTHONPATH = ''
    $pinArgs = @('-m', 'bossman.cli', 'autonomy', 'constitution', 'pin')
    $workDir = $Build
} elseif (Test-Path -LiteralPath $srcPkg) {
    $kind = 'source checkout'
    $pyCmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pyCmd) { Stop-Refuse 'python не найден в PATH (нужен, чтобы узнать путь конституции этой сборки)' }
    $py = $pyCmd.Source
    $env:PYTHONPATH = (Join-Path $Build 'bossman-core') + ';' + (Join-Path $Build 'command-center') + ';' + $Build
    $pinArgs = @('-m', 'bossman.cli', 'autonomy', 'constitution', 'pin')
    $workDir = $Build
} else {
    Stop-Refuse "это не сборка Bossman (нет runtime\python.exe и command-center\bcc\autonomy): $Build"
}
$env:PYTHONUTF8 = '1'

# The file the runtime hashes = default_constitution_path() of THAT build's own code.
$probe = 'from bcc.autonomy import constitution as c; print(c.default_constitution_path())'
$target = (& $py -c $probe | Select-Object -Last 1)
if ($LASTEXITCODE -ne 0 -or -not $target) { Stop-Refuse "сборка не назвала путь своей конституции ($py)" }
$target = $target.Trim()
$pinPath = & $py -c 'from bcc.autonomy import constitution as c; print(c.default_pin_path())'
$pinPath = ($pinPath | Select-Object -Last 1).Trim()

Write-Host ''
Write-Host "Сборка:        $Build ($kind)"
Write-Host "Живой файл:    $target"
Write-Host "Проект v3:     $Proposal"
Write-Host "SHA-256 v3:    $proposalHash"
Write-Host "Файл pin:      $pinPath   (пишет только команда bossman pin, никогда этот скрипт)"
if (-not (Test-Path -LiteralPath $target -PathType Leaf)) {
    Write-Host 'ВНИМАНИЕ: живого файла в этой сборке еще нет; он будет создан.' -ForegroundColor Yellow
}

# ---- 2. summary and diff ------------------------------------------------------------------------------------------
Write-Host ''
Write-Host '=== что меняется по сравнению с v2 ===' -ForegroundColor Cyan
Write-Host ' * Продукт: ОДИН локальный агент, который управляет ПК с телефона; один чат в пульте, одна заменяемая модель.'
Write-Host ' * Тотальное управление ПК внутри чата владельца, без подтверждения каждого шага; STOP остается у владельца.'
Write-Host ' * Кошельки, которые владелец держит на ПК для оператора: баланс = лимит; сиды и ключи не покидают ПК.'
Write-Host ' * Провайдеры: в пульте их нет; оператор сам зовет облако по правилам (сначала локально, без личной информации).'
Write-Host ' * Решает владелец (Approve/Deny): деньги вне кошельков оператора, раскрытие ключей, форматирование,'
Write-Host '   конституция/STOP/журнал/хранилище ключей, всё, что может остановить рабочий пульт.'
Write-Host ' * Правка самого Bossman: ветка, тесты, живое доказательство, независимые верификаторы (блок bossman-policy).'
Write-Host ' * Правило "4 предложения про независимую проверку" заменено машинным блоком bossman-policy.'
Write-Host ''
Write-Host '=== diff: живой файл -> v3 ===' -ForegroundColor Cyan
$git = Get-Command git -ErrorAction SilentlyContinue
if ((Test-Path -LiteralPath $target) -and $git) {
    & git --no-pager diff --no-index --no-color --unified=2 -- $target $Proposal
} elseif (Test-Path -LiteralPath $target) {
    Compare-Object (Get-Content -LiteralPath $target) (Get-Content -LiteralPath $Proposal) | Format-Table -AutoSize
} else {
    Write-Host '(живого файла нет: весь текст новый)'
}
Write-Host '=== конец diff ===' -ForegroundColor Cyan
Write-Host ''

# ---- 2b. what this text MEANS to the gate (the build's own parser; pins and writes nothing) ------------------------
Write-Host '=== эффективная политика проекта (что шлюз автономии сделает с этим текстом) ===' -ForegroundColor Cyan
Push-Location $workDir
try {
    & $py -m bossman.cli autonomy constitution explain --path $Proposal
    $explainCode = $LASTEXITCODE
} finally {
    Pop-Location
}
if ($explainCode -ne 0) { Stop-Refuse "сборка не смогла разобрать проект (код $explainCode); ничего не изменено" }
Write-Host '=== конец политики ===' -ForegroundColor Cyan
Write-Host 'Прочтите WARNINGS выше: отвергнутый блок означает откат шлюза на Claude + Codex (v1).'
Write-Host ''

# ---- 3. typed confirmation ----------------------------------------------------------------------------------------
$answer = Read-Host "Введите $Word, чтобы заменить живую конституцию на v3 (всё остальное отменяет)"
if ($answer -cne $Word) { Write-Host 'Отменено. Ничего не изменено.'; exit 0 }

# ---- 4/5. backup and replace ONLY that file -----------------------------------------------------------------------
$backup = ''
$replaced = $false
function Restore-Backup([string]$why) {
    Write-Host "ОШИБКА: $why" -ForegroundColor Red
    if ($script:backup) {
        try {
            Copy-Item -LiteralPath $script:backup -Destination $script:target -Force -ErrorAction Stop
            Write-Host "Живая конституция ВОССТАНОВЛЕНА из резервной копии: $($script:backup)" -ForegroundColor Yellow
        } catch {
            Write-Host "НЕ УДАЛОСЬ восстановить автоматически. Скопируйте вручную: $($script:backup) -> $($script:target)" -ForegroundColor Red
        }
    } elseif ($script:replaced) {
        Remove-Item -LiteralPath $script:target -Force -ErrorAction SilentlyContinue
        Write-Host 'Живого файла раньше не было: созданный файл удален.' -ForegroundColor Yellow
    } else {
        Write-Host 'Живой файл не менялся.' -ForegroundColor Yellow
    }
}

try {
    if (Test-Path -LiteralPath $target -PathType Leaf) {
        $backupDir = Join-Path (Split-Path -Parent $target) 'backup'
        New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
        $backup = Join-Path $backupDir ('BOSSMAN_CONSTITUTION_' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.md')
        Copy-Item -LiteralPath $target -Destination $backup -ErrorAction Stop
        Write-Host "Резервная копия: $backup"
    } else {
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
    }
    $replaced = $true
    Copy-Item -LiteralPath $Proposal -Destination $target -Force -ErrorAction Stop
    $h2 = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLower()
    if ($h2 -ne $proposalHash) { throw "проверка копии не прошла ($proposalHash против $h2)" }
    Write-Host "Заменено:      $target"
    Write-Host "SHA-256:       $h2"
} catch {
    Restore-Backup ("замена файла не удалась: " + $_.Exception.Message)
    exit 6
}

if ($NoPin) {
    Write-Host 'NoPin: цикл автономии остается BLOCKED, пока вы сами не выполните `bossman autonomy constitution pin`.' -ForegroundColor Yellow
    exit 0
}

# ---- 6. the existing owner pin flow (you type the 12 hex characters) ----------------------------------------------
Write-Host ''
Write-Host '=== pin (вы сами вводите первые 12 символов SHA-256, показанного ниже) ===' -ForegroundColor Cyan
$code = 1
Push-Location $workDir
try {
    & $py @pinArgs
    $code = $LASTEXITCODE
} catch {
    $code = 1
} finally {
    Pop-Location
}
if ($code -ne 0) {
    Restore-Backup "pin не завершен (код $code): конституция v3 не закреплена"
    Write-Host 'Активна прежняя конституция. Запустите скрипт снова, когда будете готовы.' -ForegroundColor Yellow
    exit $code
}
Write-Host ''
Write-Host '=== статус ===' -ForegroundColor Cyan
Push-Location $workDir
try { & $py -m bossman.cli autonomy constitution status } finally { Pop-Location }
Write-Host ''
Write-Host 'Готово. Ничего перезапускать вручную не нужно: следующий цикл автономии прочтет pin и применит политику v3.'
