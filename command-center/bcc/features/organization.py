"""ORG-01 (TZ-04 §2.1) — Organization Layer как продуктовая точка входа Command Center.

Слой V3 «КТО делает работу» живёт в `bossman-core/bossman_v3/organization`; здесь —
только включение за флагами и HTTP-проекция поверх живого `bcc`:

  * флаги `BOSSMAN_V3_ENABLED=1` ∧ `BOSSMAN_V3_ORGANIZATION=1` (по умолчанию OFF →
    все маршруты отвечают 503 `organization disabled`, ничего не создаётся);
  * исполнение — тот же `V3ExecutionBridge` → `UniversalComputerAgent` над
    замороженным V2 (реестр инструментов, decide_effect, approvals, верификация);
  * `agent_factory` маппит `AgentProfile.agent_id` → строку `agents` V2 по имени
    (или `metadata.v2_agent_id`): лестница уровней — реальные агенты, не один build_agent;
  * каждому контракту — своя задача/run V2 (`kind=organization`), чтобы аудит
    `tool_calls`, approvals (`task#<id>`) и стоимость были видны в UI как обычно;
  * `cost_meter` берёт факт из `task_runs.cost_usd` этого run'а, не из оценки контракта;
  * запросы владельцу — обычные approvals (`kind=org_review`), отчёты — шина событий.

Без установленного рядом `bossman_v3` фича честно выключена (Command Center CI ставит
только bcc). Организация — синхронный код: гоняется в рабочем потоке, а вызовы в V2
планируются на цикл svc через `CommandCenterRuntime(loop=...)`.
"""
from __future__ import annotations

import asyncio
import os
import threading
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Request

from ..db import agents as agents_t, task_runs as runs_t, tasks as tasks_t, utcnow
from . import Feature

router = APIRouter(prefix="/org")   # монтируется под /api → /api/org/...

REVIEW_KIND = "org_review"
TASK_KIND = "organization"


def _env_bool(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def enabled() -> bool:
    return _env_bool("BOSSMAN_V3_ENABLED") and _env_bool("BOSSMAN_V3_ORGANIZATION")


class OrganizationService:
    """Живая организация поверх svc. Создаётся в setup только при включённых флагах."""

    def __init__(self, svc: Any, loop: asyncio.AbstractEventLoop) -> None:
        from bossman_v3.adapters.command_center import CommandCenterRuntime
        from bossman_v3.organization import (DeterministicPlanner, OrganizationRuntime, OrganizationStore,
                                             V3ExecutionBridge)
        from ..tools import REGISTRY

        self.svc = svc
        self.rt = CommandCenterRuntime(loop=loop)
        self.root = Path(svc.settings.data_dir) / "organization"
        self.root.mkdir(parents=True, exist_ok=True)
        self.store = OrganizationStore(self.root / "organization.sqlite")
        self.lock = threading.Lock()
        bridge = V3ExecutionBridge(agent_factory=self._agent_factory, journal_root=self.root / "journals",
                                   cost_meter=self._cost_meter)
        self.runtime = OrganizationRuntime(
            store=self.store, execution=bridge, human_review=_ApprovalsPort(self),
            reporter=_BusReporter(self), planner=DeterministicPlanner(lambda t: REGISTRY.get(t) is not None),
            failure_root=str(self.root / "failures"))

    # ------------------------------------------------------------ V2 binding

    def _agent_factory(self, agent_id: str, contract):
        from bossman_v3.adapters.command_center import build_agent
        profile = next((a for a in self.store.agents() if a.agent_id == agent_id), None)
        want = (profile.metadata.get("v2_agent_id") if profile is not None and hasattr(profile, "metadata") else None)
        agent, task, run_id = self.rt.call(self._bind(agent_id, want, contract))
        return build_agent(self.rt, self.svc, task=task, agent=agent, run_id=run_id)

    async def _bind(self, agent_id: str, v2_agent_id: Any, contract) -> tuple[dict, dict, int]:
        async with self.svc.db.session() as s:
            if v2_agent_id:
                row = (await s.execute(sa.select(agents_t).where(agents_t.c.id == int(v2_agent_id)))).first()
            else:
                row = (await s.execute(sa.select(agents_t).where(agents_t.c.name == agent_id))).first()
            if row is None:
                raise RuntimeError(f"organization agent {agent_id!r} has no V2 agent (by name or metadata.v2_agent_id)")
            agent = dict(row._mapping)
            bound = dict(contract.metadata.get("v2") or {})
            task = None
            if bound.get("task_id"):
                trow = (await s.execute(sa.select(tasks_t).where(tasks_t.c.id == int(bound["task_id"])))).first()
                task = dict(trow._mapping) if trow is not None else None
            if task is None:
                res = await s.execute(sa.insert(tasks_t).values(
                    title=f"[org {contract.mission_id}/{contract.work_id}] {contract.goal[:200]}",
                    prompt=contract.goal, agent_id=agent["id"], status="running", priority=5,
                    max_retries=0, kind=TASK_KIND, created_at=utcnow(), updated_at=utcnow(),
                    meta={"organization": {"mission_id": contract.mission_id, "work_id": contract.work_id}}))
                task_id = int(res.inserted_primary_key[0])
                # run без аренды: очередь V2 его не берёт (claim только queued), recover не трогает
                rres = await s.execute(sa.insert(runs_t).values(
                    task_id=task_id, attempt=0, status="running", worker_lease_until=None,
                    started_at=utcnow(), model_alias="organization"))
                await s.commit()
                trow = (await s.execute(sa.select(tasks_t).where(tasks_t.c.id == task_id))).first()
                task = dict(trow._mapping)
                bound = {"task_id": task_id, "run_id": int(rres.inserted_primary_key[0]), "agent_id": agent["id"]}
                contract.metadata["v2"] = bound
                self.store.save_work(contract, state=_current_state(self.store, contract))
        return agent, task, int(bound["run_id"])

    def _cost_meter(self, contract, agent_id: str, res, elapsed_s: float):
        from bossman_v3.organization import Resources
        bound = dict(contract.metadata.get("v2") or {})
        usd = 0.0
        if bound.get("run_id"):
            async def q():
                async with self.svc.db.session() as s:
                    return (await s.execute(sa.select(runs_t.c.cost_usd).where(runs_t.c.id == int(bound["run_id"])))).scalar()
            usd = float(self.rt.call(q()) or 0.0)
        return Resources(usd=usd, compute_seconds=int(round(elapsed_s)))

    async def finish_v2_task(self, contract, status: str) -> None:
        bound = dict(contract.metadata.get("v2") or {})
        if not bound.get("task_id"):
            return
        async with self.svc.db.session() as s:
            await s.execute(sa.update(tasks_t).where(tasks_t.c.id == int(bound["task_id"])).values(
                status=status, updated_at=utcnow()))
            await s.execute(sa.update(runs_t).where(runs_t.c.id == int(bound["run_id"])).values(
                status=status, finished_at=utcnow()))
            await s.commit()

    # ------------------------------------------------------------ blocking API (worker thread)

    def run_mission(self, mission_id: str) -> dict:
        with self.lock:
            status = self.runtime.run_mission(mission_id)
            self._sync_v2_tasks(mission_id)
        return status.to_dict()

    def resume(self) -> list[dict]:
        with self.lock:
            return [s.to_dict() for s in self.runtime.resume()]

    def _sync_v2_tasks(self, mission_id: str) -> None:
        from bossman_v3.organization import TaskState
        for w in self.store.works(mission_id):
            if w["state"] in (TaskState.COMPLETED.value, TaskState.FAILED.value):
                self.rt.call(self.finish_v2_task(w["contract"], "completed" if w["state"] == TaskState.COMPLETED.value else "failed"))


def _current_state(store, contract):
    from bossman_v3.organization import TaskState
    row = store.work(contract.work_id)
    return TaskState(row["state"]) if row else TaskState.PLANNED


class _ApprovalsPort:
    def __init__(self, org: OrganizationService) -> None:
        self.org = org

    def request(self, contract, reason: str) -> str:
        bound = dict(contract.metadata.get("v2") or {})
        preview = (f"Организация: контракт {contract.work_id} миссии {contract.mission_id} ждёт владельца — "
                   f"{reason}\nцель: {contract.goal[:300]}")[:1000]
        appr = self.org.rt.call(self.org.svc.approvals.create(REVIEW_KIND, preview, task_id=bound.get("task_id"),
                                                              run_id=bound.get("run_id")))
        return str((appr or {}).get("id", ""))


class _BusReporter:
    def __init__(self, org: OrganizationService) -> None:
        self.org = org

    def report(self, status) -> None:
        try:
            self.org.rt.call(self.org.svc.bus.emit("org.mission.status", **status.to_dict()), timeout=10)
        except Exception:  # noqa: BLE001 — отчёт наверх не должен ронять цикл организации
            pass


# ------------------------------------------------------------------ routes

def _org(request: Request) -> OrganizationService:
    org = getattr(request.app.state.svc, "organization", None)
    if org is None:
        raise HTTPException(503, detail=getattr(request.app.state.svc, "organization_reason", "organization disabled"))
    return org


@router.get("/snapshot")
async def snapshot(request: Request) -> dict:
    org = _org(request)
    return (await asyncio.to_thread(org.runtime.snapshot)).to_dict()


@router.get("/departments")
async def departments(request: Request) -> list[dict]:
    return [d.to_dict() for d in await asyncio.to_thread(_org(request).runtime.departments)]


@router.post("/departments")
async def add_department(request: Request) -> dict:
    from bossman_v3.organization import Department
    org = _org(request)
    body = await request.json()
    try:
        d = Department.from_dict(body)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, detail=f"bad department: {exc}") from None
    await asyncio.to_thread(org.runtime.register_department, d)
    return d.to_dict()


@router.get("/agents")
async def agents(request: Request) -> list[dict]:
    return [a.to_dict() for a in await asyncio.to_thread(_org(request).store.agents)]


@router.post("/agents")
async def add_agent(request: Request) -> dict:
    from bossman_v3.organization import AgentProfile
    org = _org(request)
    body = await request.json()
    try:
        a = AgentProfile.from_dict(body)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, detail=f"bad agent: {exc}") from None
    try:
        await asyncio.to_thread(org.runtime.register_agent, a)
    except KeyError as exc:
        raise HTTPException(400, detail=str(exc)) from None
    return a.to_dict()


@router.get("/missions")
async def missions(request: Request) -> list[dict]:
    org = _org(request)
    return await asyncio.to_thread(org.store.missions)


@router.post("/missions")
async def create_mission(request: Request) -> dict:
    """{mission_id?, title, department_id, goal?, contracts?: [DelegationContract.to_dict()], ...}.
    Без `contracts` — один контракт из цели: шаги достроит планировщик или миссия
    встанет BLOCKED/no_executable_steps на первом run."""
    from bossman_v3.organization import DelegationContract, EvidenceRequirement, Resources, RiskTier
    import uuid
    org = _org(request)
    body = await request.json()
    dept = str(body.get("department_id") or "")
    if not dept:
        raise HTTPException(400, detail="department_id is required")
    mission_id = str(body.get("mission_id") or f"m-{uuid.uuid4().hex[:8]}")
    try:
        if body.get("contracts"):
            contracts = [DelegationContract.from_dict({**c, "mission_id": mission_id}) for c in body["contracts"]]
        else:
            goal = str(body.get("goal") or "").strip()
            if not goal:
                raise HTTPException(400, detail="goal or contracts is required")
            contracts = [DelegationContract(
                work_id=str(body.get("work_id") or "w1"), mission_id=mission_id, department_id=dept, goal=goal,
                required_capability=str(body.get("required_capability") or "terminal.run"),
                success_criteria=list(body.get("success_criteria") or [goal]),
                evidence_required=[EvidenceRequirement.from_dict(e) for e in body.get("evidence_required") or []],
                budget=Resources.from_dict(body.get("budget") or {"usd": 1.0, "compute_seconds": 600}),
                risk=RiskTier(str(body.get("risk") or "low").lower()), side_effect=bool(body.get("side_effect", True)))]
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, detail=f"bad mission: {exc}") from None
    try:
        status = await asyncio.to_thread(
            lambda: org.runtime.receive_mission(mission_id, title=str(body.get("title") or contracts[0].goal[:80]),
                                                department_id=dept, contracts=contracts, source="http"))
    except KeyError as exc:
        raise HTTPException(400, detail=str(exc)) from None
    return status.to_dict()


@router.post("/missions/{mission_id}/run")
async def run_mission(mission_id: str, request: Request) -> dict:
    org = _org(request)
    if await asyncio.to_thread(org.store.mission, mission_id) is None:
        raise HTTPException(404, detail="mission not found")
    return await asyncio.to_thread(org.run_mission, mission_id)


@router.post("/resume")
async def resume(request: Request) -> list[dict]:
    return await asyncio.to_thread(_org(request).resume)


@router.get("/learning")
async def learning(request: Request) -> list[dict]:
    rows = await asyncio.to_thread(_org(request).store.learning)
    return [{"agent_id": a, "capability": c, **dict(p)} for a, c, p in rows]


async def setup(svc: Any) -> None:
    svc.organization = None
    if not enabled():
        svc.organization_reason = "organization disabled (BOSSMAN_V3_ENABLED and BOSSMAN_V3_ORGANIZATION are off)"
        return
    try:
        import bossman_v3.organization  # noqa: F401
    except Exception as exc:  # noqa: BLE001 — bcc без ядра: честно выключено
        svc.organization_reason = f"organization unavailable: bossman_v3 not importable ({type(exc).__name__})"
        return
    svc.organization = OrganizationService(svc, asyncio.get_running_loop())
    svc.organization_reason = ""
    await svc.bus.emit("org.enabled", root=str(svc.organization.root))


FEATURE = Feature(name="organization", router=router, setup=setup)
