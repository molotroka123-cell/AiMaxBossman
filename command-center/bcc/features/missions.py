"""Feature 01+13 — Autopilot Missions + KPI.

Миссия — цель, а не задача: план → задачи → исполнение с лимитом воркеров,
прогресс по завершённым задачам, KPI по kpi_targets. Использует движок/очередь.
Пауза/резюм/стоп. Переживает рестарт (состояние — только в БД).
"""
from __future__ import annotations

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Request

from ..db import (kpi_history as kpi_t, missions as missions_t, tasks as tasks_t, utcnow)
from ..v2.kpi import KPI, mission_progress
from . import Feature

router = APIRouter()


def _plan_from_goal(goal: str, kpi_targets: dict) -> dict:
    """Детерминированный план: N research-задач из цели (без планировщика-модели).
    Если в системе есть агент-planner, оркестратор может заменить это вызовом модели."""
    import re
    m = re.search(r"(\d+)", goal or "")
    n = min(int(m.group(1)), 20) if m else 3
    tasks = [{"title": f"Задача {i + 1}", "kind": "research",
              "prompt": f"{goal}\n\nШаг {i + 1} из {n}: выполни свою часть и верни результат."}
             for i in range(n)]
    return {"milestones": [{"name": "Выполнение", "tasks": len(tasks)}], "tasks": tasks}


def _compile_plan(plan: dict) -> list[dict]:
    """V2.6 модуль F: план миссии проходит типизированную компиляцию через
    существующий DAG-движок (bcc/v2/task_graph) ДО постановки задач: схема,
    дубликаты, циклы, недостающие зависимости → 400, а не тихая очередь.
    Возврат — задачи плана в ТОПОЛОГИЧЕСКОМ порядке (id по возрастанию =
    порядок зависимостей для воркера). Плоский план (без depends_on) проходит
    как раньше — простое не усложняем."""
    from ..v2.task_graph import (GraphValidationError, TaskGraph, mark_running,
                                 mark_succeeded, ready_nodes)
    tasks = list(plan.get("tasks", []))
    nodes = []
    for i, t in enumerate(tasks):
        nodes.append({
            "node_id": str(t.get("node_id") or f"t{i + 1}"),
            "action_type": str(t.get("kind") or "generic"),
            "depends_on": [str(d) for d in (t.get("depends_on") or [])],
            "input": {"index": i},
        })
    try:
        graph = TaskGraph.from_list(nodes)
    except GraphValidationError as exc:
        raise HTTPException(400, {"message": "план миссии не прошёл компиляцию",
                                  "errors": str(exc)[:2000]})
    ordered: list[dict] = []
    while True:
        ready = ready_nodes(graph)
        if not ready:
            break
        for node in ready:
            mark_running(graph, node.node_id)
            mark_succeeded(graph, node.node_id)
            ordered.append(tasks[node.input["index"]])
    return ordered


async def _create_tasks(svc, mission_id: int, plan: dict) -> None:
    ordered = _compile_plan(plan)
    async with svc.db.session() as s:
        for t in ordered:
            await s.execute(sa.insert(tasks_t).values(
                title=t.get("title", "задача"), prompt=t["prompt"],
                agent_id=t.get("agent_id"), status="draft", kind=t.get("kind", "generic"),
                mission_id=mission_id, created_at=utcnow(), updated_at=utcnow()))
        await s.commit()


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
    """Держит ≤max_workers активных задач running-миссий; двигает прогресс;
    завершает/останавливает по правилам. Реентерабельно и идемпотентно."""
    async with svc.db.session() as s:
        running = (await s.execute(sa.select(missions_t).where(
            missions_t.c.status == "running"))).fetchall()
    for row in running:
        m = dict(row._mapping)
        tasks = await _mission_tasks(svc, m["id"])
        if not tasks:
            continue
        active = [t for t in tasks if t["status"] in ("queued", "running", "waiting_approval")]
        done = [t for t in tasks if t["status"] in ("completed", "failed", "stopped")]
        # таймаут миссии
        if m["duration_minutes"] and m["started_at"]:
            if (utcnow() - m["started_at"]).total_seconds() > m["duration_minutes"] * 60:
                await _finish_mission(svc, m["id"], "failed", "истёк срок миссии")
                continue
        # запуск новых до лимита воркеров
        free = max(0, (m["max_workers"] or 1) - len(active))
        drafts = [t for t in tasks if t["status"] == "draft"][:free]
        for t in drafts:
            await svc.engine.enqueue(t["id"])
        progress = len(done) / len(tasks)
        if abs(progress - (m["progress"] or 0.0)) > 1e-9:
            await _set_progress(svc, m["id"], progress)
            await svc.bus.emit("mission.progress", mission_id=m["id"], progress=round(progress, 4))
        if len(done) == len(tasks):
            await _finish_mission(svc, m["id"], "completed", "все задачи завершены")


async def _set_progress(svc, mission_id: int, progress: float) -> None:
    async with svc.db.session() as s:
        await s.execute(sa.update(missions_t).where(missions_t.c.id == mission_id).values(
            progress=progress, updated_at=utcnow()))
        await s.commit()


async def _finish_mission(svc, mission_id: int, status: str, reason: str) -> None:
    async with svc.db.session() as s:
        await s.execute(sa.update(missions_t).where(missions_t.c.id == mission_id).values(
            status=status, finished_at=utcnow(), updated_at=utcnow()))
        await s.commit()
    await svc.bus.emit(f"mission.{'completed' if status == 'completed' else 'failed'}",
                       mission_id=mission_id, reason=reason)


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


async def _setup(svc):
    import asyncio
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
    if not body.get("title"):
        raise HTTPException(422, {"message": "нужно title"})
    plan = _plan_from_goal(body.get("goal", body["title"]), body.get("kpi_targets") or {})
    async with svc.db.session() as s:
        res = await s.execute(sa.insert(missions_t).values(
            title=body["title"], goal=body.get("goal", ""), status="planning",
            duration_minutes=body.get("duration_minutes"),
            max_workers=int(body.get("max_workers", 2)),
            cloud_budget_usd=float(body.get("cloud_budget_usd", 0) or 0),
            plan=plan, kpi_targets=body.get("kpi_targets") or {},
            created_at=utcnow(), updated_at=utcnow()))
        mid = int(res.inserted_primary_key[0])
        await s.commit()
    await _create_tasks(svc, mid, plan)
    async with svc.db.session() as s:
        await s.execute(sa.update(missions_t).where(missions_t.c.id == mid).values(status="queued"))
        await s.commit()
    await svc.bus.emit("mission.created", mission_id=mid, title=body["title"], tasks=len(plan["tasks"]))
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
    mission["kpi"] = {"current": await _kpi_current(svc, mission_id),
                      "targets": mission.get("kpi_targets") or {}}
    return mission


@router.post("/missions/{mission_id}/start")
async def start_mission(mission_id: int, request: Request):
    svc = request.app.state.svc
    await _mission(svc, mission_id)
    async with svc.db.session() as s:
        await s.execute(sa.update(missions_t).where(missions_t.c.id == mission_id).values(
            status="running", started_at=utcnow(), updated_at=utcnow()))
        await s.commit()
    await svc.bus.emit("mission.started", mission_id=mission_id)
    await _tick(svc)
    return await _mission(svc, mission_id)


@router.post("/missions/{mission_id}/pause")
async def pause_mission(mission_id: int, request: Request):
    svc = request.app.state.svc
    await _mission(svc, mission_id)
    async with svc.db.session() as s:
        await s.execute(sa.update(missions_t).where(missions_t.c.id == mission_id).values(
            status="paused", updated_at=utcnow()))
        await s.commit()
    await svc.bus.emit("mission.paused", mission_id=mission_id)
    return {"ok": True, "status": "paused"}


@router.post("/missions/{mission_id}/resume")
async def resume_mission(mission_id: int, request: Request):
    svc = request.app.state.svc
    await _mission(svc, mission_id)
    async with svc.db.session() as s:
        await s.execute(sa.update(missions_t).where(missions_t.c.id == mission_id).values(
            status="running", updated_at=utcnow()))
        await s.commit()
    await svc.bus.emit("mission.started", mission_id=mission_id, resumed=True)
    await _tick(svc)
    return {"ok": True, "status": "running"}


@router.post("/missions/{mission_id}/stop")
async def stop_mission(mission_id: int, request: Request):
    svc = request.app.state.svc
    for t in await _mission_tasks(svc, mission_id):
        if t["status"] in ("queued", "running", "waiting_approval", "paused"):
            await svc.engine.stop(t["id"])
    await _finish_mission(svc, mission_id, "cancelled", "остановлено оператором")
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
