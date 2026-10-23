"""Monthly PC health check for the owner's Windows box — READ-ONLY, privacy-safe, pult-ready.

    python tools/pc_health_check.py [--stage all|performance|startup|storage|apps|security|backup] [--pult] [--out DIR]

Seven owner prompts (23.10) in one tool. Nothing is closed, deleted, disabled or changed. Privacy rule
(owner 21.10): no chat/photo/personal content is read; files inside Pictures/Videos/Desktop/Гречка and photo or
voice dataset folders are never named — only folder sizes. Each stage returns a short Russian report that can be
sent to the owner's pult (tools/pult_note.py). Stage 7 (monthly) = scheduled task created by the owner's hand or
by `--schedule`, which registers "BossmanOne-MonthlyHealth" on day 1 of each month.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PRIVATE_PARTS = ("\\pictures", "\\videos", "\\desktop", "\\гречка", "\\datasets\\gf", "\\lora-datasets", "\\voice-train",
                 "\\appdata\\local\\bossman\\commandcenter\\chat", "\\onedrive")


def ps(script: str, timeout: int = 240) -> str:
    cmd = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
           "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + script]
    try:
        return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout, check=False).stdout
    except subprocess.TimeoutExpired:
        return ""


def psj(script: str, timeout: int = 240):
    out = ps(script + " | ConvertTo-Json -Depth 4 -Compress", timeout)
    try:
        data = json.loads(out) if out.strip() else []
    except ValueError:
        return []
    return data if isinstance(data, list) else [data]


def _private(path: str) -> bool:
    low = path.lower()
    return any(p in low for p in PRIVATE_PARTS)


def _mask(path: str) -> str:
    """A private path is shown only up to its top-level private folder."""
    if not _private(path):
        return path
    low = path.lower()
    cut = min(low.find(p) + len(p) for p in PRIVATE_PARTS if low.find(p) != -1)
    return path[:cut] + "\\…(личное, не раскрывается)"


# ---- 1. performance -------------------------------------------------------------------------
def stage_performance() -> dict:
    rows = psj("$t=(Get-CimInstance Win32_OperatingSystem).TotalVisibleMemorySize*1KB; "
               "$a=Get-Process | Select-Object Id,ProcessName,CPU,WS; Start-Sleep 3; "
               "$b=Get-Process | Select-Object Id,CPU,WS; $cores=(Get-CimInstance Win32_ComputerSystem).NumberOfLogicalProcessors; "
               "$m=@{}; foreach($p in $b){$m[$p.Id]=$p}; "
               "$a | ForEach-Object { $q=$m[$_.Id]; if($q){ $d=[double]($q.CPU-$_.CPU); "
               "[pscustomobject]@{name=$_.ProcessName; pid=$_.Id; cpu_pct=[math]::Round(100*$d/3/$cores,1); "
               "mem_pct=[math]::Round(100*$q.WS/$t,1); mem_mb=[int]($q.WS/1MB)} } } | "
               "Where-Object { $_.cpu_pct -gt 5 -or $_.mem_pct -gt 5 } | Sort-Object cpu_pct -Descending")
    load = psj("$o=Get-CimInstance Win32_OperatingSystem; [pscustomobject]@{ram_total_gb=[int]($o.TotalVisibleMemorySize/1MB);"
               "ram_free_gb=[int]($o.FreePhysicalMemory/1MB); uptime_h=[int]((Get-Date)-$o.LastBootUpTime).TotalHours}")
    return {"hot_processes": rows, "system": load[0] if load else {}}


# ---- 2. startup -----------------------------------------------------------------------------
def stage_startup() -> dict:
    items = psj("Get-CimInstance Win32_StartupCommand | Select-Object Name,Command,Location,User")
    tasks = psj("Get-ScheduledTask | Where-Object { $_.State -ne 'Disabled' -and ($_.Triggers | Where-Object { "
                "$_.CimClass.CimClassName -match 'Logon|Boot' }) } | ForEach-Object { $a=$_.Actions[0]; "
                "[pscustomobject]@{task=$_.TaskName; path=$_.TaskPath; exe=$a.Execute; args=[string]$a.Arguments} }")
    for t in tasks:
        t["args"] = (t.get("args") or "")[:160]
    return {"startup_entries": items, "logon_boot_tasks": tasks}


# ---- 3. storage -----------------------------------------------------------------------------
def _dir_sizes(root: str, top: int = 20) -> list[dict]:
    rows = psj(f"Get-ChildItem -LiteralPath '{root}' -Directory -Force -ErrorAction SilentlyContinue | ForEach-Object {{ "
               "$b=(Get-ChildItem -LiteralPath $_.FullName -Recurse -File -Force -ErrorAction SilentlyContinue | "
               "Measure-Object Length -Sum).Sum; [pscustomobject]@{path=$_.FullName; gb=[math]::Round(($b/1GB),1)} }} | "
               f"Sort-Object gb -Descending | Select-Object -First {top}", timeout=900)
    return [{"path": _mask(r["path"]), "gb": r["gb"]} for r in rows if r.get("gb")]


def stage_storage() -> dict:
    drives = psj("Get-PSDrive -PSProvider FileSystem | Where-Object Used | Select-Object Name,@{n='used_gb';e={[int]($_.Used/1GB)}},"
                 "@{n='free_gb';e={[int]($_.Free/1GB)}}")
    home = os.environ.get("USERPROFILE", "C:\\Users\\asd")
    top_home = _dir_sizes(home, 20)
    big_files = psj(f"Get-ChildItem -LiteralPath '{home}' -Recurse -File -Force -ErrorAction SilentlyContinue | "
                    "Where-Object Length -gt 2GB | Sort-Object Length -Descending | Select-Object -First 20 "
                    "FullName,@{n='gb';e={[math]::Round($_.Length/1GB,1)}},LastWriteTime", timeout=900)
    downloads = psj(f"Get-ChildItem -LiteralPath '{home}\\Downloads' -File -Force -ErrorAction SilentlyContinue | "
                    "Where-Object { $_.Extension -in '.exe','.msi','.zip','.7z','.iso','.dmg' -and $_.LastWriteTime -lt (Get-Date).AddDays(-30) } | "
                    "Sort-Object Length -Descending | Select-Object -First 15 Name,@{n='mb';e={[int]($_.Length/1MB)}},LastWriteTime")
    temp = psj("foreach($d in @($env:TEMP, 'C:\\Windows\\Temp')){ $b=(Get-ChildItem -LiteralPath $d -Recurse -File -Force -ErrorAction SilentlyContinue | "
               "Measure-Object Length -Sum).Sum; [pscustomobject]@{path=$d; gb=[math]::Round($b/1GB,1)} }")
    return {"drives": drives, "top_home_folders": top_home,
            "largest_files": [{"path": _mask(f["FullName"]), "gb": f["gb"]} for f in big_files if not _private(f["FullName"])],
            "private_large_files_hidden": sum(1 for f in big_files if _private(f["FullName"])),
            "old_installers_in_downloads": downloads, "temp_dirs": temp,
            "duplicate_photos": "NOT_RUN: по правилу владельца фото не читаются"}


# ---- 4. unused apps -------------------------------------------------------------------------
def stage_apps() -> dict:
    apps = psj("$k='HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*','HKLM:\\Software\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*',"
               "'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*'; Get-ItemProperty $k -ErrorAction SilentlyContinue | "
               "Where-Object { $_.DisplayName -and -not $_.SystemComponent } | Select-Object DisplayName,InstallDate,"
               "@{n='size_mb';e={[int]($_.EstimatedSize/1024)}},InstallLocation | Sort-Object size_mb -Descending")
    recent = set()
    for p in psj("Get-ChildItem C:\\Windows\\Prefetch -Filter *.pf -ErrorAction SilentlyContinue | Where-Object LastWriteTime -gt (Get-Date).AddDays(-180) | "
                 "Select-Object @{n='n';e={($_.Name -split '-')[0].ToLower()}}"):
        recent.add(str(p.get("n", "")))
    cutoff = time.strftime("%Y%m%d", time.localtime(time.time() - 180 * 86400))
    stale = []
    for a in apps:
        name = str(a.get("DisplayName") or "")
        inst = str(a.get("InstallDate") or "")
        exe_hint = name.split(" ")[0].lower()
        ran = any(exe_hint and exe_hint in r for r in recent)
        if inst and inst < cutoff and not ran:
            stale.append({"name": name, "installed": inst, "size_mb": a.get("size_mb") or 0})
    return {"installed_count": len(apps), "candidates_unused_6m": sorted(stale, key=lambda x: -x["size_mb"])[:25],
            "method": "InstallDate старше 6 мес и нет свежего Prefetch-следа (эвристика, не точный учёт использования)"}


# ---- 5. security ----------------------------------------------------------------------------
def stage_security() -> dict:
    net = psj("Get-NetTCPConnection -State Established -ErrorAction SilentlyContinue | Where-Object { $_.RemoteAddress -notmatch '^(127\\.|::1|0\\.0\\.0\\.0)' } | "
              "Group-Object OwningProcess | ForEach-Object { $p=Get-Process -Id $_.Name -ErrorAction SilentlyContinue; "
              "[pscustomobject]@{pid=[int]$_.Name; name=$p.ProcessName; path=$p.Path; remote=($_.Group | Select-Object -First 3 | ForEach-Object { $_.RemoteAddress+':'+$_.RemotePort }) -join ','; conns=$_.Count} } | Sort-Object conns -Descending")
    unsigned = psj("Get-Process | Where-Object Path | Select-Object -Unique Path | ForEach-Object { $s=Get-AuthenticodeSignature $_.Path -ErrorAction SilentlyContinue; "
                   "if($s.Status -ne 'Valid'){ [pscustomobject]@{path=$_.Path; status=[string]$s.Status} } }", timeout=400)
    ext = psj("$roots=@(\"$env:LOCALAPPDATA\\Google\\Chrome\\User Data\\Default\\Extensions\",\"$env:LOCALAPPDATA\\Microsoft\\Edge\\User Data\\Default\\Extensions\"); "
              "foreach($r in $roots){ Get-ChildItem $r -Directory -ErrorAction SilentlyContinue | ForEach-Object { $m=Get-ChildItem $_.FullName -Recurse -Filter manifest.json -ErrorAction SilentlyContinue | Select-Object -First 1; "
              "if($m){ $j=Get-Content $m.FullName -Raw | ConvertFrom-Json; [pscustomobject]@{browser=($r -split '\\\\')[-4]; id=$_.Name; name=[string]$j.name; version=[string]$j.version} } } }")
    temp_tasks = psj("Get-ScheduledTask | Where-Object { $_.State -ne 'Disabled' -and ([string]$_.Actions[0].Arguments -match 'C:\\\\temp|-executionpolicy bypass|-enc') } | "
                     "Select-Object TaskName,@{n='args';e={([string]$_.Actions[0].Arguments).Substring(0,[Math]::Min(120,([string]$_.Actions[0].Arguments).Length))}}")
    defender = psj("Get-MpComputerStatus -ErrorAction SilentlyContinue | Select-Object AntivirusEnabled,RealTimeProtectionEnabled,AntivirusSignatureLastUpdated,QuickScanEndTime")
    return {"background_internet": net, "unsigned_running_binaries": unsigned, "browser_extensions": ext,
            "suspicious_scheduled_tasks": temp_tasks, "defender": defender[0] if defender else {}}


# ---- 6. backup -------------------------------------------------------------------------------
def stage_backup() -> dict:
    fh = ps("$k='HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\FileHistory'; if(Test-Path $k){ (Get-ItemProperty $k).PSObject.Properties | "
            "Where-Object { $_.Name -notlike 'PS*' } | ForEach-Object { $_.Name+'='+$_.Value } } else { 'FileHistory: не настроен' }")
    restore = psj("Get-ComputerRestorePoint -ErrorAction SilentlyContinue | Sort-Object CreationTime -Descending | Select-Object -First 3 Description,CreationTime")
    onedrive = ps("if(Get-Process OneDrive -ErrorAction SilentlyContinue){ 'OneDrive: процесс запущен' } else { 'OneDrive: не запущен' }")
    boss = psj("Get-ScheduledTask -TaskName 'BossmanOne-8-Backup' -ErrorAction SilentlyContinue | Get-ScheduledTaskInfo | Select-Object TaskName,LastRunTime,LastTaskResult,NextRunTime")
    dest = ps("Get-ChildItem (Join-Path $env:LOCALAPPDATA 'Bossman\\backups') -Directory -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | "
              "Select-Object -First 3 | ForEach-Object { $_.Name+' '+$_.LastWriteTime }")
    return {"windows_file_history": fh.strip().splitlines(), "restore_points": restore, "onedrive": onedrive.strip(),
            "bossman_nightly_backup_task": boss[0] if boss else {}, "bossman_backup_dirs": dest.strip().splitlines()}


STAGES = {"performance": stage_performance, "startup": stage_startup, "storage": stage_storage, "apps": stage_apps,
          "security": stage_security, "backup": stage_backup}


def summarize(name: str, d: dict) -> str:
    if name == "performance":
        s = d.get("system", {})
        lines = [f"1/7 Производительность: RAM {s.get('ram_free_gb')}/{s.get('ram_total_gb')} ГБ свободно, аптайм {s.get('uptime_h')} ч.",
                 "Процессы >5% CPU или памяти:"] + [f"  {p['name']} (pid {p['pid']}): CPU {p['cpu_pct']}%, RAM {p['mem_pct']}% ({p['mem_mb']} МБ)"
                                                   for p in d.get("hot_processes", [])[:12]]
        return "\n".join(lines) if len(lines) > 2 else lines[0] + "\nПроцессов тяжелее 5% нет."
    if name == "startup":
        lines = [f"2/7 Автозагрузка: записей {len(d['startup_entries'])}, задач планировщика при входе/загрузке {len(d['logon_boot_tasks'])}."]
        lines += [f"  [autorun] {e.get('Name')}: {str(e.get('Command'))[:80]}" for e in d["startup_entries"][:15]]
        lines += [f"  [task] {t['task']}: {t['exe']} {t['args'][:70]}" for t in d["logon_boot_tasks"][:25]]
        return "\n".join(lines)
    if name == "storage":
        lines = ["3/7 Диск: " + ", ".join(f"{x['Name']}: {x['used_gb']} ГБ занято / {x['free_gb']} ГБ свободно" for x in d["drives"])]
        lines += ["Крупнейшие папки профиля:"] + [f"  {f['gb']} ГБ  {f['path']}" for f in d["top_home_folders"][:20]]
        lines += ["Файлы >2 ГБ (личные скрыты: %d):" % d["private_large_files_hidden"]] + [f"  {f['gb']} ГБ  {f['path']}" for f in d["largest_files"][:10]]
        lines += ["Старые установщики в Downloads (>30 дней):"] + [f"  {x['mb']} МБ  {x['Name']}" for x in d["old_installers_in_downloads"][:10]]
        lines += [f"Temp: " + ", ".join(f"{t['path']} {t['gb']} ГБ" for t in d["temp_dirs"]), "Дубликаты фото: " + d["duplicate_photos"]]
        return "\n".join(lines)
    if name == "apps":
        lines = [f"4/7 Приложения: установлено {d['installed_count']}; кандидаты без следов запуска 6 мес ({d['method']}):"]
        lines += [f"  {a['name']} — установлено {a['installed']}, {a['size_mb']} МБ" for a in d["candidates_unused_6m"][:20]]
        return "\n".join(lines) + "\nНичего не удалено — решение за владельцем."
    if name == "security":
        dfn = d.get("defender", {})
        lines = [f"5/7 Безопасность: Defender AV={dfn.get('AntivirusEnabled')} RT={dfn.get('RealTimeProtectionEnabled')} базы {str(dfn.get('AntivirusSignatureLastUpdated'))[:10]}."]
        lines += ["Процессы с интернет-соединениями:"] + [f"  {n.get('name')} (pid {n.get('pid')}, {n.get('conns')} соед.) → {n.get('remote')}" for n in d["background_internet"][:15]]
        lines += [f"Неподписанные запущенные бинарники: {len(d['unsigned_running_binaries'])}"] + [f"  {u['status']}: {_mask(u['path'])}" for u in d["unsigned_running_binaries"][:12]]
        lines += [f"Расширения браузеров: {len(d['browser_extensions'])}"] + [f"  [{e['browser']}] {e['name']} {e['version']} ({e['id'][:8]}…)" for e in d["browser_extensions"][:20]]
        lines += [f"Подозрительные задачи планировщика (C:\\temp / bypass / -enc): {len(d['suspicious_scheduled_tasks'])}"] + [f"  {t['TaskName']}: {t['args']}" for t in d["suspicious_scheduled_tasks"][:12]]
        return "\n".join(lines) + "\nНичего не изменено — только наблюдение."
    if name == "backup":
        b = d.get("bossman_nightly_backup_task", {})
        lines = ["6/7 Бэкапы:", "Windows File History: " + "; ".join(d["windows_file_history"][:4]),
                 "Точки восстановления: " + ("; ".join(f"{r.get('Description')} {str(r.get('CreationTime'))[:16]}" for r in d["restore_points"]) or "нет"),
                 d["onedrive"], f"Bossman nightly_backup (задача BossmanOne-8-Backup): последний запуск {b.get('LastRunTime')}, результат {b.get('LastTaskResult')}, следующий {b.get('NextRunTime')}",
                 "Каталоги бэкапа Bossman: " + ("; ".join(d["bossman_backup_dirs"][:3]) or "не найдены")]
        return "\n".join(lines)
    return json.dumps(d, ensure_ascii=False)[:1500]


def schedule_monthly(python: str, repo: Path) -> str:
    script = str(repo / "tools" / "pc_health_check.py")
    cmd = (f"$a=New-ScheduledTaskAction -Execute '{python}' -Argument '-X utf8 \"{script}\" --stage all --pult'; "
           "$t=New-ScheduledTaskTrigger -Monthly -DaysOfMonth 1 -At 10:00; "
           "Register-ScheduledTask -TaskName 'BossmanOne-MonthlyHealth' -Action $a -Trigger $t -Description 'Monthly read-only PC health check -> pult' -Force | Out-Null; "
           "(Get-ScheduledTask -TaskName 'BossmanOne-MonthlyHealth').State")
    return ps(cmd).strip()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--stage", default="all", choices=["all", *STAGES])
    ap.add_argument("--pult", action="store_true", help="send each stage report to the owner's pult via tools/pult_note.py")
    ap.add_argument("--schedule", action="store_true", help="register the monthly scheduled task (stage 7)")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    out_dir = Path(a.out) if a.out else Path(os.environ.get("LOCALAPPDATA", ".")) / "Bossman" / "CommandCenter" / "health"
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "stages": {}, "summaries": {}}
    names = list(STAGES) if a.stage == "all" else [a.stage]
    for name in names:
        try:
            data = STAGES[name]()
        except Exception as exc:  # noqa: BLE001 - one stage must not kill the monthly run
            data = {"error": type(exc).__name__}
        report["stages"][name] = data
        text = summarize(name, data) if "error" not in data else f"{name}: ошибка {data['error']}"
        report["summaries"][name] = text
        print(text, "\n")
        if a.pult:
            subprocess.run([sys.executable, "-X", "utf8", str(HERE / "pult_note.py"), text], check=False, timeout=60)
    if a.schedule:
        report["schedule"] = schedule_monthly(sys.executable, HERE.parent)
        print("7/7 Ежемесячная проверка: задача BossmanOne-MonthlyHealth =", report["schedule"])
    path = out_dir / f"health-{time.strftime('%Y%m%d-%H%M')}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8", newline="\n")
    print("saved:", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
