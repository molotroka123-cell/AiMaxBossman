"""Autonomy control plane API — `/api/autonomy/*` (owner token, like every feature).

Goals with evidence, the constitution / lease / journal status and the release
panel (Apply / Reject / Revise / Confirm released). Apply never merges or
pushes: it records the user's decision bound to the candidate SHA + diff hash
and returns the exact fast-forward and rollback commands. Logic lives in
bcc.autonomy.service; design: docs/autonomy/AUTONOMY_CONTRACT.md.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..autonomy.goals import GoalError, GoalNotFound, TransitionError
from ..autonomy.service import AutonomyService, ReleaseRefused
from . import Feature

router = APIRouter()


class BoundIn(BaseModel):
    sha: str = Field(pattern=r"^[0-9a-f]{40}([0-9a-f]{24})?$")
    diff_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    note: str = Field(default="", max_length=2000)


class NoteIn(BaseModel):
    note: str = Field(default="", max_length=2000)


def service(svc: Any) -> AutonomyService:
    auto = getattr(svc, "autonomy", None)
    if auto is None:
        auto = svc.autonomy = AutonomyService(svc.settings.data_dir)
    return auto


def _svc(request: Request) -> AutonomyService:
    return service(request.app.state.svc)


def _call(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except GoalNotFound:
        raise HTTPException(404, "goal not found") from None
    except (ReleaseRefused, TransitionError) as exc:
        raise HTTPException(409, str(exc)) from None
    except GoalError as exc:
        raise HTTPException(400, str(exc)) from None


@router.get("/autonomy/status")
async def status(request: Request):
    return _svc(request).status()


@router.get("/autonomy/goals")
async def goals(request: Request):
    return {"items": _svc(request).list_goals()}


@router.get("/autonomy/goals/{goal_id}")
async def goal(goal_id: str, request: Request):
    return _call(_svc(request).goal_view, goal_id)


@router.post("/autonomy/goals/{goal_id}/apply")
async def apply(goal_id: str, body: BoundIn, request: Request):
    return _call(_svc(request).apply, goal_id, body.sha, body.diff_sha256, body.note)


@router.post("/autonomy/goals/{goal_id}/confirm")
async def confirm(goal_id: str, body: BoundIn, request: Request):
    return _call(_svc(request).confirm_released, goal_id, body.sha, body.diff_sha256)


@router.post("/autonomy/goals/{goal_id}/reject")
async def reject(goal_id: str, body: NoteIn, request: Request):
    return _call(_svc(request).reject, goal_id, body.note)


@router.post("/autonomy/goals/{goal_id}/revise")
async def revise(goal_id: str, body: NoteIn, request: Request):
    return _call(_svc(request).revise, goal_id, body.note)


@router.get("/autonomy/journal")
async def journal(request: Request, limit: int = 100, goal_id: str | None = None):
    limit = max(1, min(int(limit), 1000))
    return {"items": _svc(request).journal.entries(goal_id=goal_id, limit=limit)}


@router.get("/autonomy/journal/verify")
async def journal_verify(request: Request):
    v = _svc(request).journal.verify()
    return {"ok": v.ok, "entries": v.entries, "head": v.head, "reason": v.reason, "bad_seq": v.bad_seq}


FEATURE = Feature(name="autonomy", router=router)
