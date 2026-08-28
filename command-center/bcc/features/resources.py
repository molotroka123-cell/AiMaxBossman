"""Feature 12 — Resource Brain.

Поверх готовой bcc/v2/resource_brain.plan_memory: before_run-хук оценивает RAM
задачи против свободного бюджета (метрики + активные резервы), применяет политику
(balanced/performance/low_power), резервирует/освобождает, событие resource.*.
Crash-страховка: tick освобождает просроченные резервы.
"""
from __future__ import annotations

import json

import sqlalchemy as sa
from fastapi import APIRouter, Request

from ..db import (models as models_t, resource_reservations as res_t, settings_kv,
                  system_metrics as metrics_t, utcnow)
from ..v2.resource_brain import Reservation, ResourcePlan, ResourceSnapshot, plan_memory
from . import Feature

POLICY_KEY = "resources.policy"
router = APIRouter()
# enforce=false по умолчанию: Resource Brain НЕ блокирует обычные задачи (иначе на
# машине с малым свободным RAM встанет вся очередь). Управление памятью включается
# осознанно — политикой enforce=true или флагом задачи meta.resource_managed.
DEFAULT_POLICY = {"policy": "balanced", "reserve_floor_mb": 16000,
                  "total_override_mb": None, "enforce": False}


async def _policy(svc) -> dict:
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(settings_kv.c.value_enc)
                               .where(settings_kv.c.key == POLICY_KEY))).first()
    if row and row[0]:
        try:
            return {**DEFAULT_POLICY, **json.loads(svc.vault.decrypt(row[0]))}
        except Exception:
            pass
    return dict(DEFAULT_POLICY)


async def _snapshot(svc, policy: dict) -> ResourceSnapshot:
    async with svc.db.session() as s:
        metric = (await s.execute(sa.select(metrics_t).order_by(
            metrics_t.c.id.desc()).limit(1))).first()
        held = (await s.execute(sa.select(res_t).where(res_t.c.status == "held"))).fetchall()
    total = policy.get("total_override_mb") or (
        metric._mapping["ram_total_mb"] if metric else 128000)
    used = metric._mapping["ram_used_mb"] if metric else 0
    reservations = [Reservation(owner=f"{r._mapping['holder_kind']}:{r._mapping['holder_id']}",
                                memory_mb=int(r._mapping["amount_mb"]),
                                kind=r._mapping["holder_kind"])
                    for r in held]
    return ResourceSnapshot(total_memory_mb=int(total), used_system_mb=int(used),
                            reserve_floor_mb=int(policy.get("reserve_floor_mb", 16000)),
                            reservations=reservations)


async def _estimate_mb(svc, model_id: int | None) -> int:
    """Оценка RAM модели: bench.ram_mb → иначе эвристика по context_window (грубо)."""
    if not model_id:
        return 0
    async with svc.db.session() as s:
        m = (await s.execute(sa.select(models_t).where(models_t.c.id == model_id))).first()
    if m is None or m._mapping["kind"] == "cloud":
        return 0
    bench = m._mapping["bench"] if isinstance(m._mapping["bench"], dict) else {}
    if bench.get("ram_mb"):
        return int(bench["ram_mb"])
    return max(2000, int((m._mapping["context_window"] or 8192) / 8192 * 4000))


async def _reserve(svc, owner_kind: str, owner_id: int, memory_mb: int) -> int:
    from datetime import timedelta
    async with svc.db.session() as s:
        res = await s.execute(sa.insert(res_t).values(
            kind="ram", amount_mb=memory_mb, holder_kind=owner_kind, holder_id=owner_id,
            status="held", created_at=utcnow(),
            expires_at=utcnow() + timedelta(minutes=15)))   # crash-страховка
        rid = int(res.inserted_primary_key[0])
        await s.commit()
    await svc.bus.emit("resource.reserved", reservation_id=rid, amount_mb=memory_mb,
                       holder=f"{owner_kind}:{owner_id}")
    return rid


async def _release(svc, reservation_id: int) -> None:
    async with svc.db.session() as s:
        await s.execute(sa.update(res_t).where(res_t.c.id == reservation_id).values(
            status="released", released_at=utcnow()))
        await s.commit()
    await svc.bus.emit("resource.released", reservation_id=reservation_id)


async def _before_run(svc):
    async def before_run(task, run):
        policy = await _policy(svc)
        meta = task.get("meta") if isinstance(task.get("meta"), dict) else {}
        if not (policy.get("enforce") or meta.get("resource_managed")):
            return None                       # управление памятью выключено — не мешаем
        # модель агента
        async with svc.db.session() as s:
            from ..db import agents as agents_t
            agent = (await s.execute(sa.select(agents_t.c.model_id).where(
                agents_t.c.id == task["agent_id"]))).first()
        model_id = agent._mapping["model_id"] if agent else None
        need = await _estimate_mb(svc, model_id)
        if need <= 0:
            return None                       # облако/нет модели — ресурс не резервируем
        snap = await _snapshot(svc, policy)
        plan: ResourcePlan = plan_memory(snap, need, policy=policy["policy"])
        if plan.allowed:
            if plan.unload:                   # performance: выгрузить простаивающие
                for owner in plan.unload:
                    await svc.bus.emit("resource.released", holder=owner, reason="unload idle")
            rid = await _reserve(svc, "task", task["id"], need)
            async with svc.db.session() as s:
                from ..db import task_runs as runs_t
                await s.execute(sa.update(runs_t).where(runs_t.c.id == run["id"]).values(
                    reservation_id=rid))
                await s.commit()
            return None
        await svc.bus.emit("resource.denied", holder=f"task:{task['id']}",
                           amount_mb=need, reason="; ".join(plan.explanation)[:200])
        return {"defer": 30, "reason": "; ".join(plan.explanation)[:200]}
    return before_run


async def _after_run(svc):
    async def after_run(task_id, run_id, status):
        async with svc.db.session() as s:
            from ..db import task_runs as runs_t
            row = (await s.execute(sa.select(runs_t.c.reservation_id).where(
                runs_t.c.id == run_id))).first()
        if row and row._mapping["reservation_id"]:
            await _release(svc, row._mapping["reservation_id"])
    return after_run


async def _tick(svc):
    """Просроченные held-резервы → expired + release (crash-страховка)."""
    now = utcnow()
    async with svc.db.session() as s:
        stale = (await s.execute(sa.select(res_t.c.id).where(
            res_t.c.status == "held", res_t.c.expires_at.isnot(None),
            res_t.c.expires_at < now))).fetchall()
    for r in stale:
        async with svc.db.session() as s:
            await s.execute(sa.update(res_t).where(res_t.c.id == r._mapping["id"]).values(
                status="expired", released_at=utcnow()))
            await s.commit()
        await svc.bus.emit("resource.released", reservation_id=r._mapping["id"], expired=True)


async def _setup(svc):
    svc.engine.add_hook("before_run", await _before_run(svc))
    svc.engine.add_hook("after_run", await _after_run(svc))


@router.get("/resources")
async def resources(request: Request):
    svc = request.app.state.svc
    policy = await _policy(svc)
    snap = await _snapshot(svc, policy)
    async with svc.db.session() as s:
        held = (await s.execute(sa.select(res_t).where(res_t.c.status == "held"))).fetchall()
    return {"total_mb": snap.total_memory_mb, "used_mb": snap.used_system_mb,
            "reserved_mb": snap.reserved_mb, "available_mb": snap.available_for_new_mb,
            "reserve_floor_mb": snap.reserve_floor_mb, "policy": policy["policy"],
            "reservations": [dict(r._mapping) for r in held]}


@router.post("/resources/policy")
async def set_policy(request: Request):
    svc = request.app.state.svc
    body = await request.json()
    policy = await _policy(svc)
    policy.update(body or {})
    enc = svc.vault.encrypt(json.dumps(policy))
    async with svc.db.session() as s:
        await s.execute(sa.delete(settings_kv).where(settings_kv.c.key == POLICY_KEY))
        await s.execute(sa.insert(settings_kv).values(key=POLICY_KEY, value_enc=enc))
        await s.commit()
    return policy


@router.post("/resources/estimate")
async def estimate(request: Request):
    svc = request.app.state.svc
    body = await request.json()
    mb = await _estimate_mb(svc, body.get("model_id"))
    return {"model_id": body.get("model_id"), "estimated_mb": mb}


@router.post("/resources/reserve")
async def manual_reserve(request: Request):
    svc = request.app.state.svc
    body = await request.json()
    rid = await _reserve(svc, body.get("owner_kind", "model"),
                         int(body.get("owner_id", 0)), int(body["memory_mb"]))
    return {"reservation_id": rid}


@router.post("/resources/release")
async def manual_release(request: Request):
    svc = request.app.state.svc
    body = await request.json()
    await _release(svc, int(body["reservation_id"]))
    return {"ok": True}


FEATURE = Feature(name="resources", router=router, setup=_setup, tick=_tick, tick_seconds=60.0)
