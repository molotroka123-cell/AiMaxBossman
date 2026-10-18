"""Isolated experiment directory.

What is ENFORCED here (and tested): every path resolves inside ``<root>/experiments/<id>`` (no
absolute paths, no ``..``, no symlink/junction escapes), never inside the live data dir, never a
protected file; total bytes written are capped; subprocesses run with the sandbox as cwd, a scrubbed
environment (no secrets, proxies pointed at a dead address), a wall-clock timeout that kills the
process, and capped output.

What is NOT enforced (stated, not hidden): a hostile subprocess running as the same OS user could
still open sockets or write elsewhere - without an OS sandbox (container/AppContainer) that cannot be
prevented from Python. Experiments therefore only run code the owner's review allowed, and the report
lists "OS-level network isolation" as NOT_RUN.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .limits import LimitViolation, Limits, guard_write_target

_ENV_KEEP = ("PATH", "SYSTEMROOT", "SystemRoot", "COMSPEC", "PATHEXT", "WINDIR", "LANG")
_DEAD_PROXY = "http://127.0.0.1:9"


class Sandbox:
    def __init__(self, root: str | os.PathLike, exp_id: str, limits: Limits, *,
                 live_data_dir: str | os.PathLike | None = None):
        if not exp_id or any(c in exp_id for c in "/\\:.") or len(exp_id) > 80:
            raise LimitViolation("bad experiment id")
        self.limits = limits
        self.base = (Path(root) / "experiments").resolve()
        self.dir = self.base / exp_id
        self.live = Path(live_data_dir).resolve() if live_data_dir else None
        if self.live and (self.dir == self.live or self.live in self.dir.parents):
            raise LimitViolation("the sandbox may not live inside the live data directory")
        self.dir.mkdir(parents=True, exist_ok=True)
        self.dir = self.dir.resolve()
        self.written = 0

    # ---------------------------------------------------------- paths
    def resolve(self, rel: str) -> Path:
        if not rel or os.path.isabs(rel) or Path(rel).drive or rel.startswith(("/", "\\")):
            raise LimitViolation(f"absolute or empty path refused: {rel!r}")
        target = (self.dir / rel).resolve()
        if target != self.dir and self.dir not in target.parents:
            raise LimitViolation(f"path escapes the experiment directory: {rel!r}")
        if self.live and (target == self.live or self.live in target.parents):
            raise LimitViolation("path reaches the live data directory")
        guard_write_target(target)
        return target

    # ---------------------------------------------------------- file API
    def write_text(self, rel: str, text: str) -> Path:
        data = text.encode("utf-8")
        target = self.resolve(rel)
        if self.written + len(data) > self.limits.max_sandbox_bytes:
            raise LimitViolation("sandbox byte cap exceeded")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        self.written += len(data)
        return target

    def read_text(self, rel: str) -> str:
        return self.resolve(rel).read_text(encoding="utf-8")

    def copy_in(self, src: str | os.PathLike, rel: str) -> Path:
        """Copy a file INTO the sandbox (read-only access to the source)."""
        s = Path(src)
        if s.stat().st_size + self.written > self.limits.max_sandbox_bytes:
            raise LimitViolation("sandbox byte cap exceeded")
        target = self.resolve(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(s, target)
        self.written += s.stat().st_size
        return target

    def cleanup(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)

    # ---------------------------------------------------------- process
    def child_env(self) -> dict[str, str]:
        env = {k: os.environ[k] for k in _ENV_KEEP if k in os.environ}
        env.update({"TEMP": str(self.dir), "TMP": str(self.dir), "HOME": str(self.dir), "USERPROFILE": str(self.dir),
                    "HTTP_PROXY": _DEAD_PROXY, "HTTPS_PROXY": _DEAD_PROXY, "ALL_PROXY": _DEAD_PROXY,
                    "NO_PROXY": "", "PYTHONDONTWRITEBYTECODE": "1", "BCC_SELF_IMPROVE_SANDBOX": "1"})
        return env

    def run(self, argv: list[str], *, timeout: float | None = None, max_output: int = 20_000) -> dict:
        if not argv or not isinstance(argv, list):
            raise LimitViolation("argv list required (no shell)")
        wall = min(float(timeout or self.limits.max_wall_seconds), self.limits.max_wall_seconds)
        started = time.monotonic()
        try:
            proc = subprocess.run(argv, cwd=self.dir, env=self.child_env(), capture_output=True, timeout=wall,
                                  stdin=subprocess.DEVNULL, shell=False)
            code, out, err, timed_out = proc.returncode, proc.stdout, proc.stderr, False
        except subprocess.TimeoutExpired as exc:
            code, out, err, timed_out = -9, exc.stdout or b"", exc.stderr or b"", True
        return {"returncode": code, "timed_out": timed_out, "duration_s": round(time.monotonic() - started, 3),
                "stdout": out[:max_output].decode("utf-8", "replace"),
                "stderr": err[:max_output].decode("utf-8", "replace")}


def python_argv(*args: str) -> list[str]:
    return [sys.executable, "-I", *args]
