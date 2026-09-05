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
import os
import time
from datetime import timedelta
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Request

from ..db import (agents as agents_t, approvals as approvals_t, events as events_t,
                  task_runs as runs_t, tasks as tasks_t, tool_calls as tool_calls_t, utcnow)
from . import Feature

router = APIRouter()
CACHE_SECONDS = 2.0
# TRUTH-003 §14: ограниченное хранение событий — по возрасту и по числу строк
RETENTION_DAYS = int(os.environ.get("BOSSMAN_EVENTS_RETENTION_DAYS", "14"))
RETENTION_MAX_ROWS = int(os.environ.get("BOSSMAN_EVENTS_MAX_ROWS", "200000"))


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    xs = sorted(values)
    k = max(0, min(len(xs) - 1, int(round(q * (len(xs) - 1)))))
    return round(float(xs[k]), 3)


async def latency(svc) -> dict[str, Any]:
    """Базовые задержки из durable-таблиц: исполнение (tool_calls.duration_ms), верификация
    (события verification.result), миссии/задачи (created_at → updated_at у completed). Только
    измеренное; пусто → None, а не 0."""
    async with svc.db.session() as s:
        exec_ms = [float(r[0]) for r in (await s.execute(sa.select(tool_calls_t.c.duration_ms).where(
            tool_calls_t.c.duration_ms.isnot(None)).order_by(tool_calls_t.c.id.desc()).limit(2000))).fetchall()]
        ver_rows = (await s.execute(sa.select(events_t.c.data).where(events_t.c.kind == "verification.result")
                                    .order_by(events_t.c.id.desc()).limit(2000))).fetchall()
        task_rows = (await s.execute(sa.select(tasks_t.c.created_at, tasks_t.c.updated_at).where(
            tasks_t.c.status == "completed").order_by(tasks_t.c.id.desc()).limit(2000))).fetchall()
    ver_ms = [float(r[0].get("verification_ms")) for r in ver_rows if isinstance(r[0], dict) and r[0].get("verification_ms") is not None]
    task_s = [(r[1] - r[0]).total_seconds() for r in task_rows if r[0] and r[1]]
    return {"execution_ms": {"n": len(exec_ms), "p50": _pct(exec_ms, 0.5), "p95": _pct(exec_ms, 0.95)},
            "verification_ms": {"n": len(ver_ms), "p50": _pct(ver_ms, 0.5), "p95": _pct(ver_ms, 0.95)},
            "task_completion_s": {"n": len(task_s), "p50": _pct(task_s, 0.5), "p95": _pct(task_s, 0.95)}}


async def _tick(svc) -> None:
    """Ретеншн событий: раз в 10 минут, ограничено по возрасту и строкам."""
    removed = await svc.bus.prune(max_age_days=RETENTION_DAYS, max_rows=RETENTION_MAX_ROWS)
    if any(removed.values()):
        await svc.bus.emit("events.pruned", **removed, retention_days=RETENTION_DAYS, max_rows=RETENTION_MAX_ROWS)


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


async def _fleet(org_service) -> dict[str, Any]:
    """§15: durable-сводка флота, когда фича organization включена с BOSSMAN_V3_FLEET."""
    if org_service is None or getattr(org_service, "fleet", None) is None:
        return {"enabled": False, "nodes": [], "active_leases": [], "queue_depth": 0, "blocked_work": [],
                "reason": "fleet is off (BOSSMAN_V3_ENABLED + BOSSMAN_V3_ORGANIZATION + BOSSMAN_V3_FLEET)",
                "remote_transport_production_ready": False, "node_auth_production_ready": False}
    return await asyncio.to_thread(org_service.fleet_summary)



# ---------- строки владельца (TRUTH-003 §20) ----------

OWNER_ROWS_LIMIT = 50

# Состояние действия — по УЛИКАМ, а не по намерению. COMPLETE выдаётся только
# когда сработал канонический finalizer (событие task.finalized), даже если в
# таблице уже стоит completed: «зелёная галочка» не опережает доказательство.
ACTION_STATES = ("PLACED", "DISPATCHED", "EXECUTED", "OBSERVED", "VERIFIED", "UNVERIFIED", "COMPLETE",
                 "BLOCKED", "FAILED", "STOPPED")


def _action_state(task: dict, run: dict | None, calls: dict, finalized: bool) -> tuple[str, str]:
    """(состояние, почему). Возвращает состояние из ACTION_STATES и причину для владельца."""
    status = str(task.get("status") or "")
    if status == "completed":
        if finalized:
            return "COMPLETE", ""
        return ("VERIFIED" if calls.get("verified") else "UNVERIFIED",
                "нет следа финализатора текущего запуска: task.finalized не найдено")
    if status == "blocked":
        return "BLOCKED", str((task.get("meta") or {}).get("blocked_reason")
                              or (run or {}).get("error") or "исполнитель недоступен")[:200]
    if status == "failed":
        return "FAILED", str((run or {}).get("error") or "")[:200]
    if status == "stopped":
        return "STOPPED", str((run or {}).get("error") or "остановлено оператором")[:200]
    if status in ("waiting_approval", "paused"):
        return "BLOCKED", "ожидает решения владельца" if status == "waiting_approval" else "на паузе"
    if calls.get("verified"):
        return "VERIFIED", ""
    if calls.get("observed"):
        return "OBSERVED", "эффект наблюдался, проверка ещё не подтвердила"
    if calls.get("executed"):
        return "EXECUTED", "инструмент вызван; вызов — не доказательство эффекта"
    if status == "running" or (run or {}).get("status") in ("leased", "running"):
        return "DISPATCHED", ""
    return "PLACED", ""


async def owner_rows(svc, limit: int = OWNER_ROWS_LIMIT) -> list[dict[str, Any]]:
    """КТО / ГДЕ / КАКАЯ МОДЕЛЬ / ЧТО / СОСТОЯНИЕ / ПОЧЕМУ ЗАБЛОКИРОВАНО / ЦЕНА / ВНИМАНИЕ.

    Одна строка на задачу, из durable-источников. Ни промптов, ни секретов:
    в `what` идёт только заголовок задачи.
    """
    node = ""
    org_service = getattr(svc, "organization", None)
    if org_service is not None and getattr(org_service, "fleet", None) is not None:
        node = str(getattr(org_service, "node_id", "") or "")
    async with svc.db.session() as s:
        trows = (await s.execute(
            sa.select(tasks_t.c.id, tasks_t.c.title, tasks_t.c.status, tasks_t.c.agent_id,
                      tasks_t.c.updated_at, tasks_t.c.meta, agents_t.c.name.label("agent_name"),
                      agents_t.c.model_id)
            .select_from(tasks_t.outerjoin(agents_t, agents_t.c.id == tasks_t.c.agent_id))
            .order_by(tasks_t.c.updated_at.desc()).limit(limit))).fetchall()
        tasks = [dict(r._mapping) for r in trows]
        ids = [t["id"] for t in tasks]
        runs: dict[int, dict] = {}
        cost: dict[int, float | None] = {}
        calls: dict[int, dict[str, int]] = {}
        approvals: dict[int, str] = {}
        finalized: set[int] = set()
        if ids:
            for r in (await s.execute(sa.select(runs_t).where(runs_t.c.task_id.in_(ids))
                                      .order_by(runs_t.c.id.asc()))).fetchall():
                m = dict(r._mapping)
                runs[m["task_id"]] = m                       # последний прогон задачи
                previous = cost.get(m["task_id"], 0.0)
                measured = m.get("cost_usd")
                cost[m["task_id"]] = (None if previous is None or measured is None
                                      else previous + float(measured))
            for r in (await s.execute(
                    sa.select(tool_calls_t.c.task_id, tool_calls_t.c.run_id, tool_calls_t.c.status, tool_calls_t.c.verified,
                              tool_calls_t.c.observed_at).where(tool_calls_t.c.task_id.in_(ids)))).fetchall():
                m = r._mapping
                if m["run_id"] != (runs.get(m["task_id"]) or {}).get("id"):
                    continue
                c = calls.setdefault(int(m["task_id"]), {"executed": 0, "observed": 0, "verified": 0})
                if str(m["status"]) == "executed":
                    c["executed"] += 1
                if m["observed_at"] is not None:
                    c["observed"] += 1
                if bool(m["verified"]):
                    c["verified"] += 1
            for r in (await s.execute(
                    sa.select(approvals_t.c.task_id, approvals_t.c.preview)
                    .where(approvals_t.c.status == "pending",
                           approvals_t.c.task_id.in_(ids)))).fetchall():
                approvals.setdefault(int(r._mapping["task_id"]), str(r._mapping["preview"] or "")[:200])
            for r in (await s.execute(sa.select(events_t.c.data)
                                      .where(events_t.c.kind == "task.finalized"))).fetchall():
                data = r._mapping["data"] or {}
                tid = data.get("task_id") if isinstance(data, dict) else None
                if (tid in runs and data.get("run_id") == runs[tid]["id"]):
                    finalized.add(int(tid))
    out: list[dict[str, Any]] = []
    for t in tasks:
        run = runs.get(t["id"])
        c = calls.get(t["id"], {"executed": 0, "observed": 0, "verified": 0})
        state, why = _action_state(t, run, c, t["id"] in finalized)
        if t["id"] in approvals and (not why or state == "BLOCKED"):
            # конкретное «что именно ждёт решения» полезнее общей формулировки
            why = approvals[t["id"]] or why
        out.append({
            "task_id": t["id"],
            "who": t.get("agent_name") or "—",                       # КТО
            "where": node or "локальный хост",                        # ГДЕ
            "model": (run or {}).get("model_alias") or "—",           # КАКАЯ МОДЕЛЬ
            "what": str(t.get("title") or "")[:200] or f"задача #{t['id']}",
            "action_state": state,                                    # СОСТОЯНИЕ ДЕЙСТВИЯ
            "why_blocked": why,                                       # ПОЧЕМУ ЗАБЛОКИРОВАНО
            "cost_usd": round(cost[t["id"]], 6) if cost.get(t["id"]) is not None else None,
            "attention": bool(t["id"] in approvals or state in ("BLOCKED", "FAILED", "UNVERIFIED")),
            "effects": c,
            "finalized": t["id"] in finalized,
            "updated_at": t["updated_at"].isoformat() if t.get("updated_at") else "",
        })
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
        "fleet": await _fleet(org_service),
        "slo": {"status": "NOT_IMPLEMENTED", "routes": []},
        "latency": await latency(svc),
        "retention": {"events_days": RETENTION_DAYS, "events_max_rows": RETENTION_MAX_ROWS},
        "attention": await _attention(svc, organization),
        "owner_view": {"rows": await owner_rows(svc), "states": list(ACTION_STATES),
                       "rule": "COMPLETE только после канонического finalize_task (событие task.finalized)"},
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


@router.get("/observability/trace/{trace_id}")
async def trace_chain(trace_id: str, request: Request) -> dict[str, Any]:
    """Цепочка событий одного действия по trace_id (без промптов и секретов — их в шине нет)."""
    events = await request.app.state.svc.bus.by_trace(trace_id)
    return {"trace_id": trace_id, "events": [{"kind": e["kind"], "ts": str(e["ts"]), "data": e.get("data")} for e in events]}


FEATURE = Feature(name="control_plane", router=router, tick=_tick, tick_seconds=600.0)
