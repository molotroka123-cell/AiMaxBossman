"""Jeff heartbeat: availability, latency, transport and engine choice, secret-free.

One small file per Jeff surface (``pit-v1.7/heartbeat.json`` for the Telegram
process, ``pit-v1.7/web/heartbeat.json`` for the Jeff window) written by the
running Jeff itself and read by the EXISTING status paths (``bossman pit
status``, ``/api/jeff/health``, ``/api/jeff-settings/status``). No new service,
no message text, no IDs, no keys.
"""
from __future__ import annotations

import contextlib
import json
import os
import random
import tempfile
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable

from .version import JEFF_VERSION

FILE_NAME = "heartbeat.json"
INTERVAL_SECONDS = 15
STALE_AFTER_SECONDS = 60
SCHEMA = "bossman.pit.heartbeat/1"


def _build_sha() -> str | None:
    try:
        from bcc.build_identity import source_identity
        return source_identity().get("build_sha")
    except Exception:  # noqa: BLE001 - a heartbeat never fails on identity
        return None


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
            "jeff_version": JEFF_VERSION, "build_sha": _build_sha(),
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


def _descendant_pids(pid: Any) -> set[int]:
    """Every live descendant of `pid` (empty without psutil or when the process is gone)."""
    try:
        import psutil
        return {child.pid for child in psutil.Process(int(pid)).children(recursive=True)}
    except Exception:  # noqa: BLE001 - ImportError, NoSuchProcess, AccessDenied, bad pid: "no descendants", never a crash
        return set()


# -- watchdog: the survivability half of the heartbeat -------------------------------
STOP_FLAG_NAME = "stop.flag"
BACKOFF_BASE_SECONDS = 2.0
BACKOFF_CAP_SECONDS = 300.0
WATCHDOG_DIR = "watchdog"
EXIT_FATAL = 2          # config / refusal: restarting cannot help
EXIT_LOCK_HELD = 3      # another poller owns the lock: never a crash, never a second poller


def backoff_delay(attempt: int, *, base: float = BACKOFF_BASE_SECONDS,
                  cap: float = BACKOFF_CAP_SECONDS, rng: Callable[[], float] = random.random) -> float:
    """Exponential backoff with equal jitter: in [ceiling/2, ceiling], ceiling capped."""
    ceiling = min(cap, base * (2 ** max(0, min(int(attempt), 30))))
    return ceiling / 2 + rng() * ceiling / 2


class Watchdog:
    """Keeps exactly one Jeff poller alive; reads the same heartbeat file Jeff writes.

    The poller is a child process. It is restarted after a crash or when its
    heartbeat goes stale, with exponential backoff and jitter. A second poller
    is never started while the kernel poller lock is held, and a stop request
    (``stop.flag`` or a clean exit) ends the watchdog and the child with it.
    Time, sleep, jitter and the child are injected so the policy is testable
    without wall-clock races.
    """

    def __init__(self, home: Path, spawn: Callable[[], Any], *,
                 is_running: Callable[[Path], bool] | None = None,
                 beat_reader: Callable[[Path], dict[str, Any] | None] = read,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep,
                 rng: Callable[[], float] = random.random,
                 startup_grace: float = 90.0, stable_after: float = 120.0,
                 stop_grace: float = 30.0, terminate_grace: float = 10.0,
                 lock_wait: float = 20.0, poll_interval: float = 2.0, max_fatal: int = 5):
        self.home = Path(home)
        self.spawn = spawn
        self._is_running = is_running
        self.beat_reader = beat_reader
        self.clock, self.sleep, self.rng = clock, sleep, rng
        self.startup_grace, self.stable_after = startup_grace, stable_after
        self.stop_grace, self.terminate_grace = stop_grace, terminate_grace
        self.lock_wait, self.poll_interval, self.max_fatal = lock_wait, poll_interval, max_fatal
        self.restarts = 0
        self.attempt = 0
        self.last_exit: int | None = None
        self.delays: list[float] = []
        self.spawned = 0

    # -- helpers -------------------------------------------------------------------
    @property
    def stop_flag(self) -> Path:
        return self.home / STOP_FLAG_NAME

    @property
    def state_path(self) -> Path:
        return self.home / WATCHDOG_DIR / "state.json"

    def lock_held(self) -> bool:
        if self._is_running is not None:
            return bool(self._is_running(self.home))
        from .cli import _is_running
        return bool(_is_running(self.home))

    def _state(self, state: str, child_pid: int | None = None) -> None:
        try:
            write_file(self.state_path, {
                "schema": "bossman.pit.watchdog/1", "pid": os.getpid(), "state": state,
                "child_pid": child_pid, "restarts": self.restarts, "attempt": self.attempt,
                "last_exit": self.last_exit, "at": _iso(time.time()), "at_epoch": int(time.time())})
        except OSError:
            pass

    def _healthy(self, pid: int) -> bool:
        beat = self.beat_reader(self.home)
        if not beat or beat.get("availability") != "up":
            return False
        beat_pid = beat.get("pid")
        # A Windows venv `python.exe` is a launcher: the pid the watchdog spawned is the launcher, the pid that writes the
        # heartbeat is the real interpreter below it. Comparing only the two made a perfectly healthy Jeff "unhealthy"
        # after the start grace and the watchdog killed and restarted it every few minutes (seen live 30.09).
        return beat_pid == pid or beat_pid in _descendant_pids(pid)

    def _sleep_watching_stop(self, seconds: float) -> bool:
        """Sleep in slices; True if a stop was requested meanwhile."""
        end = self.clock() + seconds
        while self.clock() < end:
            if self.stop_flag.exists():
                return True
            self.sleep(max(0.001, min(self.poll_interval, end - self.clock())))
        return self.stop_flag.exists()

    def _wait_exit(self, child: Any, seconds: float) -> bool:
        end = self.clock() + seconds
        while child.poll() is None:
            if self.clock() >= end:
                return False
            self.sleep(min(self.poll_interval, 1.0))
        return True

    def _end_child(self, child: Any, grace: float) -> None:
        """Graceful first (the child sees stop.flag itself), then terminate, then kill."""
        if child.poll() is not None:
            return
        if not self._wait_exit(child, grace):
            child.terminate()
            if not self._wait_exit(child, self.terminate_grace):
                child.kill()
                self._wait_exit(child, self.terminate_grace)

    def _finish(self, why: str) -> str:
        if why == "stopped":
            self.stop_flag.unlink(missing_ok=True)
        self._state(why)
        return why

    # -- main loop -----------------------------------------------------------------
    def run(self) -> str:
        """Blocks until a stop or a permanent failure; returns why it ended."""
        if self.stop_flag.exists() and not self.lock_held():
            self.stop_flag.unlink(missing_ok=True)          # stale flag of a dead session
        fatal = 0
        child = None
        try:
            while True:
                if self.stop_flag.exists():
                    return self._finish("stopped")
                if self.lock_held():
                    # A poller (ours from before, or foreign) owns the lock: never start a second.
                    self._state("waiting_for_lock")
                    if self._sleep_watching_stop(self.poll_interval):
                        return self._finish("stopped")
                    continue
                child = self.spawn()
                self.spawned += 1
                started = self.clock()
                self._state("running", getattr(child, "pid", None))
                reason = ""
                while True:
                    self.sleep(self.poll_interval)
                    if self.stop_flag.exists():
                        self._end_child(child, self.stop_grace)
                        return self._finish("stopped")
                    if child.poll() is not None:
                        break
                    if (self.clock() - started > self.startup_grace
                            and not self._healthy(getattr(child, "pid", -1))):
                        reason = "unhealthy"
                        self._end_child(child, 0.0)
                        break
                code = child.poll()
                self.last_exit = code
                if code == 0 and not reason:
                    return self._finish("exited")
                fatal = fatal + 1 if code == EXIT_FATAL and not reason else 0
                if fatal >= self.max_fatal:
                    return self._finish("fatal")
                # The lock must be free before another spawn is even considered.
                waited = self.clock()
                while self.lock_held() and self.clock() - waited < self.lock_wait:
                    self.sleep(self.poll_interval)
                stable = self.clock() - started >= self.stable_after
                delay = backoff_delay(0 if stable else self.attempt, rng=self.rng)
                self.attempt = 1 if stable else self.attempt + 1
                self.restarts += 1
                self.delays.append(delay)
                self._state("backoff")
                if self._sleep_watching_stop(delay):
                    return self._finish("stopped")
        finally:
            if child is not None and child.poll() is None:
                # Interrupted: ask the poller to stop itself, then escalate. Never orphan it.
                with contextlib.suppress(OSError):
                    self.stop_flag.write_text(_iso(time.time()) or "", encoding="utf-8")
                self._end_child(child, self.stop_grace)
                self.stop_flag.unlink(missing_ok=True)
