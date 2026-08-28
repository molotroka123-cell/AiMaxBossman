"""Feature 06 — Visual Agent Map (данные).

Граф строится из РЕАЛЬНЫХ данных: агенты/оркестры из БД + живой статус из активных
run'ов. Никакого статичного mock-графа. Поверх готовой bcc/v2/agent_graph.
"""
from __future__ import annotations

import sqlalchemy as sa
from fastapi import APIRouter, Request

from ..db import agents as agents_t, orchestras as orch_t, orchestra_members as members_t
from ..db import task_runs as runs_t, tasks as tasks_t
from ..v2.agent_graph import AgentEdge, AgentNode, graph_payload
from . import Feature

router = APIRouter()
ACTIVE = ("queued", "running")


async def _agent_status(svc, agent_id: int) -> tuple[str, int | None, str]:
    """Живой статус агента: по активным задачам этого агента (running/queued/idle)."""
    async with svc.db.session() as s:
        active = (await s.execute(
            sa.select(runs_t.c.status, runs_t.c.task_id, runs_t.c.model_alias)
            .join(tasks_t, tasks_t.c.id == runs_t.c.task_id)
            .where(tasks_t.c.agent_id == agent_id, runs_t.c.status.in_(("leased", "running")))
            .order_by(runs_t.c.id.desc()).limit(1))).first()
        if active is None:
            queued = (await s.execute(
                sa.select(tasks_t.c.id).where(tasks_t.c.agent_id == agent_id,
                                              tasks_t.c.status.in_(ACTIVE)).limit(1))).first()
            if queued:
                return "queued", queued._mapping["id"], ""
            last = (await s.execute(
                sa.select(tasks_t.c.status).where(tasks_t.c.agent_id == agent_id)
                .order_by(tasks_t.c.id.desc()).limit(1))).first()
            return ("failed" if last and last._mapping["status"] == "failed" else "idle"), None, ""
    m = active._mapping
    return "working", m["task_id"], m["model_alias"] or ""


@router.get("/agentmap")
async def agentmap(request: Request, orchestra_id: int | None = None, mission_id: int | None = None):
    svc = request.app.state.svc
    nodes: list[AgentNode] = []
    edges: list[AgentEdge] = []

    if orchestra_id is not None:
        async with svc.db.session() as s:
            members = (await s.execute(
                sa.select(members_t, agents_t.c.name)
                .join(agents_t, agents_t.c.id == members_t.c.agent_id)
                .where(members_t.c.orchestra_id == orchestra_id)
                .order_by(members_t.c.position))).fetchall()
        managers = [m for m in members if m._mapping["role"] == "manager"]
        for r in members:
            m = r._mapping
            status, task_id, model = await _agent_status(svc, m["agent_id"])
            nid = f"agent:{m['agent_id']}"
            nodes.append(AgentNode(id=nid, label=m["name"], status=status, model=model,
                                   task=str(task_id or "")))
        # рёбра manager → workers/reviewer
        for mgr in managers:
            for r in members:
                if r._mapping["id"] == mgr._mapping["id"]:
                    continue
                edges.append(AgentEdge(source=f"agent:{mgr._mapping['agent_id']}",
                                       target=f"agent:{r._mapping['agent_id']}",
                                       kind="delegates" if r._mapping["role"] == "worker"
                                       else "reviews"))
    else:
        # без оркестра: все агенты + опционально задачи миссии
        async with svc.db.session() as s:
            q = sa.select(agents_t)
            rows = (await s.execute(q)).fetchall()
        for r in rows:
            a = r._mapping
            status, task_id, model = await _agent_status(svc, a["id"])
            nodes.append(AgentNode(id=f"agent:{a['id']}", label=a["name"], status=status,
                                   model=model, task=str(task_id or "")))

    payload = graph_payload(nodes, edges)
    payload["counts"] = {"nodes": len(nodes), "edges": len(payload["edges"])}
    return payload


@router.get("/orchestras")
async def list_orchestras(request: Request):
    """Список оркестров с членами (NL-оркестрация пишет сюда; тут — только чтение)."""
    svc = request.app.state.svc
    async with svc.db.session() as s:
        orchestras = (await s.execute(sa.select(orch_t).order_by(orch_t.c.id.desc()))).fetchall()
        out = []
        for r in orchestras:
            o = dict(r._mapping)
            members = (await s.execute(
                sa.select(members_t, agents_t.c.name)
                .join(agents_t, agents_t.c.id == members_t.c.agent_id)
                .where(members_t.c.orchestra_id == o["id"])
                .order_by(members_t.c.position))).fetchall()
            o["members"] = [dict(m._mapping) for m in members]
            out.append(o)
    return out


FEATURE = Feature(name="agentmap", router=router)
