"""AUDIT001-F5-REPLAY — single-use ledger for promotion evidence.

The ledger stores only opaque evidence digests and the consumer identity.  A
measurement may be retried by the same consumer, but must never authorize a
different candidate, including after restart, capacity pressure or concurrent
writers.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Iterable

ENV_LEDGER_PATH = "BOSSMAN_EVIDENCE_LEDGER_PATH"


def _plain(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {k: _plain(v) for k, v in sorted(asdict(value).items())}
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def evidence_key(ab_results: Iterable[Any], security_before: Any, security_after: Any,
                 shadow_runs: int) -> str:
    """Opaque digest of one measurement. Order of the A/B rows does not matter."""
    rows = sorted(json.dumps(_plain(r), sort_keys=True, ensure_ascii=True) for r in ab_results)
    blob = json.dumps(
        {"ab": rows, "before": _plain(security_before), "after": _plain(security_after),
         "shadow_runs": int(shadow_runs)},
        sort_keys=True,
        ensure_ascii=True,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class EvidenceLedger:
    """In-memory single-use ledger with bounded, fail-closed admission.

    Capacity pressure must never erase an already-spent digest.  Once full, the
    ledger refuses *new* evidence rather than evicting old replay protection.
    """

    def __init__(self, capacity: int = 10_000):
        self.capacity = max(1, int(capacity))
        self._lock = RLock()
        self._records: dict[str, str] = {}

    def _load(self) -> dict[str, str]:
        return self._records

    def _store(self, key: str, consumer: str) -> None:
        if key not in self._records and len(self._records) >= self.capacity:
            raise RuntimeError("evidence ledger capacity exhausted; refusing new evidence")
        self._records[key] = consumer

    def consume(self, key: str, consumer: str) -> str | None:
        """``None`` means usable/idempotent; otherwise return a refusal reason."""
        with self._lock:
            owner = self._load().get(key)
            if owner is None:
                try:
                    self._store(key, consumer)
                except RuntimeError as exc:
                    return str(exc)
                return None
            if owner == consumer:
                return None
            return (
                f"A/B evidence was already spent promoting {owner!r}; "
                f"{consumer!r} needs its own measured run"
            )

    def reset(self) -> None:
        with self._lock:
            self._records.clear()


@contextmanager
def _exclusive_file_lock(path: Path, timeout_s: float = 10.0):
    """Portable advisory lock used across independent ledger processes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+b")
    deadline = time.monotonic() + max(0.1, float(timeout_s))
    try:
        if os.name == "nt":
            import msvcrt
            while True:
                try:
                    fh.seek(0)
                    if fh.tell() == 0:
                        fh.write(b"\0")
                        fh.flush()
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"evidence ledger lock timeout: {path}")
                    time.sleep(0.01)
        else:
            import fcntl
            while True:
                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"evidence ledger lock timeout: {path}")
                    time.sleep(0.01)
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()


class DurableEvidenceLedger(EvidenceLedger):
    """File-backed, fail-closed replay ledger.

    The entire load→check→store transaction is protected by an OS-level lock.
    Corruption is a safety failure, never an empty database.  Writes are
    temp+fsync+replace and the parent directory is fsynced where supported.
    """

    def __init__(self, path, capacity: int = 10_000):
        super().__init__(capacity)
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.path.with_name(self.path.name + ".lock")

    def _load(self) -> dict[str, str]:
        if not self.path.exists():
            self._records = {}
            return self._records
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"evidence ledger is unreadable/corrupt: {self.path}") from exc
        if not isinstance(raw, dict):
            raise RuntimeError(f"evidence ledger has invalid root type: {self.path}")
        records: dict[str, str] = {}
        for key, value in raw.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise RuntimeError(f"evidence ledger has invalid record: {self.path}")
            records[key] = value
        if len(records) > self.capacity:
            raise RuntimeError("evidence ledger exceeds configured capacity; refusing mutation")
        self._records = records
        return self._records

    def _store(self, key: str, consumer: str) -> None:
        super()._store(key, consumer)
        tmp = self.path.with_name(
            f".{self.path.name}.{os.getpid()}.{id(self)}.tmp"
        )
        data = json.dumps(self._records, sort_keys=True, separators=(",", ":"))
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
            try:
                dir_fd = os.open(self.path.parent, os.O_RDONLY)
            except (AttributeError, OSError):
                dir_fd = None
            if dir_fd is not None:
                try:
                    os.fsync(dir_fd)
                except OSError:
                    pass
                finally:
                    os.close(dir_fd)
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass

    def consume(self, key: str, consumer: str) -> str | None:
        with self._lock:
            with _exclusive_file_lock(self.lock_path):
                owner = self._load().get(key)
                if owner is None:
                    try:
                        self._store(key, consumer)
                    except RuntimeError as exc:
                        return str(exc)
                    return None
                if owner == consumer:
                    return None
                return (
                    f"A/B evidence was already spent promoting {owner!r}; "
                    f"{consumer!r} needs its own measured run"
                )

    def reset(self) -> None:
        with self._lock:
            with _exclusive_file_lock(self.lock_path):
                self._records.clear()
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass


_DEFAULT = EvidenceLedger()


def default_ledger() -> EvidenceLedger:
    """Durable when BOSSMAN_EVIDENCE_LEDGER_PATH is configured."""
    path = os.environ.get(ENV_LEDGER_PATH, "").strip()
    if path:
        return DurableEvidenceLedger(path)
    return _DEFAULT


def reset_default_ledger() -> None:
    """Clear the process-wide in-memory ledger (test isolation)."""
    _DEFAULT.reset()
