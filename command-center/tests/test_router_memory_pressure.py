"""RC19: busy unified memory moves work to a free model; nothing else changes.

The Smart Router's memory rule existed but live routing never fed it a
measured pool or a model size. These tests pin the wired behaviour:
measured pressure + configured free fallback + explicit cloud consent →
fallback for this run; any missing piece → the agent's own model, with a
visible reason.
"""
import sqlalchemy as sa

from bcc.db import agents as agents_t, models as models_t
from bcc.features import router as router_feature

from .conftest import FakeAdapter
from .helpers import make_stack


async def _stack(env, monkeypatch, *, free_mb, fallback_price=0.0, cloud_allowed=True,
                 resident=False, bench_ram_mb=30000):
    seen: list[str] = []

    def factory(model, provider):
        async def record(calls, messages):
            seen.append(model["alias"])
        return FakeAdapter(f"via {model['alias']}", on_chat=record)

    env.svc.registry.adapter_factory = factory
    router_feature._ollama_cache.clear()
    monkeypatch.setattr(router_feature, "_measure_pool_sync",
                        lambda: (free_mb, "amd-unified") if free_mb is not None else (None, "nvidia-smi"))
    monkeypatch.setattr(router_feature, "_ollama_inventory_sync",
                        lambda root: ({"local-7b": {"size_mb": 0.0, "resident": 1.0}} if resident else {}))
    stack = await make_stack(env.client, prompt="привет")
    # drain the make_stack task so it doesn't mix with the task under test
    while (rid := await env.svc.engine.claim()) is not None:
        await env.svc.engine.execute(rid)
    seen.clear()
    cloud_provider = (await env.client.post("/api/providers", json={
        "name": "облако", "kind": "openai_compat",
        "base_url": "https://cloud.example.invalid/v1", "api_key": "sk-test-cloud"})).json()
    cloud = (await env.client.post("/api/models", json={
        "provider_id": cloud_provider["id"], "name": "free-cloud", "alias": "free-cloud",
        "kind": "cloud"})).json()
    async with env.svc.db.session() as s:
        await s.execute(sa.update(models_t).values(status="online"))
        await s.execute(sa.update(models_t).where(models_t.c.id == stack["model"]["id"])
                        .values(bench={"ram_mb": bench_ram_mb}))
        await s.execute(sa.update(models_t).where(models_t.c.id == cloud["id"]).values(
            price_in=fallback_price, price_out=fallback_price, pricing_known=True))
        await s.execute(sa.update(agents_t).where(agents_t.c.id == stack["agent"]["id"]).values(
            fallback_model_id=cloud["id"],
            permissions={"cloud_allowed": True} if cloud_allowed else {}))
        await s.commit()
    events: list[tuple[str, dict]] = []
    original_emit = env.svc.bus.emit

    async def spy(name, **kw):
        events.append((name, kw))
        return await original_emit(name, **kw)

    monkeypatch.setattr(env.svc.bus, "emit", spy)
    task = (await env.client.post("/api/tasks", json={
        "title": "t", "prompt": "привет", "agent_id": stack["agent"]["id"],
        "run_now": True})).json()["task"]
    while (rid := await env.svc.engine.claim()) is not None:
        await env.svc.engine.execute(rid)
    pressure = [kw for name, kw in events if name == "router.memory_pressure"]
    return seen, pressure, task


async def test_busy_memory_switches_to_free_fallback(env, monkeypatch):
    # 20 GB free - 8 GB headroom = 12 GB < 30 GB model
    seen, pressure, _ = await _stack(env, monkeypatch, free_mb=20000)
    assert seen == ["free-cloud"]
    assert pressure and pressure[0]["outcome"] == "switched_to_fallback"
    assert "30000MB > available 12000MB" in pressure[0]["reason"]


async def test_enough_memory_keeps_local_model(env, monkeypatch):
    seen, pressure, _ = await _stack(env, monkeypatch, free_mb=90000)
    assert seen == ["local-7b"] and pressure == []


async def test_resident_local_model_costs_nothing_extra(env, monkeypatch):
    seen, pressure, _ = await _stack(env, monkeypatch, free_mb=9000, resident=True)
    assert seen == ["local-7b"] and pressure == []


async def test_unmeasured_memory_changes_nothing(env, monkeypatch):
    seen, pressure, _ = await _stack(env, monkeypatch, free_mb=None)
    assert seen == ["local-7b"] and pressure == []


async def test_no_cloud_consent_keeps_agent_model_with_reason(env, monkeypatch):
    seen, pressure, _ = await _stack(env, monkeypatch, free_mb=20000, cloud_allowed=False)
    assert seen == ["local-7b"]
    assert pressure[0]["outcome"] == "kept_agent_model"
    assert "облако" in pressure[0]["denied"] or "cloud" in pressure[0]["denied"]


async def test_paid_fallback_is_not_used_without_budget(env, monkeypatch):
    seen, pressure, _ = await _stack(env, monkeypatch, free_mb=20000, fallback_price=0.5)
    assert seen == ["local-7b"]
    assert pressure[0]["outcome"] == "kept_agent_model"
    assert "not free" in pressure[0]["denied"]


async def test_routed_task_feeds_measured_memory_into_disqualify(env, monkeypatch):
    router_feature._ollama_cache.clear()
    monkeypatch.setattr(router_feature, "_measure_pool_sync", lambda: (20000, "amd-unified"))
    monkeypatch.setattr(router_feature, "_ollama_inventory_sync", lambda root: {})
    rules = {"memory_headroom_mb": 8000}
    available, info = await router_feature.live_available_memory_mb(rules)
    assert available == 12000 and info["probe"] == "amd-unified"
    from bcc.v2.model_router import ModelCandidate, RouteRequest, route
    big = ModelCandidate(id=1, alias="big-local", memory_mb=30000, price_in=0.0, price_out=0.0)
    free = ModelCandidate(id=2, alias="free-cloud", local=False, price_in=0.0, price_out=0.0)
    decision = route(RouteRequest(task_type="coding", available_memory_mb=available), [big, free])
    assert decision.model.alias == "free-cloud"
    assert "memory" in decision.rejected["big-local"][0]
