"""Job manager backing the control contract (jobs.create/status/cancel).

BOSSMAN is the control plane; this application is the workload. It therefore
owns its own job lifecycle and exposes it, but imports nothing from BOSSMAN.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Awaitable, Callable

from ..errors import VisionError
from ..logging_setup import get_logger
from ..secretstore import scrub

log = get_logger("jobs")


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL = {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}


@dataclass
class Job:
    id: str
    type: str
    params: dict
    status: JobStatus = JobStatus.QUEUED
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: dict | None = None
    error: str | None = None
    error_code: str | None = None
    artifacts: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "status": self.status.value,
            "params": self.params,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "result": self.result,
            "error": self.error,
            "error_code": self.error_code,
            "artifacts": list(self.artifacts),
        }

    def to_record(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "params": self.params,
            "result": self.result,
            "error": self.error,
        }


JobHandler = Callable[[Job], Awaitable[dict]]


class UnknownJobType(VisionError):
    pass


class JobManager:
    def __init__(self, *, max_history: int = 200, on_change: Callable[[Job], None] | None = None) -> None:
        self._handlers: dict[str, JobHandler] = {}
        self._jobs: dict[str, Job] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._order: list[str] = []
        self._max_history = max_history
        self._on_change = on_change

    # ------------------------------------------------------------ registry
    def register(self, job_type: str, handler: JobHandler) -> None:
        self._handlers[job_type] = handler

    @property
    def job_types(self) -> list[str]:
        return sorted(self._handlers)

    # -------------------------------------------------------------- create
    def create(self, job_type: str, params: dict | None = None) -> Job:
        if job_type not in self._handlers:
            raise UnknownJobType(f"unknown job type {job_type!r}; known: {', '.join(self.job_types)}")
        job = Job(id=uuid.uuid4().hex, type=job_type, params=dict(params or {}))
        self._jobs[job.id] = job
        self._order.append(job.id)
        self._notify(job)
        self._trim()
        task = asyncio.create_task(self._run(job), name=f"job:{job_type}:{job.id[:8]}")
        self._tasks[job.id] = task
        return job

    async def _run(self, job: Job) -> None:
        job.status = JobStatus.RUNNING
        job.started_at = datetime.now(timezone.utc)
        self._notify(job)
        try:
            result = await self._handlers[job.type](job)
            job.result = result
            job.status = JobStatus.SUCCEEDED
        except asyncio.CancelledError:
            job.status = JobStatus.CANCELLED
            job.error = "cancelled"
            job.error_code = "cancelled"
            job.finished_at = datetime.now(timezone.utc)
            self._notify(job)
            raise
        except VisionError as exc:
            job.status = JobStatus.FAILED
            job.error = exc.safe_message
            job.error_code = exc.code
            log.warning("job %s failed", job.id)
        except Exception as exc:  # noqa: BLE001 - boundary of an untrusted handler
            job.status = JobStatus.FAILED
            job.error = scrub(f"{type(exc).__name__}: {exc}")
            job.error_code = "internal_error"
            log.warning("job %s crashed", job.id)
        finally:
            if job.finished_at is None:
                job.finished_at = datetime.now(timezone.utc)
                self._notify(job)

    # -------------------------------------------------------- introspection
    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list(self, limit: int = 50) -> list[Job]:
        ids = list(reversed(self._order))[:limit]
        return [self._jobs[i] for i in ids if i in self._jobs]

    def artifacts(self, job_id: str | None = None) -> list[dict]:
        if job_id is not None:
            job = self._jobs.get(job_id)
            return list(job.artifacts) if job else []
        out: list[dict] = []
        for job_id_ in reversed(self._order):
            out.extend(self._jobs[job_id_].artifacts)
        return out

    # -------------------------------------------------------------- cancel
    async def cancel(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job is None or job.status in TERMINAL:
            return False
        task = self._tasks.get(job_id)
        if task is None or task.done():
            return False
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        return True

    async def shutdown(self, timeout: float = 10.0) -> None:
        """Cancel everything still running and wait for the tasks to finish."""
        pending = [task for task in self._tasks.values() if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.wait(pending, timeout=timeout)

    # ------------------------------------------------------------ internal
    def _notify(self, job: Job) -> None:
        if self._on_change is not None:
            try:
                self._on_change(job)
            except Exception:  # pragma: no cover - persistence must not break jobs
                log.warning("job persistence failed for %s", job.id)

    def _trim(self) -> None:
        while len(self._order) > self._max_history:
            oldest = self._order.pop(0)
            job = self._jobs.get(oldest)
            if job is not None and job.status in TERMINAL:
                self._jobs.pop(oldest, None)
                self._tasks.pop(oldest, None)
            elif job is not None:
                # Still running: keep it, but do not let it block trimming.
                self._order.append(oldest)
                break
