"""Runtime surface for the Bossman 1.5 autonomy kernel.

Read-only planning/status API over the same Bossman data root. This surface
does not execute tools, grant approvals, create accounts or change stable code.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from bossman_v3.autonomy_kernel import BossmanAutonomyKernel
from bossman_v3.resource_manager import RouteCandidate, RoutePolicy
from . import Feature

router = APIRouter(prefix="/v15/autonomy", tags=["v1.5"])


class CandidateBody(BaseModel):
    id: str = Field(min_length=1, max_length=200)
    quality_lcb: float = Field(ge=0, le=1)
    cost_usd: float | None = Field(default=None, ge=0)
    latency_ms: float | None = Field(default=None, ge=0)
    energy_wh: float | None = Field(default=None, ge=0)
    local: bool
    ram_estimate: int = Field(default=0, ge=0)
    health: str = "healthy"
    tools_ok: bool = True


class PlanBody(BaseModel):
    task_id: str = Field(min_length=1, max_length=200)
    task_class: str = Field(min_length=1, max_length=200)
    candidates: list[CandidateBody] = Field(min_length=1, max_length=64)
    required_roles: list[str] = Field(default_factory=list, max_length=8)
    max_team: int = Field(default=3, ge=1, le=8)
    min_quality_lcb: float = Field(default=0.70, ge=0, le=1)
    max_cost_usd: float | None = Field(default=None, ge=0)
    max_latency_ms: float | None = Field(default=None, ge=0)
    graph_refs: list[tuple[str, str]] = Field(default_factory=list, max_length=32)
    owner_input_required: bool = False


def _kernel(svc) -> BossmanAutonomyKernel:
    kernel = getattr(svc, "v15_autonomy", None)
    if kernel is None:
        kernel = BossmanAutonomyKernel(Path(svc.settings.data_dir) / "v1.5")
        svc.v15_autonomy = kernel
    return kernel


async def _setup(svc):
    svc.v15_autonomy = BossmanAutonomyKernel(Path(svc.settings.data_dir) / "v1.5")


@router.get("/status")
async def status(request: Request):
    kernel = _kernel(request.app.state.svc)
    return {
        "wired": True,
        "roles": kernel.society.candidates("*"),
        "graph": {"nodes": len(kernel.graph.nodes), "edges": len(kernel.graph.edges)},
        "principles": {
            "persistent_roles": True,
            "skill_memory": True,
            "temporal_operating_graph": True,
            "multi_objective_routing": True,
            "authority_source": "existing_bossman_policy",
        },
    }


@router.post("/plan")
async def plan(body: PlanBody, request: Request):
    kernel = _kernel(request.app.state.svc)
    policy = RoutePolicy(
        min_quality_lcb=body.min_quality_lcb,
        max_cost_usd=body.max_cost_usd,
        max_latency_ms=body.max_latency_ms,
        unknown_price_block=True,
    )
    candidates = [RouteCandidate(**row.model_dump()) for row in body.candidates]
    out = kernel.plan(
        task_id=body.task_id,
        task_class=body.task_class,
        route_candidates=candidates,
        route_policy=policy,
        graph_refs=[(a, b) for a, b in body.graph_refs],
        required_roles=tuple(body.required_roles),
        max_team=body.max_team,
        owner_input_required=body.owner_input_required,
    )
    return {
        "task_id": out.task_id,
        "task_class": out.task_class,
        "team": list(out.team),
        "route": {
            "candidate_id": out.route.candidate_id,
            "reason": out.route.reason,
            "score": out.route.score,
            "considered": list(out.route.considered),
        },
        "skill_refs": list(out.skill_refs),
        "memory_refs": list(out.memory_refs),
        "graph_context": out.graph_context,
        "owner_input_required": out.owner_input_required,
        "authoritative": False,
    }


FEATURE = Feature(name="v15_autonomy", router=router, setup=_setup)
