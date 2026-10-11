# Bossman hardware/OS security audit (read-only; owner request 11.10.2026: «обучи Bossman кибербезу моего железа»).
# Prints a Russian report with PASS/WARN/FAIL per check and writes it as JSON + text next to the
# remote-access logs: %LOCALAPPDATA%\Bossman\security\audit-<date>.{json,txt}. Nothing is changed here;
# fixes are listed as commands for the owner (UAC) or for a Bossman task with explicit approval.
# No secrets, passwords or key material are read or printed.
param([switch]$Quiet)
$ErrorActionPreference = 'SilentlyContinue'
$dir = Join-Path $env:LOCALAPPDATA 'Bossman\security'
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmm'
$rows = New-Object System.Collections.Generic.List[object]
function Add([string]$id, [string]$status, [string]$what, [string]$fix = '') {
    $rows.Add([pscustomobject]@{ id = $id; status = $status; what = $what; fix = $fix })
}

# 1. Defender realtime + signatures
$mp = Get-MpComputerStatus
if ($mp) {
    $age = if ($mp.AntivirusSignatureLastUpdated) { (Get-Date) - $mp.AntivirusSignatureLastUpdated } else { New-TimeSpan -Days 999 }
    Add 'defender.realtime' $(if ($mp.RealTimeProtectionEnabled) { 'PASS' } else { 'FAIL' }) "Defender real-time: $($mp.RealTimeProtectionEnabled)" 'Set-MpPreference -DisableRealtimeMonitoring $false'
    Add 'defender.signatures' $(if ($age.TotalDays -lt 3) { 'PASS' } else { 'WARN' }) ("Сигнатуры обновлены {0:N1} дн. назад" -f $age.TotalDays) 'Update-MpSignature'
    Add 'defender.tamper' $(if ($mp.IsTamperProtected) { 'PASS' } else { 'WARN' }) "Tamper protection: $($mp.IsTamperProtected)" 'Windows Security → Virus protection settings → Tamper Protection'
} else { Add 'defender' 'WARN' 'Defender не отвечает (другой антивирус?)' }

# 2. Firewall profiles
foreach ($p in Get-NetFirewallProfile) {
    Add "firewall.$($p.Name.ToLower())" $(if ($p.Enabled) { 'PASS' } else { 'FAIL' }) "Firewall $($p.Name): enabled=$($p.Enabled) inbound=$($p.DefaultInboundAction)" "Set-NetFirewallProfile -Name $($p.Name) -Enabled True -DefaultInboundAction Block"
}

# 3. Listening ports: anything bound to 0.0.0.0/:: other than well-known Windows services
$listen = Get-NetTCPConnection -State Listen | Where-Object { $_.LocalAddress -in '0.0.0.0', '::' }
$exposed = foreach ($c in $listen) {
    $pr = Get-Process -Id $c.OwningProcess
    $pn = if ($pr) { $pr.ProcessName } else { "pid$($c.OwningProcess)" }
    if ($pn -notin 'svchost', 'System', 'lsass', 'services', 'wininit', 'spoolsv') { "$($c.LocalPort)/$pn" }
}
Add 'ports.exposed' $(if ($exposed) { 'WARN' } else { 'PASS' }) ("Порты на всех интерфейсах (не Windows-службы): " + ($(if ($exposed) { $exposed -join ', ' } else { 'нет' }))) 'Bossman/Ollama/бэкенды должны слушать 127.0.0.1; проверьте правило firewall для каждого'
$bossman = Get-NetTCPConnection -State Listen | Where-Object { $_.LocalPort -in 8801, 8871, 11434, 8765 }
$bad = $bossman | Where-Object { $_.LocalAddress -notin '127.0.0.1', '::1' }
Add 'ports.bossman_local' $(if ($bad) { 'FAIL' } else { 'PASS' }) ("Bossman/Ollama порты: " + (($bossman | ForEach-Object { "$($_.LocalPort)@$($_.LocalAddress)" }) -join ', ')) 'Привязать к 127.0.0.1 (OLLAMA_HOST=127.0.0.1, bcc --host 127.0.0.1)'

# 4. RDP, SMB1, remote registry, WinRM
$rdp = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server').fDenyTSConnections
Add 'rdp' $(if ($rdp -eq 1) { 'PASS' } else { 'WARN' }) "RDP: $(if ($rdp -eq 1) { 'выключен' } else { 'включён' })" 'Если не нужен: Set-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server" fDenyTSConnections 1'
$smb1 = (Get-WindowsOptionalFeature -Online -FeatureName SMB1Protocol).State
if (-not $smb1) { $smb1 = (Get-SmbServerConfiguration).EnableSMB1Protocol; $smb1 = if ($null -eq $smb1) { $null } elseif ($smb1) { 'Enabled' } else { 'Disabled' } }
Add 'smb1' $(if ("$smb1" -eq 'Disabled') { 'PASS' } elseif (-not $smb1) { 'WARN' } else { 'FAIL' }) "SMB1: $(if ($smb1) { $smb1 } else { 'неизвестно (нужны права администратора)' })" 'Disable-WindowsOptionalFeature -Online -FeatureName SMB1Protocol'
$winrm = Get-Service WinRM
Add 'winrm' $(if ($winrm.Status -ne 'Running') { 'PASS' } else { 'WARN' }) "WinRM: $($winrm.Status)" 'Stop-Service WinRM; Set-Service WinRM -StartupType Disabled'
$rr = Get-Service RemoteRegistry
Add 'remote_registry' $(if ($rr.Status -ne 'Running') { 'PASS' } else { 'WARN' }) "RemoteRegistry: $($rr.Status)" 'Set-Service RemoteRegistry -StartupType Disabled'

# 5. UAC, autologon, local admins, guest
$uac = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System'
Add 'uac' $(if ($uac.EnableLUA -eq 1 -and $uac.ConsentPromptBehaviorAdmin -ge 2) { 'PASS' } else { 'WARN' }) "UAC EnableLUA=$($uac.EnableLUA) prompt=$($uac.ConsentPromptBehaviorAdmin)" 'Оставить UAC включённым'
$wl = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
$plainPw = [bool]$wl.DefaultPassword
Add 'autologon.plaintext' $(if ($plainPw) { 'FAIL' } else { 'PASS' }) "Пароль автологона в реестре открытым текстом: $plainPw (AutoAdminLogon=$($wl.AutoAdminLogon))" 'Использовать Sysinternals Autologon (LSA-секрет), удалить DefaultPassword'
$admins = (Get-LocalGroupMember -Group 'Administrators' | Select-Object -ExpandProperty Name) -join ', '
Add 'admins' 'INFO' "Локальные администраторы: $admins" 'Лишних убрать: Remove-LocalGroupMember'
$guest = Get-LocalUser -Name Guest
Add 'guest' $(if ($guest -and $guest.Enabled) { 'FAIL' } else { 'PASS' }) "Guest enabled: $($guest.Enabled)" 'Disable-LocalUser Guest'

# 6. Updates, BitLocker, Secure Boot
$hot = Get-HotFix | Sort-Object InstalledOn -Descending | Select-Object -First 1
$hage = if ($hot.InstalledOn) { ((Get-Date) - $hot.InstalledOn).TotalDays } else { 999 }
Add 'updates' $(if ($hage -lt 45) { 'PASS' } else { 'WARN' }) ("Последнее обновление {0:N0} дн. назад ({1})" -f $hage, $hot.HotFixID) 'Settings → Windows Update'
$bl = Get-BitLockerVolume -MountPoint $env:SystemDrive
Add 'bitlocker' $(if ($bl.ProtectionStatus -eq 'On') { 'PASS' } else { 'WARN' }) "BitLocker C: $($bl.ProtectionStatus)" 'Включить BitLocker (ключ восстановления сохранить вне ПК)'
$sb = Confirm-SecureBootUEFI
Add 'secureboot' $(if ($sb) { 'PASS' } else { 'WARN' }) "Secure Boot: $sb" 'Включить в UEFI'

# 7. Autoruns outside Program Files / Windows (unsigned or from user temp)
$runKeys = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run', 'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run'
$odd = foreach ($k in $runKeys) { (Get-ItemProperty $k).PSObject.Properties | Where-Object { $_.Name -notlike 'PS*' -and $_.Value -match '\\Temp\\|\\AppData\\Local\\Temp|\\Downloads\\' } | ForEach-Object { "$($_.Name)=$($_.Value)" } }
Add 'autoruns.temp' $(if ($odd) { 'FAIL' } else { 'PASS' }) ("Автозапуск из Temp/Downloads: " + ($(if ($odd) { $odd -join '; ' } else { 'нет' }))) 'Удалить запись и проверить файл'
$tasks = Get-ScheduledTask | Where-Object { $_.State -ne 'Disabled' -and $_.Actions.Execute -match 'Temp|Downloads|\.vbs|wscript|mshta' }
Add 'tasks.suspicious' $(if ($tasks) { 'WARN' } else { 'PASS' }) ("Задачи с запуском из Temp/скриптов: " + ($(if ($tasks) { ($tasks.TaskName -join ', ') } else { 'нет' }))) 'Проверить Unregister-ScheduledTask'

# 8. Keys and repos: files that must never be world-readable or in a checkout
$keyFiles = Get-ChildItem "$env:LOCALAPPDATA\Bossman\keys" -ErrorAction SilentlyContinue
$inRepo = Get-ChildItem "$env:USERPROFILE\Bossman" -Recurse -Depth 3 -Include '*.env', 'provider-keys*', '*.pem', 'id_rsa*' -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -notmatch '\\\.venv|\\venv|site-packages|node_modules|\\tmp\\|pytest|\\\.tmp|\\dist\d*\\|\\runtime\\|cacert\.pem$|roots\.pem$|\.example$|companion\.env$' }
Add 'keys.in_repo' $(if ($inRepo) { 'WARN' } else { 'PASS' }) ("Файлы ключей внутри checkout'ов: " + ($(if ($inRepo) { ($inRepo.FullName -replace [regex]::Escape($env:USERPROFILE), '~') -join '; ' } else { 'нет' }))) 'Перенести в %LOCALAPPDATA%\Bossman\keys и в .gitignore'
Add 'keys.store' 'INFO' "Ключей в хранилище Bossman: $(@($keyFiles).Count) файл(ов)" ''

# 9. DNS / hosts tampering
$hostsExtra = Get-Content "$env:SystemRoot\System32\drivers\etc\hosts" | Where-Object { $_ -match '^\s*\d' -and $_ -notmatch '127\.0\.0\.1\s+localhost|::1\s+localhost' }
Add 'hosts' $(if ($hostsExtra) { 'WARN' } else { 'PASS' }) ("Нестандартные записи hosts: " + ($(if ($hostsExtra) { $hostsExtra.Count } else { 0 }))) 'Проверить hosts'

# ---- report
$fail = @($rows | Where-Object status -eq 'FAIL').Count; $warn = @($rows | Where-Object status -eq 'WARN').Count
$text = @("Аудит безопасности ПК Bossman — $(Get-Date -Format 'dd.MM.yyyy HH:mm') — FAIL: $fail, WARN: $warn", '')
foreach ($r in $rows) { $text += ('[{0}] {1} — {2}' -f $r.status, $r.id, $r.what); if ($r.fix -and $r.status -in 'FAIL', 'WARN') { $text += ("      → {0}" -f $r.fix) } }
$text -join "`n" | Set-Content (Join-Path $dir "audit-$stamp.txt") -Encoding utf8
$rows | ConvertTo-Json -Depth 3 | Set-Content (Join-Path $dir "audit-$stamp.json") -Encoding utf8
if (-not $Quiet) { $text -join "`n" }
exit $(if ($fail -gt 0) { 2 } elseif ($warn -gt 0) { 1 } else { 0 })
