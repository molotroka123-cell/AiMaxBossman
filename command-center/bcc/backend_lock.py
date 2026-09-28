"""One Bossman backend per data root.

The owner's desktop had two shortcuts from two installs: the window started
its own server on one port, the terminal started another on a different port,
and both wrote the same data root. Nothing noticed, because every guard was
per *port*. This lock is per *data root*:

* ``backend.lock`` holds an exclusive OS byte-range lock for the lifetime of
  the serving process. The OS drops it when the process dies, so a crash
  never leaves a stale lock and nothing has to be cleaned up by hand.
* ``backend.json`` beside it says who holds it (pid, host, port, build) so the
  window and the terminal can attach to that server instead of starting a
  second one. It is only trusted while the lock is actually held.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

LOCK_NAME = "backend.lock"
INFO_NAME = "backend.json"


class BackendAlreadyRunning(RuntimeError):
    def __init__(self, info: dict[str, Any] | None):
        self.info = info or {}
        where = (f"http://{self.info.get('host', '127.0.0.1')}:{self.info['port']}"
                 if self.info.get("port") else "адрес неизвестен")
        super().__init__(
            f"Bossman для этих данных уже запущен ({where}, pid {self.info.get('pid', '?')}, "
            f"сборка {str(self.info.get('build_sha') or '?')[:12]}) — второй сервер на тех же "
            "данных не запускаю")


def _try_lock(handle) -> bool:
    try:
        if sys.platform == "win32":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(handle) -> None:
    try:
        if sys.platform == "win32":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


def _open(data_dir: Path):
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / LOCK_NAME
    handle = open(path, "a+b")
    if handle.seek(0, os.SEEK_END) == 0:
        handle.write(b"\0")          # a byte to lock on
        handle.flush()
    return handle


def _read_info(data_dir: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((data_dir / INFO_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


class BackendLock:
    """Held by the serving process; ``release`` is optional (process exit frees it)."""

    def __init__(self, data_dir: Path, handle, info: dict[str, Any]):
        self.data_dir = data_dir
        self._handle = handle
        self.info = info

    def update(self, **fields: Any) -> None:
        self.info.update(fields)
        _write_info(self.data_dir, self.info)

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            current = _read_info(self.data_dir)
            if current and current.get("pid") == self.info.get("pid"):
                (self.data_dir / INFO_NAME).unlink(missing_ok=True)
        except OSError:
            pass
        _unlock(self._handle)
        self._handle.close()
        self._handle = None


def _write_info(data_dir: Path, info: dict[str, Any]) -> None:
    tmp = data_dir / (INFO_NAME + ".tmp")
    tmp.write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, data_dir / INFO_NAME)


def acquire(data_dir: str | Path, *, host: str, port: int,
            build_sha: str | None = None, kind: str = "server") -> BackendLock:
    """Take the data-root lock or raise BackendAlreadyRunning with the holder's info."""
    base = Path(data_dir)
    handle = _open(base)
    if not _try_lock(handle):
        handle.close()
        raise BackendAlreadyRunning(_read_info(base))
    info = {"pid": os.getpid(), "host": host, "port": int(port), "build_sha": build_sha,
            "kind": kind, "started_at": time.time()}
    _write_info(base, info)
    return BackendLock(base, handle, info)


def running_backend(data_dir: str | Path) -> dict[str, Any] | None:
    """Who serves this data root right now, or None (lock free or no info)."""
    base = Path(data_dir)
    if not (base / LOCK_NAME).exists():
        return None
    try:
        handle = _open(base)
    except OSError:
        return None
    try:
        if _try_lock(handle):
            _unlock(handle)
            return None                  # nobody holds it: any backend.json is stale
        return _read_info(base)
    finally:
        handle.close()
