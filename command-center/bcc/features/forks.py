"""Feature 05 — Replay / Fork Session.

Поверх готовой bcc/v2/replay.fork_checkpoint и таблицы checkpoints (её пишет
engine на каждый шаг). Форк = новая задача с сообщениями выбранного чекпоинта
(+ override инструкции/агента/модели). Оригинал не меняется. Lineage.
"""
from __future__ import annotations

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Request

from ..db import (agents as agents_t, checkpoints as cp_t, session_forks as forks_t,
                  tasks as tasks_t, task_runs as runs_t, utcnow)
from ..v2.replay import ForkRequest, fork_checkpoint
from . import Feature
from .router import check_forced_model

router = APIRouter()


async def _force_model_hook(svc):
    """Форк с другой моделью: meta.force_model_id → pick_model возвращает её.

    F-016: принудительная модель проходит ту же политику, что и авто-выбор
    (облако fail-closed, цена, способности). Отказ → None (модель агента /
    роутер) + событие router.force_refused с причиной; молча «взять что
    указали» нельзя.
    """
    async def pick_model(task, agent):
        meta = task.get("meta") if isinstance(task.get("meta"), dict) else {}
        mid = meta.get("force_model_id")
        if not mid:
            return None
        reasons = await check_forced_model(svc, mid, meta=meta, agent=agent,
                                           kind=task.get("kind") or "generic")
        if reasons:
            await svc.bus.emit("router.force_refused", task_id=task.get("id"),
                               model_id=mid, reason="; ".join(reasons)[:500])
            return None
        return {"model_id": int(mid)}
    return pick_model


async def _agent_row(svc, agent_id) -> dict:
    if not agent_id:
        return {}
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(agents_t).where(agents_t.c.id == int(agent_id)))).first()
    return dict(row._mapping) if row is not None else {}


async def _setup(svc):
    svc.engine.add_hook("pick_model", await _force_model_hook(svc))


@router.get("/runs/{run_id}/checkpoints")
async def list_checkpoints(run_id: int, request: Request):
    svc = request.app.state.svc
    async with svc.db.session() as s:
        rows = (await s.execute(sa.select(cp_t.c.id, cp_t.c.step, cp_t.c.note, cp_t.c.created_at)
                                .where(cp_t.c.run_id == run_id).order_by(cp_t.c.step))).fetchall()
    return [dict(r._mapping) for r in rows]


@router.post("/runs/{run_id}/fork")
async def fork(run_id: int, request: Request):
    svc = request.app.state.svc
    body = await request.json()
    async with svc.db.session() as s:
        run = (await s.execute(sa.select(runs_t).where(runs_t.c.id == run_id))).first()
        if run is None:
            raise HTTPException(404, {"message": "прогон не найден"})
        run = dict(run._mapping)
        src_task = (await s.execute(sa.select(tasks_t).where(
            tasks_t.c.id == run["task_id"]))).first()
        src_task = dict(src_task._mapping)
        cp_id = body.get("checkpoint_id")
        if cp_id:
            cp = (await s.execute(sa.select(cp_t).where(cp_t.c.id == cp_id))).first()
        else:
            cp = (await s.execute(sa.select(cp_t).where(cp_t.c.run_id == run_id)
                                  .order_by(cp_t.c.step.desc()).limit(1))).first()
    if cp is None:
        raise HTTPException(400, {"message": "нет чекпоинта для форка",
                                  "hint": "у прогона ещё нет шагов"})
    cp = dict(cp._mapping)
    req = ForkRequest(original_run_id=run_id, checkpoint_step=cp["step"],
                      new_agent_id=body.get("agent_id"), new_model_id=body.get("model_id"),
                      instruction_override=body.get("instruction"))
    forked = fork_checkpoint({"messages": cp["messages"]}, req)

    meta = dict(src_task.get("meta") or {})
    meta["fork_of_run"] = run_id
    if body.get("model_id"):
        try:
            meta["force_model_id"] = int(body["model_id"])
        except (TypeError, ValueError):
            raise HTTPException(400, {"message": "model_id должен быть целым числом"})
        # F-016: модель проверяется политикой ДО создания задачи/прогона —
        # отказ роутера не должен оставлять после себя форк-задачу.
        agent = await _agent_row(svc, body.get("agent_id") or src_task.get("agent_id"))
        reasons = await check_forced_model(
            svc, meta["force_model_id"], meta=meta, agent=agent,
            kind=src_task.get("kind") or "generic")
        if reasons:
            raise HTTPException(403, {
                "message": "модель отклонена политикой роутера",
                "model_id": meta["force_model_id"], "reasons": reasons,
                "hint": "разрешите облако явно (cloud_allowed=true) или выберите местную модель"})
    async with svc.db.session() as s:
        res = await s.execute(sa.insert(tasks_t).values(
            title=f"Форк #{run_id}: {src_task.get('title', '')}"[:300],
            prompt=src_task["prompt"], agent_id=body.get("agent_id") or src_task["agent_id"],
            status="draft", kind=src_task.get("kind", "generic"),
            parent_task_id=src_task["id"], mission_id=src_task.get("mission_id"),
            meta=meta, created_at=utcnow(), updated_at=utcnow()))
        new_task_id = int(res.inserted_primary_key[0])
        await s.commit()
    # запускаем форк с checkpoint выбранного шага
    new_run_id = await svc.engine.enqueue(new_task_id, checkpoint=forked)
    async with svc.db.session() as s:
        await s.execute(sa.insert(forks_t).values(
            source_run_id=run_id, checkpoint_id=cp["id"], new_task_id=new_task_id,
            changes={"step": cp["step"], "agent_override": body.get("agent_id"),
                     "model_override": body.get("model_id"),
                     "instruction": body.get("instruction")},
            created_at=utcnow()))
        await s.commit()
    await svc.bus.emit("session.forked", fork_id=new_run_id, source_run_id=run_id,
                       new_task_id=new_task_id)
    return {"new_task_id": new_task_id, "new_run_id": new_run_id, "from_step": cp["step"]}


@router.get("/forks")
async def lineage(request: Request, task_id: int):
    """Дерево: исходная задача → её форки (по parent_task_id, рекурсивно)."""
    svc = request.app.state.svc

    async def children(tid: int) -> list[dict]:
        async with svc.db.session() as s:
            rows = (await s.execute(sa.select(tasks_t.c.id, tasks_t.c.title, tasks_t.c.status)
                                    .where(tasks_t.c.parent_task_id == tid))).fetchall()
        out = []
        for r in rows:
            node = dict(r._mapping)
            node["forks"] = await children(node["id"])
            out.append(node)
        return out

    async with svc.db.session() as s:
        root = (await s.execute(sa.select(tasks_t.c.id, tasks_t.c.title, tasks_t.c.status)
                                .where(tasks_t.c.id == task_id))).first()
    if root is None:
        raise HTTPException(404, {"message": "задача не найдена"})
    node = dict(root._mapping)
    node["forks"] = await children(task_id)
    return node


FEATURE = Feature(name="forks", router=router, setup=_setup)
