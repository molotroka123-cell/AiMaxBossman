"""Free cloud pool rotation (owner table 03.11: «Пул бесплатных облачных моделей — PARTIAL, авто-ротации нет»).

Live failure the owner listed: NIM Ultra 503 at night, kimi-k3 410, Gemini free 429 after ~15 calls — and a run simply
failed. With permissions.free_pool the engine now tries the next healthy free cloud model, records each failure as a
health observation (cooldown), and skips cooling models on the next call. Negative controls: no opt-in -> no rotation;
an auth failure (owner action) is not rotated through.
"""
from __future__ import annotations

import sqlalchemy as sa

from bcc import free_pool as fp
from bcc import model_health as mh
from bcc.db import agents as agents_t, models as models_t
from bcc.providers import ChatResult, ProviderError

from .conftest import FakeAdapter
from .helpers import make_stack
from .test_v21_tool_loop import FINISHED, _run_task


class _Fail(FakeAdapter):
    def __init__(self, exc: ProviderError):
        super().__init__()
        self.exc = exc

    async def chat(self, model, messages, **kw):
        self.calls += 1
        raise self.exc


class _Text(FakeAdapter):
    def __init__(self, text: str):
        super().__init__()
        self.text = text

    async def chat(self, model, messages, **kw):
        self.calls += 1
        return ChatResult(text=self.text, tokens_in=5, tokens_out=5)


async def _stack(env, *, perms, primary_exc):
    stack = await make_stack(env.client, max_steps=2, prompt="скажи привет")
    prov = (await env.client.post("/api/providers", json={
        "name": "free", "kind": "openai_compat", "base_url": "https://free.example/v1",
        "api_key": "sk-test-free"})).json()
    ids = {}
    for name in ("free-throttled", "free-silent", "free-good"):
        m = (await env.client.post("/api/models", json={
            "provider_id": prov["id"], "name": name, "alias": name, "kind": "cloud",
            "price_in": 0.0, "price_out": 0.0})).json()
        ids[name] = m["id"]
    async with env.svc.db.session() as s:
        await s.execute(sa.update(models_t).where(models_t.c.id.in_(list(ids.values()))).values(pricing_known=True))
        await s.execute(sa.update(agents_t).where(agents_t.c.id == stack["agent"]["id"]).values(
            fallback_model_id=None, permissions=perms))
        await s.commit()
    adapters = {
        "free-throttled": _Fail(ProviderError("429 too many", kind="rate_limit", status_code=429)),
        "free-silent": _Text("   "),
        "free-good": _Text("привет из бесплатного пула"),
    }
    primary = _Fail(primary_exc)

    def factory(m, p):
        return adapters.get(m.get("name"), primary)

    env.svc.registry.adapter_factory = factory
    return stack, ids, adapters, primary


async def _health(env, model_id):
    async with env.svc.db.session() as s:
        row = (await s.execute(sa.select(models_t.c.health).where(models_t.c.id == model_id))).first()
    return mh.HealthRecord.from_dict(row[0] if row else None)


async def test_pool_rotates_to_the_next_free_model_and_cools_the_failed_ones(env):
    stack, ids, adapters, primary = await _stack(
        env, perms={"free_pool": True}, primary_exc=ProviderError("503 down", kind="http", status_code=503))
    status = await _run_task(env, stack["task"]["id"], timeout=10, until=FINISHED)
    assert status == "completed", status
    assert adapters["free-good"].calls >= 1
    # the models that failed in the pool are now cooling: the next call skips them
    throttled = await _health(env, ids["free-throttled"])
    silent = await _health(env, ids["free-silent"])
    tried = adapters["free-throttled"].calls + adapters["free-silent"].calls
    assert tried >= 1, "rotation should have tried a failing pool model before the good one"
    for rec, called in ((throttled, adapters["free-throttled"].calls), (silent, adapters["free-silent"].calls)):
        if called:
            assert rec.in_cooldown(), rec.to_dict()


async def test_negative_control_no_opt_in_no_rotation(env):
    stack, ids, adapters, primary = await _stack(
        env, perms={}, primary_exc=ProviderError("503 down", kind="http", status_code=503))
    status = await _run_task(env, stack["task"]["id"], timeout=10, until=FINISHED)
    assert status != "completed" or adapters["free-good"].calls == 0
    assert adapters["free-good"].calls == 0


async def test_negative_control_auth_failure_is_not_rotated(env):
    stack, ids, adapters, primary = await _stack(
        env, perms={"free_pool": True}, primary_exc=ProviderError("401 bad key", kind="http", status_code=401))
    await _run_task(env, stack["task"]["id"], timeout=10, until=FINISHED)
    assert adapters["free-good"].calls == 0


def test_pool_order_skips_cooling_and_broken():
    healthy = mh.record_observation(None, mh.HEALTHY)
    cooling = mh.record_observation(None, mh.THROTTLED)
    unmeasured = mh.HealthRecord()
    order = fp.pool_order([(1, cooling), (2, unmeasured), (3, healthy)])
    assert order == [3, 2], order


def test_caps_and_curated_pool():
    assert fp.caps_compatible({"coding": True}, {"coding": True, "tools": True})
    assert not fp.caps_compatible({"coding": True}, {"vision": True}), "a coder is not replaced by a non-coder"
    assert not fp.caps_compatible({}, {"coding": True}, needs_tools=True)
    assert fp.pool_ids([3, "5", "x"]) == {3, 5}
    assert fp.pool_ids(True) is None


async def _with_paid_fallback(env, adapters, *, perms):
    stack, ids, _, _ = adapters
    prov = (await env.client.post("/api/providers", json={
        "name": "paid", "kind": "openai_compat", "base_url": "https://paid.example/v1", "api_key": "sk-test-paid"})).json()
    paid = (await env.client.post("/api/models", json={
        "provider_id": prov["id"], "name": "paid-glm", "alias": "paid-glm", "kind": "cloud",
        "price_in": 0.028, "price_out": 0.028})).json()
    async with env.svc.db.session() as s:
        await s.execute(sa.update(models_t).where(models_t.c.id == paid["id"]).values(pricing_known=True))
        await s.execute(sa.update(agents_t).where(agents_t.c.id == stack["agent"]["id"]).values(
            fallback_model_id=paid["id"], permissions=perms))
        await s.commit()
    return paid


async def test_harness_ladder_free_pool_before_paid_fallback(env):
    """Harness ladder: local -> free pool -> paid capped fallback. The paid model is not called while a free one answers."""
    built = await _stack(env, perms={}, primary_exc=ProviderError("503 down", kind="http", status_code=503))
    stack, ids, adapters, primary = built
    await _with_paid_fallback(env, built, perms={"free_pool": True, "free_pool_first": True})
    paid_adapter = _Text("ответ платной модели")
    factory = env.svc.registry.adapter_factory
    env.svc.registry.adapter_factory = lambda m, p: paid_adapter if m.get("name") == "paid-glm" else factory(m, p)
    status = await _run_task(env, stack["task"]["id"], timeout=10, until=FINISHED)
    assert status == "completed", status
    assert adapters["free-good"].calls >= 1
    assert paid_adapter.calls == 0, "the paid fallback must wait until the free pool is exhausted"


async def test_harness_ladder_paid_fallback_when_pool_exhausted(env):
    built = await _stack(env, perms={}, primary_exc=ProviderError("503 down", kind="http", status_code=503))
    stack, ids, adapters, primary = built
    await _with_paid_fallback(env, built, perms={"free_pool": [ids["free-throttled"]], "free_pool_first": True})
    paid_adapter = _Text("ответ платной модели")
    factory = env.svc.registry.adapter_factory
    env.svc.registry.adapter_factory = lambda m, p: paid_adapter if m.get("name") == "paid-glm" else factory(m, p)
    status = await _run_task(env, stack["task"]["id"], timeout=10, until=FINISHED)
    assert status == "completed", status
    assert adapters["free-throttled"].calls == 1 and adapters["free-good"].calls == 0, "curated pool only"
    assert paid_adapter.calls >= 1


async def test_rotate_does_not_poison_health_on_a_non_provider_error():
    """Review 03.11 (GLM-5.3 via 7API): a LookupError from adapter_for was recorded as PROVIDER_DOWN -> cooldown."""
    recorded = []

    async def call(mid):
        if mid == 1:
            raise LookupError("model row gone")
        return "ok"

    async def on_failure(mid, status, detail):
        recorded.append((mid, status))

    out = await fp.rotate([1, 2], call, on_failure)
    assert out.model_id == 2 and recorded == []
    assert out.tried[0][1] == "skipped"
