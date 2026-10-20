#!/usr/bin/env python3
"""Report-only watchdog for hung AI-agent CLI processes (codex, Claude Code, Antigravity, opencode, Qwen Code).

It NEVER kills, suspends or re-prioritises anything. It takes two samples of the process table ``--sample`` seconds
apart and reports:

* ``hung``   — agent roots that look stuck: age > ``--min-age-h`` (6 h) AND the CPU time of the agent's whole process
  tree grew < ``--cpu-min`` (2 s) over the window AND no descendant process started within ``--child-window-h`` (1 h);
* ``policy`` — any agent process started with ``--dangerously-skip-permissions`` / ``bypassPermissions`` (or the codex
  equivalent ``--dangerously-bypass-approvals-and-sandbox``). This is a separate list, not a hang verdict.

Bossman services (backend ``-m bcc``, pult/companion, Jeff ``bcc.pit``, phone app, War Terminal, governor, engines)
and everything they started are never reported; the patterns are reused from tools/resource_governor/governor.py.

Output: JSON (stdout) with a Russian pult text inside (``pult_text``), command lines redacted (tokens, keys, paths
under .ssh / vault, the home folder). Each finding carries the suggested MANUAL close command. Only with ``--send`` the
text goes to the owner through ``tools/pult_note.send()``; a pid (pid + create time) is sent at most once per
``--dedup-h`` (6 h), remembered in a small state file that is written only after a successful send.

Usage:
  python tools/agent_watchdog.py                 # 60 s window, print JSON
  python tools/agent_watchdog.py --sample 30 --text
  python tools/agent_watchdog.py --send          # also send NEW findings to the owner's pult
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
SCHEMA = "bossman.agent-watchdog/1"
STATE_NAME = "agent-watchdog-state.json"

DEFAULTS = {"sample_s": 60.0, "min_age_h": 6.0, "cpu_min_s": 2.0, "child_window_h": 1.0, "dedup_h": 6.0}


# ---------------------------------------------------------------------------------------------------------------
# Bossman service patterns: reused from the resource governor (single source of truth), local copy as a fallback
# ---------------------------------------------------------------------------------------------------------------
def _load_governor():
    try:
        spec = importlib.util.spec_from_file_location("_bossman_governor", HERE / "resource_governor" / "governor.py")
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:  # noqa: BLE001 - the watchdog must still run when the governor cannot be imported
        return None


_GOV = _load_governor()
if _GOV is not None:
    SERVICE_CMD = tuple(_GOV.SERVICE_CMD)
    SERVICE_NAMES = tuple(_GOV.SERVICE_NAMES)
else:  # pragma: no cover - mirrors governor.py 20.10
    SERVICE_CMD = tuple(re.compile(p, re.I) for p in (
        r"-m\s+bcc(\s|\.|$)", r"-m\s+bossmobile\b", r"-m\s+warterminal\b", r"owner_one_bossman",
        r"governor\.py\"?\s+(run|status)", r"search_proxy", r"keep_awake", r"\\direct_gen\\[a-z_]+_worker\.py",
        r"facefusion\.py", r"comfyui\\main\.py", r"\bmain\.py\b.*--listen"))
    SERVICE_NAMES = ("sd-cli.exe", "sd-server.exe", "ollama.exe", "ollama app.exe", "llama-server.exe", "ffmpeg.exe")

# a few explicit names on top of the governor's list (pult, Jeff, phone app, governor started in other ways)
EXTRA_SERVICE_CMD = tuple(re.compile(p, re.I) for p in (
    r"bcc[\\.]pit\b", r"telegram_companion", r"\bbossmobile\b", r"\bwarterminal\b", r"resource_governor",
    r"agent_watchdog\.py"))


def is_bossman_service(name: str, cmd: str) -> bool:
    if (name or "").lower() in SERVICE_NAMES:
        return True
    norm = (cmd or "").replace("/", "\\")
    return any(p.search(norm) for p in SERVICE_CMD) or any(p.search(norm) for p in EXTRA_SERVICE_CMD)


# ---------------------------------------------------------------------------------------------------------------
# agent recognition
# ---------------------------------------------------------------------------------------------------------------
_CODEX_CMD = re.compile(r"@openai\\codex|\\codex(\.exe|\.cmd|\.js)?\"?\s+(exec|app-server|e)\b|^\"?codex(\.exe)?\"?(\s|$)", re.I)
_CLAUDE_CMD = re.compile(r"@anthropic-ai\\claude-code|claude-code\\cli\.js|\\claude(\.exe)?\"?(\s|$)", re.I)
_OPENCODE_CMD = re.compile(r"\bopencode(-ai)?\b", re.I)
_QWEN_CMD = re.compile(r"@qwen-code\\|\bqwen-code\b|\\qwen(\.exe|\.cmd)?\"?(\s|$)", re.I)
BYPASS = re.compile(r"--dangerously-skip-permissions|bypasspermissions|--dangerously-bypass-approvals-and-sandbox",
                    re.I)


def agent_kind(name: str, cmd: str) -> str | None:
    """'codex' | 'claude' | 'antigravity' | 'opencode' | 'qwen' or None."""
    low_name = (name or "").lower()
    norm = (cmd or "").replace("/", "\\")
    if low_name.startswith("language_server") and "antigravity" in norm.lower():
        return "antigravity"
    if low_name in ("codex.exe", "codex") or _CODEX_CMD.search(norm):
        return "codex"
    if low_name in ("claude.exe", "claude") or _CLAUDE_CMD.search(norm):
        return "claude"
    if low_name in ("opencode.exe", "opencode") or (low_name.startswith(("node", "bun")) and _OPENCODE_CMD.search(norm)):
        return "opencode"
    if low_name in ("qwen.exe", "qwen") or _QWEN_CMD.search(norm):
        return "qwen"
    return None


def agent_role(kind: str, cmd: str) -> str:
    low = (cmd or "").lower()
    if kind == "codex":
        if "app-server" in low:
            return "app-server"
        if re.search(r"\bexec\b", low):
            return "exec"
    if kind == "claude" and re.search(r"(\s|^)(-p|--print)(\s|$)", low):
        return "print"
    if kind == "antigravity":
        return "language_server"
    return "interactive"


# ---------------------------------------------------------------------------------------------------------------
# redaction
# ---------------------------------------------------------------------------------------------------------------
_REDACT = [
    (re.compile(r"[^\s\"']*[\\/]\.ssh[\\/][^\s\"']*", re.I), "<path-redacted>"),
    (re.compile(r"[^\s\"']*[\\/]vault[\\/]?[^\s\"']*", re.I), "<path-redacted>"),
    (re.compile(r"(?i)\b(bearer|token|basic)\s+[A-Za-z0-9._~+/=-]{12,}"), r"\1 <redacted>"),
    (re.compile(r"(?i)(--?[a-z0-9-]*(?:token|key|secret|password|passwd|auth)[a-z0-9-]*)(=|\s+)(\"[^\"]*\"|'[^']*'|\S+)"),
     r"\1\2<redacted>"),
    (re.compile(r"(?i)\b([A-Z0-9_]*(?:TOKEN|KEY|SECRET|PASSWORD|PASSWD)[A-Z0-9_]*)=(\"[^\"]*\"|'[^']*'|\S+)"),
     r"\1=<redacted>"),
    (re.compile(r"\b(sk-(?:ant-|proj-|or-)?[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9]{12,}|github_pat_[A-Za-z0-9_]{12,}"
                r"|xox[abpr]-[A-Za-z0-9-]{8,}|AIza[0-9A-Za-z_-]{20,}|nvapi-[A-Za-z0-9_-]{12,}|hf_[A-Za-z0-9]{12,})"),
     "<redacted>"),
    (re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b"), "<redacted>"),                # Telegram bot token
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}"), "<redacted>"),   # JWT
    (re.compile(r"\b[A-Fa-f0-9]{40,}\b"), "<redacted>"),                             # long hex secrets
    (re.compile(r"(?<![\\/\w-])(?=[A-Za-z_+-]*\d)[A-Za-z0-9+_-]{40,}={0,2}"), "<redacted>"),   # long key-like blobs
]


def redact(cmd: str, limit: int = 160, home: str | None = None) -> str:
    text = " ".join((cmd or "").split())
    for pat, repl in _REDACT:
        text = pat.sub(repl, text)
    home = home if home is not None else str(Path.home())
    if home and len(home) > 3:
        text = re.sub(re.escape(home), "~", text, flags=re.I)
        text = re.sub(re.escape(home.replace("\\", "/")), "~", text, flags=re.I)
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


# ---------------------------------------------------------------------------------------------------------------
# process table (psutil, else CIM through PowerShell). Record: pid, ppid, name, cmd, ct (epoch s), cpu (s or None)
# ---------------------------------------------------------------------------------------------------------------
def collect_psutil() -> list[dict]:
    import psutil

    out = []
    for p in psutil.process_iter(["pid", "ppid", "name", "cmdline", "create_time"]):
        try:
            info = p.info
            try:
                ct = p.cpu_times()
                cpu = float(ct.user + ct.system)
            except (psutil.Error, OSError):
                cpu = None
            cmdline = info.get("cmdline")
            out.append({"pid": info["pid"], "ppid": info.get("ppid"), "name": info.get("name") or "",
                        "cmd": " ".join(cmdline) if isinstance(cmdline, list) else "",
                        "ct": float(info.get("create_time") or 0.0), "cpu": cpu})
        except (psutil.Error, OSError):
            continue
    return out


_CIM_PS = ("Get-CimInstance Win32_Process | ForEach-Object { [pscustomobject]@{ pid=$_.ProcessId; "
           "ppid=$_.ParentProcessId; name=$_.Name; cmd=$_.CommandLine; "
           "ct=$(if ($_.CreationDate) { [DateTimeOffset]::new($_.CreationDate).ToUnixTimeMilliseconds()/1000 } else { 0 }); "
           "k=$_.KernelModeTime; u=$_.UserModeTime } } | ConvertTo-Json -Compress")


def parse_cim(raw: str) -> list[dict]:
    data = json.loads(raw or "[]")
    if isinstance(data, dict):
        data = [data]
    out = []
    for r in data:
        try:
            k, u = r.get("k"), r.get("u")
            cpu = (float(k or 0) + float(u or 0)) / 1e7 if (k is not None or u is not None) else None
            out.append({"pid": int(r["pid"]), "ppid": int(r.get("ppid") or 0), "name": r.get("name") or "",
                        "cmd": r.get("cmd") or "", "ct": float(r.get("ct") or 0), "cpu": cpu})
        except (TypeError, ValueError, KeyError):
            continue
    return out


def collect_cim() -> list[dict]:
    exe = "powershell.exe"
    res = subprocess.run([exe, "-NoProfile", "-NonInteractive", "-Command", _CIM_PS], capture_output=True,
                         text=True, encoding="utf-8", errors="replace", timeout=120, check=False)
    return parse_cim(res.stdout)


def collect() -> tuple[list[dict], str]:
    try:
        import psutil  # noqa: F401

        return collect_psutil(), "psutil"
    except ImportError:
        return collect_cim(), "cim"


# ---------------------------------------------------------------------------------------------------------------
# pure analysis
# ---------------------------------------------------------------------------------------------------------------
def _key(p: dict) -> tuple[int, int]:
    return int(p["pid"]), int(round(float(p.get("ct") or 0)))


def _children_map(procs: list[dict]) -> dict[int, list[dict]]:
    """Real children only: the parent must be older than the child (pid reuse guard)."""
    by_pid = {p["pid"]: p for p in procs}
    kids: dict[int, list[dict]] = {}
    for p in procs:
        parent = by_pid.get(p.get("ppid"))
        if parent is None or parent["pid"] == p["pid"] or (parent.get("ct") or 0) > (p.get("ct") or 0) + 1.0:
            continue
        kids.setdefault(parent["pid"], []).append(p)
    return kids


def _descendants(pid: int, kids: dict[int, list[dict]]) -> list[dict]:
    out, stack, seen = [], list(kids.get(pid, [])), {pid}
    while stack:
        c = stack.pop()
        if c["pid"] in seen:
            continue
        seen.add(c["pid"])
        out.append(c)
        stack.extend(kids.get(c["pid"], []))
    return out


def _ancestors(p: dict, by_pid: dict[int, dict]) -> list[dict]:
    out, cur, hops = [], p, 0
    while hops < 64:
        parent = by_pid.get(cur.get("ppid"))
        if parent is None or parent["pid"] == cur["pid"] or (parent.get("ct") or 0) > (cur.get("ct") or 0) + 1.0:
            break
        out.append(parent)
        cur, hops = parent, hops + 1
    return out


def fmt_age(seconds: float) -> str:
    seconds = max(0, int(seconds))
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f"{d} д {h} ч"
    if h:
        return f"{h} ч {m} мин"
    return f"{m} мин"


def close_command(pid: int) -> str:
    return f"taskkill /PID {pid} /T"


def analyze(first: list[dict], second: list[dict], window_s: float, now: float, *, me: int | None = None,
            min_age_h: float = DEFAULTS["min_age_h"], cpu_min_s: float = DEFAULTS["cpu_min_s"],
            child_window_h: float = DEFAULTS["child_window_h"], home: str | None = None) -> dict:
    """Two samples of the process table -> {'agents', 'hung', 'policy', 'counts'}. Pure, no I/O."""
    by_pid = {p["pid"]: p for p in second}
    kids = _children_map(second)
    cpu_before = {_key(p): p.get("cpu") for p in first}
    me = os.getpid() if me is None else me

    service_roots = {p["pid"] for p in second if is_bossman_service(p.get("name", ""), p.get("cmd", ""))}
    agents, hung, policy = [], [], []
    skipped_service = 0
    for p in second:
        kind = agent_kind(p.get("name", ""), p.get("cmd", ""))
        if kind is None or p["pid"] == me:
            continue
        anc = _ancestors(p, by_pid)
        if p["pid"] in service_roots or any(a["pid"] in service_roots for a in anc):
            skipped_service += 1
            continue
        agent_anc = [a for a in anc if agent_kind(a.get("name", ""), a.get("cmd", ""))]
        nested = bool(agent_anc)
        age = now - float(p.get("ct") or now)
        desc = _descendants(p["pid"], kids)
        tree = [p] + desc
        # CPU growth of the whole tree; a process new in the window counts with all its CPU
        known, delta = True, 0.0
        for q in tree:
            cur = q.get("cpu")
            before = cpu_before.get(_key(q), 0.0 if (q.get("ct") or 0) >= now - window_s - 1 else None)
            if cur is None or before is None:
                if q is p:
                    known = False
                continue
            delta += max(0.0, float(cur) - float(before))
        newest_child = max((float(q.get("ct") or 0) for q in desc), default=None)
        recent_child = newest_child is not None and newest_child > now - child_window_h * 3600
        cmd_short = redact(p.get("cmd", "") or p.get("name", ""), home=home)
        rec = {"pid": p["pid"], "create_time": round(float(p.get("ct") or 0), 3), "name": p.get("name", ""),
               "kind": kind, "role": agent_role(kind, p.get("cmd", "")), "age_s": int(age), "age": fmt_age(age),
               "cpu_delta_s": round(delta, 3) if known else None, "window_s": window_s,
               "descendants": len(desc), "recent_child": recent_child, "nested": nested, "cmd": cmd_short,
               "close_cmd": close_command(p["pid"])}
        agents.append(rec)
        m = BYPASS.search(p.get("cmd", "") or "")
        # a wrapper (node codex.js -> codex.exe) repeats its parent's flag: report the outermost one only
        if m and not (agent_anc and BYPASS.search(agent_anc[0].get("cmd", "") or "")):
            policy.append({**rec, "flag": m.group(0)})
        if nested:
            continue                                   # judged together with its agent parent (tree CPU)
        if age > min_age_h * 3600 and known and delta < cpu_min_s and not recent_child:
            hung.append(rec)
    agents.sort(key=lambda r: -r["age_s"])
    hung.sort(key=lambda r: -r["age_s"])
    policy.sort(key=lambda r: -r["age_s"])
    return {"agents": agents, "hung": hung, "policy": policy,
            "counts": {"processes": len(second), "agents": len(agents), "hung": len(hung), "policy": len(policy),
                       "skipped_service": skipped_service}}


# ---------------------------------------------------------------------------------------------------------------
# de-dup state (pid + create time, so a reused pid is a new finding)
# ---------------------------------------------------------------------------------------------------------------
def state_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "Bossman" / "agent-watchdog" / STATE_NAME


def finding_key(list_name: str, rec: dict) -> str:
    return f"{list_name}:{rec['pid']}:{int(round(rec.get('create_time') or 0))}"


def load_state(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and isinstance(data.get("reported"), dict) else {"reported": {}}
    except (OSError, ValueError):
        return {"reported": {}}


def select_new(report: dict, state: dict, now: float, dedup_h: float = DEFAULTS["dedup_h"]) -> dict:
    """Findings not reported within ``dedup_h``: {'hung': [...], 'policy': [...]}"""
    seen = state.get("reported", {})
    out = {}
    for name in ("hung", "policy"):
        out[name] = [r for r in report.get(name, [])
                     if now - float(seen.get(finding_key(name, r), 0) or 0) >= dedup_h * 3600]
    return out


def mark_reported(state: dict, new: dict, now: float, keep_h: float = 48.0) -> dict:
    reported = {k: v for k, v in state.get("reported", {}).items() if now - float(v or 0) < keep_h * 3600}
    for name in ("hung", "policy"):
        for r in new.get(name, []):
            reported[finding_key(name, r)] = now
    return {"schema": SCHEMA, "reported": reported}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------------------------------------------
# Russian pult text
# ---------------------------------------------------------------------------------------------------------------
def _cpu_txt(r: dict) -> str:
    return "CPU ?" if r.get("cpu_delta_s") is None else f"CPU +{r['cpu_delta_s']:.1f} с за {int(r['window_s'])} с"


def pult_text(findings: dict, counts: dict, now: float, *, min_age_h: float, cpu_min_s: float,
              child_window_h: float) -> str:
    stamp = datetime.fromtimestamp(now).strftime("%d.%m %H:%M")
    lines = [f"Сторож агентов {stamp} — только отчёт, ничего не закрыто и не изменено."]
    hung, policy = findings.get("hung", []), findings.get("policy", [])
    if not hung and not policy:
        lines.append("Новых подозрений нет.")
    if hung:
        lines.append(f"Похоже на зависание: {len(hung)} (возраст > {min_age_h:g} ч, CPU < {cpu_min_s:g} с за окно, "
                     f"новых дочерних процессов нет {child_window_h:g} ч)")
        for i, r in enumerate(hung, 1):
            lines.append(f"{i}) {r['kind']} {r['role']} · pid {r['pid']} · {r['age']} · {_cpu_txt(r)}")
            lines.append(f"   {r['cmd']}")
            if r["role"] == "app-server" or r["kind"] == "antigravity":
                lines.append("   (может быть просто открытый редактор/приложение — проверьте перед закрытием)")
            elif r["role"] == "interactive":
                lines.append("   (интерактивная сессия может просто ждать ввода — проверьте окно перед закрытием)")
            lines.append(f"   закрыть вручную: {r['close_cmd']}")
    if policy:
        lines.append(f"Политика — запуск с обходом разрешений: {len(policy)}")
        for i, r in enumerate(policy, 1):
            lines.append(f"{i}) {r['kind']} · pid {r['pid']} · {r['age']} · флаг {r['flag']}")
            lines.append(f"   {r['cmd']}")
            lines.append(f"   закрыть вручную (если не ваш запуск): {r['close_cmd']}")
    lines.append(f"Всего агентов: {counts.get('agents', 0)}, служб Bossman пропущено: {counts.get('skipped_service', 0)}. "
                 "Команды выполняются только вами.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------------------------------------------
def run(*, sample_s: float, min_age_h: float, cpu_min_s: float, child_window_h: float, dedup_h: float,
        send: bool, state_file: Path, collector: Callable[[], tuple[list[dict], str]] = collect,
        sleeper: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.time,
        sender: Callable[[str], Any] | None = None) -> dict:
    first, source = collector()
    t0 = clock()
    sleeper(sample_s)
    second, _ = collector()
    now = clock()
    window = max(sample_s, now - t0)
    rep = analyze(first, second, window, now, min_age_h=min_age_h, cpu_min_s=cpu_min_s,
                  child_window_h=child_window_h)
    state = load_state(state_file)
    new = select_new(rep, state, now, dedup_h)
    text = pult_text(new, rep["counts"], now, min_age_h=min_age_h, cpu_min_s=cpu_min_s,
                     child_window_h=child_window_h)
    result: dict[str, Any] = {"schema": SCHEMA, "at": datetime.fromtimestamp(now).isoformat(timespec="seconds"),
                              "source": source, "window_s": round(window, 1),
                              "thresholds": {"min_age_h": min_age_h, "cpu_min_s": cpu_min_s,
                                             "child_window_h": child_window_h, "dedup_h": dedup_h},
                              **rep, "new": {k: [r["pid"] for r in v] for k, v in new.items()},
                              "pult_text": text, "sent": None}
    if send:
        if not new["hung"] and not new["policy"]:
            result["sent"] = {"skipped": "nothing new"}
        else:
            if sender is None:
                sys.path.insert(0, str(HERE))
                import pult_note  # noqa: PLC0415

                sender = pult_note.send
            res = sender(text)
            result["sent"] = res if isinstance(res, dict) else {"ok": True}
            save_state(state_file, mark_reported(state, new, now))
    return result


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Сторож зависших CLI агентов (только отчёт, ничего не закрывает)")
    ap.add_argument("--sample", type=float, default=DEFAULTS["sample_s"], help="окно замера CPU, с (по умолчанию 60)")
    ap.add_argument("--min-age-h", type=float, default=DEFAULTS["min_age_h"])
    ap.add_argument("--cpu-min", type=float, default=DEFAULTS["cpu_min_s"], help="порог роста CPU за окно, с")
    ap.add_argument("--child-window-h", type=float, default=DEFAULTS["child_window_h"])
    ap.add_argument("--dedup-h", type=float, default=DEFAULTS["dedup_h"])
    ap.add_argument("--state", type=Path, default=None, help="файл состояния de-dup")
    ap.add_argument("--send", action="store_true", help="отправить НОВЫЕ находки владельцу в пульт (pult_note.send)")
    ap.add_argument("--text", action="store_true", help="печатать только русский текст для пульта")
    a = ap.parse_args(argv)
    try:
        result = run(sample_s=a.sample, min_age_h=a.min_age_h, cpu_min_s=a.cpu_min, child_window_h=a.child_window_h,
                     dedup_h=a.dedup_h, send=a.send, state_file=a.state or state_path())
    except Exception as exc:  # noqa: BLE001 - report the failure, never a traceback with command lines
        print(f"ОТКАЗ: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(result["pult_text"] if a.text else json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
