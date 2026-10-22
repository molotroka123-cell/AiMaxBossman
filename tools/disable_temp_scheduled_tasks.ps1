<#
.SYNOPSIS
Reversible backup + disable (or restore) of non-Microsoft Scheduled Tasks executing out of C:\Temp or C:\Windows\Temp.
Requires explicit owner confirmation ("да") before running -Action Disable.

.EXAMPLE
powershell -NoProfile -ExecutionPolicy Bypass -File tools\disable_temp_scheduled_tasks.ps1 -Action DryRun
powershell -NoProfile -ExecutionPolicy Bypass -File tools\disable_temp_scheduled_tasks.ps1 -Action Disable
powershell -NoProfile -ExecutionPolicy Bypass -File tools\disable_temp_scheduled_tasks.ps1 -Action Restore -BackupDir "$env:LOCALAPPDATA\Bossman\backups\scheduled-tasks-20261022"
#>
param(
    [ValidateSet("DryRun", "Disable", "Restore")]
    [string]$Action = "DryRun",
    [string]$BackupDir = "$env:LOCALAPPDATA\Bossman\backups\scheduled-tasks-20261022"
)

$ErrorActionPreference = "Stop"

if ($Action -eq "Restore") {
    $manifestPath = Join-Path $BackupDir "manifest.json"
    if (-not (Test-Path $manifestPath)) {
        throw "Backup manifest not found: $manifestPath"
    }
    $items = Get-Content $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($item in $items) {
        Enable-ScheduledTask -TaskPath $item.TaskPath -TaskName $item.TaskName | Out-Null
        Write-Host "[RESTORED] $($item.TaskPath)$($item.TaskName)"
    }
    exit 0
}

$tasks = Get-ScheduledTask | Where-Object {
    $_.TaskPath -notlike "\Microsoft\*" -and $_.State -ne "Disabled"
} | Where-Object {
    $exec = (($_.Actions | ForEach-Object { "$($_.Execute) $($_.Arguments)" }) -join " | ").ToLowerInvariant()
    $exec.Contains("\temp\") -or $exec.Contains("/temp/") -or $exec.Contains("c:/temp") -or
    $exec.Contains("\appdata\local\temp") -or $exec.Contains("/appdata/local/temp")
}

if ($Action -eq "DryRun") {
    Write-Host "Found $($tasks.Count) active scheduled tasks pointing to Temp:"
    foreach ($t in $tasks) {
        $exec = ($t.Actions | ForEach-Object { "$($_.Execute) $($_.Arguments)" }) -join " | "
        Write-Host "  $($t.TaskPath)$($t.TaskName) -> $exec"
    }
    exit 0
}

New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null
$manifest = @()
foreach ($t in $tasks) {
    $safeName = ($t.TaskPath.Trim("\") + "_" + $t.TaskName) -replace "[^A-Za-z0-9_.-]", "_"
    $xmlPath = Join-Path $BackupDir "$safeName.xml"
    Export-ScheduledTask -TaskPath $t.TaskPath -TaskName $t.TaskName | Out-File -FilePath $xmlPath -Encoding UTF8
    Disable-ScheduledTask -TaskPath $t.TaskPath -TaskName $t.TaskName | Out-Null
    $manifest += [pscustomobject]@{
        TaskPath = $t.TaskPath
        TaskName = $t.TaskName
        XmlFile  = $xmlPath
    }
    Write-Host "[BACKUP+DISABLED] $($t.TaskPath)$($t.TaskName) -> $xmlPath"
}
$manifest | ConvertTo-Json -Depth 4 | Out-File -FilePath (Join-Path $BackupDir "manifest.json") -Encoding UTF8
Write-Host "Completed: $($manifest.Count) tasks backed up to $BackupDir and disabled."
