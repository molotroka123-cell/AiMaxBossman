"""Stage 10 — read-only статус фабрики. Никаких мутаций и публикаций отсюда."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..perimeter import SCOPE_ADMIN, require_scope

from .subsystem import FACTORY

# Периметр: статус фабрики и ПОЛНЫЙ diff патча — только admin-устройству.
router = APIRouter(prefix="/dev-factory", tags=["dev-factory"],
                   dependencies=[Depends(require_scope(SCOPE_ADMIN))])


@router.get("/jobs")
async def jobs() -> list[dict]:
    return [{"id": j.id, "task": j.task, "state": j.state.value,
             "attempts_used": j.budget.used, "attempts_max": j.budget.max_attempts,
             "files": list(j.patch.files) if j.patch else [], "error": j.error}
            for j in FACTORY.jobs.values()]


@router.get("/jobs/{job_id}/patch")
async def patch(job_id: str) -> dict:
    from .. import errors
    job = FACTORY.jobs.get(job_id)
    if job is None:
        raise errors.NotFound(f"unknown dev job: {job_id}")
    if job.patch is None:
        return {"id": job.id, "state": job.state.value, "patch": None}
    return {"id": job.id, "state": job.state.value, "files": list(job.patch.files),
            "sha256": job.patch.sha256, "evidence": job.patch.evidence_summary,
            "diff": job.patch.diff}
