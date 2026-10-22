"""Read-only cyber-defence audit of the owner's Windows PC for Bossman (owner 22.10: «Боссман должен кибер защищать мой ПК»).

Observes only: Defender, firewall, listening ports, autoruns, scheduled tasks, failed logons, UAC/SmartScreen, Tailscale funnel.
Never reads file contents, chats or photos (owner rule 21.10). Every response action lives in a separate ASK-gated tool.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time

PS = ["powershell", "-NoProfile", "-NonInteractive", "-Command"]
EXPOSED = {3389: "RDP", 445: "SMB", 5985: "WinRM", 5986: "WinRM-S", 11434: "Ollama", 8801: "Bossman", 8840: "Bossman Mobile",
           8830: "War Terminal", 4096: "OpenCode"}
SIG_MAX_AGE_DAYS = 3


def ps_json(script: str, timeout: float = 60.0):
    """Run one read-only PowerShell snippet and parse its JSON output; errors become a finding, never a crash."""
    try:
        p = subprocess.run(PS + [script + " | ConvertTo-Json -Depth 4 -Compress"], capture_output=True, text=True,
                           timeout=timeout, encoding="utf-8", errors="replace")
        return json.loads(p.stdout) if p.stdout.strip() else None
    except (subprocess.TimeoutExpired, ValueError, OSError) as exc:
        return {"_error": str(exc)[:200]}


def _list(x):
    return x if isinstance(x, list) else ([] if x is None else [x])


def check_defender(f: list[dict]) -> None:
    d = ps_json("Get-MpComputerStatus | Select-Object RealTimeProtectionEnabled,AntivirusEnabled,AntivirusSignatureAge,"
                "QuickScanAge,IsTamperProtected")
    if not isinstance(d, dict) or "_error" in d:
        f.append({"severity": "HIGH", "check": "defender", "detail": "Get-MpComputerStatus недоступен — Defender выключен или заменён?"})
        return
    if not d.get("RealTimeProtectionEnabled") or not d.get("AntivirusEnabled"):
        f.append({"severity": "HIGH", "check": "defender", "detail": "защита в реальном времени выключена"})
    if int(d.get("AntivirusSignatureAge") or 0) > SIG_MAX_AGE_DAYS:
        f.append({"severity": "HIGH", "check": "defender", "detail": f"сигнатурам {d.get('AntivirusSignatureAge')} дн."})
    if not d.get("IsTamperProtected"):
        f.append({"severity": "MEDIUM", "check": "defender", "detail": "Tamper Protection выключена"})
    if int(d.get("QuickScanAge") or 0) > 7:
        f.append({"severity": "LOW", "check": "defender", "detail": f"быстрое сканирование {d.get('QuickScanAge')} дн. назад"})


def check_firewall(f: list[dict]) -> None:
    for p in _list(ps_json("Get-NetFirewallProfile | Select-Object Name,Enabled")):
        if isinstance(p, dict) and not p.get("Enabled"):
            f.append({"severity": "HIGH", "check": "firewall", "detail": f"профиль {p.get('Name')} выключен"})


def check_ports(f: list[dict]) -> None:
    rows = _list(ps_json("Get-NetTCPConnection -State Listen | Select-Object LocalAddress,LocalPort,OwningProcess"))
    procs = {int(p["Id"]): p.get("ProcessName") for p in _list(ps_json("Get-Process | Select-Object Id,ProcessName")) if isinstance(p, dict)}
    for r in rows:
        if not isinstance(r, dict):
            continue
        port, addr = int(r.get("LocalPort") or 0), str(r.get("LocalAddress") or "")
        public, name = addr in ("0.0.0.0", "::"), EXPOSED.get(port)
        owner = procs.get(int(r.get("OwningProcess") or 0))
        if name in ("RDP", "SMB", "WinRM", "WinRM-S") and public:
            f.append({"severity": "HIGH", "check": "ports", "detail": f"{name} :{port} слушает наружу ({owner})"})
        elif name and public:
            f.append({"severity": "MEDIUM", "check": "ports", "detail": f"{name} :{port} не на loopback — должен быть 127.0.0.1 ({owner})"})


def check_autoruns(f: list[dict]) -> None:
    script = ("$keys='HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run','HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\RunOnce',"
              "'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run','HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\RunOnce';"
              "$out=@(); foreach($k in $keys){ if(Test-Path $k){ (Get-ItemProperty $k).PSObject.Properties | Where-Object { $_.Name -notlike 'PS*' } |"
              " ForEach-Object { $exe=($_.Value -replace '^\"([^\"]+)\".*','$1') -replace '^(\\S+\\.exe).*','$1';"
              " $sig = if(Test-Path $exe){ (Get-AuthenticodeSignature $exe).Status.ToString() } else { 'NotFound' };"
              " $out += [pscustomobject]@{Key=$k;Name=$_.Name;Exe=$exe;Sig=$sig} } } }; $out")
    for a in _list(ps_json(script, timeout=120)):
        if isinstance(a, dict) and a.get("Sig") != "Valid":
            sev = "MEDIUM" if a.get("Sig") in ("NotSigned", "NotFound", "HashMismatch") else "LOW"
            f.append({"severity": sev, "check": "autorun", "detail": f"{a.get('Name')}: {a.get('Exe')} подпись {a.get('Sig')}"})


def check_tasks(f: list[dict]) -> None:
    script = ("Get-ScheduledTask | Where-Object { $_.TaskPath -notlike '\\Microsoft\\*' -and $_.State -ne 'Disabled' } |"
              " ForEach-Object { [pscustomobject]@{Name=$_.TaskName;Path=$_.TaskPath;Exec=($_.Actions | ForEach-Object { $_.Execute + ' ' + $_.Arguments }) -join ' | '} }")
    for t in _list(ps_json(script, timeout=120)):
        ex = str((t or {}).get("Exec") or "").lower()
        if any(s in ex for s in ("\\temp\\", "/temp/", "c:/temp", "\\appdata\\local\\temp", "/appdata/local/temp", "-enc ", "-encodedcommand", "bitsadmin", "mshta", "wscript")):
            f.append({"severity": "MEDIUM", "check": "tasks", "detail": f"{t.get('Path')}{t.get('Name')}: {ex[:120]}"})


def check_security_log(f: list[dict]) -> None:
    script = ("$e=Get-WinEvent -FilterHashtable @{LogName='Security';Id=4625,4732,1102;StartTime=(Get-Date).AddHours(-24)} -ErrorAction SilentlyContinue;"
              "$fail=@($e | Where-Object Id -eq 4625); [pscustomobject]@{Failed=$fail.Count;"
              "TopIp=($fail | ForEach-Object { $_.Properties[19].Value } | Group-Object | Sort-Object Count -Descending | Select-Object -First 1 | ForEach-Object { $_.Name + ' x' + $_.Count });"
              "NewAdmins=@($e | Where-Object Id -eq 4732).Count; Cleared=@($e | Where-Object Id -eq 1102).Count}")
    d = ps_json(script, timeout=120)
    if not isinstance(d, dict) or "_error" in d:
        f.append({"severity": "LOW", "check": "seclog", "detail": "журнал Security недоступен (нужны права администратора)"})
        return
    if int(d.get("Failed") or 0) >= 20:
        f.append({"severity": "HIGH", "check": "seclog", "detail": f"{d['Failed']} неудачных входов за 24 ч, чаще всего {d.get('TopIp')}"})
    if int(d.get("NewAdmins") or 0):
        f.append({"severity": "HIGH", "check": "seclog", "detail": f"добавлено администраторов: {d['NewAdmins']}"})
    if int(d.get("Cleared") or 0):
        f.append({"severity": "HIGH", "check": "seclog", "detail": "журнал Security очищался (1102)"})


def check_hardening(f: list[dict]) -> None:
    d = ps_json("$p='HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Policies\\System';"
                "[pscustomobject]@{UAC=(Get-ItemProperty $p).EnableLUA;SmartScreen=(Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Explorer' -ErrorAction SilentlyContinue).SmartScreenEnabled}")
    if isinstance(d, dict) and d.get("UAC") == 0:
        f.append({"severity": "HIGH", "check": "hardening", "detail": "UAC выключен"})
    if isinstance(d, dict) and str(d.get("SmartScreen") or "").lower() == "off":
        f.append({"severity": "MEDIUM", "check": "hardening", "detail": "SmartScreen выключен"})
    try:
        out = subprocess.run(["tailscale", "funnel", "status"], capture_output=True, text=True, timeout=20).stdout.lower()
        if "https://" in out and "no funnel" not in out:
            f.append({"severity": "HIGH", "check": "tailscale", "detail": "Tailscale funnel активен — Bossman виден из интернета"})
    except (OSError, subprocess.TimeoutExpired):
        pass


def grade(findings: list[dict]) -> str:
    sev = {x["severity"] for x in findings}
    return "RED" if "HIGH" in sev else ("YELLOW" if "MEDIUM" in sev else "GREEN")


def run_audit() -> dict:
    findings: list[dict] = []
    for check in (check_defender, check_firewall, check_ports, check_autoruns, check_tasks, check_security_log, check_hardening):
        try:
            check(findings)
        except Exception as exc:  # noqa: BLE001 - one broken check must not hide the others
            findings.append({"severity": "LOW", "check": check.__name__, "detail": f"проверка упала: {type(exc).__name__}"})
    return {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "grade": grade(findings), "findings": findings}


def to_html(rep: dict) -> str:
    icon = {"RED": "🔴", "YELLOW": "🟡", "GREEN": "🟢"}[rep["grade"]]
    lines = [f"<b>{icon} Киберзащита ПК: {rep['grade']}</b> · {rep['ts'][:16]}"]
    for x in rep["findings"] or [{"severity": "OK", "check": "all", "detail": "замечаний нет"}]:
        lines.append(f"<b>{x['severity']}</b> {x['check']}: <code>{x['detail'].replace('&', '&amp;').replace('<', '&lt;')}</code>")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--json", help="write the full report here")
    ap.add_argument("--pult", action="store_true", help="send the short report to the owner's pult (HTML)")
    a = ap.parse_args(argv)
    rep = run_audit()
    print(to_html(rep) if a.pult else json.dumps(rep, ensure_ascii=False, indent=1))
    if a.json:
        with open(a.json, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(rep, fh, ensure_ascii=False, indent=1)
    if a.pult:
        from tools import pult_note
        pult_note.send(to_html(rep), html=True)
    return 2 if rep["grade"] == "RED" else 0


if __name__ == "__main__":
    sys.exit(main())
