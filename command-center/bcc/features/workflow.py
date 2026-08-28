"""Feature 16 — Workflow Builder (n8n-подобный канвас миссии).

Ничего не выдумывает: узлы и рёбра выводятся из реальных строк БД —
mission.plan, tasks, task_runs (+ route), models, approvals, checkpoints,
run_events. Раскладка (x/y по слоям) считается здесь, чтобы UI просто рисовал
SVG и анимировал живые рёбра.

Endpoints (только чтение; запуск/стоп/approve — существующие ручки миссий и
approvals, дублировать мутации незачем):
  GET /api/workflow/missions            — компактный список миссий для селектора
  GET /api/workflow/missions/{id}       — граф + run overview + timeline +
                                          metrics + queue + log + approvals
  GET /api/workflow/missions/{id}/log   — хвост журнала (инкрементально, after=id)
"""
from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Request

from ..db import (agents as agents_t, approvals as approvals_t, checkpoints as checkpoints_t,
                  missions as missions_t, models as models_t, run_events as events_t,
                  task_runs as runs_t, tasks as tasks_t, utcnow)
from . import Feature

router = APIRouter()

NODE_W = 208
NODE_H = 72
COL_W = 268
ROW_H = 108
PAD_X = 40
PAD_Y = 40

# статусы задач/раннов → словарь канваса (тот же, что в UI компонентах)
_TASK_STATUS = {
    "draft": "pending", "queued": "queued", "running": "running",
    "waiting_approval": "waiting", "paused": "waiting", "completed": "success",
    "failed": "failed", "stopped": "stopped",
}
_MISSION_STATUS = {
    "draft": "pending", "planning": "pending", "queued": "queued", "running": "running",
    "paused": "waiting", "completed": "success", "failed": "failed", "cancelled": "stopped",
}
_KIND_ICON = {
    "browser": "browser", "terminal": "terminal", "research": "search",
    "review": "shield", "skill": "skills", "generic": "agents",
}


def _ms(a: datetime | None, b: datetime | None) -> int | None:
    if a is None or b is None:
        return None
    return max(0, int((b - a).total_seconds() * 1000))


def _short(text: str, n: int = 46) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


# ---------- сбор сырых данных ----------

async def _load(svc, mission_id: int) -> dict:
    async with svc.db.session() as s:
        mission = (await s.execute(sa.select(missions_t)
                                   .where(missions_t.c.id == mission_id))).first()
        if mission is None:
            raise HTTPException(404, {"message": "миссия не найдена"})
        tasks = (await s.execute(sa.select(tasks_t)
                                 .where(tasks_t.c.mission_id == mission_id)
                                 .order_by(tasks_t.c.id))).fetchall()
        task_ids = [r._mapping["id"] for r in tasks]
        runs = []
        approvals = []
        checkpoints = 0
        log = []
        if task_ids:
            runs = (await s.execute(sa.select(runs_t)
                                    .where(runs_t.c.task_id.in_(task_ids))
                                    .order_by(runs_t.c.id))).fetchall()
            approvals = (await s.execute(sa.select(approvals_t)
                                         .where(approvals_t.c.task_id.in_(task_ids))
                                         .order_by(approvals_t.c.id.desc()))).fetchall()
            run_ids = [r._mapping["id"] for r in runs]
            if run_ids:
                checkpoints = int((await s.execute(
                    sa.select(sa.func.count()).select_from(checkpoints_t)
                    .where(checkpoints_t.c.run_id.in_(run_ids)))).scalar() or 0)
                log = (await s.execute(sa.select(events_t)
                                       .where(events_t.c.run_id.in_(run_ids))
                                       .order_by(events_t.c.id.desc()).limit(60))).fetchall()
        agent_rows = (await s.execute(sa.select(agents_t))).fetchall()
        model_rows = (await s.execute(sa.select(models_t))).fetchall()
    return {
        "mission": dict(mission._mapping),
        "tasks": [dict(r._mapping) for r in tasks],
        "runs": [dict(r._mapping) for r in runs],
        "approvals": [dict(r._mapping) for r in approvals],
        "checkpoints": checkpoints,
        "log": [dict(r._mapping) for r in reversed(log)],
        "agents": {r._mapping["id"]: dict(r._mapping) for r in agent_rows},
        "models": {r._mapping["alias"]: dict(r._mapping) for r in model_rows},
        "models_by_id": {r._mapping["id"]: dict(r._mapping) for r in model_rows},
    }


def _last_run_by_task(runs: list[dict]) -> dict[int, dict]:
    last: dict[int, dict] = {}
    for r in runs:
        last[r["task_id"]] = r          # runs отсортированы по id → останется последний
    return last


# ---------- граф ----------

def _graph(data: dict) -> dict:
    mission = data["mission"]
    tasks = data["tasks"]
    runs = data["runs"]
    last_run = _last_run_by_task(runs)
    now = utcnow()

    nodes: list[dict] = []
    edges: list[dict] = []
    layers: list[list[str]] = []

    def add(node: dict, layer: int) -> str:
        while len(layers) <= layer:
            layers.append([])
        layers[layer].append(node["id"])
        node["layer"] = layer
        nodes.append(node)
        return node["id"]

    def link(src: str, dst: str, label: str = "", kind: str = "flow",
             active: bool = False) -> None:
        edges.append({"id": f"{src}->{dst}:{kind}", "source": src, "target": dst,
                      "label": label, "kind": kind, "active": active})

    m_status = _MISSION_STATUS.get(str(mission.get("status")), "pending")
    running = m_status == "running"

    # 0. триггер
    add({"id": "trigger", "kind": "trigger", "title": "Mission Trigger",
         "subtitle": _short(mission.get("goal") or mission.get("title") or "", 40),
         "status": m_status, "icon": "play",
         "meta": {"mission_id": mission["id"],
                  "workers": mission.get("max_workers")}}, 0)

    # 1. планировщик
    plan = mission.get("plan") if isinstance(mission.get("plan"), dict) else {}
    planned = len(plan.get("tasks") or [])
    add({"id": "planner", "kind": "planner", "title": "Task Planner",
         "subtitle": f"{planned or len(tasks)} задач в плане",
         "status": "success" if tasks else "pending", "icon": "schedules",
         "meta": {"milestones": plan.get("milestones") or []}}, 1)
    link("trigger", "planner", "запуск", active=running)

    # 2. роутер — только если решения роутера реально записаны
    routes = [r.get("route") for r in runs if isinstance(r.get("route"), dict)]
    router_node = None
    if routes or tasks:
        reasons = [str(r.get("reason") or r.get("strategy") or "") for r in routes if r]
        router_node = add({"id": "router", "kind": "router", "title": "Smart Model Router",
                           "subtitle": _short(next((x for x in reasons if x), "выбор модели по цене и здоровью"), 40),
                           "status": "success" if routes else ("running" if running else "pending"),
                           "icon": "models",
                           "meta": {"decisions": len(routes)}}, 2)
        link("planner", "router", "план", active=running)

    # 3. модели — реально использованные в раннах этой миссии
    used: dict[str, dict] = {}
    for r in runs:
        alias = r.get("model_alias")
        if not alias:
            continue
        m = used.setdefault(alias, {"alias": alias, "runs": 0, "tokens": 0, "cost": 0.0})
        m["runs"] += 1
        m["tokens"] += int(r.get("tokens_in") or 0) + int(r.get("tokens_out") or 0)
        m["cost"] += float(r.get("cost_usd") or 0.0)
    if not used:
        # раннов ещё нет: показываем модели агентов, назначенных задачам плана
        for t in tasks:
            agent = data["agents"].get(t.get("agent_id"))
            model = data["models_by_id"].get(agent.get("model_id")) if agent else None
            if model:
                used.setdefault(model["alias"], {"alias": model["alias"], "runs": 0,
                                                 "tokens": 0, "cost": 0.0})

    model_node_by_alias: dict[str, str] = {}
    for alias, info in used.items():
        model = data["models"].get(alias) or {}
        cloud = str(model.get("kind") or "local") == "cloud"
        node_id = f"model:{alias}"
        add({"id": node_id, "kind": "model", "title": _short(alias, 22),
             "subtitle": ("Cloud · Best" if cloud else "Local · Private"),
             "status": {"online": "success", "offline": "failed", "error": "failed"}
                       .get(str(model.get("status")), "pending" if not info["runs"] else "success"),
             "icon": "cloud" if cloud else "cpu",
             "meta": {"runs": info["runs"], "tokens": info["tokens"],
                      "cost_usd": round(info["cost"], 6), "cloud": cloud}}, 3)
        model_node_by_alias[alias] = node_id
        if router_node:
            link("router", node_id, "cloud" if cloud else "local",
                 active=running and info["runs"] > 0)

    # 4. задачи-агенты
    task_layer = 4 if model_node_by_alias else 3
    review_needed = False
    for t in tasks:
        run = last_run.get(t["id"])
        status = _TASK_STATUS.get(str(t.get("status")), "pending")
        if status == "waiting":
            review_needed = True
        agent = data["agents"].get(t.get("agent_id"))
        elapsed = None
        if run:
            elapsed = _ms(run.get("started_at"), run.get("finished_at") or
                          (now if str(run.get("status")) == "running" else None))
        node_id = f"task:{t['id']}"
        add({"id": node_id, "kind": "task", "title": _short(t.get("title") or f"Задача #{t['id']}", 24),
             "subtitle": _short((agent or {}).get("name") or str(t.get("kind") or "generic"), 26),
             "status": status, "icon": _KIND_ICON.get(str(t.get("kind")), "agents"),
             "elapsed_ms": elapsed,
             "meta": {"task_id": t["id"], "run_id": (run or {}).get("id"),
                      "kind": t.get("kind"), "model_alias": (run or {}).get("model_alias"),
                      "attempt": (run or {}).get("attempt"),
                      "error": _short((run or {}).get("error") or "", 120) or None}}, task_layer)
        src = model_node_by_alias.get((run or {}).get("model_alias"))
        if src:
            link(src, node_id, "", active=status == "running")
        elif router_node:
            link(router_node, node_id, "", active=status == "running")
        else:
            link("planner", node_id, "", active=status == "running")

    # 5. Reviewer Gate — только если ревью реально есть (approval'ы или ожидание)
    pending_appr = [a for a in data["approvals"] if str(a.get("status")) == "pending"]
    rejected = [a for a in data["approvals"] if str(a.get("status")) == "rejected"]
    gate_id = None
    if data["approvals"] or review_needed:
        gate_status = ("waiting" if pending_appr else
                       "failed" if rejected and not data["approvals"][0].get("decided_at") else
                       "success" if data["approvals"] else "pending")
        gate_id = add({"id": "gate", "kind": "gate", "title": "Reviewer Gate",
                       "subtitle": (f"на проверке: {len(pending_appr)}" if pending_appr
                                    else f"решений: {len(data['approvals'])}"),
                       "status": gate_status, "icon": "shield",
                       "meta": {"pending": len(pending_appr),
                                "rejected": len(rejected)}}, task_layer + 1)
        for t in tasks:
            link(f"task:{t['id']}", gate_id, "", active=_TASK_STATUS.get(
                str(t.get("status"))) == "waiting")

    # 6. память (чекпойнты — реальные строки)
    mem_id = add({"id": "memory", "kind": "memory", "title": "Memory Save",
                  "subtitle": f"чекпойнтов: {data['checkpoints']}",
                  "status": "success" if data["checkpoints"] else "pending", "icon": "database",
                  "meta": {"checkpoints": data["checkpoints"]}},
                 (task_layer + 2) if gate_id else (task_layer + 1))
    if gate_id:
        link(gate_id, mem_id, "Approved", kind="approved",
             active=bool(data["approvals"]) and not pending_appr)
        if rejected:
            # обратная связь существует только если отказ реально был
            for t in tasks:
                link(gate_id, f"task:{t['id']}", "Changes requested", kind="rejected",
                     active=bool(pending_appr))
                break
    else:
        for t in tasks:
            link(f"task:{t['id']}", mem_id, "", active=False)

    # 7. отчёт
    done = str(mission.get("status")) in ("completed", "failed", "cancelled")
    rep_id = add({"id": "report", "kind": "report", "title": "Report Output",
                  "subtitle": ("итог миссии" if done else "ожидает завершения"),
                  "status": m_status if done else "pending", "icon": "info",
                  "meta": {"progress": mission.get("progress")}},
                 nodes[-1]["layer"] + 1)
    link(mem_id, rep_id, "", active=running)

    # раскладка: колонка = слой, строки центрируются по высоте самого широкого слоя
    tallest = max((len(col) for col in layers), default=1)
    height = PAD_Y * 2 + max(1, tallest) * ROW_H
    by_id = {n["id"]: n for n in nodes}
    for li, col in enumerate(layers):
        x = PAD_X + li * COL_W
        span = len(col)
        for ri, nid in enumerate(col):
            y = PAD_Y + (height - PAD_Y * 2) * (ri + 0.5) / span - NODE_H / 2
            by_id[nid].update({"x": x, "y": round(y, 1), "w": NODE_W, "h": NODE_H})
    width = PAD_X * 2 + max(1, len(layers)) * COL_W - (COL_W - NODE_W)
    return {"nodes": nodes, "edges": edges, "width": width, "height": height,
            "node_w": NODE_W, "node_h": NODE_H}


# ---------- run overview / timeline / metrics ----------

def _overview(data: dict) -> dict:
    mission = data["mission"]
    runs = data["runs"]
    tasks = data["tasks"]
    now = utcnow()
    active = [r for r in runs if str(r.get("status")) == "running"]
    tokens_in = sum(int(r.get("tokens_in") or 0) for r in runs)
    tokens_out = sum(int(r.get("tokens_out") or 0) for r in runs)
    cost = round(sum(float(r.get("cost_usd") or 0.0) for r in runs), 6)
    done = [t for t in tasks if str(t.get("status")) in ("completed", "failed", "stopped")]
    progress = float(mission.get("progress") or 0.0)
    if tasks and not progress:
        progress = len(done) / len(tasks)

    started = mission.get("started_at")
    eta_seconds = None
    if started and progress > 0 and str(mission.get("status")) == "running":
        elapsed = (now - started).total_seconds()
        eta_seconds = max(0, int(elapsed / progress - elapsed))

    task_by_id = {t["id"]: t for t in tasks}
    agents = []
    for r in active:
        t = task_by_id.get(r["task_id"], {})
        agent = data["agents"].get(t.get("agent_id"))
        agents.append({
            "run_id": r["id"], "task_id": r["task_id"],
            "title": t.get("title") or f"Задача #{r['task_id']}",
            "agent": (agent or {}).get("name") or "—",
            "model_alias": r.get("model_alias"),
            "elapsed_ms": _ms(r.get("started_at"), now),
        })
    return {
        "run_id": active[0]["id"] if active else (runs[-1]["id"] if runs else None),
        "status": mission.get("status"),
        "progress": round(progress, 4),
        "started_at": started, "finished_at": mission.get("finished_at"),
        "eta_seconds": eta_seconds,
        "tokens_in": tokens_in, "tokens_out": tokens_out,
        "cost_usd": cost, "budget_usd": float(mission.get("cloud_budget_usd") or 0.0),
        "tasks_total": len(tasks), "tasks_done": len(done),
        "active_agents": agents,
    }


def _timeline(data: dict) -> dict:
    runs = [r for r in data["runs"] if r.get("started_at")]
    now = utcnow()
    if not runs:
        return {"origin": None, "span_ms": 0, "rows": []}
    origin = min(r["started_at"] for r in runs)
    end = max((r.get("finished_at") or now) for r in runs)
    span = max(1, _ms(origin, end) or 1)
    task_by_id = {t["id"]: t for t in data["tasks"]}
    rows: dict[int, dict] = {}
    for r in runs:
        t = task_by_id.get(r["task_id"], {})
        row = rows.setdefault(r["task_id"], {
            "node_id": f"task:{r['task_id']}", "task_id": r["task_id"],
            "label": t.get("title") or f"Задача #{r['task_id']}",
            "status": _TASK_STATUS.get(str(t.get("status")), "pending"), "segments": []})
        finished = r.get("finished_at") or now
        row["segments"].append({
            "run_id": r["id"], "attempt": r.get("attempt"),
            "start_ms": _ms(origin, r["started_at"]),
            "end_ms": _ms(origin, finished),
            "status": _TASK_STATUS.get(str(r.get("status")), str(r.get("status") or "pending")),
            "model_alias": r.get("model_alias"),
        })
    return {"origin": origin, "span_ms": span, "rows": list(rows.values())}


def _metrics(data: dict) -> dict:
    runs = data["runs"]
    workers = max(1, int(data["mission"].get("max_workers") or 1))
    now = utcnow()
    started = [r for r in runs if r.get("started_at")]
    agent_ms = sum(_ms(r["started_at"], r.get("finished_at") or now) or 0 for r in started)
    total_ms = 0
    if started:
        origin = min(r["started_at"] for r in started)
        end = max((r.get("finished_at") or now) for r in started)
        total_ms = _ms(origin, end) or 0
    # Загрузка считается от ёмкости пула (wall-clock × воркеры), иначе при N
    # воркерах сумма времени раннов легко даёт «247%».
    capacity_ms = total_ms * workers
    idle_ms = max(0, capacity_ms - agent_ms)
    by_model: dict[str, dict] = {}
    for r in runs:
        alias = r.get("model_alias") or "—"
        entry = by_model.setdefault(alias, {"label": alias, "tokens": 0, "cost_usd": 0.0, "runs": 0})
        entry["tokens"] += int(r.get("tokens_in") or 0) + int(r.get("tokens_out") or 0)
        entry["cost_usd"] += float(r.get("cost_usd") or 0.0)
        entry["runs"] += 1
    series = sorted(by_model.values(), key=lambda x: -x["tokens"])
    # донат: 3 слота валидированной палитры + нейтральное «Прочее»
    if len(series) > 3:
        rest = series[3:]
        series = series[:3] + [{"label": "Прочее", "tokens": sum(x["tokens"] for x in rest),
                                "cost_usd": sum(x["cost_usd"] for x in rest),
                                "runs": sum(x["runs"] for x in rest), "other": True}]
    total_tokens = sum(x["tokens"] for x in series) or 0
    for x in series:
        x["cost_usd"] = round(x["cost_usd"], 6)
        x["share"] = round(x["tokens"] / total_tokens, 4) if total_tokens else 0.0
    return {
        "total_ms": total_ms, "agent_ms": agent_ms, "idle_ms": idle_ms,
        "workers": workers, "capacity_ms": capacity_ms,
        "agent_share": round(min(1.0, agent_ms / capacity_ms), 4) if capacity_ms else 0.0,
        "idle_share": round(idle_ms / capacity_ms, 4) if capacity_ms else 0.0,
        "tokens_in": sum(int(r.get("tokens_in") or 0) for r in runs),
        "tokens_out": sum(int(r.get("tokens_out") or 0) for r in runs),
        "tokens_total": total_tokens,
        "cost_usd": round(sum(float(r.get("cost_usd") or 0.0) for r in runs), 6),
        "runs": len(runs),
        "by_model": series,
    }


def _queue(data: dict) -> list[dict]:
    task_by_id = {t["id"]: t for t in data["tasks"]}
    now = utcnow()
    out = []
    for r in sorted(data["runs"], key=lambda x: -x["id"])[:12]:
        t = task_by_id.get(r["task_id"], {})
        out.append({
            "run_id": r["id"], "task_id": r["task_id"],
            "title": t.get("title") or f"Задача #{r['task_id']}",
            "status": _TASK_STATUS.get(str(r.get("status")), str(r.get("status") or "pending")),
            "attempt": r.get("attempt"), "model_alias": r.get("model_alias"),
            "started_at": r.get("started_at"), "finished_at": r.get("finished_at"),
            "duration_ms": _ms(r.get("started_at"),
                               r.get("finished_at") or (now if str(r.get("status")) == "running" else None)),
            "error": _short(r.get("error") or "", 140) or None,
        })
    return out


def _log_rows(data: dict) -> list[dict]:
    run_task = {r["id"]: r["task_id"] for r in data["runs"]}
    return [{"id": e["id"], "ts": e["ts"], "level": e.get("level"), "kind": e.get("kind"),
             "message": e.get("message"), "run_id": e.get("run_id"),
             "task_id": run_task.get(e.get("run_id"))} for e in data["log"]]


def _approval_rows(data: dict) -> list[dict]:
    now = utcnow()
    task_by_id = {t["id"]: t for t in data["tasks"]}
    out = []
    for a in data["approvals"]:
        if str(a.get("status")) != "pending":
            continue
        t = task_by_id.get(a.get("task_id"), {})
        out.append({"id": a["id"], "kind": a.get("kind"), "preview": a.get("preview") or "",
                    "task_id": a.get("task_id"), "run_id": a.get("run_id"),
                    "task_title": t.get("title"), "created_at": a.get("created_at"),
                    "age_seconds": int((now - a["created_at"]).total_seconds())
                    if a.get("created_at") else None})
    return out


# ---------- API ----------

@router.get("/workflow/missions")
async def workflow_missions(request: Request):
    svc = request.app.state.svc
    async with svc.db.session() as s:
        rows = (await s.execute(sa.select(
            missions_t.c.id, missions_t.c.title, missions_t.c.status, missions_t.c.progress,
            missions_t.c.started_at, missions_t.c.updated_at)
            .order_by(missions_t.c.id.desc()).limit(50))).fetchall()
    return [dict(r._mapping) for r in rows]


@router.get("/workflow/missions/{mission_id}")
async def workflow_mission(mission_id: int, request: Request):
    svc = request.app.state.svc
    data = await _load(svc, mission_id)
    m = data["mission"]
    return {
        "mission": {"id": m["id"], "title": m["title"], "goal": m.get("goal"),
                    "status": m.get("status"), "progress": m.get("progress"),
                    "max_workers": m.get("max_workers"),
                    "duration_minutes": m.get("duration_minutes"),
                    "cloud_budget_usd": m.get("cloud_budget_usd"),
                    "started_at": m.get("started_at"), "finished_at": m.get("finished_at")},
        "graph": _graph(data),
        "run": _overview(data),
        "timeline": _timeline(data),
        "metrics": _metrics(data),
        "queue": _queue(data),
        "log": _log_rows(data),
        "approvals": _approval_rows(data),
        "server_time": utcnow(),
    }


@router.get("/workflow/missions/{mission_id}/log")
async def workflow_log(mission_id: int, request: Request, after: int = 0, limit: int = 100):
    svc = request.app.state.svc
    async with svc.db.session() as s:
        task_ids = [r._mapping["id"] for r in (await s.execute(
            sa.select(tasks_t.c.id).where(tasks_t.c.mission_id == mission_id))).fetchall()]
        if not task_ids:
            return []
        run_ids = [r._mapping["id"] for r in (await s.execute(
            sa.select(runs_t.c.id).where(runs_t.c.task_id.in_(task_ids)))).fetchall()]
        if not run_ids:
            return []
        rows = (await s.execute(sa.select(events_t)
                                .where(sa.and_(events_t.c.run_id.in_(run_ids),
                                               events_t.c.id > after))
                                .order_by(events_t.c.id).limit(min(limit, 300)))).fetchall()
    return [dict(r._mapping) for r in rows]


FEATURE = Feature(name="workflow", router=router)
