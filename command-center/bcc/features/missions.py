"""Feature 01+13 — Autopilot Missions + KPI.

Миссия — цель, а не задача: план → задачи → исполнение с лимитом воркеров,
прогресс по завершённым задачам, KPI по kpi_targets. Использует движок/очередь.
Пауза/резюм/стоп. Переживает рестарт (состояние — только в БД).
"""
from __future__ import annotations

import asyncio
import math

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Request

from ..db import (kpi_history as kpi_t, missions as missions_t, tasks as tasks_t, utcnow)
from ..v2.kpi import KPI, mission_progress
from ..mission_continuity import (ACTIVE, BINDING, TERMINAL, bindings, compile_plan,
                                 dispatch_state, plan_digest, projection)
from . import Feature

router = APIRouter()


def _plan_from_goal(goal: str, kpi_targets: dict) -> dict:
    """Детерминированный план: N research-задач из цели (без планировщика-модели).
    Если в системе есть агент-planner, оркестратор может заменить это вызовом модели."""
    import re
    m = re.search(r"(\d+)", goal or "")
    n = min(int(m.group(1)[:6]), 20) if m else 3
    tasks = [{"title": f"Задача {i + 1}", "kind": "research",
              "prompt": f"{goal}\n\nШаг {i + 1} из {n}: выполни свою часть и верни результат."}
             for i in range(n)]
    return {"milestones": [{"name": "Выполнение", "tasks": len(tasks)}], "tasks": tasks}


def _compile_plan(plan: dict) -> list[dict]:
    """Validate once, retain stable node IDs and dependencies in durable rows."""
    from ..v2.task_graph import GraphValidationError
    try:
        return compile_plan(plan)
    except (GraphValidationError, ValueError, TypeError) as exc:
        raise HTTPException(400, {"message": "план миссии не прошёл компиляцию",
                                  "errors": str(exc)[:2000]}) from exc


async def _insert_plan(session, mission_id: int, plan: dict) -> dict:
    """Only the caller commits: parent, binding and all children are atomic."""
    digest = plan_digest(plan)
    nodes = {}
    for t in _compile_plan(plan):
        meta = dict(t.get("meta") or {})
        meta[BINDING] = {"mission_id": mission_id, "node_id": t["node_id"], "plan_digest": digest}
        result = await session.execute(sa.insert(tasks_t).values(
            title=t.get("title", "задача"), prompt=t["prompt"],
            agent_id=t.get("agent_id"), status="draft", kind=t["kind"], meta=meta,
            mission_id=mission_id, created_at=utcnow(), updated_at=utcnow()))
        nodes[t["node_id"]] = int(result.inserted_primary_key[0])
    return {"version": 1, "plan_digest": digest, "nodes": nodes}


async def _create_tasks(svc, mission_id: int, plan: dict) -> None:
    """Idempotent repair for a legacy planning row, never duplicate its graph."""
    plan = {**plan, "tasks": _compile_plan(plan)}
    async with svc.db.session() as session:
        if svc.db.url.startswith("sqlite"):
            await session.execute(sa.text("BEGIN IMMEDIATE"))
        query = sa.select(missions_t).where(missions_t.c.id == mission_id)
        if not svc.db.url.startswith("sqlite"):
            query = query.with_for_update()
        row = (await session.execute(query)).first()
        if row is None:
            raise HTTPException(404, {"message": "миссия не найдена"})
        mission = dict(row._mapping)
        children = [dict(r._mapping) for r in (await session.execute(
            sa.select(tasks_t).where(tasks_t.c.mission_id == mission_id))).fetchall()]
        if children:
            _, state = bindings(mission, children)
            if state == "BOUND" and plan_digest(mission["plan"]) == plan_digest(plan):
                return
            raise HTTPException(409, {"message": "план уже материализован или требует миграции"})
        if mission["status"] not in {"draft", "planning", "queued"}:
            raise HTTPException(409, {"message": "нельзя заменить начатую миссию"})
        meta = dict(mission.get("meta") or {})
        meta[BINDING] = await _insert_plan(session, mission_id, plan)
        await session.execute(sa.update(missions_t).where(missions_t.c.id == mission_id).values(
            plan=plan, meta=meta, status="queued", updated_at=utcnow()))
        await session.commit()


async def _mission(svc, mission_id: int) -> dict:
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(missions_t).where(missions_t.c.id == mission_id))).first()
    if row is None:
        raise HTTPException(404, {"message": "миссия не найдена"})
    return dict(row._mapping)


async def _mission_tasks(svc, mission_id: int) -> list[dict]:
    async with svc.db.session() as s:
        rows = (await s.execute(sa.select(tasks_t).where(
            tasks_t.c.mission_id == mission_id).order_by(tasks_t.c.id))).fetchall()
    return [dict(r._mapping) for r in rows]


# ---------- KPI ----------

async def _kpi_current(svc, mission_id: int) -> dict[str, float]:
    async with svc.db.session() as s:
        rows = (await s.execute(sa.select(kpi_t.c.key, kpi_t.c.value)
                                .where(kpi_t.c.mission_id == mission_id)
                                .order_by(kpi_t.c.id))).fetchall()
    cur: dict[str, float] = {}
    for r in rows:
        cur[r._mapping["key"]] = r._mapping["value"]     # последняя строка ключа = текущее
    return cur


async def _apply_kpi(svc, mission_id: int, key: str, delta: float,
                     source_task_id: int | None = None) -> dict:
    mission = await _mission(svc, mission_id)
    if source_task_id is not None:
        async with svc.db.session() as s:
            owner = (await s.execute(sa.select(tasks_t.c.mission_id)
                                     .where(tasks_t.c.id == source_task_id))).first()
        if owner is None or owner._mapping["mission_id"] != mission_id:
            raise HTTPException(409, {"message": "задача-источник не принадлежит этой миссии",
                                      "hint": "нельзя менять KPI чужой миссии"})
    cur = await _kpi_current(svc, mission_id)
    new_val = cur.get(key, 0.0) + delta
    async with svc.db.session() as s:
        await s.execute(sa.insert(kpi_t).values(
            mission_id=mission_id, key=key, value=new_val, delta=delta,
            source_task_id=source_task_id, ts=utcnow()))
        await s.commit()
    cur[key] = new_val
    progress = _kpi_progress(mission.get("kpi_targets") or {}, cur)
    await svc.bus.emit("mission.progress", mission_id=mission_id, key=key,
                       value=new_val, progress=progress)
    return {"key": key, "value": new_val, "progress": progress}


def _kpi_progress(targets: dict, current: dict) -> float:
    kpis = [KPI(key=k, label=k, target=float(t), current=current.get(k, 0.0))
            for k, t in targets.items() if t]
    return round(mission_progress(kpis), 4)


# ---------- жизненный цикл миссии (tick) ----------

async def _tick(svc):
    # A process-local optimization; durable enqueue admission below remains the
    # authority when several services share the same database.
    lock = getattr(svc, "_mission_tick_lock", None)
    if lock is None:
        lock = svc._mission_tick_lock = asyncio.Lock()
    async with lock:
        await _tick_locked(svc)


async def _tick_locked(svc):
    """Держит ≤max_workers активных задач running-миссий; двигает прогресс;
    завершает/останавливает по правилам. Реентерабельно и идемпотентно."""
    async with svc.db.session() as s:
        running = (await s.execute(sa.select(missions_t).where(
            missions_t.c.status == "running"))).fetchall()
    for row in running:
        m = await _mission(svc, int(row._mapping["id"]))
        if m["status"] != "running":
            continue
        tasks = await _mission_tasks(svc, m["id"])
        _, binding_state = bindings(m, tasks)
        if binding_state not in {"BOUND", "LEGACY_FLAT"}:
            await _finish_mission(svc, m["id"], "failed", binding_state)
            continue
        if not tasks:
            continue
        active = [t for t in tasks if t["status"] in ACTIVE]
        done = [t for t in tasks if t["status"] == "completed"]
        failed = [t for t in tasks if t["status"] in ("failed", "stopped", "cancelled")]
        if failed:
            await _finish_mission(svc, m["id"], "failed", "required child did not complete successfully")
            continue
        # таймаут миссии
        if m["duration_minutes"] and m["started_at"]:
            if (utcnow() - m["started_at"]).total_seconds() >= m["duration_minutes"] * 60:
                await _finish_mission(svc, m["id"], "failed", "истёк срок миссии")
                continue
        # запуск новых до лимита воркеров
        free = max(0, (m["max_workers"] or 1) - len(active))
        drafts = [t for t in tasks if t["status"] == "draft"
                  and dispatch_state(m, tasks, t["id"]).ready][:free]
        for t in drafts:
            await svc.engine.enqueue(t["id"], only_if_draft=True)
        progress = len(done) / len(tasks)
        if abs(progress - (m["progress"] or 0.0)) > 1e-9:
            await _set_progress(svc, m["id"], progress)
            await svc.bus.emit("mission.progress", mission_id=m["id"], progress=round(progress, 4))
        if len(done) == len(tasks):
            await _finish_mission(svc, m["id"], "completed", "все задачи завершены")


async def _set_progress(svc, mission_id: int, progress: float) -> None:
    async with svc.db.session() as s:
        await s.execute(sa.update(missions_t).where(
            missions_t.c.id == mission_id, missions_t.c.status == "running").values(
            progress=progress, updated_at=utcnow()))
        await s.commit()


async def _finish_mission(svc, mission_id: int, status: str, reason: str) -> None:
    if status not in {"completed", "failed"}:
        raise ValueError("automatic completion accepts completed/failed only")
    async with svc.db.session() as s:
        updated = await s.execute(sa.update(missions_t).where(
            missions_t.c.id == mission_id, missions_t.c.status == "running").values(
            status=status, finished_at=utcnow(), updated_at=utcnow()))
        await s.commit()
    if updated.rowcount:
        await svc.bus.emit(f"mission.{status}", mission_id=mission_id, reason=reason)


async def _transition(svc, mission_id: int, target: str, allowed: set[str]) -> dict:
    current = await _mission(svc, mission_id)
    if current["status"] == target:
        return current  # idempotent: do not extend the deadline on repeated Start
    if current["status"] not in allowed or current["status"] in TERMINAL:
        raise HTTPException(409, {"message": "недопустимый переход миссии",
                                  "from": current["status"], "to": target})
    values = {"status": target, "updated_at": utcnow()}
    if target == "running":
        values["started_at"] = sa.func.coalesce(missions_t.c.started_at, utcnow())
    if target in TERMINAL:
        values["finished_at"] = utcnow()
    async with svc.db.session() as session:
        changed = await session.execute(sa.update(missions_t).where(
            missions_t.c.id == mission_id, missions_t.c.status == current["status"]).values(**values))
        await session.commit()
    if not changed.rowcount:
        raise HTTPException(409, {"message": "состояние миссии изменилось, обновите данные"})
    return await _mission(svc, mission_id)


async def _dispatch_check(svc, task: dict) -> dict | None:
    if not task.get("mission_id"):
        return None
    mission = await _mission(svc, task["mission_id"])
    children = await _mission_tasks(svc, mission["id"])
    state = dispatch_state(mission, children, task["id"])
    if not state.ready:
        if state.reason in {"MISSION_QUEUED", "MISSION_PAUSED", "DEPENDENCIES_PENDING"}:
            return {"defer": 5, "reason": state.reason}
        return {"fail": state.reason}
    if (mission["duration_minutes"] and mission["started_at"]
            and (utcnow() - mission["started_at"]).total_seconds() >= mission["duration_minutes"] * 60):
        return {"fail": "MISSION_DEADLINE_EXPIRED"}
    return None


# ---------- KPI по завершению задач ----------

async def _on_events(svc):
    """Подписка: task.completed с meta.kpi_key двигает KPI миссии реальным выполнением."""
    q = svc.bus.subscribe()
    try:
        while True:
            msg = await q.get()
            if msg.get("kind") != "task.completed":
                continue
            task_id = msg.get("task_id")
            if task_id is None:
                continue
            async with svc.db.session() as s:
                row = (await s.execute(sa.select(tasks_t.c.mission_id, tasks_t.c.meta)
                                       .where(tasks_t.c.id == task_id))).first()
            if row is None or not row._mapping["mission_id"]:
                continue
            meta = row._mapping["meta"] if isinstance(row._mapping["meta"], dict) else {}
            key = meta.get("kpi_key")
            if key:
                try:
                    await _apply_kpi(svc, row._mapping["mission_id"], key,
                                     float(meta.get("kpi_delta", 1)), source_task_id=task_id)
                except HTTPException:
                    pass
    except Exception:
        return
    finally:
        svc.bus.unsubscribe(q)


async def _setup(svc):
    async def before_run(task, run):
        return await _dispatch_check(svc, task)

    async def completion_gate(task, run_id, answer):
        if not task.get("mission_id"):
            return {"verdict": "NOT_APPLICABLE"}
        mission = await _mission(svc, task["mission_id"])
        _, state = bindings(mission, await _mission_tasks(svc, mission["id"]))
        if state not in {"BOUND", "LEGACY_FLAT"} or mission["status"] not in {"running", "paused"}:
            return {"verdict": "FAIL", "requeue": False, "reason": f"mission continuity: {state}/{mission['status']}"}
        # This gate does not provide post-state evidence. The existing finalizer
        # must still verify all required effects independently.
        return {"verdict": "PASS"}

    svc.engine.add_hook("before_run", before_run)
    svc.engine.add_hook("gate_completion", completion_gate)
    # регистрируем в svc._tasks, чтобы svc.stop() отменил подписку (иначе течёт
    # и после закрытия БД крутится в цикле)
    task = asyncio.create_task(_on_events(svc), name="bcc-mission-kpi")
    if hasattr(svc, "_tasks"):
        svc._tasks.append(task)


# ---------- API ----------

@router.post("/missions")
async def create_mission(request: Request):
    svc = request.app.state.svc
    body = await request.json()
    if (type(body) is not dict or type(body.get("title")) is not str
            or not body["title"].strip() or len(body["title"]) > 300):
        raise HTTPException(422, {"message": "нужно непустое title до 300 символов"})
    goal = body.get("goal", body["title"])
    workers = body.get("max_workers", 2)
    duration = body.get("duration_minutes")
    budget = body.get("cloud_budget_usd", 0)
    kpis = body.get("kpi_targets") or {}
    if type(goal) is not str or len(goal) > 65536:
        raise HTTPException(422, {"message": "goal должен быть ограниченным текстом"})
    if type(workers) is not int or not 1 <= workers <= 64:
        raise HTTPException(422, {"message": "max_workers: целое число 1..64"})
    if duration is not None and (type(duration) is not int or not 1 <= duration <= 10080):
        raise HTTPException(422, {"message": "duration_minutes: целое число 1..10080"})
    if type(budget) not in (int, float) or not 0 <= budget <= 1_000_000 or not math.isfinite(budget):
        raise HTTPException(422, {"message": "cloud_budget_usd: конечный неотрицательный бюджет"})
    if (type(kpis) is not dict or len(kpis) > 128 or any(
            type(k) is not str or type(v) not in (int, float) or not 0 <= v <= 1e12 or not math.isfinite(v)
            for k, v in kpis.items())):
        raise HTTPException(422, {"message": "kpi_targets: конечные неотрицательные цели"})
    raw_plan = body["plan"] if "plan" in body else _plan_from_goal(goal, kpis)
    ordered = _compile_plan(raw_plan)
    # Owner-submitted plans use the ordinary tool loop. Native executors have
    # their own admission/project contracts, not arbitrary user-selected kinds.
    if any(t["kind"] not in {"generic", "research"} for t in ordered):
        raise HTTPException(400, {"message": "публичный план допускает generic/research; native приложения используют свои API"})
    plan = {**raw_plan, "tasks": ordered}
    async with svc.db.session() as session:
        result = await session.execute(sa.insert(missions_t).values(
            title=body["title"], goal=goal, status="queued", duration_minutes=duration,
            max_workers=workers, cloud_budget_usd=budget, plan=plan, kpi_targets=kpis,
            created_at=utcnow(), updated_at=utcnow()))
        mid = int(result.inserted_primary_key[0])
        binding = await _insert_plan(session, mid, plan)
        await session.execute(sa.update(missions_t).where(missions_t.c.id == mid).values(
            meta={BINDING: binding}))
        await session.commit()
    await svc.bus.emit("mission.created", mission_id=mid, title=body["title"], tasks=len(ordered))
    return await _mission(svc, mid)


@router.get("/missions")
async def list_missions(request: Request):
    svc = request.app.state.svc
    async with svc.db.session() as s:
        rows = (await s.execute(sa.select(missions_t).order_by(missions_t.c.id.desc()))).fetchall()
    return [dict(r._mapping) for r in rows]


@router.get("/missions/{mission_id}")
async def get_mission(mission_id: int, request: Request):
    svc = request.app.state.svc
    mission = await _mission(svc, mission_id)
    mission["tasks"] = await _mission_tasks(svc, mission_id)
    mission["continuity"] = projection(mission, mission["tasks"])
    mission["kpi"] = {"current": await _kpi_current(svc, mission_id),
                      "targets": mission.get("kpi_targets") or {}}
    return mission


@router.post("/missions/{mission_id}/start")
async def start_mission(mission_id: int, request: Request):
    svc = request.app.state.svc
    await _transition(svc, mission_id, "running", {"draft", "queued"})
    await svc.bus.emit("mission.started", mission_id=mission_id)
    await _tick(svc)
    return await _mission(svc, mission_id)


@router.post("/missions/{mission_id}/pause")
async def pause_mission(mission_id: int, request: Request):
    svc = request.app.state.svc
    await _transition(svc, mission_id, "paused", {"running", "queued"})
    await svc.bus.emit("mission.paused", mission_id=mission_id)
    return {"ok": True, "status": "paused"}


@router.post("/missions/{mission_id}/resume")
async def resume_mission(mission_id: int, request: Request):
    svc = request.app.state.svc
    await _transition(svc, mission_id, "running", {"paused"})
    await svc.bus.emit("mission.started", mission_id=mission_id, resumed=True)
    await _tick(svc)
    return {"ok": True, "status": "running"}


@router.post("/missions/{mission_id}/stop")
async def stop_mission(mission_id: int, request: Request):
    svc = request.app.state.svc
    # Durable terminal parent FIRST. A concurrent tick/resume cannot admit new
    # children while stop() cancels the existing workers. Repeated stop is safe.
    await _transition(svc, mission_id, "cancelled", {"draft", "planning", "queued", "running", "paused", "waiting_approval"})
    for t in await _mission_tasks(svc, mission_id):
        if t["status"] not in TERMINAL:
            await svc.engine.stop(t["id"])
    await svc.bus.emit("mission.cancelled", mission_id=mission_id, reason="остановлено оператором")
    return {"ok": True, "status": "cancelled"}


@router.get("/missions/{mission_id}/kpi")
async def get_kpi(mission_id: int, request: Request):
    svc = request.app.state.svc
    mission = await _mission(svc, mission_id)
    cur = await _kpi_current(svc, mission_id)
    return {"current": cur, "targets": mission.get("kpi_targets") or {},
            "progress": _kpi_progress(mission.get("kpi_targets") or {}, cur)}


@router.post("/missions/{mission_id}/kpi")
async def post_kpi(mission_id: int, request: Request):
    svc = request.app.state.svc
    body = await request.json()
    if not body.get("key"):
        raise HTTPException(422, {"message": "нужно key"})
    return await _apply_kpi(svc, mission_id, body["key"], float(body.get("delta", 1)),
                            source_task_id=body.get("source_task_id"))


@router.get("/missions/{mission_id}/kpi/history")
async def kpi_history(mission_id: int, request: Request, key: str | None = None, limit: int = 100):
    svc = request.app.state.svc
    async with svc.db.session() as s:
        q = sa.select(kpi_t).where(kpi_t.c.mission_id == mission_id)
        if key:
            q = q.where(kpi_t.c.key == key)
        rows = (await s.execute(q.order_by(kpi_t.c.id.desc()).limit(min(limit, 200)))).fetchall()
    return [dict(r._mapping) for r in rows]


FEATURE = Feature(name="missions", router=router, setup=_setup, tick=_tick, tick_seconds=5.0)
