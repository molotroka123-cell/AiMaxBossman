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


def test_pareto_frontier_and_negative_control_dominated():
    from bcc.v2.model_router import pareto_frontier, pick_pareto_model

    points = [
        {"task_type": "generic", "alias": "fast-100", "runs": 5, "ok_runs": 5,
         "success_rate": 1.0, "mean_duration_s": 5.1, "memory_mb": 16000},
        {"task_type": "generic", "alias": "slow-100", "runs": 5, "ok_runs": 5,
         "success_rate": 1.0, "mean_duration_s": 46.0, "memory_mb": 32000},
        {"task_type": "generic", "alias": "mid-88", "runs": 9, "ok_runs": 8,
         "success_rate": 8 / 9, "mean_duration_s": 39.6, "memory_mb": 20000},
    ]
    front = pareto_frontier(points, min_runs=2, min_success_rate=0.8)
    by_alias = {p.alias: p for p in front}
    assert by_alias["fast-100"].pareto_optimal is True
    # Negative control: slower/heavier or lower-success models are dominated
    assert by_alias["slow-100"].pareto_optimal is False
    assert "fast-100" in by_alias["slow-100"].dominated_by
    assert by_alias["mid-88"].pareto_optimal is False
    rec = pick_pareto_model(points, "generic", min_runs=2, min_success_rate=0.8)
    assert rec is not None and rec.alias == "fast-100"


async def test_router_pareto_endpoint(env):
    from datetime import datetime, timedelta, timezone
    from bcc.db import task_runs as runs_t

    stack = await make_stack(env.client)
    tid = stack["task"]["id"]
    t0 = datetime(2026, 10, 19, 1, 0, 0, tzinfo=timezone.utc)
    async with env.svc.db.session() as s:
        await s.execute(sa.insert(runs_t).values([
            {"task_id": tid, "attempt": 1, "status": "completed",
             "model_alias": "fast-model", "started_at": t0,
             "finished_at": t0 + timedelta(seconds=5)},
            {"task_id": tid, "attempt": 2, "status": "completed",
             "model_alias": "slow-model", "started_at": t0,
             "finished_at": t0 + timedelta(seconds=40)},
        ]))
        await s.commit()
    res = (await env.client.get("/api/router/pareto?task_type=generic&min_runs=1")).json()
    assert res["recommended"]["alias"] == "fast-model"
    assert res["speedup_vs_slowest_x"] == 8.0

