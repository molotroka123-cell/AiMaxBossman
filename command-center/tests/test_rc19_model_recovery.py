"""RC19 audit (models): a dead or refused model must not hide a live one.

Owner PC, 2026-09-28: "привет" failed after ~11 s. The agent's OpenRouter model
was refused by the pricing gate (removed from the catalog), its fallback on
:8082 was dead, the ladder retried that same route and then "switched" to the
same dead fallback, and a live Ollama model was never considered. The registry
kept showing :8082 as online because a failed real call never reached it.

Each test pins one link of that chain, plus the neighbours the audit found
(cloud consent on the alternate, privacy refusal, degraded path, checkpoint).
"""
from __future__ import annotations

from datetime import timedelta

import pytest
import sqlalchemy as sa

from bcc import model_health as mh
from bcc.db import agents as agents_t, models as models_t, task_runs, tasks
from bcc.provider_governance import GovernedAdapter
from bcc.providers import ChatResult, Health, ProviderError
from bcc.reality import recovery as rec
from bcc.tools import REGISTRY, ToolResult, ToolSpec
from bcc.v2.tables import provider_catalog_models as catalog_t

from .conftest import FakeAdapter
from .helpers import make_stack

OLLAMA = "http://127.0.0.1:11434/v1"
DEAD = "http://127.0.0.1:8082/v1"
OPENROUTER = "https://openrouter.ai/api/v1"
PAID = "https://api.example.invalid/v1"


class _Endpoint:
    """One fake server per base_url; records every call that reached it."""

    def __init__(self, url: str, calls: list, *, alive: bool, served=None):
        self.url, self.calls, self.alive, self.served = url, calls, alive, served

    async def chat(self, model, messages, **kw):
        self.calls.append((self.url, model, kw.get("tools")))
        if not self.alive:
            raise ProviderError(f"нет связи с {self.url}: ConnectError", kind="network")
        return ChatResult(text=f"ответ {model}", tokens_in=5, tokens_out=2, model=model)

    async def health(self):
        return Health(status="ok" if self.alive else "offline", latency_ms=1)

    async def list_models(self):
        return [m["id"] for m in await self.list_model_info()]

    async def list_model_info(self):
        return list(self.served or [])


def _factory(calls: list, alive: set[str], served: dict | None = None):
    def make(model, provider):
        url = provider["base_url"]
        return _Endpoint(url, calls, alive=url in alive, served=(served or {}).get(url))
    return make


async def _model(env, url, name, kind, *, price=None):
    provider = (await env.client.post("/api/providers", json={
        "name": f"p-{name}", "kind": "openai_compat", "base_url": url,
        "api_key": "sk-test-abcd"})).json()
    body = {"provider_id": provider["id"], "name": name, "alias": name, "kind": kind}
    if price is not None:
        body.update(price_in=price, price_out=price)
    return (await env.client.post("/api/models", json=body)).json()


async def _agent(env, model_id, fallback_id=None, **extra):
    body = {"name": f"agent-{model_id}-{fallback_id}", "model_id": model_id, "max_steps": 1,
            **extra}
    if fallback_id is not None:
        body["fallback_model_id"] = fallback_id
    return (await env.client.post("/api/agents", json=body)).json()


async def _task(env, agent_id, *, max_retries=2, meta=None):
    task = (await env.client.post("/api/tasks", json={
        "title": "t", "prompt": "привет", "agent_id": agent_id, "run_now": True,
        "max_retries": max_retries})).json()["task"]
    if meta is not None:
        async with env.svc.db.session() as s:
            await s.execute(sa.update(tasks).where(tasks.c.id == task["id"]).values(meta=meta))
            await s.commit()
    return task


async def _drive(env, limit=12):
    engine = env.svc.engine
    engine.retry_base_delay = 0
    engine.retry_max_delay = 0
    for _ in range(limit):
        rid = await engine.claim()
        if rid is None:
            break
        await engine.execute(rid)


async def _run(env, task_id) -> dict:
    async with env.svc.db.session() as s:
        row = (await s.execute(sa.select(task_runs).where(
            task_runs.c.task_id == task_id))).first()
    return dict(row._mapping)


async def _model_row(env, model_id) -> dict:
    async with env.svc.db.session() as s:
        return dict((await s.execute(sa.select(models_t).where(
            models_t.c.id == model_id))).first()._mapping)


# ------------------------------------------------------------ pure ladder

def test_our_own_gate_refusal_is_not_retried_on_the_same_route():
    cls = rec.classify_failure("unknown cloud pricing; refresh catalog before inference",
                               kind="budget")
    assert cls == rec.POLICY
    assert rec.classify_failure("router policy denied model 3: cloud disabled",
                                kind="policy") == rec.POLICY
    rung = rec.next_rung(rec.Ladder(cls), current_model_id=1, retries_left=5, max_retries=5)
    assert rung.name != rec.RETRY_SAME


def test_the_alternate_is_never_a_model_already_tried_for_this_failure():
    live = mh.HealthRecord()                                   # never probed, reachable
    rung = rec.next_rung(rec.Ladder(rec.TRANSIENT), current_model_id=1, fallback_model_id=2,
                         healthy_models=[(2, mh.record_observation(None, mh.HEALTHY)), (3, live)],
                         retries_left=0, tried={2})
    assert rung.name == rec.ALTERNATE_MODEL and rung.model_id == 3


def test_an_old_success_is_history_not_health():
    old = mh._now() - timedelta(seconds=mh.STALE_AFTER_SECONDS + 60)
    stale = mh.HealthRecord(status=mh.HEALTHY, checked_at=old, samples=3, successes=3)
    fresh = mh.record_observation(None, mh.HEALTHY)
    assert stale.usable() is False and fresh.usable() is True
    # it ranks with "unknown now", not above a live model and not with broken ones
    assert stale.rank_key()[0] == mh.HealthRecord().rank_key()[0] == 1
    assert mh.select_fallback([("stale", stale), ("fresh", fresh)]) == "fresh"
    assert mh.select_fallback([("stale", stale)]) == "stale"


# ------------------------------------------------- the owner's "привет"

async def test_refused_model_and_dead_fallback_recover_onto_a_live_local_model(env):
    calls: list = []
    cloud = await _model(env, OPENROUTER, "vendor/removed:free", "cloud")    # no known price
    dead = await _model(env, DEAD, "pilot", "local")
    live = await _model(env, OLLAMA, "qwen3:8b", "local")
    env.svc.registry.adapter_factory = _factory(calls, {OLLAMA})
    await env.svc.registry.record_model_health(dead["id"], mh.HEALTHY)      # probed once, long ago
    agent = await _agent(env, cloud["id"], dead["id"])
    task = await _task(env, agent["id"], max_retries=2)

    await _drive(env)

    run = await _run(env, task["id"])
    assert run["status"] == "completed", run["error"]
    assert run["model_alias"] == live["alias"]
    # the dead port was called once per failure, not re-offered as the "alternate"
    assert [c[0] for c in calls].count(DEAD) == 1
    # and the registry learned it: offline + measured provider_down, not "online"
    row = await _model_row(env, dead["id"])
    assert row["status"] == "offline" and row["status_detail"].startswith("runtime: ")
    assert mh.HealthRecord.from_dict(row["health"]).status == mh.PROVIDER_DOWN


async def test_with_nothing_live_the_owner_sees_both_causes_without_retries(env):
    calls: list = []
    cloud = await _model(env, OPENROUTER, "vendor/removed:free", "cloud")
    dead = await _model(env, DEAD, "pilot", "local")
    env.svc.registry.adapter_factory = _factory(calls, set())
    agent = await _agent(env, cloud["id"], dead["id"])
    task = await _task(env, agent["id"], max_retries=2)

    await _drive(env)

    run = await _run(env, task["id"])
    assert run["status"] == "failed"
    assert "unknown cloud pricing" in run["error"] and "ConnectError" in run["error"]
    assert run["attempt"] == 0, "a refusal of our own gate was retried on the same route"
    assert len(calls) == 1


async def test_a_removed_catalog_model_is_named_as_removed(env):
    cloud = await _model(env, OPENROUTER, "vendor/removed:free", "cloud")
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter()
    async with env.svc.db.session() as s:
        await s.execute(sa.insert(catalog_t).values(
            provider_id=cloud["provider_id"], remote_id="vendor/removed:free", stale=True))
        await s.execute(sa.insert(catalog_t).values(
            provider_id=cloud["provider_id"], remote_id="vendor/other", stale=False))
        await s.commit()
    adapter, _model_row_ = await env.svc.registry.adapter_for(cloud["id"])
    assert isinstance(adapter, GovernedAdapter)
    with pytest.raises(ProviderError) as info:
        await adapter.chat("vendor/removed:free", [{"role": "user", "content": "x"}])
    assert info.value.kind == "budget"
    assert "no longer in the provider catalog" in str(info.value)


# --------------------------------------------------------- cloud consent

async def test_recovery_never_moves_a_local_task_onto_an_unconsented_cloud_model(env):
    calls: list = []
    dead = await _model(env, DEAD, "pilot", "local")
    paid = await _model(env, PAID, "paid-model", "cloud", price=15.0)
    await env.svc.registry.record_model_health(paid["id"], mh.HEALTHY)
    env.svc.registry.adapter_factory = _factory(calls, {PAID})
    agent = await _agent(env, dead["id"])
    task = await _task(env, agent["id"], max_retries=0)

    await _drive(env)

    assert (await _run(env, task["id"]))["status"] == "failed"
    assert not [c for c in calls if c[0] == PAID], "paid cloud model called without consent"


async def test_a_stored_alternate_is_checked_for_cloud_consent_even_unrouted(env):
    calls: list = []
    dead = await _model(env, DEAD, "pilot", "local")
    paid = await _model(env, PAID, "paid-model", "cloud", price=15.0)
    env.svc.registry.adapter_factory = _factory(calls, {PAID})
    agent = await _agent(env, dead["id"])
    await _task(env, agent["id"], max_retries=0)
    rid = await env.svc.engine.claim()
    async with env.svc.db.session() as s:
        await s.execute(sa.update(task_runs).where(task_runs.c.id == rid).values(
            checkpoint={"messages": [], "step": 0, "recovery_model_id": paid["id"]}))
        await s.commit()
    await env.svc.engine.execute(rid)
    assert not [c for c in calls if c[0] == PAID]


async def test_a_privacy_refusal_fails_the_run_instead_of_stranding_it(env):
    calls: list = []
    dead = await _model(env, DEAD, "pilot", "local")
    paid = await _model(env, PAID, "paid-model", "cloud", price=1.0)
    env.svc.registry.adapter_factory = _factory(calls, {PAID})
    agent = await _agent(env, dead["id"], paid["id"])        # owner-configured cloud fallback
    task = await _task(env, agent["id"], max_retries=0, meta={"privacy": "private"})

    await _drive(env, limit=2)

    run = await _run(env, task["id"])
    assert run["status"] == "failed", run["status"]
    assert "privacy" in (run["error"] or "")
    assert not [c for c in calls if c[0] == PAID]


# ------------------------------------------------ routed agent fallback

async def test_a_policy_refusal_of_the_agent_model_still_tries_its_fallback(env):
    calls: list = []
    cloud = await _model(env, OPENROUTER, "vendor/unpriced", "cloud")
    live = await _model(env, OLLAMA, "qwen3:8b", "local")
    env.svc.registry.adapter_factory = _factory(calls, {OLLAMA})
    agent = await _agent(env, cloud["id"], live["id"])
    task = await _task(env, agent["id"], max_retries=0, meta={"route": True})

    await _drive(env)

    run = await _run(env, task["id"])
    assert run["status"] == "completed", run["error"]
    assert run["model_alias"] == live["alias"]


# --------------------------------------------------------- ladder in engine

async def test_the_degraded_path_really_sends_no_tools(env):
    calls: list = []

    async def handler(args, ctx):
        return ToolResult(content="ok", one_line="ok")

    REGISTRY.register(ToolSpec(name="test.rc19", description="t", handler=handler,
                               input_schema={}, default_effect="auto"))
    try:
        live = await _model(env, OLLAMA, "qwen3:8b", "local")
        env.svc.registry.adapter_factory = _factory(calls, {OLLAMA})
        agent = await _agent(env, live["id"], tools=["test.rc19"])
        await _task(env, agent["id"], max_retries=0)
        rid = await env.svc.engine.claim()
        async with env.svc.db.session() as s:
            await s.execute(sa.update(task_runs).where(task_runs.c.id == rid).values(
                checkpoint={"messages": [], "step": 0,
                            "recovery_degrade": {"stream": False, "tools": False}}))
            await s.commit()
        await env.svc.engine.execute(rid)
    finally:
        REGISTRY.unregister("test.rc19")
    assert calls and calls[0][2] is None, "degraded path still sent the tool schemas"


async def test_a_saved_step_keeps_the_runs_recovery_state(env):
    stack = await make_stack(env.client)
    rid = await env.svc.engine.claim()
    ladder = {"failure_class": "silent", "spent": ["alternate_model"], "transitions": 1,
              "classes": ["silent"]}
    async with env.svc.db.session() as s:
        await s.execute(sa.update(task_runs).where(task_runs.c.id == rid).values(
            checkpoint={"messages": [], "step": 0, "recovery_ladder": ladder,
                        "recovery_model_id": stack["model"]["id"]}))
        await s.commit()
    await env.svc.engine._save_checkpoint(rid, [{"role": "user", "content": "x"}], 1, note="tools")
    cp = (await _run(env, stack["task"]["id"]))["checkpoint"]
    assert cp["step"] == 1 and cp["recovery_ladder"] == ladder
    assert cp["recovery_model_id"] == stack["model"]["id"]


async def test_a_model_switch_does_not_spend_the_owners_same_route_retries(env):
    stack = await make_stack(env.client, max_retries=1)
    other = await _model(env, OLLAMA, "qwen3:8b", "local")
    await env.svc.registry.record_model_health(other["id"], mh.HEALTHY)
    task = stack["task"]
    rid = await env.svc.engine.claim()
    await env.svc.engine._handle_failure(rid, task, "429 rate limit", [], 0)
    first = await _run(env, task["id"])
    assert (first["checkpoint"] or {}).get("recovery_model_id") == other["id"]
    await env.svc.engine._handle_failure(rid, task, "429 rate limit", [], 0)
    second = await _run(env, task["id"])
    assert second["status"] == "queued", "the owner's one retry was eaten by the model switch"
    assert second["checkpoint"]["note"] == f"recovery:{rec.RETRY_SAME}"


# ------------------------------------------------------ registry / router

async def test_check_model_requires_the_model_on_a_multi_model_server(env):
    calls: list = []
    wrong = await _model(env, OLLAMA, "pilot-7b", "local")
    env.svc.registry.adapter_factory = _factory(calls, {OLLAMA}, served={
        OLLAMA: [{"id": "qwen3:8b", "owned_by": "library"}, {"id": "llama3:8b"}]})
    out = await env.svc.registry.check_model(wrong["id"])
    assert out["status"] == "error" and "pilot-7b" in out["detail"]


async def test_check_model_is_lenient_for_a_single_model_llamacpp_server(env):
    calls: list = []
    m = await _model(env, "http://127.0.0.1:8080/v1", "local-7b", "local")
    env.svc.registry.adapter_factory = _factory(calls, {"http://127.0.0.1:8080/v1"}, served={
        "http://127.0.0.1:8080/v1": [{"id": "C:/models/qwen.gguf", "owned_by": "llamacpp"}]})
    assert (await env.svc.registry.check_model(m["id"]))["status"] == "online"
    # Ollama's implicit :latest tag is the same model
    tagged = await _model(env, OLLAMA, "qwen3", "local")
    env.svc.registry.adapter_factory = _factory(calls, {OLLAMA}, served={
        OLLAMA: [{"id": "qwen3:latest"}, {"id": "llama3:8b"}]})
    assert (await env.svc.registry.check_model(tagged["id"]))["status"] == "online"


async def test_healing_brings_a_runtime_offline_model_back(env):
    from bcc.features import healing
    calls: list = []
    m = await _model(env, OLLAMA, "qwen3:8b", "local")
    await env.svc.registry.mark_runtime_offline(m["id"], "нет связи")
    env.svc.registry.adapter_factory = _factory(calls, set(), served={OLLAMA: [{"id": "qwen3:8b"}]})
    await healing._tick(env.svc)
    row = await _model_row(env, m["id"])
    assert row["status"] == "offline" and row["status_detail"].startswith("runtime: "), \
        "a failed re-check must keep the model on the re-check list"
    env.svc.registry.adapter_factory = _factory(calls, {OLLAMA}, served={OLLAMA: [{"id": "qwen3:8b"}]})
    await healing._tick(env.svc)
    assert (await _model_row(env, m["id"]))["status"] == "online"


async def test_the_router_does_not_route_onto_a_model_in_measured_cooldown(env):
    from bcc.features import router as router_feature
    m = await _model(env, OLLAMA, "qwen3:8b", "local")
    async with env.svc.db.session() as s:
        await s.execute(sa.update(models_t).where(models_t.c.id == m["id"]).values(status="online"))
        await s.commit()
    rules = await router_feature._rules(env.svc)
    cand = {c.id: c for c in await router_feature._candidates(env.svc, rules)}
    assert cand[m["id"]].online is True
    await env.svc.registry.record_model_health(m["id"], mh.PROVIDER_DOWN, "runtime: 503")
    cand = {c.id: c for c in await router_feature._candidates(env.svc, rules)}
    assert cand[m["id"]].online is False


async def test_busy_memory_does_not_switch_to_a_local_fallback_that_fits_no_better(env, monkeypatch):
    from bcc.features import router as router_feature
    seen: list = []

    def factory(model, provider):
        async def record(calls, messages):
            seen.append(model["alias"])
        return FakeAdapter(f"via {model['alias']}", on_chat=record)

    env.svc.registry.adapter_factory = factory
    router_feature._ollama_cache.clear()
    monkeypatch.setattr(router_feature, "_measure_pool_sync", lambda: (20000, "amd-unified"))
    monkeypatch.setattr(router_feature, "_ollama_inventory_sync", lambda root: {})
    stack = await make_stack(env.client, prompt="привет")
    await _drive(env)
    seen.clear()
    big = await _model(env, OLLAMA, "big-70b", "local")
    async with env.svc.db.session() as s:
        await s.execute(sa.update(models_t).values(status="online", bench={"ram_mb": 30000}))
        await s.execute(sa.update(agents_t).where(agents_t.c.id == stack["agent"]["id"]).values(
            fallback_model_id=big["id"]))
        await s.commit()
    await _task(env, stack["agent"]["id"], max_retries=0)
    await _drive(env)
    assert seen == [stack["model"]["alias"]], seen


# ------------------------------------------------------- admission / bootstrap

async def test_automatic_selection_skips_an_agent_whose_model_is_certain_to_be_refused(env):
    from bcc.task_admission import select_executor
    cloud = await _model(env, OPENROUTER, "vendor/unpriced", "cloud")
    live = await _model(env, OLLAMA, "qwen3:8b", "local")
    first = await _agent(env, cloud["id"])
    second = await _agent(env, live["id"])
    async with env.svc.db.session() as s:
        chosen = await select_executor(s, prompt="привет", agent_id=None)
    assert chosen["id"] == second["id"] != first["id"]


async def test_an_env_bootstrapped_model_takes_its_price_from_a_synced_catalog(env):
    from bcc.features import openrouter as feature
    provider = (await env.client.post("/api/providers", json={
        "name": "or", "kind": "openai_compat", "base_url": OPENROUTER,
        "api_key": "sk-test-abcd"})).json()
    async with env.svc.db.session() as s:
        await s.execute(sa.insert(catalog_t).values(
            provider_id=provider["id"], remote_id="vendor/free:free", stale=False,
            raw_metadata={"id": "vendor/free:free",
                          "pricing": {"prompt": "0", "completion": "0"}}))
        await s.commit()
    created = await feature._ensure_models(env.svc, provider["id"], ["vendor/free:free"])
    assert created
    async with env.svc.db.session() as s:
        row = (await s.execute(sa.select(models_t).where(
            models_t.c.name == "vendor/free:free"))).first()._mapping
    assert row["pricing_known"] is True and row["price_in"] == 0.0 and row["price_out"] == 0.0
