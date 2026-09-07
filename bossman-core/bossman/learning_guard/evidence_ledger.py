"""AUDIT001-F5-REPLAY — single-use ledger for promotion evidence.

The gap the audit recorded: ``ABResult`` carries no candidate id or version, so
one measured A/B run promoted candidate ``v1`` and then, replayed verbatim,
promoted ``v9`` as well. No stateless predicate over ``(candidate, evidence)``
can separate two candidates that differ only by version and are handed
byte-identical measurements — separating them needs memory of what that
measurement has already been spent on. That is what this ledger is.

Contract
--------
* The key is the *evidence* alone: the A/B results, both security snapshots and
  the shadow-run count, in a canonical order-insensitive form. Reordering the
  A/B list does not mint a fresh key.
* The consumer is the candidate identity *including its version*
  (``candidate_id`` + ``rollback_ref``).
* First use records the consumer. Presenting the same evidence again for the
  same consumer is allowed — a retried promotion is not a replay. Presenting it
  for any other consumer is refused.
* Consumption happens once the evidence has passed the structural scope checks,
  so a malformed or cross-corpus attempt does not burn a measurement.

``EvidenceLedger`` is in-memory and process-wide; ``DurableEvidenceLedger``
serializes cooperating processes and writes the same records atomically so the
refusal survives restart. Spent records are not an evictable cache; exhaustion,
corruption and I/O uncertainty refuse new promotion. Only opaque identities are
stored, not measurements, prompts or costs.
"""
from __future__ import annotations

import hashlib
import json
import os
import contextlib
import tempfile
import time
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
    blob = json.dumps({"ab": rows, "before": _plain(security_before), "after": _plain(security_after),
                       "shadow_runs": int(shadow_runs)}, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class EvidenceLedger:
    """Bounded single-use ledger. At capacity new evidence is REFUSED, not evicted."""

    def __init__(self, capacity: int = 10_000):
        self.capacity = max(1, int(capacity))
        self._lock = RLock()
        self._records: dict[str, str] = {}

    def _load(self) -> dict[str, str]:
        return self._records

    def _store(self, key: str, consumer: str) -> None:
        self._records[key] = consumer

    def consume(self, key: str, consumer: str) -> str | None:
        """``None`` = the evidence may be used; otherwise the refusal reason."""
        if (type(key) is not str or type(consumer) is not str or not key or not consumer
                or len(key) > 1024 or len(consumer) > 1024):
            return "invalid evidence/consumer identity"
        with self._lock:
            records = self._load()
            owner = records.get(key)
            if owner is None:
                if len(records) >= self.capacity:
                    return "evidence ledger capacity exhausted; retained spent records, promotion refused"
                self._store(key, consumer)
                return None
            if owner == consumer:
                return None
            return (f"A/B evidence was already spent promoting {owner!r}; "
                    f"{consumer!r} needs its own measured run")

    def reset(self) -> None:
        with self._lock:
            self._records.clear()


class DurableEvidenceLedger(EvidenceLedger):
    """Cross-process read/validate/consume transaction on a trusted local directory.

    Existing flat JSON maps remain readable. An initialized marker prevents a
    missing established ledger from being mistaken for a new install. Corrupt,
    missing-after-init, oversize and unreadable data refuse promotion unchanged.
    Writes fsync a unique temp file then atomically replace under a permanent
    advisory lock. All cooperating writers must use this class. This does NOT
    detect consistent rollback/deletion of both data and marker by a party with
    directory access; external monotonic storage is needed for that threat.
    """
    MAX_FILE_BYTES = 16 * 1024 * 1024
    MARKER = b"bossman-evidence-ledger-v1\n"

    def __init__(self, path, capacity: int = 10_000, *, lock_timeout_s: float = 2.0):
        super().__init__(capacity)
        if not 0 < lock_timeout_s <= 30:
            raise ValueError("bounded positive lock timeout required")
        self.path = Path(path).absolute()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self.marker_path = self.path.with_suffix(self.path.suffix + ".initialized")
        self.lock_timeout_s = lock_timeout_s

    @staticmethod
    def _pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate ledger identity")
            result[key] = value
        return result

    @contextlib.contextmanager
    def _file_lock(self):
        # Stable file separate from the replaced JSON; never delete the lock.
        if any(p.is_symlink() for p in (self.path, self.lock_path, self.marker_path)):
            raise ValueError("symlink ledger path refused")
        handle = self.lock_path.open("a+b")
        acquired = False
        try:
            if os.fstat(handle.fileno()).st_size == 0:
                handle.write(b"0")
                handle.flush()
            deadline = time.monotonic() + self.lock_timeout_s
            while True:
                try:
                    if os.name == "nt":
                        import msvcrt
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("evidence ledger busy; promotion refused")
                    time.sleep(min(.01, max(0, deadline - time.monotonic())))
            yield
        finally:
            try:
                if acquired:
                    if os.name == "nt":
                        import msvcrt
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()

    def _load(self) -> dict[str, str]:
        marker = self.marker_path.exists()
        if marker and self.marker_path.read_bytes() != self.MARKER:
            raise ValueError("invalid initialization marker")
        if not self.path.exists():
            if marker:
                raise ValueError("initialized ledger missing; reconciliation required")
            self._records = {}
            return self._records
        with self.path.open("rb") as handle:
            data = handle.read(self.MAX_FILE_BYTES + 1)
        if len(data) > self.MAX_FILE_BYTES:
            raise ValueError("oversize ledger")
        raw = json.loads(data.decode("utf-8-sig"), object_pairs_hook=self._pairs)
        if (type(raw) is not dict or any(type(k) is not str or not k or len(k) > 1024
                or type(v) is not str or not v or len(v) > 1024 for k, v in raw.items())):
            raise ValueError("malformed ledger")
        self._records = raw
        return self._records

    def _sync_directory(self):
        if os.name != "nt":
            fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

    def _store(self, key: str, consumer: str) -> None:
        records = dict(self._records)
        records[key] = consumer
        data = json.dumps(records, sort_keys=True, allow_nan=False).encode("utf-8")
        if len(data) > self.MAX_FILE_BYTES:
            raise ValueError("ledger byte limit; promotion refused")
        # A crash during initialization is an explicit blocked state, not loss
        # of a spend that was reported as successful.
        if not self.marker_path.exists():
            with self.marker_path.open("xb") as marker:
                marker.write(self.MARKER)
                marker.flush()
                os.fsync(marker.fileno())
            self._sync_directory()
        fd, name = tempfile.mkstemp(prefix=self.path.name + ".", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, self.path)
            self._sync_directory()
            self._records = records
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def consume(self, key: str, consumer: str) -> str | None:
        try:
            with self._lock, self._file_lock():
                return super().consume(key, consumer)
        except (OSError, ValueError, TypeError, UnicodeError) as exc:
            # No key, prompt, path or malformed raw contents in the refusal.
            return f"evidence ledger unavailable ({type(exc).__name__}); promotion refused"

    def reset(self) -> None:
        raise RuntimeError("durable spent evidence cannot be reset; owner reconciliation required")


_DEFAULT = EvidenceLedger()


def default_ledger() -> EvidenceLedger:
    """Durable when the owner points ``BOSSMAN_EVIDENCE_LEDGER_PATH`` at a file,
    otherwise process-wide in memory. The in-memory default keeps the pure value
    layer pure; a deployment that promotes across restarts must set the path."""
    path = os.environ.get(ENV_LEDGER_PATH, "").strip()
    if path:
        return DurableEvidenceLedger(path)
    return _DEFAULT


def reset_default_ledger() -> None:
    """Clear the process-wide in-memory ledger (test isolation)."""
    _DEFAULT.reset()
