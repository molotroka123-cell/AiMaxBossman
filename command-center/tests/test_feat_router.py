"""Feature 02 Router: скоринг над реестром + хук pick_model (A–E из §8 мастера)."""
import sqlalchemy as sa

from bcc.db import models as models_t
from bcc.v2.model_router import ModelCandidate, RouteRequest, route

from .conftest import FakeAdapter
from .helpers import make_stack


def _cand(alias, **kw):
    kw.setdefault("price_in", 0.0)
    kw.setdefault("price_out", 0.0)
    return ModelCandidate(id=alias, alias=alias, **kw)


def test_coding_picks_coder_by_role():
    models = [_cand("local-fast", capabilities={"tools"}, role_scores={"coding": 0.2}),
              _cand("local-coder", capabilities={"coding"}, role_scores={"coding": 0.9})]
    d = route(RouteRequest(task_type="coding", requires={"coding"}), models)
    assert d.model.alias == "local-coder"


def test_unhealthy_excluded_fallback():
    models = [_cand("local-coder", online=False, capabilities={"coding"}),
              _cand("cloud-rev", local=False, capabilities={"coding"})]
    d = route(RouteRequest(task_type="coding", requires={"coding"}), models)
    assert d.model.alias == "cloud-rev"
    assert "unhealthy/offline" in d.rejected["local-coder"][0]


def test_cloud_excluded_when_budget_zero():
    models = [_cand("cloud-rev", local=False, capabilities={"coding"})]
    d = route(RouteRequest(task_type="coding", requires={"coding"}, cloud_allowed=False), models)
    assert d.model is None and "cloud disabled" in d.rejected["cloud-rev"]


def test_insufficient_ram_rejected():
    models = [_cand("big", capabilities=set(), memory_mb=70000)]
    d = route(RouteRequest(task_type="generic", available_memory_mb=40000), models)
    assert d.model is None and "memory" in d.rejected["big"][0]


async def test_pick_hook_overrides_agent_model(env):
    seen = []

    def factory(model, provider):
        return FakeAdapter(f"via {model['alias']}", on_chat=_rec(seen, model["alias"]))
    env.svc.registry.adapter_factory = factory
    stack = await make_stack(env.client)
    # добавим быструю модель с ролью coding и пометим её online
    fast = (await env.client.post("/api/models", json={
        "provider_id": stack["provider"]["id"], "name": "coder", "alias": "router-coder",
        "kind": "local", "caps": {"coding": True}})).json()
    await env.client.patch("/api/router/rules",
                           json={"role_scores": {"router-coder": {"coding": 0.9}}})
    async with env.svc.db.session() as s:
        await s.execute(sa.update(models_t).values(status="online"))
        await s.commit()
    # задача типа coding → роутер выберет router-coder, не модель агента.
    # kind выставляем до запуска (composer выставит meta.route аналогично)
    task = (await env.client.post("/api/tasks", json={
        "title": "код", "prompt": "напиши функцию", "agent_id": stack["agent"]["id"],
        "run_now": False})).json()["task"]
    from bcc.db import tasks as tt
    async with env.svc.db.session() as s:
        await s.execute(sa.update(tt).where(tt.c.id == task["id"]).values(kind="coding"))
        await s.commit()
    await env.svc.engine.enqueue(task["id"])
    for _ in range(10):                       # в очереди есть и задача из make_stack
        rid = await env.svc.engine.claim()
        if rid is None:
            break
        await env.svc.engine.execute(rid)
    assert "router-coder" in seen
    explain = (await env.client.get(f"/api/router/explain?task_id={task['id']}")).json()
    assert explain["route"]["alias"] == "router-coder"


def _rec(seen, alias):
    async def on_chat(_c, _m):
        seen.append(alias)
    return on_chat
