"""Job records, history, cancellation and disk accounting.

Job state lives in two places on purpose: an in-memory index for the running
process, and a `job.json` inside each job directory so history survives a
restart. The filesystem is the source of truth on reload.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .errors import DiskQuotaError, JobNotFoundError
from .paths import dir_size_bytes, resolve_within, safe_job_id

JOB_RECORD_NAME = "job.json"

QUEUED = "queued"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"
CANCELLED = "cancelled"
TIMED_OUT = "timed_out"

TERMINAL_STATES = {SUCCEEDED, FAILED, CANCELLED, TIMED_OUT}


@dataclass(slots=True)
class StageRecord:
    name: str
    status: str  # ok | failed | skipped | not_available
    started_at: float
    finished_at: float | None = None
    detail: dict = field(default_factory=dict)

    @property
    def duration_s(self) -> float | None:
        return None if self.finished_at is None else self.finished_at - self.started_at


@dataclass(slots=True)
class JobRecord:
    id: str
    kind: str  # design | import
    status: str
    created_at: float
    updated_at: float
    directory: str
    request: dict = field(default_factory=dict)
    stages: list[dict] = field(default_factory=list)
    result: dict | None = None
    error: dict | None = None
    cancel_requested: bool = False
    bytes_used: int = 0

    def as_dict(self) -> dict:
        d = asdict(self)
        d["terminal"] = self.status in TERMINAL_STATES
        return d


class JobStore:
    """Thread-safe job index over a jobs directory."""

    def __init__(self, jobs_dir: Path, *, job_quota_bytes: int, total_quota_bytes: int, max_retained: int = 500):
        self.jobs_dir = Path(jobs_dir)
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.job_quota_bytes = job_quota_bytes
        self.total_quota_bytes = total_quota_bytes
        self.max_retained = max_retained
        self._lock = threading.RLock()
        self._records: dict[str, JobRecord] = {}
        self._cancels: dict[str, threading.Event] = {}
        self.reload()

    # ------------------------------------------------------------- lifecycle
    def reload(self) -> None:
        with self._lock:
            self._records.clear()
            for child in sorted(self.jobs_dir.iterdir()) if self.jobs_dir.is_dir() else []:
                record_path = child / JOB_RECORD_NAME
                if not child.is_dir() or not record_path.is_file():
                    continue
                try:
                    data = json.loads(record_path.read_text(encoding="utf-8"))
                    data.pop("terminal", None)
                    self._records[data["id"]] = JobRecord(**data)
                except Exception:
                    continue

    def job_dir(self, job_id: str) -> Path:
        return resolve_within(self.jobs_dir, safe_job_id(job_id))

    def create(self, job_id: str, kind: str, request: dict) -> JobRecord:
        safe = safe_job_id(job_id)
        with self._lock:
            self._enforce_total_quota()
            directory = self.job_dir(safe)
            directory.mkdir(parents=True, exist_ok=True)
            now = time.time()
            record = JobRecord(
                id=safe,
                kind=kind,
                status=QUEUED,
                created_at=now,
                updated_at=now,
                directory=str(directory),
                request=request,
            )
            self._records[safe] = record
            self._cancels[safe] = threading.Event()
            self._persist(record)
            self._prune()
            return record

    def get(self, job_id: str) -> JobRecord:
        safe = safe_job_id(job_id)
        with self._lock:
            record = self._records.get(safe)
        if record is None:
            raise JobNotFoundError(f"job {safe!r} not found")
        return record

    def exists(self, job_id: str) -> bool:
        try:
            self.get(job_id)
            return True
        except (JobNotFoundError, Exception):
            return False

    def list(self, *, limit: int = 50, offset: int = 0, status: str | None = None) -> list[JobRecord]:
        with self._lock:
            records = sorted(self._records.values(), key=lambda r: r.created_at, reverse=True)
        if status:
            records = [r for r in records if r.status == status]
        return records[offset: offset + limit]

    def update(self, job_id: str, **changes) -> JobRecord:
        with self._lock:
            record = self.get(job_id)
            for key, value in changes.items():
                setattr(record, key, value)
            record.updated_at = time.time()
            record.bytes_used = dir_size_bytes(Path(record.directory))
            self._persist(record)
            return record

    def add_stage(self, job_id: str, stage: StageRecord) -> None:
        with self._lock:
            record = self.get(job_id)
            record.stages.append({
                "name": stage.name,
                "status": stage.status,
                "started_at": stage.started_at,
                "finished_at": stage.finished_at,
                "duration_s": stage.duration_s,
                "detail": stage.detail,
            })
            record.updated_at = time.time()
            self._persist(record)

    # ---------------------------------------------------------- cancellation
    def cancel_event(self, job_id: str) -> threading.Event:
        safe = safe_job_id(job_id)
        with self._lock:
            return self._cancels.setdefault(safe, threading.Event())

    def request_cancel(self, job_id: str) -> JobRecord:
        with self._lock:
            record = self.get(job_id)
            if record.status in TERMINAL_STATES:
                return record
            record.cancel_requested = True
            record.updated_at = time.time()
            self.cancel_event(record.id).set()
            self._persist(record)
            return record

    def is_cancelled(self, job_id: str) -> bool:
        return self.cancel_event(job_id).is_set()

    # ---------------------------------------------------------------- quota
    def check_job_quota(self, job_id: str) -> int:
        record = self.get(job_id)
        used = dir_size_bytes(Path(record.directory))
        if used > self.job_quota_bytes:
            raise DiskQuotaError(
                f"job {record.id!r} uses {used} bytes, above the per-job quota {self.job_quota_bytes}",
                detail={"used_bytes": used, "quota_bytes": self.job_quota_bytes},
            )
        return used

    def total_bytes(self) -> int:
        return dir_size_bytes(self.jobs_dir)

    def _enforce_total_quota(self) -> None:
        used = self.total_bytes()
        if used > self.total_quota_bytes:
            raise DiskQuotaError(
                f"jobs directory uses {used} bytes, above the total quota {self.total_quota_bytes}",
                detail={"used_bytes": used, "quota_bytes": self.total_quota_bytes},
            )

    def _prune(self) -> None:
        """Forget the oldest terminal jobs beyond the retention limit (index only)."""
        records = sorted(self._records.values(), key=lambda r: r.created_at)
        excess = len(records) - self.max_retained
        for record in records[:max(0, excess)]:
            if record.status in TERMINAL_STATES:
                self._records.pop(record.id, None)
                self._cancels.pop(record.id, None)

    # -------------------------------------------------------------- helpers
    def _persist(self, record: JobRecord) -> None:
        path = Path(record.directory) / JOB_RECORD_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(record.as_dict(), indent=2, sort_keys=True, default=str), encoding="utf-8")
        tmp.replace(path)

    def metrics(self) -> dict:
        with self._lock:
            records = list(self._records.values())
        by_status: dict[str, int] = {}
        for r in records:
            by_status[r.status] = by_status.get(r.status, 0) + 1
        return {
            "jobs_total": len(records),
            "jobs_by_status": by_status,
            "jobs_dir": str(self.jobs_dir),
            "disk_used_bytes": self.total_bytes(),
            "disk_quota_bytes": self.total_quota_bytes,
            "per_job_quota_bytes": self.job_quota_bytes,
            "max_retained": self.max_retained,
        }
