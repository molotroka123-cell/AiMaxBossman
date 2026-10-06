"""Bossman 1.5 runtime-failure -> self-repair inbox.

This feature does not patch stable code. It captures code/harness-shaped failures
from ordinary tasks as redacted, deduplicated evidence for Bossman's own
self-improvement runner. Network, approvals, CAPTCHA and owner-input waits are
not code bugs and are deliberately excluded.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from fastapi import APIRouter, Request

from ..plugin_security import redact_text
from . import Feature

router = APIRouter(prefix="/v15/self-repair", tags=["v1.5"])

_REPAIR_PROC: subprocess.Popen | None = None


def _runner() -> Path | None:
    installed = Path(sys.executable).resolve().parents[1] / "app-support" / "bossman_15_self_improve.py"
    if installed.is_file():
        return installed
    checkout = Path(__file__).resolve().parents[3] / "tools" / "bossman_15_self_improve.py"
    return checkout if checkout.is_file() else None


def _owner_repo(svc) -> Path | None:
    state = Path(svc.settings.data_dir) / "v1.5" / "owner-run" / "state.json"
    try:
        body = json.loads(state.read_text(encoding="utf-8"))
        raw = str(body.get("repo") or "").strip()
    except (OSError, ValueError, TypeError):
        raw = ""
    if not raw:
        raw = os.environ.get("BOSSMAN_SELF_IMPROVE_REPO", "").strip()
    if not raw:
        return None
    repo = Path(raw).expanduser().resolve()
    return repo if (repo / ".git").exists() else None


def _pid_alive(pid: int) -> bool:
    try:
        import psutil
        return psutil.pid_exists(int(pid))
    except Exception:
        try:
            os.kill(int(pid), 0)
            return True
        except OSError:
            return False


def _latest_pending(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    latest = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("signature"):
            latest[str(row["signature"])] = row
    return [row for row in latest.values() if row.get("status") == "QUEUED"]

CODE_RX = re.compile(
    r"(Traceback|AssertionError|TypeError|AttributeError|KeyError|ImportError|ModuleNotFoundError|"
    r"FileNotFoundError|OSError|SyntaxError|pytest|FAILED\s+[^\n]+::|internal error)",
    re.I,
)
EXCLUDE_RX = re.compile(
    r"(network|rate.?limit|captcha|approval|owner.?input|waiting.?for.?owner|timeout|"
    r"quota|credential|login required|stopped|cancelled)",
    re.I,
)
PATH_RX = re.compile(r"(?<![\w.-])((?:command-center|bossman-core|tools|learning|tests)/[\w./-]+\.py)")
TEST_RX = re.compile(r"((?:command-center/|bossman-core/)?tests/[\w./-]+\.py(?:::[\w\[\].-]+)?)")


def _path(svc) -> Path:
    p = Path(svc.settings.data_dir) / "v1.5" / "self-repair" / "inbox.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def classify(error: str) -> dict | None:
    clean = redact_text(str(error or ""))[:6000]
    if not clean or EXCLUDE_RX.search(clean) or not CODE_RX.search(clean):
        return None
    paths = sorted(set(PATH_RX.findall(clean)))[:20]
    tests = sorted(set(TEST_RX.findall(clean)))[:20]
    signature = hashlib.sha256(re.sub(r"\d+", "#", clean).encode()).hexdigest()[:16]
    return {"signature": signature, "error": clean, "paths": paths, "tests": tests}


def _recent(path: Path, limit: int = 100) -> list[dict]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-max(1, limit):]:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


async def _tick(svc):
    """If a real code failure is queued, run one bounded repair worker automatically."""
    global _REPAIR_PROC
    base = Path(svc.settings.data_dir) / "v1.5" / "self-repair"
    inbox = base / "inbox.jsonl"
    pid_file = base / "worker.pid"

    if _REPAIR_PROC is not None:
        if _REPAIR_PROC.poll() is None:
            return
        await svc.bus.emit("v15.self_repair.worker_finished", returncode=_REPAIR_PROC.returncode)
        _REPAIR_PROC = None
        pid_file.unlink(missing_ok=True)

    try:
        stale_pid = int(pid_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        stale_pid = 0
    if stale_pid and _pid_alive(stale_pid):
        return
    if stale_pid:
        pid_file.unlink(missing_ok=True)

    pending = _latest_pending(inbox)
    if not pending:
        return
    repo = _owner_repo(svc)
    script = _runner()
    if repo is None or script is None:
        # Keep QUEUED. Owner-run/Telegram status can expose the concrete blocker.
        return

    work = base / "worker"
    work.mkdir(parents=True, exist_ok=True)
    log = (base / "worker.log").open("ab", buffering=0)
    argv = [
        sys.executable, "-I", str(script),
        "--repo", str(repo),
        "--data-dir", str(svc.settings.data_dir),
        "repair", "--work", str(work),
    ]
    try:
        _REPAIR_PROC = subprocess.Popen(
            argv, cwd=repo, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONUTF8": "1"},
            **({"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
               if sys.platform == "win32" else {"start_new_session": True}))
    except Exception as exc:
        await svc.bus.emit("v15.self_repair.worker_start_failed",
                           error=type(exc).__name__, queued=len(pending))
        return
    finally:
        log.close()
    pid_file.write_text(str(_REPAIR_PROC.pid), encoding="utf-8")
    await svc.bus.emit("v15.self_repair.worker_started",
                       pid=_REPAIR_PROC.pid, queued=len(pending), repo=str(repo))


async def _setup(svc):
    async def capture(task, run_id, error):
        finding = classify(error)
        if finding is None:
            return
        path = _path(svc)
        recent = _recent(path, 200)
        # Same signature is one repair candidate; occurrences remain observable.
        previous = next((r for r in reversed(recent) if r.get("signature") == finding["signature"]), None)
        row = {
            "schema": "bossman.v1.5.self-repair/1",
            "id": finding["signature"],
            "signature": finding["signature"],
            "status": "QUEUED",
            "task_id": task.get("id"),
            "run_id": run_id,
            "at": time.time(),
            "error": finding["error"],
            "paths": finding["paths"],
            "tests": finding["tests"],
            "occurrence": int((previous or {}).get("occurrence") or 0) + 1,
        }
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        await svc.bus.emit("v15.self_repair.queued", repair_id=row["id"],
                           task_id=task.get("id"), run_id=run_id,
                           paths=row["paths"], tests=row["tests"])
    svc.engine.add_hook("on_failure", capture, critical=False)


@router.get("/inbox")
async def inbox(request: Request, limit: int = 50):
    rows = _recent(_path(request.app.state.svc), min(max(int(limit), 1), 200))
    # Collapse repeats for the owner view.
    latest = {}
    for row in rows:
        latest[row.get("signature") or row.get("id")] = row
    return {"items": list(latest.values())[-limit:]}


FEATURE = Feature(name="v15_self_repair", router=router, setup=_setup,
                  tick=_tick, tick_seconds=5.0)
