"""Deep Fix gate (F4.1 «план верификации привязан ДО патча»), флаг OFF.

BOSSMAN_DEEP_FIX_ENABLED=1 включает; иначе хуки — no-op (ни одной записи в meta).

Что делает при включении:
  before_run     — если у задачи есть review.evidence, вычисляет plan_hash
                   (канонический JSON evidence+criteria) и ПРИВЯЗЫВАЕТ его к задаче
                   при первом прогоне (meta.deep_fix.plan_hash, bound_run_id);
  gate_completion — если текущий план не совпадает с привязанным (кто-то
                   «подвинул ворота» после старта: другой файл, другая строка),
                   завершение запрещено: эскалация человеку (waiting_approval,
                   approval kind=review_escalation) с обоими хэшами. Сам
                   вердикт по свежим доказательствам остаётся у review_gate —
                   этот гейт только следит, что доказывается ТО, что обещали.
Верификатор (bcc/v2/verification) не читает транскрипт модели — изоляция F4.2
обеспечивается конструкцией, здесь ничего не дублируется.
"""
from __future__ import annotations

import hashlib
import json
import os

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Request

from ..db import tasks as tasks_t, utcnow
from . import Feature

FLAG = "BOSSMAN_DEEP_FIX_ENABLED"
router = APIRouter()


def enabled() -> bool:
    return os.environ.get(FLAG, "").strip().lower() in ("1", "true", "yes")


def plan_hash(review: dict | None) -> str:
    """Хэш плана верификации: только структурированные ожидания + критерий."""
    review = review or {}
    canon = {"evidence": review.get("evidence") or [], "criteria": review.get("criteria") or ""}
    return hashlib.sha256(json.dumps(canon, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


async def _meta(svc, task_id: int) -> dict:
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(tasks_t.c.meta).where(tasks_t.c.id == task_id))).first()
    return (row._mapping["meta"] if row and isinstance(row._mapping["meta"], dict) else {}) or {}


async def _set_meta(svc, task_id: int, meta: dict) -> None:
    async with svc.db.session() as s:
        await s.execute(sa.update(tasks_t).where(tasks_t.c.id == task_id).values(
            meta=meta, updated_at=utcnow()))
        await s.commit()


async def _before_run(svc):
    async def before_run(task, run):
        if not enabled():
            return None
        meta = await _meta(svc, task["id"])
        review = meta.get("review")
        if not review or not review.get("evidence"):
            return None                      # без плана привязывать нечего
        if meta.get("deep_fix", {}).get("plan_hash"):
            return None                      # уже привязан первым прогоном
        meta["deep_fix"] = {"plan_hash": plan_hash(review),
                            "bound_run_id": (run or {}).get("id"),
                            "bound_at": utcnow().isoformat()}
        await _set_meta(svc, task["id"], meta)
        await svc.bus.emit("deep_fix.plan_bound", task_id=task["id"],
                           plan_hash=meta["deep_fix"]["plan_hash"][:16])
        return None
    return before_run


async def _gate(svc):
    async def gate_completion(task, run_id, answer):
        if not enabled():
            return None
        meta = await _meta(svc, task["id"])
        bound = (meta.get("deep_fix") or {}).get("plan_hash")
        if not bound:
            return None
        current = plan_hash(meta.get("review"))
        if current == bound:
            return None                      # план тот же — решает review_gate
        reason = (f"план верификации изменён после привязки (goalpost moved): "
                  f"bound={bound[:12]}… current={current[:12]}…")
        await svc.approvals.create(kind="review_escalation", task_id=task["id"], run_id=run_id,
                                   preview=f"Deep Fix: {reason}\nНужно решение человека: "
                                           f"принять новый план или вернуть исходный.")
        await svc.bus.emit("deep_fix.plan_mismatch", task_id=task["id"], run_id=run_id)
        return {"verdict": "fail", "requeue": False, "status": "waiting_approval",
                "reasons": reason}
    return gate_completion


@router.get("/deep_fix/status")
async def status(request: Request, task_id: int):
    svc = request.app.state.svc
    meta = await _meta(svc, task_id)
    if not meta:
        raise HTTPException(404, {"message": "задача не найдена"})
    return {"enabled": enabled(), "bound": meta.get("deep_fix") or {},
            "current_plan_hash": plan_hash(meta.get("review"))}


async def _setup(svc):
    svc.engine.add_hook("before_run", await _before_run(svc))
    svc.engine.add_hook("gate_completion", await _gate(svc))


FEATURE = Feature(name="deep_fix", router=router, setup=_setup)
