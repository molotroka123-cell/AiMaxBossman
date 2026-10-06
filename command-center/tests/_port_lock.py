"""Cross-process lock for tests that start a REAL app on a fixed port (File Commander's default 8911).

pytest -n 4 runs those modules in different workers; two of them listening on 8911 at once gave "порт 8911 уже занят процессом, которого
BOSSMAN не…" / disabled Start buttons (CI py3.14, 2026-10-06). The lock serialises only those tests; everything else stays parallel.
"""
from __future__ import annotations

import contextlib
import os
import tempfile
import time
from pathlib import Path

LOCK_PATH = Path(tempfile.gettempdir()) / "bossman-tests-port-8911.lock"
FIXED_PORT_MODULES = frozenset({
    "test_apps_files_browser_owner", "test_apps_files_http_owner", "test_apps_files_lost_response",
    "test_apps_owner_path_regression", "test_apps_release_runtime", "test_file_commander_proxy_identity",
    "test_redteam_rc_20260921", "test_smoke_live_owner",
})


def _try_lock(fd: int) -> bool:
    try:
        if os.name == "nt":
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(fd: int) -> None:
    try:
        if os.name == "nt":
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


@contextlib.contextmanager
def exclusive_port_lock(path: Path = LOCK_PATH, timeout: float = 900.0):
    """Hold the lock for the body; wait up to `timeout` seconds, then raise (never run unlocked)."""
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)
    deadline = time.monotonic() + timeout
    try:
        while not _try_lock(fd):
            if time.monotonic() > deadline:
                raise TimeoutError(f"could not get the fixed-port test lock {path} in {timeout:.0f}s")
            time.sleep(0.2)
    except BaseException:
        os.close(fd)
        raise
    try:
        yield
    finally:
        _unlock(fd)
