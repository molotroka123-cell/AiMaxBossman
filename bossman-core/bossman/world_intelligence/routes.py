from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..perimeter import SCOPE_CHAT, require_scope
from ..world_intelligence.subsystem import PythiaWorldSubsystem, get_pythia


# Pythia — источник знания только для чтения: минимальный скоуп chat (Stage 6),
# на уровне роутера, чтобы ни одна ручка не осталась без auth. Ни один маршрут
# world_intelligence не мутирует состояние — admin-скоуп тут не нужен.
router = APIRouter(prefix="/world_intelligence", tags=["world-intelligence"],
                   dependencies=[Depends(require_scope(SCOPE_CHAT))])


# ---------- Pythia health ----------

class HealthOut(BaseModel):
    status: str
    detail: str | None = None
    latency_ms: int | None = None


@router.get("/health", response_model=HealthOut, summary="Pythia World Intelligence health")
async def world_health(
    pythia: PythiaWorldSubsystem = Depends(get_pythia),
) -> HealthOut:
    data = await pythia.health()
    if data is None:
        return HealthOut(status="offline", detail="Pythia not reachable")
    return HealthOut(status="online", detail=data.get("detail"))


# ---------- agent/view (main machine-readable endpoint) ----------

class AgentViewOut(BaseModel):
    summary: str
    domains: list[str]
    events_by_domain: dict[str, int]
    event_count: int
    predictions: list[dict[str, Any]]
    market_watch: dict[str, Any]
    source: str
    timestamp: float


@router.get(
    "/agent/view",
    response_model=AgentViewOut,
    summary="Pythia agent view — machine-readable intelligence snapshot",
)
async def world_agent_view(
    pythia: PythiaWorldSubsystem = Depends(get_pythia),
) -> AgentViewOut:
    data = await pythia.agent_view()
    if data is None:
        # Return empty-but-valid structure when Pythia offline (fail-soft)
        return AgentViewOut(
            summary="",
            domains=[],
            events_by_domain={},
            event_count=0,
            predictions=[],
            market_watch={},
            source="pythia",
            timestamp=0.0,
        )
    return AgentViewOut(**data)


# ---------- predictions ----------

@router.get("/predictions", summary="Pythia predictions")
async def world_predictions(
    pythia: PythiaWorldSubsystem = Depends(get_pythia),
) -> dict[str, Any]:
    data = await pythia.predictions()
    if data is None:
        return {"predictions": [], "detail": "Pythia offline or unavailable"}
    return data


# ---------- world ----------

@router.get("/world", summary="Pythia world state")
async def world_world(
    pythia: PythiaWorldSubsystem = Depends(get_pythia),
) -> dict[str, Any]:
    data = await pythia.world()
    if data is None:
        return {"world": {}, "detail": "Pythia offline or unavailable"}
    return data


# ---------- health score ----------

@router.get("/health-score", summary="Pythia health score")
async def world_health_score(
    pythia: PythiaWorldSubsystem = Depends(get_pythia),
) -> dict[str, Any]:
    data = await pythia.health_score()
    if data is None:
        return {"health_score": None, "detail": "Pythia offline or unavailable"}
    return data


# ---------- state ----------

@router.get("/state", summary="Pythia state")
async def world_state(
    pythia: PythiaWorldSubsystem = Depends(get_pythia),
) -> dict[str, Any]:
    data = await pythia.state()
    if data is None:
        return {"state": {}, "detail": "Pythia offline or unavailable"}
    return data


# ---------- state/stream ----------

@router.get("/state/stream", summary="Pythia state stream")
async def world_state_stream(
    pythia: PythiaWorldSubsystem = Depends(get_pythia),
) -> dict[str, Any]:
    data = await pythia.state_stream()
    if data is None:
        return {"state": [], "detail": "Pythia offline or unavailable"}
    return data