"""Stage 11 — AI Lab: инспекция траекторий + REST-роутер.

Только чтение raw trajectory; кандидаты/evals/exports через CandidateStore/
EvalRunner/Exporter. Никаких мутаций cloud_policy/агентов/провайдеров.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from .. import errors
from .candidates import CandidateStore
from .export import EvalRunner, Exporter, LocalTrainingAdapter

router = APIRouter(prefix="/api/lab")


def _store(request: Request) -> CandidateStore:
    svc = request.app.state.svc
    root = getattr(svc.settings, "ai_lab_dir", None) or Path(svc.settings.data_dir) / "ai_lab"
    return CandidateStore(root)


def _exporter(request: Request) -> Exporter:
    svc = request.app.state.svc
    root = Path(getattr(svc.settings, "ai_lab_dir", None) or
                Path(svc.settings.data_dir) / "ai_lab") / "exports"
    return Exporter(_store(request), root)


class DecideIn(BaseModel):
    approve: bool
    by: str = "owner"


class EvalIn(BaseModel):
    cases: list[dict] = Field(default_factory=list)
    model_alias: str = "bossman-fast"
    max_cases: int = 5


@router.get("/trajectories/{sandbox_id}")
async def inspect_trajectory(sandbox_id: str, request: Request):
    """Read-only инспекция raw-траектории из workspace песочницы."""
    svc = request.app.state.svc
    ws = Path(getattr(svc.settings, "sandbox_workspace", Path("_sandbox")))
    path = ws / sandbox_id / "trajectory.jsonl"
    if not path.is_file():
        raise errors.BossmanError(f"trajectory not found: {sandbox_id}",
                                  code=errors.ErrorCode.NOT_FOUND)
    from .candidates import load_trajectory
    events, sha, meta = load_trajectory(path)
    return {"sandbox_id": sandbox_id, "sha256": sha, **meta,
            "events_preview": events[:20]}     # превью; сырьё уходит только в candidates


@router.get("/candidates")
async def list_candidates(request: Request):
    return await _list(_store(request))


async def _list(store: CandidateStore):
    return store.list()


@router.post("/candidates/{trajectory_path:path}")
async def create_candidate(trajectory_path: str, request: Request):
    body = await request.json()
    cand = _store(request).create(trajectory_path,
                                  sandbox_id=str(body.get("sandbox_id", "unknown")))
    return {"id": cand.id, "state": cand.state, "samples": len(cand.samples),
            "reasons": list(cand.reasons)}


@router.post("/candidates/{candidate_id}/decide")
async def decide_candidate(candidate_id: str, body: DecideIn, request: Request):
    cand = _store(request).decide(candidate_id, approve=body.approve, by=body.by)
    return {"id": cand.id, "state": cand.state, "decided_by": cand.decided_by}


@router.post("/evals/run")
async def run_eval(body: EvalIn, request: Request):
    runner = EvalRunner(chat_fn=None, brain=getattr(request.app.state.svc, "brain", None))
    return runner.run(body.cases, model_alias=body.model_alias,
                      max_cases=max(0, body.max_cases))


@router.post("/exports/{candidate_id}/sft")
async def export_sft(candidate_id: str, request: Request):
    path = _exporter(request).export_sft(candidate_id)
    return {"path": str(path)}


@router.post("/exports/{candidate_id}/dpo")
async def export_dpo(candidate_id: str, request: Request):
    path = _exporter(request).export_dpo(candidate_id)
    return {"path": str(path)}


@router.post("/exports/{candidate_id}/launch_training")
async def launch_training(candidate_id: str, request: Request):
    """Демонстративно отказной путь: адаптер по умолчанию не сконфигурирован."""
    path = _exporter(request).launch_training(Path(f"{candidate_id}.sft.jsonl"))
    return {"armed": path}
