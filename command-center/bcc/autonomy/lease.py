"""Global engineering lease: exactly one writer (Claude CLI / Codex CLI session) at a time.

``<root>/engineering.lease`` holds the current lease (JSON); every read-modify-
write happens under the cross-process lock ``<root>/engineering.lease.lock``,
so two processes can never both believe they hold the lease. A lease is stale
when its heartbeat is older than its TTL or its holder process is gone; a stale
lease is taken over, and the worker processes registered under it (orphans)
are killed through the injected ``killer`` first. Every acquire / takeover /
release is journaled when a journal is given.
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from .journal import Journal, atomic_write_bytes, file_lock


class LeaseBusy(RuntimeError):
    def __init__(self, current: "LeaseToken"):
        super().__init__(f"engineering lease held by {current.holder} for {current.goal_id} (pid {current.pid})")
        self.current = current


class LeaseLost(RuntimeError):
    pass


@dataclass(frozen=True)
class LeaseToken:
    token: str
    goal_id: str
    holder: str
    pid: int
    acquired_at: float
    heartbeat_at: float
    ttl_s: float
    processes: tuple[int, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["processes"] = list(self.processes)
        return d


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        import psutil
        if not psutil.pid_exists(pid):
            return False
        try:
            return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
        except psutil.Error:
            return False
    except ImportError:  # pragma: no cover - psutil is a Command Center dependency
        pass
    if os.name == "nt":  # pragma: no cover
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return code.value == 259
    try:  # pragma: no cover
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def kill_process_group(pid: int) -> None:
    """Kill a worker and everything it started (best effort, never raises)."""
    try:
        import psutil
        try:
            root = psutil.Process(pid)
            procs = root.children(recursive=True) + [root]
        except psutil.Error:
            procs = []
        for p in procs:
            try:
                p.kill()
            except psutil.Error:
                pass
        psutil.wait_procs(procs, timeout=5)
        return
    except ImportError:  # pragma: no cover
        pass
    if os.name == "nt":  # pragma: no cover
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False)
    else:  # pragma: no cover
        import signal
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except OSError:
            pass


class EngineeringLease:
    def __init__(self, root: str | os.PathLike, *, journal: Journal | None = None,
                 clock: Callable[[], float] = time.time, killer: Callable[[int], None] = kill_process_group,
                 is_alive: Callable[[int], bool] = pid_alive, pid: int | None = None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "engineering.lease"
        self.lock_path = self.root / "engineering.lease.lock"
        self.journal = journal
        self._clock = clock
        self._killer = killer
        self._alive = is_alive
        self._pid = os.getpid() if pid is None else pid

    # .......................................................... internals
    def _read(self) -> LeaseToken | None:
        if not self.path.exists():
            return None
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            return LeaseToken(**{**d, "processes": tuple(int(p) for p in d.get("processes", ()))})
        except (OSError, ValueError, TypeError):
            return None           # unreadable lease: treated as stale (its orphans are unknown -> journaled)

    def _write(self, t: LeaseToken) -> None:
        atomic_write_bytes(self.path, json.dumps(t.as_dict(), sort_keys=True).encode("utf-8"))

    def _stale_reason(self, t: LeaseToken) -> str:
        if self._clock() - t.heartbeat_at > t.ttl_s:
            return "heartbeat expired"
        if not self._alive(t.pid):
            return "holder process is gone"
        return ""

    def _log(self, kind: str, payload: dict) -> None:
        if self.journal is not None:
            self.journal.append(kind, payload)

    def _kill_orphans(self, t: LeaseToken) -> list[int]:
        killed = []
        for p in t.processes:
            if self._alive(p):
                try:
                    self._killer(p)
                finally:
                    killed.append(p)
        return killed

    def _expire(self, t: LeaseToken, reason: str) -> None:
        killed = self._kill_orphans(t)
        self.path.unlink(missing_ok=True)
        self._log("lease.expired", {"goal_id": t.goal_id, "holder": t.holder, "pid": t.pid, "reason": reason,
                                    "orphans_killed": killed})

    # .......................................................... api
    def peek(self) -> dict | None:
        """Read-only view for dashboards: never expires, never kills."""
        t = self._read()
        if t is None:
            return None
        return {**t.as_dict(), "stale_reason": self._stale_reason(t)}

    def current(self) -> LeaseToken | None:
        with file_lock(self.lock_path):
            t = self._read()
            if t is None:
                if self.path.exists():
                    self.path.unlink(missing_ok=True)
                    self._log("lease.expired", {"reason": "unreadable lease file"})
                return None
            reason = self._stale_reason(t)
            if reason:
                self._expire(t, reason)
                return None
            return t

    def acquire(self, goal_id: str, holder: str, ttl_s: float = 300.0) -> LeaseToken:
        if ttl_s <= 0:
            raise ValueError("ttl_s must be positive")
        with file_lock(self.lock_path):
            t = self._read()
            if t is not None:
                reason = self._stale_reason(t)
                if not reason:
                    raise LeaseBusy(t)
                self._expire(t, reason)
                self._log("lease.takeover", {"goal_id": goal_id, "holder": holder, "from_holder": t.holder,
                                             "from_goal_id": t.goal_id, "reason": reason})
            now = self._clock()
            new = LeaseToken(token=secrets.token_hex(16), goal_id=goal_id, holder=holder, pid=self._pid,
                             acquired_at=now, heartbeat_at=now, ttl_s=float(ttl_s))
            self._write(new)
            self._log("lease.acquired", {"goal_id": goal_id, "holder": holder, "pid": self._pid, "ttl_s": ttl_s})
            return new

    def _owned(self, token: LeaseToken | str) -> LeaseToken:
        value = token.token if isinstance(token, LeaseToken) else token
        t = self._read()
        if t is None or t.token != value:
            raise LeaseLost("the lease is no longer held by this token")
        reason = self._stale_reason(t)
        if reason:
            self._expire(t, reason)
            raise LeaseLost(f"the lease expired: {reason}")
        return t

    def heartbeat(self, token: LeaseToken | str) -> LeaseToken:
        with file_lock(self.lock_path):
            t = self._owned(token)
            new = LeaseToken(**{**asdict(t), "heartbeat_at": self._clock()})
            self._write(new)
            return new

    def register_process(self, token: LeaseToken | str, pid: int) -> LeaseToken:
        """Record a worker process (group leader) that must not outlive the lease."""
        with file_lock(self.lock_path):
            t = self._owned(token)
            new = LeaseToken(**{**asdict(t), "processes": tuple(sorted(set(t.processes) | {int(pid)}))})
            self._write(new)
            self._log("lease.process", {"goal_id": t.goal_id, "holder": t.holder, "pid": int(pid)})
            return new

    def release(self, token: LeaseToken | str) -> dict:
        """Release; any registered worker still running is killed first (verified stop)."""
        with file_lock(self.lock_path):
            value = token.token if isinstance(token, LeaseToken) else token
            t = self._read()
            if t is None or t.token != value:
                raise LeaseLost("the lease is not held by this token")
            killed = self._kill_orphans(t)
            still = [p for p in t.processes if self._alive(p)]
            if still:
                self._log("lease.release_failed", {"goal_id": t.goal_id, "alive": still})
                raise LeaseLost(f"worker processes still alive after kill: {still}")
            self.path.unlink(missing_ok=True)
            self._log("lease.released", {"goal_id": t.goal_id, "holder": t.holder, "orphans_killed": killed})
            return {"released": True, "orphans_killed": killed}


__all__ = ["EngineeringLease", "LeaseBusy", "LeaseLost", "LeaseToken", "kill_process_group", "pid_alive"]
