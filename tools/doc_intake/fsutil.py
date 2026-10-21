"""Atomic writes, verified copies and a cross-process lock (stdlib only).

A crash mid-write must leave either the old file or the new one, never a
truncated manifest or note: temp file in the same directory, fsync, then
``os.replace`` (atomic within one volume). Windows may briefly refuse the
replace while an indexer/AV holds the target open, so it is retried.
"""
from __future__ import annotations

import hashlib
import os
import time
import uuid
from pathlib import Path

CHUNK = 1024 * 1024


class SourceChanged(RuntimeError):
    pass


class LockBusy(RuntimeError):
    pass


def _tmp_for(path: Path) -> Path:
    return path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")


def _replace(src: Path, dst: Path, attempts: int = 10) -> None:
    for attempt in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.05 * (attempt + 1))


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp_for(path)
    try:
        with open(tmp, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        _replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while block := handle.read(CHUNK):
            digest.update(block)
    return digest.hexdigest()


def copy_verified(src: Path, dest: Path, expected_sha256: str) -> None:
    """Copy src -> dest (never moves or touches src); the copy must hash to expected_sha256."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp_for(dest)
    digest = hashlib.sha256()
    try:
        st = os.stat(src)
        with open(src, "rb") as reader, open(tmp, "wb") as writer:
            while block := reader.read(CHUNK):
                digest.update(block)
                writer.write(block)
            writer.flush()
            os.fsync(writer.fileno())
        if digest.hexdigest() != expected_sha256:
            raise SourceChanged("файл изменился во время приёма: повторите, когда запись закончится")
        os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
        _replace(tmp, dest)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


class FileLock:
    """Exclusive lock file (O_CREAT|O_EXCL). Held only around manifest read-modify-write."""

    def __init__(self, path: Path, *, timeout: float = 30.0, stale_after: float = 120.0):
        self.path = Path(path)
        self.timeout = timeout
        self.stale_after = stale_after

    def __enter__(self) -> "FileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                try:
                    os.write(fd, f"{os.getpid()} {time.time():.0f}".encode())
                finally:
                    os.close(fd)
                return self
            except (FileExistsError, PermissionError):
                try:
                    if time.time() - self.path.stat().st_mtime > self.stale_after:
                        self.path.unlink()           # holder died mid-transaction
                        continue
                except OSError:
                    pass
                if time.monotonic() > deadline:
                    raise LockBusy(f"библиотека занята другим процессом ({self.path})") from None
                time.sleep(0.05)

    def __exit__(self, *exc) -> None:
        try:
            self.path.unlink()
        except OSError:
            pass
