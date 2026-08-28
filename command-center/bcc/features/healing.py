"""Feature 14 — Self-Healing.

Поверх готовой bcc/v2/recovery.RecoveryPolicy: обнаружение падений endpoint'ов
моделей (окно сетевых ошибок), degraded→recovered цикл, generic report-API для
других подсистем (browser/terminal). Лимит попыток → эскалация человеку.
"""
from __future__ import annotations

import json
import time

import sqlalchemy as sa
from fastapi import APIRouter, Request

from ..db import models as models_t, recovery_attempts as rec_t, settings_kv, utcnow
from ..v2.recovery import RecoveryPolicy
from . import Feature

RULES_KEY = "healing.rules"
router = APIRouter()

# окно ошибок в памяти: model_id → [(ts, sig)]
_error_window: dict[int, list[float]] = {}
_attempts: dict[str, list[float]] = {}   # target → метки времени попыток


async def _rules(svc) -> dict:
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(settings_kv.c.value_enc)
                               .where(settings_kv.c.key == RULES_KEY))).first()
    if row and row[0]:
        try:
            return json.loads(svc.vault.decrypt(row[0]))
        except Exception:
            pass
    return {"window_seconds": 300, "error_threshold": 3, "attempt_limit": 3}


async def _attempt(svc, target_kind: str, target_id, failure: str, action: str,
                   status: str = "started", detail: dict | None = None) -> int:
    async with svc.db.session() as s:
        res = await s.execute(sa.insert(rec_t).values(
            target_kind=target_kind, target_id=target_id if isinstance(target_id, int) else None,
            failure=failure[:500], action=action, status=status,
            detail=detail or {}, created_at=utcnow()))
        rid = int(res.inserted_primary_key[0])
        await s.commit()
    ev = {"started": "recovery.started", "completed": "recovery.completed",
          "escalated": "recovery.escalated"}.get(status, "recovery.started")
    await svc.bus.emit(ev, attempt_id=rid, target=f"{target_kind}:{target_id}",
                       failure=failure[:200], action=action)
    return rid


def _within_limit(target: str, rules: dict) -> bool:
    now = time.monotonic()
    window = rules.get("window_seconds", 300)
    marks = [t for t in _attempts.get(target, []) if now - t < window]
    marks.append(now)
    _attempts[target] = marks
    return len(marks) <= rules.get("attempt_limit", 3)


async def _on_failure(svc):
    async def on_failure(task, run_id, error):
        rules = await _rules(svc)
        if "network" not in error.lower() and "не ответил" not in error and "нет связи" not in error:
            return
        # найдём модель агента
        async with svc.db.session() as s:
            from ..db import agents as agents_t
            agent = (await s.execute(sa.select(agents_t.c.model_id).where(
                agents_t.c.id == task["agent_id"]))).first()
        model_id = agent._mapping["model_id"] if agent else None
        if not model_id:
            return
        now = time.monotonic()
        window = rules.get("window_seconds", 300)
        marks = [t for t in _error_window.get(model_id, []) if now - t < window]
        marks.append(now)
        _error_window[model_id] = marks
        if len(marks) < rules.get("error_threshold", 3):
            return
        # порог: re-check модели
        target = f"model:{model_id}"
        if not _within_limit(target, rules):
            await _attempt(svc, "model", model_id, error, "escalate", "escalated")
            await svc.approvals.create(kind="healing_escalation",
                                       preview=f"Модель {model_id} не восстанавливается")
            return
        await _attempt(svc, "model", model_id, error, "retry", "started")
        try:
            health = await svc.registry.check_model(model_id)
        except Exception:
            health = {"status": "error"}
        if health.get("status") != "online":
            async with svc.db.session() as s:
                await s.execute(sa.update(models_t).where(models_t.c.id == model_id).values(
                    status="error", status_detail="degraded: endpoint недоступен"))
                await s.commit()
            await svc.bus.emit("model.degraded", id=model_id)
    return on_failure


async def _tick(svc):
    """Degraded-модели: периодический re-check; online → recovery.completed."""
    async with svc.db.session() as s:
        degraded = (await s.execute(sa.select(models_t.c.id).where(
            models_t.c.status == "error",
            models_t.c.status_detail.like("degraded%")))).fetchall()
    for r in degraded:
        mid = r._mapping["id"]
        try:
            health = await svc.registry.check_model(mid)
        except Exception:
            continue
        if health.get("status") == "online":
            _error_window.pop(mid, None)
            _attempts.pop(f"model:{mid}", None)
            await _attempt(svc, "model", mid, "endpoint восстановлен", "retry", "completed")


@router.post("/healing/report")
async def report(request: Request):
    """Единая точка: другие подсистемы (browser/terminal) сообщают о сбое."""
    svc = request.app.state.svc
    body = await request.json()
    rules = await _rules(svc)
    target_kind = body.get("target_kind", "component")
    target_id = body.get("target_id")
    target = f"{target_kind}:{target_id}"
    action = {"browser": "restart_component", "terminal": "restart_component"}.get(
        target_kind, "retry")
    if not _within_limit(target, rules):
        rid = await _attempt(svc, target_kind, target_id, body.get("failure", ""),
                             "escalate", "escalated")
        await svc.approvals.create(kind="healing_escalation",
                                   preview=f"{target} не восстанавливается")
        return {"attempt_id": rid, "status": "escalated"}
    rid = await _attempt(svc, target_kind, target_id, body.get("failure", ""), action, "started")
    return {"attempt_id": rid, "status": "started", "action": action}


@router.get("/healing/attempts")
async def attempts(request: Request, limit: int = 100):
    svc = request.app.state.svc
    async with svc.db.session() as s:
        rows = (await s.execute(sa.select(rec_t).order_by(rec_t.c.id.desc())
                                .limit(min(limit, 200)))).fetchall()
    return [dict(r._mapping) for r in rows]


@router.get("/healing/rules")
async def get_rules(request: Request):
    return await _rules(request.app.state.svc)


@router.patch("/healing/rules")
async def patch_rules(request: Request):
    svc = request.app.state.svc
    body = await request.json()
    rules = await _rules(svc)
    rules.update(body or {})
    enc = svc.vault.encrypt(json.dumps(rules))
    async with svc.db.session() as s:
        await s.execute(sa.delete(settings_kv).where(settings_kv.c.key == RULES_KEY))
        await s.execute(sa.insert(settings_kv).values(key=RULES_KEY, value_enc=enc))
        await s.commit()
    return rules


async def _setup(svc):
    svc.engine.add_hook("on_failure", await _on_failure(svc))


FEATURE = Feature(name="healing", router=router, setup=_setup, tick=_tick, tick_seconds=30.0)
