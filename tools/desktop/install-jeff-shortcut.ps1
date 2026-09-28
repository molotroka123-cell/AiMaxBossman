# Creates a separate Jeff shortcut. Never touches the BOSSMAN shortcut.
#
#   install-jeff-shortcut.ps1 [-Name "Jeff"] [-Destination <folder>] [-DataDir <dir>]
#                             [-Port 8850] [-Python <python.exe>] [-Remove]
#
# The shortcut runs `python -m bcc.jeff_desktop` with the working directory set
# to THIS checkout's command-center folder, so the window always runs the code
# of the checkout the shortcut was created from (the launcher refuses a Jeff
# server of another build). Nothing is written except the .lnk file.
[CmdletBinding()]
param(
    [string]$Name = "Jeff",
    [string]$Destination = "",
    [string]$DataDir = "",
    [int]$Port = 8850,
    [string]$Python = "",
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$CommandCenter = Join-Path $Repo "command-center"
if (-not $Destination) { $Destination = [Environment]::GetFolderPath("Desktop") }
if (-not (Test-Path -LiteralPath $Destination -PathType Container)) {
    throw "Destination folder does not exist: $Destination"
}
if ($Name -match '[\\/:*?"<>|]') { throw "Shortcut name contains characters Windows does not allow" }
$Shortcut = Join-Path $Destination ($Name + ".lnk")

if ($Remove) {
    if (Test-Path -LiteralPath $Shortcut) { Remove-Item -LiteralPath $Shortcut -Force }
    Write-Host "Jeff shortcut removed: $Shortcut"
    exit 0
}

if (-not $Python) {
    $Venv = Join-Path $Repo ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $Venv) { $Python = $Venv }
    else { $Python = (Get-Command python -ErrorAction Stop).Source }
}
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw "Python not found: $Python" }
if ($Port -lt 1024 -or $Port -gt 65535) { throw "Port must be 1024..65535" }

$Arguments = "-m bcc.jeff_desktop --port $Port"
if ($DataDir) { $Arguments += " --data-dir `"$DataDir`"" }

$Icon = Join-Path $CommandCenter "ui\icons\bossman.ico"
$Shell = New-Object -ComObject WScript.Shell
$Link = $Shell.CreateShortcut($Shortcut)
$Link.TargetPath = $Python
$Link.Arguments = $Arguments
$Link.WorkingDirectory = $CommandCenter
$Link.WindowStyle = 7
if (Test-Path -LiteralPath $Icon) { $Link.IconLocation = "$Icon,0" }
$Link.Description = "Jeff — participant-safe assistant window (no owner authority)"
$Link.Save()

Write-Host "Jeff shortcut created: $Shortcut"
Write-Host "Target: $Python $Arguments (cwd $CommandCenter)"
