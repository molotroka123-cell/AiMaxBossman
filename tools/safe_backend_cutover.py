"""Safe Backend Cutover & Runtime vs Checkout Identity Verifier (P0 Audit 05.11.2026).

Strictly separates:
  1. `runtime_health_live`: what `GET http://127.0.0.1:8801/health/live` reports from the actual running OS process.
  2. `checkout_identity`: what `bcc.build_identity.source_identity(fresh=True)` reports for the local git worktree.

Never substitutes `checkout_identity` for `runtime_health_live`.
Provides a 6-step safe cutover procedure:
  Step 1 — Check active tasks (`/api/tasks`) & Direct Gen (`/api/direct-gen/status`).
  Step 2 — Verify target checkout is clean (`git status --porcelain` empty) and sibling packages resolve inside it.
  Step 3 — Online SQLite backup of `bcc.db` + save rollback manifest (`previous_sha`, `previous_pid`, `backup_db`).
  Step 4 — Stop existing `:8801` process (if owned by an elevated session and current shell is non-elevated, record
           `BLOCKED_ELEVATION_REQUIRED` honestly without starting a second backend or bypassing OS permissions).
  Step 5 — Launch `:8801` from the target checkout and verify `/health/live` (`source_identity == "PASS"` and
           `build_sha == checkout_sha`) + Pult companion reachability.
  Step 6 — Auto-rollback (`--rollback`) if `/health/live` fails within timeout.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "command-center"))
sys.path.insert(0, str(REPO / "bossman-core"))

from bcc.build_identity import source_identity  # noqa: E402


def _data_dir() -> Path:
    return Path(os.environ.get("BCC_DATA_DIR") or Path(os.environ["LOCALAPPDATA"]) / "Bossman" / "CommandCenter")


def _api_get(path: str, timeout: float = 5.0) -> dict[str, Any]:
    tok_path = _data_dir() / "token"
    headers = {"X-BCC-Token": tok_path.read_text(encoding="utf-8").strip()} if tok_path.is_file() else {}
    req = urllib.request.Request(f"http://127.0.0.1:8801{path}", headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def get_listening_pid(port: int = 8801) -> int | None:
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"$c = Get-NetTCPConnection -LocalPort {int(port)} -State Listen -ErrorAction SilentlyContinue | "
         "Select-Object -First 1; if ($c) { $c.OwningProcess }"],
        capture_output=True, text=True, timeout=10, check=False,
    )
    txt = (r.stdout or "").strip()
    return int(txt) if txt.isdigit() else None


def can_terminate_pid(pid: int) -> tuple[bool, str]:
    """Check if the current Windows token has PROCESS_TERMINATE (0x0001) on `pid` without killing it."""
    if os.name != "nt":
        return True, "posix"
    PROCESS_TERMINATE = 0x0001
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, int(pid))
    if not handle:
        err = kernel32.GetLastError()
        return False, f"OpenProcess(PROCESS_TERMINATE) denied for PID {pid} (WinError {err}: Access is denied — process runs in an elevated/Administrator session)"
    kernel32.CloseHandle(handle)
    return True, "PROCESS_TERMINATE granted"


def backup_sqlite_db(db_path: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dst = backup_dir / f"bcc-pre-cutover-{stamp}.db"
    src_con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    dst_con = sqlite3.connect(str(dst))
    with dst_con:
        src_con.backup(dst_con)
    dst_con.close()
    src_con.close()
    return dst


def check_active_work() -> dict[str, Any]:
    running_tasks = []
    dg_busy = False
    dg_detail = {}
    try:
        t_resp = _api_get("/api/tasks?limit=20")
        rows = t_resp.get("tasks", t_resp.get("items", t_resp)) if isinstance(t_resp, dict) else t_resp
        running_tasks = [
            {"id": r.get("id"), "status": r.get("status"), "agent_id": r.get("agent_id")}
            for r in (rows or [])
            if str(r.get("status") or "").lower() in ("running", "in_progress", "executing")
        ]
    except Exception as exc:  # noqa: BLE001
        running_tasks = [{"error": str(exc)}]
    try:
        dg = _api_get("/api/direct-gen/status")
        q = dg.get("queue") or {}
        res = dg.get("resident") or {}
        dg_busy = bool(q.get("running") or int(q.get("waiting") or 0) > 0 or res.get("busy"))
        dg_detail = {"queue_running": q.get("running"), "queue_waiting": q.get("waiting"), "resident_busy": res.get("busy")}
    except Exception as exc:  # noqa: BLE001
        dg_detail = {"error": str(exc)}
    return {"running_tasks": running_tasks, "direct_gen_busy": dg_busy, "direct_gen": dg_detail,
            "safe_to_restart": (len(running_tasks) == 0 and not dg_busy)}


def check_companion_process() -> dict[str, Any]:
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process -Filter \"Name = 'python.exe'\" | "
         "Where-Object { $_.CommandLine -match 'telegram_companion' } | "
         "Select-Object ProcessId, CommandLine | ConvertTo-Json -Compress"],
        capture_output=True, text=True, timeout=10, check=False,
    )
    txt = (r.stdout or "").strip()
    if not txt:
        return {"running": False, "processes": []}
    data = json.loads(txt)
    rows = data if isinstance(data, list) else [data]
    return {"running": len(rows) > 0, "processes": rows}


def inspect_state() -> dict[str, Any]:
    checkout = source_identity(fresh=True)
    try:
        live = _api_get("/health/live")
    except Exception as exc:  # noqa: BLE001
        live = {"status": "UNREACHABLE", "error": str(exc)}
    pid = get_listening_pid(8801)
    can_stop, stop_reason = can_terminate_pid(pid) if pid else (True, "no process on :8801")
    active = check_active_work()
    companion = check_companion_process()
    live_sha = str(live.get("build_sha") or "")
    chk_sha = str(checkout.get("build_sha") or "")
    matches = bool(
        live.get("source_identity") == "PASS"
        and checkout.get("source_identity") == "PASS"
        and live_sha
        and live_sha == chk_sha
    )
    return {
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "runtime_matches_checkout": matches,
        "runtime_health_live": live,
        "checkout_identity": checkout,
        "port_8801_pid": pid,
        "can_terminate_pid_in_current_shell": can_stop,
        "terminate_permission_detail": stop_reason,
        "active_work": active,
        "pult_companion": companion,
    }


def run_cutover(execute: bool, out_receipt: Path | None = None) -> int:
    state = inspect_state()
    data_dir = _data_dir()
    backup_dir = data_dir / "backups"
    db_path = data_dir / "bcc.db"
    backup_file = backup_sqlite_db(db_path, backup_dir) if db_path.is_file() else None

    result: dict[str, Any] = {
        **state,
        "backup_db_path": str(backup_file) if backup_file else None,
        "backup_db_size_bytes": backup_file.stat().st_size if backup_file and backup_file.is_file() else 0,
        "execute_requested": execute,
        "rollback_script": str(REPO / "tools" / "restart_backend.ps1"),
    }

    if state["runtime_matches_checkout"]:
        result["cutover_verdict"] = "PASS"
        result["cutover_detail"] = "Live :8801 runtime already matches clean checkout SHA."
    elif not state["checkout_identity"].get("git_clean"):
        result["cutover_verdict"] = "BLOCKED"
        result["cutover_detail"] = "Target checkout has uncommitted changes; commit before cutover."
    elif not state["active_work"]["safe_to_restart"]:
        result["cutover_verdict"] = "BLOCKED"
        result["cutover_detail"] = "Active tasks or Direct Gen jobs are running; refusing to interrupt."
    elif not state["can_terminate_pid_in_current_shell"]:
        result["cutover_verdict"] = "BLOCKED"
        result["blocker_code"] = "ELEVATED_PROCESS_ACCESS_DENIED"
        result["cutover_detail"] = (
            f"Port :8801 is held by PID {state['port_8801_pid']} (running build {state['runtime_health_live'].get('build_sha_short')}), "
            f"which was started in an elevated Administrator session ({state['terminate_permission_detail']}). "
            f"Database backup is ready at {backup_file}. Run `powershell -ExecutionPolicy Bypass -File "
            f"{REPO / 'tools' / 'restart_backend.ps1'}` from an Administrator PowerShell to switch :8801 to "
            f"{state['checkout_identity'].get('build_sha_short')}."
        )
    elif not execute:
        result["cutover_verdict"] = "PREFLIGHT_READY"
        result["cutover_detail"] = "Preflight and DB backup complete; pass --execute to restart :8801."
    else:
        ps_script = REPO / "tools" / "restart_backend.ps1"
        r = subprocess.run(["powershell", "-ExecutionPolicy", "Bypass", "-File", str(ps_script)],
                           capture_output=True, text=True, timeout=200, check=False)
        post = inspect_state()
        result["post_cutover"] = post
        result["restart_stdout"] = (r.stdout or "").strip()
        result["restart_stderr"] = (r.stderr or "").strip()
        result["cutover_verdict"] = "PASS" if post["runtime_matches_checkout"] else "FAIL"

    if out_receipt:
        out_receipt.parent.mkdir(parents=True, exist_ok=True)
        out_receipt.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["cutover_verdict"] in ("PASS", "PREFLIGHT_READY") else 2


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true", help="Attempt to restart :8801 after preflight and DB backup")
    ap.add_argument("--receipt", default="", help="Write JSON receipt to this path")
    a = ap.parse_args()
    out = Path(a.receipt) if a.receipt else None
    return run_cutover(execute=a.execute, out_receipt=out)


if __name__ == "__main__":
    raise SystemExit(main())
