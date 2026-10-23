"""Owner activity marker: the owner's live requests always beat background work on the local models.

Incident 23.10: ``tools/context_probe.py`` held the local ollama with a ~127k-token prompt for ~10 minutes; the
owner's chat turn from the mobile pult to the same local model queued behind it and the phone showed «нет связи».

One short-lived file is the signal, written by the backend and read by any background job (no backend round-trip,
works while the backend itself is busy):

    <data>/runtime/owner_active.json   {"schema", "source", "at", "expires_at", "pid"}

* Writers: ``features/chat_threads`` on every owner chat turn (``POST /api/chat/threads/{id}/send`` - the web chat
  and the mobile pult both come through it) and, while that turn's task is still running, a keeper refreshes it.
  ``expires_at`` only moves forward (a short refresh never shortens a longer window).
* Readers: background consumers of the local models call :func:`active` / :func:`background_gate` before a cycle
  and :func:`wait_until_idle` when told to wait. A stale or unreadable marker means "idle" (fail-open for the
  background is wrong only in the sense that it may run; a broken marker must never freeze the owner's PC work).

Stdlib only: imported by the backend and by standalone tools (``sys.path`` = ``<repo>/command-center``). The marker
holds no chat content - only the surface name and timestamps (owner rule 21.10: debug by ids/statuses only).
"""
from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path
from typing import Any, Callable

SCHEMA = "bossman.owner-activity/1"
FILE_NAME = "owner_active.json"
#: how long one owner signal keeps background local-model work away (seconds)
DEFAULT_TTL_S = 600.0
TTL_ENV = "BOSSMAN_OWNER_ACTIVE_TTL_S"
#: exit code of a background tool that refused to start / was preempted because the owner is active
EXIT_OWNER_ACTIVE = 3


def ttl_s() -> float:
    try:
        value = float(os.environ.get(TTL_ENV, "") or DEFAULT_TTL_S)
    except ValueError:
        return DEFAULT_TTL_S
    return value if value > 0 else DEFAULT_TTL_S


def default_data_dir() -> Path:
    """Same resolution as the tools and the resource governor: ``BCC_DATA_DIR``, else the owner's working data
    dir ``%LOCALAPPDATA%\\Bossman\\CommandCenter``."""
    configured = os.environ.get("BCC_DATA_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / "Bossman" / "CommandCenter"


def marker_path(data_dir: Path | str | None = None) -> Path:
    return Path(data_dir if data_dir is not None else default_data_dir()) / "runtime" / FILE_NAME


def _read(path: Path) -> dict | None:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _write_atomic(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.stem}.{secrets.token_hex(4)}.tmp")
    try:
        tmp.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:          # Windows: a reader holds the file for a moment
                if attempt == 4:
                    raise
                time.sleep(0.02 * (attempt + 1))
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def mark(source: str, *, data_dir: Path | str | None = None, ttl: float | None = None,
         now: float | None = None) -> dict:
    """The owner is active right now: background local-model work must yield until ``expires_at``."""
    now = time.time() if now is None else float(now)
    path = marker_path(data_dir)
    expires = now + (ttl_s() if ttl is None else float(ttl))
    old = _read(path)
    if old is not None:
        try:
            expires = max(expires, float(old.get("expires_at") or 0.0))
        except (TypeError, ValueError):
            pass
    doc = {"schema": SCHEMA, "source": str(source)[:40], "at": now, "expires_at": expires, "pid": os.getpid()}
    _write_atomic(path, doc)
    return doc


def active(*, data_dir: Path | str | None = None, now: float | None = None) -> dict | None:
    """The marker when the owner is active (not expired), else ``None``."""
    doc = _read(marker_path(data_dir))
    if doc is None:
        return None
    try:
        expires = float(doc.get("expires_at") or 0.0)
    except (TypeError, ValueError):
        return None
    now = time.time() if now is None else float(now)
    return doc if expires > now else None


def describe(doc: dict | None, *, now: float | None = None) -> str:
    if not doc:
        return "owner idle"
    now = time.time() if now is None else float(now)
    left = max(0, int(float(doc.get("expires_at") or 0.0) - now))
    return f"owner active ({doc.get('source') or 'unknown'}), background local-model work waits ~{left} s"


def background_gate(*, data_dir: Path | str | None = None, now: float | None = None) -> str:
    """'' when a background job may use the local models now, else the reason it must skip/wait."""
    doc = active(data_dir=data_dir, now=now)
    return f"owner_active:{doc.get('source') or 'unknown'}" if doc else ""


def wait_until_idle(*, data_dir: Path | str | None = None, poll_s: float = 15.0, max_wait_s: float = 0.0,
                    sleep: Callable[[float], Any] = time.sleep, clock: Callable[[], float] = time.time,
                    log: Callable[[str], Any] | None = None) -> bool:
    """Block while the owner is active. True when idle; False when ``max_wait_s`` (> 0) ran out first."""
    started = clock()
    said = False
    while True:
        doc = active(data_dir=data_dir, now=clock())
        if doc is None:
            return True
        if log and not said:
            log(describe(doc, now=clock()))
            said = True
        if max_wait_s > 0 and clock() - started >= max_wait_s:
            return False
        sleep(poll_s)
