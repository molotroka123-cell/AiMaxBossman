# Creates a separate Jeff desktop shortcut without changing the BOSSMAN shortcut.
[CmdletBinding()]
param([switch]$Remove)

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$Desktop = [Environment]::GetFolderPath("Desktop")
$Shortcut = Join-Path $Desktop "Jeff.lnk"

if ($Remove) {
    if (Test-Path $Shortcut) { Remove-Item -LiteralPath $Shortcut -Force }
    Write-Host "Jeff shortcut removed: $Shortcut"
    exit 0
}

$Python = Join-Path $Repo ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    throw "Bossman .venv is missing. Run start-bossman.ps1 once before installing Jeff shortcut."
}

$Icon = Join-Path $Repo "command-center\ui\icons\bossman.ico"
$Shell = New-Object -ComObject WScript.Shell
$Link = $Shell.CreateShortcut($Shortcut)
$Link.TargetPath = $Python
$Link.Arguments = "-m bcc.jeff_desktop"
$Link.WorkingDirectory = $Repo
if (Test-Path $Icon) { $Link.IconLocation = "$Icon,0" }
$Link.Description = "Jeff — participant-safe Bossman assistant UX"
$Link.Save()

Write-Host "Jeff shortcut created: $Shortcut"
Write-Host "It uses the existing Bossman backend and a separate browser profile."
