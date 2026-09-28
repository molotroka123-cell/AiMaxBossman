"""RC19: model availability follows measurements and the live catalog.

Seen on the owner's data copy (2026-09-28):
  * local endpoints :8081/:8082 were stopped, yet their models stayed «online»;
  * OpenRouter withdrew `nex-agi/nex-n2.5-pro:free`, yet the registry kept it
    «online», the CMD default agent was auto-selected with it and runs died on
    «unknown cloud pricing»;
  * «free» must be the LIVE catalog price 0/0 — a `:free` suffix or a
    hand-typed 0/0 is not evidence.

Every catalog here is a fake ASGI provider or a table row; no network.
"""
from datetime import timedelta

import httpx
import sqlalchemy as sa

from bcc.db import agents as agents_t, models as models_t, utcnow
from bcc.features import healing, openrouter as openrouter_feature
from bcc.features import router as router_feature
from bcc.providers import Health
from bcc.v2 import openrouter_ext, openrouter_identity
from bcc.v2.tables import provider_catalog_models as catalog_t

from tests.v2.fake_provider_app import app as fake_app

from .conftest import FakeAdapter
from .helpers import make_stack


def _patch_openrouter_transport(monkeypatch, app=fake_app):
    orig_init = openrouter_ext.OpenRouterClient.__init__

    def new_init(self, api_key, base_url=openrouter_ext.DEFAULT_BASE, transport=None):
        orig_init(self, api_key, base_url="http://router/v1", transport=httpx.ASGITransport(app=app))
    monkeypatch.setattr(openrouter_ext.OpenRouterClient, "__init__", new_init)


async def _provider(env, *, name, base_url, key="sk-test-abcd"):
    return (await env.client.post("/api/providers", json={
        "name": name, "kind": "openai_compat", "base_url": base_url, "api_key": key})).json()


async def _model(env, provider_id, name, *, kind="cloud", status="online", **values):
    row = (await env.client.post("/api/models", json={
        "provider_id": provider_id, "name": name, "alias": name, "kind": kind})).json()
    async with env.svc.db.session() as s:
        await s.execute(sa.update(models_t).where(models_t.c.id == row["id"]).values(
            status=status, **values))
        await s.commit()
    return row


async def _status(env, model_id):
    async with env.svc.db.session() as s:
        return (await s.execute(sa.select(models_t.c.status, models_t.c.status_detail)
                                .where(models_t.c.id == model_id))).first()._mapping


def _spy_events(env, monkeypatch):
    events: list[tuple[str, dict]] = []
    original = env.svc.bus.emit

    async def spy(event, /, **kw):
        events.append((event, kw))
        return await original(event, **kw)
    monkeypatch.setattr(env.svc.bus, "emit", spy)
    return events


# ---------------------------------------------------------------- local health

class _EndpointAdapter(FakeAdapter):
    """health() answers like a local server that is up or stopped."""

    def __init__(self, alive: dict):
        super().__init__("ok")
        self.alive = alive

    async def health(self) -> Health:
        if self.alive["up"]:
            return Health(status="ok", latency_ms=1)
        return Health(status="offline", detail="нет связи с http://127.0.0.1:8082/v1: ConnectError")


async def test_dead_local_endpoint_is_offline_after_probe_and_online_after_revival(env):
    alive = {"up": False}
    env.svc.registry.adapter_factory = lambda m, p: _EndpointAdapter(alive)
    local = await _provider(env, name="local-fast", base_url="http://127.0.0.1:8082/v1")
    cloud = await _provider(env, name="cloud", base_url="https://cloud.example.invalid/v1")
    stale = utcnow() - timedelta(minutes=10)
    dead = await _model(env, local["id"], "qwen-fast", kind="local", last_check=stale)
    fresh = await _model(env, local["id"], "qwen-fresh", kind="local", last_check=utcnow())
    remote = await _model(env, cloud["id"], "cloud-x", last_check=stale)

    probed = await healing.probe_local_models(env.svc, {})
    assert probed == [dead["id"]]               # fresh check and cloud model untouched
    assert (await _status(env, dead["id"]))["status"] == "offline"
    assert (await _status(env, fresh["id"]))["status"] == "online"
    assert (await _status(env, remote["id"]))["status"] == "online"

    alive["up"] = True
    async with env.svc.db.session() as s:
        await s.execute(sa.update(models_t).where(models_t.c.id == dead["id"]).values(last_check=stale))
        await s.commit()
    await healing.probe_local_models(env.svc, {})
    assert (await _status(env, dead["id"]))["status"] == "online"


async def test_local_probe_can_be_switched_off(env):
    env.svc.registry.adapter_factory = lambda m, p: _EndpointAdapter({"up": False})
    local = await _provider(env, name="local-main", base_url="http://127.0.0.1:8081/v1")
    m = await _model(env, local["id"], "qwen-main", kind="local",
                     last_check=utcnow() - timedelta(hours=1))
    assert await healing.probe_local_models(env.svc, {"local_probe_seconds": 0}) == []
    assert (await _status(env, m["id"]))["status"] == "online"


# ---------------------------------------------------------------- live catalog

async def test_sync_marks_models_missing_from_live_catalog_unavailable(env, monkeypatch):
    _patch_openrouter_transport(monkeypatch)
    prov = await _provider(env, name="openrouter", base_url="http://router/v1", key="sk-or-test")
    gone = await _model(env, prov["id"], "nex-agi/nex-n2.5-pro:free",
                        price_in=0.0, price_out=0.0, pricing_known=True)
    back = await _model(env, prov["id"], "fake/fast", status="unavailable")

    result = (await env.client.post(f"/api/openrouter/{prov['id']}/sync?force=true")).json()
    assert result["unavailable"] == ["nex-agi/nex-n2.5-pro:free"]
    row = await _status(env, gone["id"])
    assert row["status"] == "unavailable" and "каталог" in row["status_detail"]
    # a model the catalog lists again is not «online» until a probe proves it
    assert (await _status(env, back["id"]))["status"] == "unknown"


async def test_empty_catalog_changes_no_status(env, monkeypatch):
    from fastapi import FastAPI
    empty = FastAPI()

    @empty.get("/v1/models")
    async def models():
        return {"data": []}
    _patch_openrouter_transport(monkeypatch, app=empty)
    prov = await _provider(env, name="openrouter", base_url="http://router/v1", key="sk-or-test")
    m = await _model(env, prov["id"], "fake/fast")
    await env.client.post(f"/api/openrouter/{prov['id']}/sync?force=true")
    assert (await _status(env, m["id"]))["status"] == "online"


async def test_catalog_check_tick_uses_the_installation_provider(env, monkeypatch):
    _patch_openrouter_transport(monkeypatch)
    events = _spy_events(env, monkeypatch)
    assert await openrouter_feature.catalog_check(env.svc) is None      # no OpenRouter yet
    prov = await _provider(env, name="openrouter", base_url="http://router/v1", key="sk-or-test")
    await openrouter_identity.remember_provider(env.svc.db, env.svc.vault, prov["id"])
    gone = await _model(env, prov["id"], "nex-agi/nex-n2.5-pro:free")
    result = await openrouter_feature.catalog_check(env.svc)
    assert result["unavailable"] == ["nex-agi/nex-n2.5-pro:free"]
    assert (await _status(env, gone["id"]))["status"] == "unavailable"
    assert ("model.catalog_missing", {"provider_id": prov["id"],
                                      "aliases": ["nex-agi/nex-n2.5-pro:free"]}) in events


async def test_free_means_live_catalog_price_not_suffix(env):
    orp = await _provider(env, name="OpenRouter", base_url="https://openrouter.ai/api/v1")
    typed = await _model(env, orp["id"], "vendor/typed-zero:free",
                         price_in=0.0, price_out=0.0, pricing_known=True)
    live_free = await _model(env, orp["id"], "vendor/live-free:free",
                             price_in=0.0, price_out=0.0, pricing_known=True)
    priced = await _model(env, orp["id"], "vendor/priced:free",
                          price_in=0.0, price_out=0.0, pricing_known=True)
    async with env.svc.db.session() as s:
        for remote, p_in, p_out in (("vendor/live-free:free", 0.0, 0.0),
                                    ("vendor/priced:free", 0.1, 0.4)):
            await s.execute(sa.insert(catalog_t).values(
                provider_id=orp["id"], remote_id=remote, display_name=remote,
                price_in=p_in, price_out=p_out, stale=False, last_synced_at=utcnow()))
        await s.commit()
    by_id = {int(c.id): c for c in await router_feature._candidates(env.svc, {})}
    # never synced into the catalog → price unknown and not routable, whatever the suffix says
    assert by_id[typed["id"]].price_in is None and not by_id[typed["id"]].online
    assert router_feature._is_free(by_id[live_free["id"]]) and by_id[live_free["id"]].online
    assert not router_feature._is_free(by_id[priced["id"]])
    assert by_id[priced["id"]].price_out == 0.4


# ---------------------------------------------------------------- routing

async def _withdrawn_agent_stack(env, monkeypatch, *, fallback: bool, cloud_allowed=True):
    seen: list[str] = []

    def factory(model, provider):
        async def record(calls, messages):
            seen.append(model["alias"])
        return FakeAdapter(f"via {model['alias']}", on_chat=record)

    env.svc.registry.adapter_factory = factory
    monkeypatch.setattr(router_feature, "_measure_pool_sync", lambda: (None, "none"))
    monkeypatch.setattr(router_feature, "_ollama_inventory_sync", lambda root: {})
    router_feature._ollama_cache.clear()
    stack = await make_stack(env.client, prompt="привет")
    while (rid := await env.svc.engine.claim()) is not None:
        await env.svc.engine.execute(rid)
    seen.clear()
    orp = await _provider(env, name="OpenRouter", base_url="https://openrouter.ai/api/v1")
    nex = await _model(env, orp["id"], "nex-agi/nex-n2.5-pro:free", status="unavailable",
                       price_in=None, price_out=None, pricing_known=False)
    async with env.svc.db.session() as s:
        await s.execute(sa.update(models_t).where(models_t.c.id == stack["model"]["id"])
                        .values(status="online"))
        await s.execute(sa.update(agents_t).where(agents_t.c.id == stack["agent"]["id"]).values(
            model_id=nex["id"],
            fallback_model_id=stack["model"]["id"] if fallback else None,
            permissions={"cloud_allowed": True} if cloud_allowed else {}))
        await s.commit()
    events = _spy_events(env, monkeypatch)
    task = (await env.client.post("/api/tasks", json={
        "title": "t", "prompt": "привет", "agent_id": stack["agent"]["id"],
        "run_now": True})).json()["task"]
    while (rid := await env.svc.engine.claim()) is not None:
        await env.svc.engine.execute(rid)
    final = (await env.client.get(f"/api/tasks/{task['id']}")).json()
    withdrawn = [kw for name, kw in events if name == "router.model_withdrawn"]
    return seen, withdrawn, final


async def test_withdrawn_agent_model_routes_to_fallback_before_the_call(env, monkeypatch):
    seen, withdrawn, final = await _withdrawn_agent_stack(env, monkeypatch, fallback=True)
    assert seen == ["local-7b"]                      # the withdrawn model was never called
    assert withdrawn and withdrawn[0]["outcome"] == "switched_to_fallback"
    assert "каталог" in withdrawn[0]["reason"]
    assert final["task"]["status"] == "completed"
    route = final["runs"][-1]["route"]
    assert route["fallback_from"] == "nex-agi/nex-n2.5-pro:free"


async def test_withdrawn_model_without_fallback_takes_free_ladder_local_first(env, monkeypatch):
    seen, withdrawn, final = await _withdrawn_agent_stack(env, monkeypatch, fallback=False)
    assert seen == ["local-7b"]
    assert withdrawn[0]["outcome"] == "switched_to_ladder"
    assert final["task"]["status"] == "completed"


async def test_auto_executor_skips_agent_with_withdrawn_model(env):
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter("ok")
    stack = await make_stack(env.client)
    orp = await _provider(env, name="OpenRouter", base_url="https://openrouter.ai/api/v1")
    nex = await _model(env, orp["id"], "nex-agi/nex-n2.5-pro:free", status="unavailable")
    first = (await env.client.post("/api/agents", json={
        "name": "CMD-NEX", "system_prompt": "x", "model_id": nex["id"]})).json()
    async with env.svc.db.session() as s:
        # make the withdrawn agent the one auto-selection would try first
        await s.execute(sa.update(agents_t).where(agents_t.c.id == first["id"]).values(id=0))
        await s.commit()
    pre = (await env.client.post("/api/tasks/preflight", json={"prompt": "", "agent_id": None})).json()
    assert pre["ok"] is True and pre["agent"]["id"] == stack["agent"]["id"]


async def test_second_openrouter_provider_is_priced_by_the_synced_catalog(env):
    # owner data: «OpenRouter free (test key)» was never synced; its typed 0/0 is not evidence
    canonical = await _provider(env, name="OpenRouter", base_url="https://openrouter.ai/api/v1")
    test_key = await _provider(env, name="OpenRouter free (test key)",
                               base_url="https://openrouter.ai/api/v1")
    gone = await _model(env, test_key["id"], "nex-agi/nex-n2.5-pro:free",
                        price_in=0.0, price_out=0.0, pricing_known=True)
    listed = await _model(env, test_key["id"], "nvidia/nemotron-3-ultra-550b-a55b:free",
                          price_in=0.0, price_out=0.0, pricing_known=True)
    async with env.svc.db.session() as s:
        await s.execute(sa.insert(catalog_t).values(
            provider_id=canonical["id"], remote_id="nvidia/nemotron-3-ultra-550b-a55b:free",
            display_name="nemotron", price_in=0.0, price_out=0.0, stale=False,
            last_synced_at=utcnow()))
        await s.commit()
    by_id = {int(c.id): c for c in await router_feature._candidates(env.svc, {})}
    assert not by_id[gone["id"]].online and by_id[gone["id"]].price_in is None
    assert by_id[listed["id"]].online and router_feature._is_free(by_id[listed["id"]])
