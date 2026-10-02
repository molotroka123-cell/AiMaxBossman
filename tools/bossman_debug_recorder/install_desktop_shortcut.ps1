$ErrorActionPreference = 'Stop'

$toolRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$desktop = [Environment]::GetFolderPath('Desktop')
$shortcutPath = Join-Path $desktop 'Bossman Debug Recorder.lnk'
$target = Join-Path $toolRoot 'START-RECORDER.cmd'

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $target
$shortcut.WorkingDirectory = $toolRoot
$shortcut.Description = 'Record Bossman clicks and runtime logs for an acceptance run'
$shortcut.IconLocation = "$env:SystemRoot\System32\shell32.dll,71"
$shortcut.Save()

Write-Output "Created: $shortcutPath"
