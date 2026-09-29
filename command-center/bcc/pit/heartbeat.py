"""Jeff heartbeat: availability, latency, transport and engine choice, secret-free.

One small file per Jeff surface (``pit-v1.7/heartbeat.json`` for the Telegram
process, ``pit-v1.7/web/heartbeat.json`` for the Jeff window) written by the
running Jeff itself and read by the EXISTING status paths (``bossman pit
status``, ``/api/jeff/health``, ``/api/jeff-settings/status``). No new service,
no message text, no IDs, no keys.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from collections import deque
from pathlib import Path
from typing import Any

FILE_NAME = "heartbeat.json"
INTERVAL_SECONDS = 15
STALE_AFTER_SECONDS = 60
SCHEMA = "bossman.pit.heartbeat/1"


def _iso(ts: float | None) -> str | None:
    return None if not ts else time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


class Heartbeat:
    """In-process collector; ``snapshot`` is what gets written / served."""

    def __init__(self, home: Path, surface: str):
        self.home = Path(home)
        self.surface = surface
        self.started = time.time()
        self.replies_ok = 0
        self.replies_failed = 0
        self.last_reply_at: float | None = None
        self.last_latency_ms: int | None = None
        self._latencies: deque[int] = deque(maxlen=20)
        self.llm: dict[str, str] = {}
        self.last_error: dict[str, Any] | None = None
        self.poll_ok_at: float | None = None
        self.poll_error: str = ""
        self.state = "starting"

    @property
    def path(self) -> Path:
        return self.home / FILE_NAME

    def note_route(self, *, model: str, provider: str, ok: bool, latency_ms: int, error: str = "") -> None:
        now = time.time()
        if ok:
            self.replies_ok += 1
            self.last_reply_at = now
            self.last_latency_ms = int(latency_ms)
            self._latencies.append(int(latency_ms))
            self.llm = {"model": str(model)[:80], "provider": "local" if provider == "local" else "cloud_free"}
        else:
            self.replies_failed += 1
            self.last_error = {"at": _iso(now), "kind": str(error or "route_failed")[:80]}

    def note_poll(self, ok: bool, error: str = "") -> None:
        if ok:
            self.poll_ok_at = time.time()
            self.poll_error = ""
            self.state = "running"
        else:
            self.poll_error = str(error)[:80]
            self.last_error = {"at": _iso(time.time()), "kind": "telegram:" + self.poll_error}

    def snapshot(self, *, queue: int = 0, stt: dict | None = None, tts: dict | None = None,
                 state: str | None = None) -> dict[str, Any]:
        now = time.time()
        avg = int(sum(self._latencies) / len(self._latencies)) if self._latencies else None
        return {
            "schema": SCHEMA, "surface": self.surface, "pid": os.getpid(),
            "state": state or self.state, "at": _iso(now), "at_epoch": int(now),
            "started_at": _iso(self.started), "uptime_s": int(now - self.started),
            "replies_ok": self.replies_ok, "replies_failed": self.replies_failed,
            "last_reply_at": _iso(self.last_reply_at),
            "last_reply_latency_ms": self.last_latency_ms, "avg_latency_ms": avg,
            "llm": dict(self.llm),
            "stt": {"available": bool((stt or {}).get("available")), "engine": (stt or {}).get("engine", "local")},
            "tts": {"available": bool((tts or {}).get("available")), "engine": (tts or {}).get("engine", "local")},
            "telegram": {"last_poll_ok_at": _iso(self.poll_ok_at), "error": self.poll_error},
            "queue": int(queue),
            "last_error": self.last_error,
        }

    def write(self, **kwargs: Any) -> dict[str, Any]:
        payload = self.snapshot(**kwargs)
        try:
            write_file(self.path, payload)
        except OSError:
            pass
        return payload


def write_file(path: Path, payload: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            handle.flush()
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def read(home: Path) -> dict[str, Any] | None:
    """The last heartbeat with a computed ``availability``: up / stale / stopped / absent."""
    path = Path(home) / FILE_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    age = max(0, int(time.time()) - int(data.get("at_epoch") or 0))
    data["age_s"] = age
    if data.get("state") == "stopped":
        data["availability"] = "stopped"
    else:
        data["availability"] = "up" if age <= STALE_AFTER_SECONDS else "stale"
    return data


def jeff_process_count() -> int:
    """Live processes running the Jeff Telegram poller (`... pit start`); must be 0 or 1."""
    try:
        import psutil
    except ImportError:
        return -1
    count = 0
    me = os.getpid()
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            tokens = [str(t).lower() for t in (proc.info.get("cmdline") or [])]
        except (psutil.Error, TypeError):
            continue
        if proc.info.get("pid") == me or "start" not in tokens:
            continue
        head = tokens[: tokens.index("start")]
        if head and head[-1] in {"pit", "bcc.pit", "bcc.pit.cli"}:
            count += 1
    return count
