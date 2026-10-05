"""``pit doctor`` identity block: who am I, who is the backend, who starts us.

Failure this prevents (RC19): doctor once ran from a different build than the
one the scheduler starts, and Jeff's config pointed at a foreign backend on
:8800. Every mismatch here is a BLOCKED line with a remedy, never a silent PASS.
No secrets and no message text are read or printed.
"""
from __future__ import annotations

import os
import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Callable

from .version import JEFF_VERSION

TASK_NAMES = ("BossmanOne-1-Backend", "BossmanOne-2-Jeff", "BossmanOne-3-Companion")
_TASK_NS = "{http://schemas.microsoft.com/windows/2004/02/mit/task}"
_SHA12 = re.compile(r"[0-9a-f]{12}")
BACKEND_REMEDY = ("restart the backend from the same install as Jeff "
                  "(Stop-ScheduledTask/Start-ScheduledTask BossmanOne-1-Backend)")


def _row(name: str, status: str, detail: str = "", remedy: str = "") -> dict[str, Any]:
    row: dict[str, Any] = {"check": name, "status": status, "detail": detail[:300]}
    if remedy:
        row["remedy"] = remedy
    return row


def fetch_backend_identity(core_url: str, timeout: float = 3.0) -> dict[str, Any] | None:
    """Unauthenticated ``/api/identity`` of whatever listens on ``core_url``."""
    import httpx
    try:
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            response = client.get(core_url.rstrip("/") + "/api/identity")
        data = response.json()
    except Exception:  # noqa: BLE001 - unreachable and non-JSON are both "no identity"
        return None
    return data if isinstance(data, dict) else None


def _decode(raw: bytes) -> str:
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff") or b"\x00" in raw[:4]:
        return raw.decode("utf-16", errors="replace")
    return raw.decode("utf-8", errors="replace")


def parse_task_xml(text: str) -> dict[str, str]:
    """Command, arguments and working dir of a scheduled task's first Exec action."""
    body = re.sub(r"^\s*<\?xml[^>]*\?>", "", text.lstrip("﻿"))
    root = ET.fromstring(body)
    node = root.find(f".//{_TASK_NS}Exec")
    if node is None:
        node = root.find(".//Exec")
    out = {"command": "", "arguments": "", "workdir": ""}
    if node is None:
        return out
    for key, tag in (("command", "Command"), ("arguments", "Arguments"), ("workdir", "WorkingDirectory")):
        child = node.find(f"{_TASK_NS}{tag}")
        if child is None:
            child = node.find(tag)
        out[key] = (child.text or "").strip() if child is not None else ""
    return out


def read_scheduled_tasks(names: tuple[str, ...] = TASK_NAMES) -> dict[str, dict[str, str] | None] | None:
    """Windows Task Scheduler view; ``None`` where there is no scheduler."""
    if os.name != "nt":
        return None
    found: dict[str, dict[str, str] | None] = {}
    for name in names:
        try:
            done = subprocess.run(["schtasks", "/Query", "/TN", name, "/XML"],
                                  capture_output=True, timeout=15, check=False)
            found[name] = parse_task_xml(_decode(done.stdout)) if done.returncode == 0 else None
        except (OSError, subprocess.SubprocessError, ET.ParseError):
            found[name] = None
    return found


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(path.strip('"'))) if path else ""


def install_home(task: dict[str, str]) -> str:
    """The install a task starts: its working dir, else two levels above the exe."""
    if task.get("workdir"):
        return _norm(task["workdir"])
    command = task.get("command", "").strip('"')
    return _norm(str(Path(command).parent.parent)) if command else ""


def scheduler_checks(own_sha: str | None, tasks: dict[str, dict[str, str] | None] | None,
                     own_code_dir: str) -> list[dict[str, Any]]:
    if tasks is None:
        return [_row("scheduled_tasks", "SKIP", "no Windows Task Scheduler on this host")]
    rows: list[dict[str, Any]] = []
    missing = [name for name in TASK_NAMES if not tasks.get(name)]
    if missing:
        rows.append(_row("scheduled_tasks", "FAIL", "missing: " + ", ".join(missing),
                         "run tools/owner_one_bossman.ps1 to register BossmanOne-1/2/3"))
        return rows
    homes = {name: install_home(tasks[name] or {}) for name in TASK_NAMES}
    if len(set(homes.values())) != 1:
        rows.append(_row("scheduled_tasks", "BLOCKED",
                         "tasks start different installs: " + "; ".join(f"{k}={v}" for k, v in homes.items()),
                         "re-run tools/owner_one_bossman.ps1 so all three tasks use one install"))
        return rows
    home = next(iter(homes.values()))
    rows.append(_row("scheduled_tasks", "PASS", f"BossmanOne-1/2/3 -> {home}"))
    match = _SHA12.findall(Path(home).name.lower()) if home else []
    if own_sha and match and not own_sha.lower().startswith(match[-1]):
        rows.append(_row("scheduler_build", "BLOCKED",
                         f"tasks start build {match[-1]}, doctor runs {own_sha[:12]}",
                         "run doctor from the scheduler's install or re-run tools/owner_one_bossman.ps1"))
    elif match and own_sha:
        rows.append(_row("scheduler_build", "PASS", f"scheduler build {match[-1]} = doctor build"))
    elif home and not _norm(own_code_dir).startswith(home):
        rows.append(_row("scheduler_build", "BLOCKED",
                         f"doctor code {own_code_dir} is outside the scheduler install {home}",
                         "run doctor from the scheduler's install or re-run tools/owner_one_bossman.ps1"))
    else:
        rows.append(_row("scheduler_build", "PASS", "doctor runs from the scheduler's install"))
    return rows


def identity_checks(settings: Any, home: Path, data_dir: Path, *,
                    own: dict[str, Any] | None = None,
                    fetch_identity: Callable[[str], dict[str, Any] | None] = fetch_backend_identity,
                    read_tasks: Callable[[], dict[str, dict[str, str] | None] | None] = read_scheduled_tasks,
                    process_count: Callable[[], int] | None = None,
                    heartbeat_read: Callable[[Path], dict[str, Any] | None] | None = None,
                    code_dir: str | None = None) -> list[dict[str, Any]]:
    from bcc.build_identity import UNKNOWN, data_dir_fingerprint, source_identity

    from . import heartbeat as pit_heartbeat

    own = own if own is not None else source_identity(fresh=True)
    process_count = process_count or pit_heartbeat.jeff_process_count
    heartbeat_read = heartbeat_read or pit_heartbeat.read
    code_dir = code_dir or str(Path(__file__).resolve().parents[1])
    rows: list[dict[str, Any]] = []

    own_sha = own.get("build_sha")
    if own_sha:
        rows.append(_row("own_build", "PASS", f"Jeff {JEFF_VERSION} build {own_sha[:12]}"))
    else:
        state = own.get("source_identity") or UNKNOWN
        rows.append(_row("own_build", "BLOCKED", f"Jeff {JEFF_VERSION} build {state}",
                         "run doctor from an installed build or a clean checkout"))

    rows.append(_row("data_path", "PASS", f"{data_dir}"))

    if getattr(settings, "web_only", False) and not getattr(settings, "core_url", ""):
        rows.append(_row("backend_identity", "SKIP", "no backend configured"))
    else:
        theirs = fetch_identity(settings.core_url)
        if theirs is None:
            rows.append(_row("backend_identity", "FAIL", f"no identity from {settings.core_url}",
                             "start the backend (BossmanOne-1-Backend) or fix core_url"))
        else:
            b_sha = theirs.get("build_sha")
            b_fp = theirs.get("data_dir_fingerprint")
            problems = []
            if not b_sha or not own_sha:
                problems.append(f"build unproven (jeff={own_sha and own_sha[:12]}, backend={b_sha and b_sha[:12]})")
            elif b_sha != own_sha:
                problems.append(f"build mismatch: jeff {own_sha[:12]} != backend {b_sha[:12]}")
            if b_fp and b_fp != data_dir_fingerprint(data_dir):
                problems.append("backend uses a different data directory than Jeff")
            elif not b_fp:
                problems.append("backend did not report its data directory")
            detail = (f"backend {str(b_sha)[:12]} at {settings.core_url}"
                      + ("" if not problems else "; " + "; ".join(problems)))
            rows.append(_row("backend_identity", "BLOCKED" if problems else "PASS", detail,
                             BACKEND_REMEDY if problems else ""))

    count = process_count()
    if count < 0:
        rows.append(_row("pollers", "SKIP", "psutil missing: cannot count Jeff pollers"))
    elif count > 1:
        rows.append(_row("pollers", "BLOCKED", f"{count} Jeff pollers running",
                         "bossman pit stop, end the extra processes, start once"))
    else:
        rows.append(_row("pollers", "PASS", f"{count} Jeff poller(s)"))

    beat = heartbeat_read(home)
    if beat and beat.get("availability") == "up":
        running = beat.get("build_sha")
        if running and own_sha and running != own_sha:
            rows.append(_row("running_jeff_build", "BLOCKED",
                             f"running Jeff is {str(running)[:12]}, doctor is {own_sha[:12]}",
                             "restart Jeff from the scheduler's install"))
        else:
            rows.append(_row("running_jeff_build", "PASS", f"{str(running or '?')[:12]}"))
        tts, stt = beat.get("tts") or {}, beat.get("stt") or {}
        rows.append(_row("voice", "PASS" if tts.get("available") else "SKIP",
                         f"tts={tts.get('engine', '?')}:{'ok' if tts.get('available') else 'off'} "
                         f"stt={stt.get('engine', '?')}:{'ok' if stt.get('available') else 'off'}"))
    else:
        rows.append(_row("running_jeff_build", "SKIP", "Jeff is not running (no live heartbeat)"))
        rows.append(_row("voice", "SKIP", "Jeff is not running"))

    mode = "LOCAL_ONLY_TEST" if getattr(settings, "local_chat_only", False) else "CLOUD_FREE"
    local = ",".join(getattr(settings, "local_models", ()) or ()) or "none"
    rows.append(_row("chat_route", "PASS", f"{mode}; local={local}"))

    rows.extend(scheduler_checks(own_sha, read_tasks(), code_dir))
    return rows
