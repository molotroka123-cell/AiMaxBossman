"""Model freshness tick: a measurement ages out after 30 minutes, so something must re-measure it - boundedly,
through the existing ``Registry.test_model``, never with a paid request, and honestly when it fails.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa

from bcc import health as health_mod, model_health as mh
from bcc.db import models as models_t
from bcc.features import model_freshness as mf

from .conftest import FakeAdapter
from .helpers import make_stack


async def _set_health(env, model_id: int, status: str, *, minutes_ago: float) -> None:
    when = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    record = mh.record_observation(None, status, "seeded", now=when)
    async with env.svc.db.session() as s:
        await s.execute(sa.update(models_t).where(models_t.c.id == model_id).values(health=record.to_dict()))
        await s.commit()


async def _add_model(env, stack, name: str, **columns) -> int:
    created = (await env.client.post("/api/models", json={
        "provider_id": stack["provider"]["id"], "name": name, "alias": name})).json()["id"]
    if columns:
        async with env.svc.db.session() as s:
            await s.execute(sa.update(models_t).where(models_t.c.id == created).values(**columns))
            await s.commit()
    return created


async def test_a_stale_model_in_use_is_measured_again_and_readiness_recovers(env):
    stack = await make_stack(env.client)
    mid = stack["model"]["id"]
    await _set_health(env, mid, mh.HEALTHY, minutes_ago=40)               # last success: history, not health
    comps = await health_mod.model_components(env.svc)
    assert comps["models"]["status"] == "stale" and comps["models"]["available"] == 0

    adapter = FakeAdapter("готово")
    env.svc.registry.adapter_factory = lambda m, p: adapter
    result = await mf.run_cycle(env.svc)
    assert [p["outcome"] for p in result["probed"]] == [mh.HEALTHY] and adapter.calls == 1
    assert (await env.svc.registry.model_health(mid)).usable() is True
    assert (await health_mod.model_components(env.svc))["models"]["status"] == "ok"


async def test_a_fresh_measurement_is_left_alone(env):
    stack = await make_stack(env.client)
    mid = stack["model"]["id"]
    await _set_health(env, mid, mh.HEALTHY, minutes_ago=1)
    adapter = FakeAdapter("готово")
    env.svc.registry.adapter_factory = lambda m, p: adapter
    result = await mf.run_cycle(env.svc)
    assert result["probed"] == [] and result["skipped"]["fresh"] == 1 and adapter.calls == 0


async def test_a_paid_cloud_model_is_never_probed_here(env):
    stack = await make_stack(env.client)
    paid = await _add_model(env, stack, "paid-cloud-model", kind="cloud", price_in=3.0, price_out=15.0,
                            pricing_known=True)
    unpriced = await _add_model(env, stack, "unpriced-cloud-model", kind="cloud", pricing_known=False)
    free = await _add_model(env, stack, "free-cloud-model:free", kind="cloud", price_in=0.0, price_out=0.0,
                            pricing_known=True)
    for model_id in (paid, unpriced, free):
        await _set_health(env, model_id, mh.HEALTHY, minutes_ago=60)
    await _set_health(env, stack["model"]["id"], mh.HEALTHY, minutes_ago=1)
    seen: list[str] = []

    class Spy(FakeAdapter):
        async def chat(self, model, messages, **kw):
            seen.append(model)
            return await super().chat(model, messages, **kw)

    env.svc.registry.adapter_factory = lambda m, p: Spy("готово")
    result = await mf.run_cycle(env.svc)
    assert seen == ["free-cloud-model:free"]                              # price known and zero: the only one
    assert result["skipped"]["paid_model_not_probed"] == 2


async def test_a_cycle_is_bounded(env):
    stack = await make_stack(env.client)
    ids = [stack["model"]["id"]] + [await _add_model(env, stack, f"local-extra-{n}") for n in range(5)]
    for model_id in ids:
        await _set_health(env, model_id, mh.HEALTHY, minutes_ago=45)
    adapter = FakeAdapter("готово")
    env.svc.registry.adapter_factory = lambda m, p: adapter
    result = await mf.run_cycle(env.svc)
    assert len(result["probed"]) == mf.MAX_PER_CYCLE == 3 and adapter.calls == 3
    assert result["candidates"] == 6


async def test_the_models_the_owner_runs_are_probed_before_the_oldest_leftovers(env):
    """On the owner's data the oldest records were dead local ports and old free-tier models; the local model in use
    (healthy a few hours ago) must not wait three cycles behind them."""
    stack = await make_stack(env.client)
    used = stack["model"]["id"]                                           # local, referenced by the agent
    old_cloud = await _add_model(env, stack, "old-free-cloud:free", kind="cloud", price_in=0.0, price_out=0.0,
                                 pricing_known=True)
    dead_local = await _add_model(env, stack, "dead-local-port")
    for model_id, minutes in ((old_cloud, 60 * 24 * 3), (dead_local, 60 * 24 * 2)):
        await _set_health(env, model_id, mh.HEALTHY, minutes_ago=minutes)
    await _set_health(env, used, mh.HEALTHY, minutes_ago=240)
    picked, _skipped = await mf._candidates(env.svc, datetime.now(timezone.utc))
    order = [row["id"] for row, _record in picked]
    assert order[0] in (used, dead_local) and order.index(old_cloud) == len(order) - 1
    assert order.index(used) < order.index(old_cloud)


async def test_a_slow_model_is_recorded_as_a_timeout_not_as_health(env, monkeypatch):
    stack = await make_stack(env.client)
    mid = stack["model"]["id"]
    await _set_health(env, mid, mh.HEALTHY, minutes_ago=45)
    monkeypatch.setattr(mf, "PROBE_TIMEOUT_S", 0.05)

    class Slow(FakeAdapter):
        async def chat(self, model, messages, **kw):
            await asyncio.sleep(5)

    env.svc.registry.adapter_factory = lambda m, p: Slow()
    result = await mf.run_cycle(env.svc)
    assert result["probed"][0]["outcome"] == mh.TIMEOUT
    record = await env.svc.registry.model_health(mid)
    assert record.status == mh.TIMEOUT and record.usable() is False
    comps = await health_mod.model_components(env.svc)
    assert comps["models"]["status"] == "error"                           # measured broken is shown as broken


async def test_a_failing_provider_does_not_stop_the_cycle_and_is_not_health(env):
    stack = await make_stack(env.client)
    bad = stack["model"]["id"]
    good = await _add_model(env, stack, "second-local")
    await _set_health(env, bad, mh.HEALTHY, minutes_ago=50)
    await _set_health(env, good, mh.HEALTHY, minutes_ago=40)

    def factory(model, provider):
        return FakeAdapter("готово", fail_times=99, error="connection refused") if model["name"] == "local-7b" \
            else FakeAdapter("готово")

    env.svc.registry.adapter_factory = factory
    result = await mf.run_cycle(env.svc)
    outcomes = {p["id"]: p["outcome"] for p in result["probed"]}
    assert outcomes[good] == mh.HEALTHY and outcomes[bad] == mh.PROVIDER_DOWN
    assert (await env.svc.registry.model_health(bad)).usable() is False


async def test_a_rejected_key_is_not_hammered(env):
    stack = await make_stack(env.client)
    mid = stack["model"]["id"]
    await _set_health(env, mid, mh.UNAUTHORIZED, minutes_ago=45)
    adapter = FakeAdapter("готово")
    env.svc.registry.adapter_factory = lambda m, p: adapter
    result = await mf.run_cycle(env.svc)
    assert result["probed"] == [] and adapter.calls == 0
    assert result["skipped"]["key_rejected_owner_must_fix"] == 1


async def test_the_switch_the_first_delay_and_the_status_endpoint(env, monkeypatch):
    stack = await make_stack(env.client)
    await _set_health(env, stack["model"]["id"], mh.HEALTHY, minutes_ago=45)
    adapter = FakeAdapter("готово")
    env.svc.registry.adapter_factory = lambda m, p: adapter

    monkeypatch.setenv("BOSSMAN_MODEL_FRESHNESS", "0")
    await mf._tick(env.svc)
    assert adapter.calls == 0                                             # switched off

    monkeypatch.setenv("BOSSMAN_MODEL_FRESHNESS", "1")
    monkeypatch.setitem(mf._state, "first_seen", None)
    monkeypatch.setattr(mf, "FIRST_DELAY_S", 0.05)
    await mf._tick(env.svc)                       # the FIRST call waits the start-up grace inside and measures right after
    assert adapter.calls == 1 and mf._state["cycles"] >= 1
    await mf._tick(env.svc)                       # later calls measure at once (the tick period is the pacing)
    assert adapter.calls == 1                     # ...and a fresh measurement is left alone

    body = (await env.client.get("/api/model-freshness")).json()
    assert body["enabled"] is True and body["limits"]["paid_models"] == "never probed here"
    assert body["models"]["total"] >= 1


async def test_local_model_is_not_probed_while_a_direct_gen_job_holds_the_gpu(env):
    """18.10: a local probe loads a cold model onto the GPU, times out at 40 s and Ollama logs 499 every ~11 min."""
    stack = await make_stack(env.client)
    mid = stack["model"]["id"]
    await _set_health(env, mid, mh.HEALTHY, minutes_ago=40)
    adapter = FakeAdapter("готово")
    env.svc.registry.adapter_factory = lambda m, p: adapter

    class _Busy:
        _running = "job-1"
        _waiting: list = []

    env.svc.direct_gen = _Busy()
    busy = await mf.run_cycle(env.svc)
    assert busy["probed"] == [] and adapter.calls == 0
    assert busy["skipped"]["gpu_job_active_local_not_probed"] == 1

    env.svc.direct_gen = None                      # negative control: with no GPU job the same model IS probed
    idle = await mf.run_cycle(env.svc)
    assert len(idle["probed"]) == 1 and adapter.calls == 1
