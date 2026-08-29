"""Stage 8 — read-only HTTP-статус песочницы. Никаких мутаций через этот роутер."""
from __future__ import annotations

from fastapi import APIRouter

from . import sandbox_enabled
from .subsystem import MANAGER

router = APIRouter(prefix="/sandbox", tags=["sandbox"])


@router.get("/status")
async def status() -> dict:
    return {
        "enabled": sandbox_enabled(),
        "runtime": MANAGER.runtime.name,
        "sessions": len(MANAGER.sessions),
        "active_leases": len(MANAGER.resources.active()),
    }


@router.get("/sessions")
async def sessions() -> list[dict]:
    return [
        {"id": s.id, "state": s.state.value,
         "risk": (s.risk.level.value if s.risk else None),
         "policy": (s.policy.mode.value if s.policy else None),
         "isolation": (s.policy.isolation_tier.value if s.policy else None),
         "lease": s.lease_id, "error": s.error}
        for s in MANAGER.sessions.values()
    ]
