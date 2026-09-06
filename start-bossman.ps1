# start-bossman.ps1 — ЕДИНСТВЕННАЯ точка входа для владельца на Windows.
#
# Зачем: до сих пор из свежего клона запустить было нечем. `install-bossman.cmd`
# только создаёт ярлык и падает с "No module named bcc", потому что пакет ещё не
# установлен, — то есть предлагает установить то, что должен был установить сам.
#
# Здесь один сценарий делает всё по порядку и останавливается на первой реальной
# проблеме, а не на пятой её производной:
#
#   1. проверяет Python 3.11+
#   2. создаёт .venv (если его нет)
#   3. ставит три пакета репозитория в режиме разработки
#   4. запускает `bossman doctor` и ОТКАЗЫВАЕТСЯ стартовать при BLOCKED
#   5. открывает окно Command Center
#
# Использование:
#   .\start-bossman.ps1              полный путь: установка → доктор → запуск
#   .\start-bossman.ps1 -DoctorOnly  только диагностика
#   .\start-bossman.ps1 -SkipInstall  пропустить установку (быстрый повторный старт)
#   .\start-bossman.ps1 -EveningTest  запустить вечернюю приёмку вместо окна
#   .\start-bossman.ps1 -Port 8801    другой порт

[CmdletBinding()]
param(
    [switch]$DoctorOnly,
    [switch]$SkipInstall,
    [switch]$EveningTest,
    [int]$Port = 0,
    [switch]$Web
)

$ErrorActionPreference = "Stop"
# Консоль Windows по умолчанию в OEM-кодировке; весь вывод здесь UTF-8.
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

$Repo = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Repo

function Write-Step($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }
function Write-Bad($text)  { Write-Host "    $text" -ForegroundColor Red }

# ---------------------------------------------------------------- 1. Python
Write-Step "Python"
$PythonExe = $null
foreach ($candidate in @("py -3.13", "py -3.12", "py -3.11", "python", "python3")) {
    $parts = $candidate.Split(" ")
    $exe = $parts[0]
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
    $argv = @()
    if ($parts.Count -gt 1) { $argv += $parts[1] }
    $argv += @("-c", "import sys; sys.exit(0 if sys.version_info[:2] >= (3,11) else 1)")
    & $exe @argv 2>$null
    if ($LASTEXITCODE -eq 0) { $PythonExe = $exe; $PythonArgs = @(); if ($parts.Count -gt 1) { $PythonArgs = @($parts[1]) }; break }
}
if (-not $PythonExe) {
    Write-Bad "Не найден Python 3.11 или новее."
    Write-Bad "Установите его: winget install Python.Python.3.12"
    exit 1
}
Write-Host "    $PythonExe $PythonArgs"

# ------------------------------------------------------------------ 2. venv
$VenvPython = Join-Path $Repo ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Step "Создаю виртуальное окружение .venv"
    & $PythonExe @PythonArgs -m venv (Join-Path $Repo ".venv")
    if ($LASTEXITCODE -ne 0) { Write-Bad "Не удалось создать .venv"; exit 1 }
}

# --------------------------------------------------------------- 3. пакеты
if (-not $SkipInstall) {
    Write-Step "Устанавливаю пакеты (первый запуск занимает несколько минут)"
    & $VenvPython -m pip install --upgrade pip --quiet
    & $VenvPython -m pip install --quiet -e . -e (Join-Path $Repo "command-center") -e (Join-Path $Repo "bossman-core")
    if ($LASTEXITCODE -ne 0) {
        Write-Bad "Установка пакетов не удалась. Полный вывод:"
        & $VenvPython -m pip install -e . -e (Join-Path $Repo "command-center") -e (Join-Path $Repo "bossman-core")
        exit 1
    }
}

# ---------------------------------------------------------------- 4. доктор
Write-Step "Предполётная проверка"
if ($Port -gt 0) { $env:BCC_PORT = "$Port" }
& $VenvPython (Join-Path $Repo "scripts\bossman_doctor.py") --json-out (Join-Path $Repo "doctor.json")
$DoctorCode = $LASTEXITCODE
if ($DoctorCode -ne 0) {
    Write-Bad "Есть BLOCKED-проверки: запуск остановлен намеренно."
    Write-Bad "Почините их и запустите снова. Подробности: doctor.json"
    exit $DoctorCode
}
if ($DoctorOnly) { exit 0 }

# ----------------------------------------------------------------- 5. запуск
if ($EveningTest) {
    Write-Step "Вечерняя приёмка владельца"
    & $VenvPython (Join-Path $Repo "scripts\evening_acceptance.py") run
    exit $LASTEXITCODE
}

Write-Step "Открываю BOSSMAN Command Center"
$LaunchArgs = @("-m", "bcc.desktop")
if ($Web) { $LaunchArgs += "--web" }
if ($Port -gt 0) { $LaunchArgs += @("--port", "$Port") }
& $VenvPython @LaunchArgs
exit $LASTEXITCODE
