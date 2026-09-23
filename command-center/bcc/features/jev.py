"""Jev decision engine — Phase 1 SHADOW wiring (docs/JEV_DECISION_ENGINE.md).

Flag ``BOSSMAN_JEV_ENABLED`` is OFF by default. Off at startup → this feature
registers NOTHING (no hook, no bus subscription, no network): Bossman behaves
exactly as before, with or without a Jev key. Only the read-only
``GET /api/jev/status`` answers, so the owner can see "disabled".

On: one NON-critical ``pick_model`` hook that always returns ``None`` — it can
never choose a model — and schedules a background shadow job. The job waits
briefly for the Smart Router's own ``router.route_selected`` event for the task
(the router stays the only authority), asks Jev off the hot path, and records
the decision and its agreement with what Bossman actually used.

Egress gate: the prompt excerpt goes to a cloud API, so a task is shadowed only
when the router's own fail-closed cloud policy allows cloud for it AND its
privacy is "public". Otherwise the record says ``egress_not_allowed`` and no
request is made.

Kill switch without rebuild: create ``<data_dir>/jev.disabled`` (or the path
in ``BOSSMAN_JEV_KILL_FILE``) — checked on every call — or unset the flag and
restart.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Request

from ..db import models as models_t, providers as providers_t
from ..jev import config as jev_config, upstream
from ..jev.client import JevClient
from ..jev.decision import Baseline, JevDecisionProvider, ShadowRecorder, TaskContext
from ..v2.model_router import derive_local
from . import Feature

router = APIRouter()
ROUTE_WAIT_S = 3.0
MAX_PENDING = 16


class _State:
    def __init__(self, data_dir: Path):
        self.cfg = jev_config.load()
        self.recorder = ShadowRecorder(Path(data_dir) / "jev" / "shadow-decisions.jsonl")
        self.provider = JevDecisionProvider(JevClient(self.cfg), self.recorder, self.cfg)
        self.routes: dict[Any, int] = {}          # task_id → model_id from router.route_selected
        self.waiters: dict[Any, asyncio.Future] = {}
        self.sem = asyncio.Semaphore(2)
        self.jobs: set[asyncio.Task] = set()
        self.dropped = 0


async def _baseline(svc, model_id: int | None, agent: dict, source: str) -> Baseline:
    tools = agent.get("tools") if isinstance(agent.get("tools"), list) else []
    base = Baseline(source=source, tool_route="none" if not tools else None)
    if model_id is None:
        return base
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(models_t.c.alias, models_t.c.kind,
                                         providers_t.c.kind.label("pk"), providers_t.c.base_url)
                               .select_from(models_t.outerjoin(providers_t,
                                                               models_t.c.provider_id == providers_t.c.id))
                               .where(models_t.c.id == int(model_id)))).first()
    if row is None:
        return base
    m = row._mapping
    local, _why = derive_local(m["kind"], m["pk"], m["base_url"])
    base.model_alias = m["alias"]
    base.locality = "local" if local else "cloud"
    base.model_route = None if local else "cloud_reasoner"
    return base


async def _egress_allowed(svc, task: dict, agent: dict) -> bool:
    from .router import _rules, cloud_policy
    meta = task.get("meta") if isinstance(task.get("meta"), dict) else {}
    if str(meta.get("privacy") or "public") != "public":
        return False
    allowed, _why = cloud_policy(meta, agent, await _rules(svc))
    return allowed


async def _shadow_job(svc, state: _State, task: dict, agent: dict, waiter: asyncio.Future) -> None:
    task_id = task.get("id")
    try:
        try:
            model_id, source = await asyncio.wait_for(waiter, ROUTE_WAIT_S), "router"
        except asyncio.TimeoutError:
            model_id, source = agent.get("model_id"), "agent_model"
        finally:
            state.waiters.pop(task_id, None)
            state.routes.pop(task_id, None)
        baseline = await _baseline(svc, model_id, agent, source)
        tools = [str(t) for t in (agent.get("tools") or []) if isinstance(t, (str, int))]
        ctx = TaskContext(task_id=task_id, kind=str(task.get("kind") or "generic"),
                          prompt=str(task.get("prompt") or ""), tools=tools)
        if not await _egress_allowed(svc, task, agent):
            state.recorder.write({"task_id": task_id, "task_class": ctx.kind, "mode": "shadow",
                                  "prompt_sha": ctx.fingerprint(), "jev": None, "agreement": {},
                                  "fallback_reason": "egress_not_allowed", "authoritative": False,
                                  "baseline": baseline.__dict__})
            return
        async with state.sem:
            record = await asyncio.to_thread(state.provider.shadow, ctx, baseline)
        await svc.bus.emit("jev.shadow_decision", task_id=task_id,
                           fallback_reason=record.get("fallback_reason"),
                           agreement=record.get("agreement"), latency_ms=record.get("latency_ms"))
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 — shadow never affects the task
        return


async def _consume(svc, state: _State) -> None:
    q = svc.bus.subscribe()
    try:
        while True:
            msg = await q.get()
            if msg.get("kind") != "router.route_selected":
                continue
            task_id = msg.get("task_id")
            waiter = state.waiters.get(task_id)
            if waiter is not None and not waiter.done():
                waiter.set_result(msg.get("model_id"))
            else:
                state.routes[task_id] = msg.get("model_id")
                while len(state.routes) > 256:
                    state.routes.pop(next(iter(state.routes)))
    except asyncio.CancelledError:
        raise
    finally:
        svc.bus.unsubscribe(q)


def _make_hook(svc, state: _State):
    async def jev_shadow(task, agent):
        # Returns None ALWAYS: Jev never picks the model in phase 1.
        if not jev_config.load().active:
            return None
        if len(state.jobs) >= MAX_PENDING:
            state.dropped += 1
            return None
        loop = asyncio.get_running_loop()
        task_id = task.get("id")
        waiter = loop.create_future()
        if task_id in state.routes:
            waiter.set_result(state.routes.pop(task_id))
        state.waiters[task_id] = waiter
        job = asyncio.create_task(_shadow_job(svc, state, dict(task), dict(agent or {}), waiter),
                                  name=f"bcc-jev-shadow-{task_id}")
        state.jobs.add(job)
        job.add_done_callback(state.jobs.discard)
        return None
    return jev_shadow


async def _setup(svc) -> None:
    svc.jev = None
    if not jev_config.load().enabled:
        return                                   # disabled: no hook, no subscription, no network
    jev_config.set_default_kill_dir(Path(svc.settings.data_dir))   # env path still wins
    state = _State(svc.settings.data_dir)
    svc.jev = state
    svc.engine.add_hook("pick_model", _make_hook(svc, state), critical=False)
    task = asyncio.create_task(_consume(svc, state), name="bcc-jev-shadow-consumer")
    if hasattr(svc, "_tasks"):
        svc._tasks.append(task)


@router.get("/jev/status")
async def status(request: Request):
    svc = request.app.state.svc
    state: _State | None = getattr(svc, "jev", None)
    out: dict[str, Any] = {
        "decision": jev_config.load().public(),
        "browser": jev_config.load_browser().public(),
        "phase": "shadow",
        "authoritative": False,
        "wired": state is not None,
        "upstream": {"repo": upstream.UPSTREAM_REPO, "commit": upstream.UPSTREAM_COMMIT,
                     "license": upstream.UPSTREAM_LICENSE},
        "contract": upstream.CONTRACT_STATUS,
    }
    if state is not None:
        out["client"] = state.provider.client.status()
        out["shadow"] = state.recorder.summary()
        out["dropped"] = state.dropped
    return out


@router.get("/jev/shadow")
async def shadow_records(request: Request, limit: int = 50):
    state: _State | None = getattr(request.app.state.svc, "jev", None)
    if state is None:
        return {"records": [], "wired": False}
    limit = max(1, min(int(limit), 500))
    return {"records": state.recorder.recent[-limit:], "wired": True}


FEATURE = Feature(name="jev", router=router, setup=_setup)
