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
writes the same records atomically to one JSON file so the refusal survives a
restart. Neither stores measurements, prompts or costs — only opaque digests.
"""
from __future__ import annotations

import hashlib
import json
import os
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
    """In-memory single-use ledger. Bounded: oldest records fall out first."""

    def __init__(self, capacity: int = 10_000):
        self.capacity = max(1, int(capacity))
        self._lock = RLock()
        self._records: dict[str, str] = {}

    def _load(self) -> dict[str, str]:
        return self._records

    def _store(self, key: str, consumer: str) -> None:
        self._records[key] = consumer
        while len(self._records) > self.capacity:
            self._records.pop(next(iter(self._records)))

    def consume(self, key: str, consumer: str) -> str | None:
        """``None`` = the evidence may be used; otherwise the refusal reason."""
        with self._lock:
            owner = self._load().get(key)
            if owner is None:
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
    """File-backed ledger: the refusal survives a process restart.

    One JSON object written through a temp file and ``os.replace``. A corrupt or
    unreadable file is treated as empty *for reads* but never silently deleted;
    the write below rebuilds it. That is fail-open only for records this process
    never saw, which is the same exposure a fresh install has.
    """

    def __init__(self, path, capacity: int = 10_000):
        super().__init__(capacity)
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict[str, str]:
        if not self.path.exists():
            self._records = {}
            return self._records
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        self._records = {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}
        return self._records

    def _store(self, key: str, consumer: str) -> None:
        super()._store(key, consumer)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._records, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)

    def reset(self) -> None:
        super().reset()
        try:
            self.path.unlink()
        except OSError:
            pass


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
