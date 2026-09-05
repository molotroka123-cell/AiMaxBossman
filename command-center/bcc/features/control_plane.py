"""TZ-08 §2.5 (OBS-03, UX-05) — CEO Control Plane: один машиночитаемый снимок.

`GET /api/control-plane` собирает ТОЛЬКО durable-источники (одинаково после
рестарта): организация (`bossman_v3.organization.control_plane.snapshot`, если
фича включена), очередь движка (`task_runs` по статусам), казначейство (остаток
жёсткого потолка Fable из ledger-файла + конверты организации + burn-rate по
`task_runs.cost_usd` за час), флот (пока не подключён к bcc — честно `enabled=false`),
SLO (гистограмм задержек ещё нет — `NOT_IMPLEMENTED`, не пустая «зелёная» таблица)
и `attention` — всё, что ждёт владельца, старое сверху. Кэш 2 с.
"""
from __future__ import annotations

import asyncio
import time
from datetime import timedelta
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Request

from ..db import approvals as approvals_t, task_runs as runs_t, utcnow
from . import Feature

router = APIRouter()
CACHE_SECONDS = 2.0


async def _queue(svc) -> dict[str, int]:
    async with svc.db.session() as s:
        res = await s.execute(sa.select(runs_t.c.status, sa.func.count()).group_by(runs_t.c.status))
        counts = {str(r[0]): int(r[1]) for r in res.fetchall()}
    return {k: counts.get(k, 0) for k in ("queued", "leased", "running", "waiting_approval")} | counts


async def _burn_rate(svc) -> float:
    since = utcnow() - timedelta(hours=1)
    async with svc.db.session() as s:
        total = (await s.execute(sa.select(sa.func.coalesce(sa.func.sum(runs_t.c.cost_usd), 0.0)).where(
            runs_t.c.finished_at.isnot(None), runs_t.c.finished_at >= since))).scalar()
    return float(total or 0.0)


def _fable() -> dict[str, Any]:
    from .. import fable_cap
    if not fable_cap.LEDGER_AVAILABLE:
        return {"status": "UNAVAILABLE", "reason": getattr(fable_cap, "LEDGER_PROBLEM", "")[:200]}
    try:
        budget = fable_cap.canonical_budget()
        remaining = float(budget.remaining())
        cap = getattr(budget, "total_usd", None)
        return {"status": "OK", "remaining_usd": round(remaining, 6),
                "cap_usd": float(cap) if cap is not None else None, "ledger": str(getattr(budget, "path", ""))}
    except Exception as exc:  # noqa: BLE001 — снимок не должен падать из-за казначейства
        return {"status": "ERROR", "reason": f"{type(exc).__name__}: {exc}"[:200]}


async def _attention(svc, org: dict[str, Any] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    async with svc.db.session() as s:
        rows = (await s.execute(sa.select(approvals_t).where(approvals_t.c.status == "pending")
                                .order_by(approvals_t.c.created_at.asc()).limit(200))).fetchall()
    for r in rows:
        m = r._mapping
        out.append({"kind": f"approval:{m['kind']}", "ref": f"approval#{m['id']}",
                    "since": m["created_at"].isoformat() if m["created_at"] else "",
                    "why": str(m.get("preview") or "")[:200], "task_id": m.get("task_id")})
    if org and org.get("enabled", True):
        for w in org.get("waiting_approval") or []:
            out.append({"kind": "org:waiting_approval", "ref": f"work#{w.get('work_id', '')}",
                        "since": str(w.get("updated_at") or ""), "why": str(w.get("reason") or w.get("last_reason") or "")[:200]})
        for b in org.get("blocked") or []:
            out.append({"kind": "org:blocked", "ref": f"work#{b.get('work_id', '')}",
                        "since": str(b.get("updated_at") or ""), "why": str(b.get("reason") or b.get("last_reason") or "")[:200]})
        for a in org.get("failing_agents") or []:
            out.append({"kind": "org:failing_agent", "ref": f"agent#{a.get('agent_id', '')}", "since": "",
                        "why": f"reliability {a.get('reliability', '?')}"})
    out.sort(key=lambda x: (x["since"] == "", x["since"]))
    return out


async def build(svc) -> dict[str, Any]:
    organization: dict[str, Any]
    org_service = getattr(svc, "organization", None)
    if org_service is None:
        organization = {"enabled": False, "reason": getattr(svc, "organization_reason", "organization disabled")}
    else:
        snap = await asyncio.to_thread(org_service.runtime.snapshot)
        organization = {"enabled": True, **snap.to_dict()}
    queue, burn = await _queue(svc), await _burn_rate(svc)
    fable = await asyncio.to_thread(_fable)
    remaining = fable.get("remaining_usd")
    eta_h = (remaining / burn) if (isinstance(remaining, (int, float)) and burn > 0) else None
    return {
        "now": utcnow().isoformat(),
        "organization": organization,
        "queue": queue,
        "treasury": {"fable": fable, "envelopes": organization.get("treasury") if organization.get("enabled") else {},
                     "burn_rate_usd_per_h": round(burn, 6), "eta_exhaustion_hours": eta_h},
        "fleet": {"enabled": False, "nodes": [], "placements": [],
                  "reason": "fleet control plane lives in bossman_v3.fleet and is not wired into Command Center yet"},
        "slo": {"status": "NOT_IMPLEMENTED", "routes": []},
        "attention": await _attention(svc, organization),
    }


@router.get("/control-plane")
async def control_plane(request: Request) -> dict[str, Any]:
    svc = request.app.state.svc
    cache = getattr(svc, "_control_plane_cache", None)
    now = time.monotonic()
    if cache and now - cache[0] < CACHE_SECONDS:
        return cache[1]
    body = await build(svc)
    svc._control_plane_cache = (now, body)
    return body


FEATURE = Feature(name="control_plane", router=router)
