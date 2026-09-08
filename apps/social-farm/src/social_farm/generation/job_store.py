"""Durable state for browser-driven media generation jobs.

The store is deliberately provider-agnostic.  Site adapters may disappear,
restart, or require owner intervention; the durable record remains the source
of truth for Bossman orchestration and post-state evidence.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Iterable
import json
import os
import tempfile
import time

from .higgsfield_browser_contracts import BrowserGenerationState, TERMINAL_STATES


@dataclass(slots=True)
class GenerationJobRecord:
    job_id: str
    mission_id: str
    provider: str
    state: BrowserGenerationState
    attempt: int = 0
    created_at_epoch_s: float = 0.0
    updated_at_epoch_s: float = 0.0
    provider_job_id: str | None = None
    output_path: str | None = None
    artifact_sha256: str | None = None
    safe_message: str = ""
    last_error_class: str | None = None
    owner_action_required: bool = False

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    def to_json(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["state"] = self.state.value
        return payload

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "GenerationJobRecord":
        data = dict(payload)
        data["state"] = BrowserGenerationState(data["state"])
        return cls(**data)


class GenerationJobStore:
    """Tiny crash-safe JSON store using atomic replace.

    This is intentionally not a queue implementation.  Scheduling lives in the
    orchestrator; this class only preserves observable state across restarts.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()

    def _read_all(self) -> dict[str, GenerationJobRecord]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"generation job store is unreadable: {self.path}") from exc
        if not isinstance(raw, dict) or raw.get("version") != 1:
            raise RuntimeError("unsupported generation job store format")
        jobs = raw.get("jobs", {})
        if not isinstance(jobs, dict):
            raise RuntimeError("generation job store jobs must be an object")
        return {job_id: GenerationJobRecord.from_json(data) for job_id, data in jobs.items()}

    def _write_all(self, jobs: dict[str, GenerationJobRecord]) -> None:
        payload = {
            "version": 1,
            "updated_at_epoch_s": time.time(),
            "jobs": {job_id: record.to_json() for job_id, record in jobs.items()},
        }
        fd, temp_name = tempfile.mkstemp(prefix=self.path.name + ".", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        finally:
            try:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
            except OSError:
                pass

    def create(self, *, job_id: str, mission_id: str, provider: str) -> GenerationJobRecord:
        now = time.time()
        with self._lock:
            jobs = self._read_all()
            if job_id in jobs:
                return jobs[job_id]
            record = GenerationJobRecord(
                job_id=job_id,
                mission_id=mission_id,
                provider=provider,
                state=BrowserGenerationState.CREATED,
                created_at_epoch_s=now,
                updated_at_epoch_s=now,
            )
            jobs[job_id] = record
            self._write_all(jobs)
            return record

    def get(self, job_id: str) -> GenerationJobRecord | None:
        with self._lock:
            return self._read_all().get(job_id)

    def save(self, record: GenerationJobRecord) -> GenerationJobRecord:
        record.updated_at_epoch_s = time.time()
        with self._lock:
            jobs = self._read_all()
            jobs[record.job_id] = record
            self._write_all(jobs)
        return record

    def list(self, *, include_terminal: bool = True) -> list[GenerationJobRecord]:
        with self._lock:
            records = list(self._read_all().values())
        records.sort(key=lambda item: (item.created_at_epoch_s, item.job_id))
        if include_terminal:
            return records
        return [record for record in records if not record.terminal]

    def resumable(self) -> Iterable[GenerationJobRecord]:
        return self.list(include_terminal=False)


__all__ = ["GenerationJobRecord", "GenerationJobStore"]
