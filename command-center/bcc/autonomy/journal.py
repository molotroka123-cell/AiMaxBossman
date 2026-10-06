"""Append-only, hash-chained decision and evidence journal (constitution goal 6).

One JSON object per line in ``<root>/journal.jsonl``::

    {"seq": 3, "ts": "...", "kind": "goal.transition", "payload": {...}, "prev": "<hash of seq 2>", "hash": "..."}

``hash = sha256(canonical JSON of the entry without "hash")`` and the first
entry links to ``GENESIS``. ``<root>/journal.head`` repeats the last seq and
hash, so cutting lines off the end is detected as well as editing, reordering
or deleting lines in the middle. Payloads are secret-redacted BEFORE hashing
(the same key-name + token-shape recognisers the rest of Bossman uses), so the
journal never stores a credential and verification never needs one.

Writers in several processes serialise on ``<root>/journal.lock``.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

GENESIS = "0" * 64
REDACTED = "***REDACTED***"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | os.PathLike) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 16), b""):
            h.update(block)
    return h.hexdigest()


# ----------------------------------------------------------------- redaction

def redact(value: Any) -> Any:
    """Key-name + known token-shape redaction, reusing bcc.plugin_security and
    bcc.pit.secret_filter (Telegram bot token, private keys, password=...)."""
    from ..pit.secret_filter import redact_secrets
    from ..plugin_security import redact as redact_keys

    cleaned = redact_keys(value, scrub_text=True)

    def _walk(v: Any) -> Any:
        if isinstance(v, str):
            return redact_secrets(v)[0].replace("[REDACTED_SECRET]", REDACTED)
        if isinstance(v, dict):
            return {k: _walk(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [_walk(x) for x in v]
        return v

    return _walk(cleaned)


# ----------------------------------------------------------------- file lock

@contextlib.contextmanager
def file_lock(path: Path, timeout: float = 30.0) -> Iterator[None]:
    """Cross-process exclusive lock on ``path`` (msvcrt / fcntl)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+b")
    deadline = time.monotonic() + timeout
    try:
        if os.name == "nt":
            import msvcrt
            while True:
                try:
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() > deadline:
                        raise TimeoutError(f"lock busy: {path}") from None
                    time.sleep(0.01)
            try:
                yield
            finally:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            while True:
                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() > deadline:
                        raise TimeoutError(f"lock busy: {path}") from None
                    time.sleep(0.01)
            try:
                yield
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    finally:
        fh.close()


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


# ----------------------------------------------------------------- journal

@dataclass(frozen=True)
class JournalVerdict:
    ok: bool
    entries: int
    head: str
    reason: str = ""
    bad_seq: int | None = None


class JournalError(RuntimeError):
    pass


def _entry_hash(entry: dict) -> str:
    return sha256_bytes(canonical({k: v for k, v in entry.items() if k != "hash"}))


class Journal:
    def __init__(self, root: str | os.PathLike, *, clock: Callable[[], str] = utc_now):
        self.root = Path(root)
        self.path = self.root / "journal.jsonl"
        self.head_path = self.root / "journal.head"
        self.lock_path = self.root / "journal.lock"
        self._clock = clock

    # .......................................................... write
    def _last(self) -> tuple[int, str]:
        if not self.head_path.exists():
            if self.path.exists() and self.path.stat().st_size:
                raise JournalError("journal.head is missing while the journal has entries")
            return 0, GENESIS
        head = json.loads(self.head_path.read_text(encoding="utf-8"))
        return int(head["seq"]), str(head["hash"])

    def append(self, kind: str, payload: Any) -> str:
        if not isinstance(kind, str) or not kind or len(kind) > 128:
            raise ValueError("journal kind must be a short non-empty string")
        with file_lock(self.lock_path):
            seq, prev = self._last()
            entry = {"seq": seq + 1, "ts": self._clock(), "kind": kind, "payload": redact(payload), "prev": prev}
            entry["hash"] = _entry_hash(entry)
            line = canonical(entry) + b"\n"
            with open(self.path, "ab") as fh:
                fh.write(line)
                fh.flush()
                os.fsync(fh.fileno())
            atomic_write_bytes(self.head_path, canonical({"seq": entry["seq"], "hash": entry["hash"]}))
            return entry["hash"]

    # .......................................................... read
    def _lines(self) -> list[bytes]:
        if not self.path.exists():
            return []
        return [ln for ln in self.path.read_bytes().split(b"\n") if ln.strip()]

    def entries(self, *, kind: str | None = None, goal_id: str | None = None, limit: int | None = None) -> list[dict]:
        out = []
        for raw in self._lines():
            try:
                e = json.loads(raw)
            except ValueError:
                continue
            if kind and e.get("kind") != kind and not str(e.get("kind", "")).startswith(kind + "."):
                continue
            if goal_id and not (isinstance(e.get("payload"), dict) and e["payload"].get("goal_id") == goal_id):
                continue
            out.append(e)
        return out[-limit:] if limit else out

    def head(self) -> str:
        return self._last()[1]

    def verify(self) -> JournalVerdict:
        prev = GENESIS
        seq = 0
        for raw in self._lines():
            try:
                e = json.loads(raw)
            except ValueError:
                return JournalVerdict(False, seq, prev, "unparseable line", seq + 1)
            if not isinstance(e, dict) or set(e) != {"seq", "ts", "kind", "payload", "prev", "hash"}:
                return JournalVerdict(False, seq, prev, "malformed entry", seq + 1)
            if e["seq"] != seq + 1:
                return JournalVerdict(False, seq, prev, f"sequence gap: expected {seq + 1}, got {e['seq']}", seq + 1)
            if e["prev"] != prev:
                return JournalVerdict(False, seq, prev, "broken chain (prev hash mismatch)", e["seq"])
            if _entry_hash(e) != e["hash"]:
                return JournalVerdict(False, seq, prev, "entry content was modified", e["seq"])
            prev, seq = e["hash"], e["seq"]
        try:
            head_seq, head_hash = self._last()
        except (JournalError, ValueError, KeyError) as exc:
            return JournalVerdict(False, seq, prev, f"head unreadable: {exc}")
        if (head_seq, head_hash) != (seq, prev):
            return JournalVerdict(False, seq, prev, f"head mismatch: head says seq {head_seq}, file ends at {seq}"
                                                    " (entries removed or appended outside the journal)")
        return JournalVerdict(True, seq, prev)


__all__ = ["GENESIS", "Journal", "JournalError", "JournalVerdict", "atomic_write_bytes", "canonical", "file_lock",
           "redact", "sha256_bytes", "sha256_file", "utc_now"]
