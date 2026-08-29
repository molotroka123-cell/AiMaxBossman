"""Feature 02/04 — OpenRouter: sync каталога, pin, live-пробы (fake-provider)."""
import httpx
import sqlalchemy as sa

from bcc.db import models as models_t, providers as providers_t
from bcc.v2 import openrouter_ext
from bcc.v2.tables import provider_catalog_models as catalog_t

from tests.v2.fake_provider_app import app as fake_app


def _patch_openrouter_transport(monkeypatch):
    """OpenRouterClient ходит в fake-provider через ASGI-транспорт, без сети."""
    transport = httpx.ASGITransport(app=fake_app)
    orig_init = openrouter_ext.OpenRouterClient.__init__

    def new_init(self, api_key, base_url=openrouter_ext.DEFAULT_BASE, transport=None):
        orig_init(self, api_key, base_url="http://router/v1", transport=httpx.ASGITransport(app=fake_app))
    monkeypatch.setattr(openrouter_ext.OpenRouterClient, "__init__", new_init)


async def _openrouter_provider(env):
    return (await env.client.post("/api/providers", json={
        "name": "openrouter", "kind": "openai_compat",
        "base_url": "http://router/v1", "api_key": "sk-or-test"})).json()


async def test_sync_pins_and_survives_refresh(env, monkeypatch):
    _patch_openrouter_transport(monkeypatch)
    prov = await _openrouter_provider(env)
    # каталог синхронизирован из fake (2 модели с метаданными)
    synced = await env.client.post(f"/api/openrouter/{prov['id']}/sync")
    assert synced.status_code == 200
    catalog = (await env.client.get(f"/api/openrouter/{prov['id']}/catalog")).json()
    ids = {c["remote_id"] for c in catalog}
    assert {"fake/fast", "fake/vision"} <= ids
    fast = next(c for c in catalog if c["remote_id"] == "fake/fast")
    assert fast["context_window"] == 32768 and "tools" in fast["supported_parameters"]

    # pin модели в активный реестр
    pinned = (await env.client.post(f"/api/openrouter/{prov['id']}/pin",
                                    json={"remote_id": "fake/fast", "alias": "or-fast"})).json()
    assert pinned["alias"] == "or-fast"
    async with env.svc.db.session() as s:
        m = (await s.execute(sa.select(models_t).where(models_t.c.alias == "or-fast"))).first()
    assert m is not None and m._mapping["kind"] == "cloud"

    # повторный sync НЕ разрушает pinned-модель и её алиас
    await env.client.post(f"/api/openrouter/{prov['id']}/sync")
    async with env.svc.db.session() as s:
        still = (await s.execute(sa.select(models_t).where(models_t.c.alias == "or-fast"))).first()
    assert still is not None
    # повторный pin того же алиаса — идемпотентно (already)
    again = (await env.client.post(f"/api/openrouter/{prov['id']}/pin",
                                   json={"remote_id": "fake/fast", "alias": "or-fast"})).json()
    assert again.get("already") is True


async def test_probe_chat_and_tools(env, monkeypatch):
    _patch_openrouter_transport(monkeypatch)
    prov = await _openrouter_provider(env)
    await env.client.post(f"/api/openrouter/{prov['id']}/sync")
    pinned = (await env.client.post(f"/api/openrouter/{prov['id']}/pin",
                                    json={"remote_id": "fake/fast", "alias": "or-fast"})).json()
    probed = (await env.client.post(f"/api/openrouter/models/{pinned['model_id']}/probe")).json()
    caps = {p["capability"]: p["verified"] for p in probed["probes"]}
    assert caps.get("chat") is True
    assert caps.get("tools") is True          # заявлен tools → проба реально прошла
    assert caps.get("structured_output") is True
    assert caps.get("vision") is None         # не заявлен → пробу не гоняли
    # advertised vs verified сохранены
    stored = (await env.client.get(
        f"/api/openrouter/models/{pinned['model_id']}/capabilities")).json()
    assert any(c["capability"] == "chat" and c["verified"] for c in stored)
    tools_row = next(c for c in stored if c["capability"] == "tools")
    assert tools_row["advertised"] is True and tools_row["verified"] is True


async def test_sync_without_key_422(env):
    prov = (await env.client.post("/api/providers", json={
        "name": "openrouter-nokey", "kind": "openai_compat",
        "base_url": "http://router/v1"})).json()
    r = await env.client.post(f"/api/openrouter/{prov['id']}/sync")
    assert r.status_code == 422       # нет ключа — понятная ошибка


# ---------- Connect: валидация ключа + авто-каталог ----------

async def test_connect_validates_key_and_syncs(env, monkeypatch):
    _patch_openrouter_transport(monkeypatch)
    prov = await _openrouter_provider(env)
    r = await env.client.post(f"/api/openrouter/{prov['id']}/connect")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["models"] >= 2 and body["cached"] is False
    # каталог появился сразу: selector может выбирать без ручного ввода id
    catalog = (await env.client.get(f"/api/openrouter/{prov['id']}/catalog")).json()
    assert {"fake/fast", "fake/vision"} <= {c["remote_id"] for c in catalog}


async def test_connect_invalid_key_clean_error(env, monkeypatch):
    orig_init = openrouter_ext.OpenRouterClient.__init__

    def new_init(self, api_key, base_url=openrouter_ext.DEFAULT_BASE, transport=None):
        orig_init(self, api_key, base_url="http://router/v1",
                  transport=httpx.ASGITransport(app=fake_app))
    monkeypatch.setattr(openrouter_ext.OpenRouterClient, "__init__", new_init)
    prov = (await env.client.post("/api/providers", json={
        "name": "openrouter-bad", "kind": "openai_compat",
        "base_url": "http://router/v1", "api_key": "bad"})).json()
    r = await env.client.post(f"/api/openrouter/{prov['id']}/connect")
    assert r.status_code == 400
    msg = r.json()["error"]["message"]
    assert "401" in msg and "bad" not in msg      # чистая ошибка, ключ не эхом


async def test_set_key_and_reconnect(env, monkeypatch):
    _patch_openrouter_transport(monkeypatch)
    prov = (await env.client.post("/api/providers", json={
        "name": "openrouter-nokey", "kind": "openai_compat",
        "base_url": "http://router/v1"})).json()
    # без ключа connect — 422
    assert (await env.client.post(f"/api/openrouter/{prov['id']}/connect")).status_code == 422
    # сохранили ключ → connect проходит
    r = await env.client.patch(f"/api/openrouter/{prov['id']}/key",
                               json={"api_key": "sk-or-test"})
    assert r.status_code == 200
    r = await env.client.post(f"/api/openrouter/{prov['id']}/connect")
    assert r.status_code == 200 and r.json()["ok"] is True
    # ключ наружу не отдаётся: маска, не значение
    provs = (await env.client.get("/api/providers")).json()
    me = next(p for p in provs if p["id"] == prov["id"])
    assert "sk-or-test" not in (me.get("api_key_masked") or "")


# ---------- Cache: TTL, force, переживание недоступности ----------

async def test_sync_ttl_cache_hit_and_force_refresh(env, monkeypatch):
    _patch_openrouter_transport(monkeypatch)
    prov = await _openrouter_provider(env)
    calls = {"n": 0}
    orig_list = openrouter_ext.OpenRouterClient.list_models

    async def counting(self):
        calls["n"] += 1
        return await orig_list(self)

    monkeypatch.setattr(openrouter_ext.OpenRouterClient, "list_models", counting)

    first = (await env.client.post(f"/api/openrouter/{prov['id']}/sync")).json()
    assert first["cached"] is False and calls["n"] == 1
    # повторный sync в пределах TTL — из кэша, без сети
    second = (await env.client.post(f"/api/openrouter/{prov['id']}/sync")).json()
    assert second["cached"] is True and calls["n"] == 1
    # force (кнопка Refresh) — реальный поход в сеть
    forced = (await env.client.post(f"/api/openrouter/{prov['id']}/sync?force=true")).json()
    assert forced["cached"] is False and calls["n"] == 2
    # каталог цел
    catalog = (await env.client.get(f"/api/openrouter/{prov['id']}/catalog")).json()
    assert "fake/fast" in {c["remote_id"] for c in catalog}


async def test_outage_keeps_cached_catalog(env, monkeypatch):
    _patch_openrouter_transport(monkeypatch)
    prov = await _openrouter_provider(env)
    await env.client.post(f"/api/openrouter/{prov['id']}/sync")

    async def dead(self):
        raise httpx.ConnectError("network is down")

    monkeypatch.setattr(openrouter_ext.OpenRouterClient, "list_models", dead)
    r = await env.client.post(f"/api/openrouter/{prov['id']}/sync?force=true")
    assert r.status_code == 503
    body = r.json()["error"]
    assert body["cached_models"] >= 2 and body["last_synced_at"]
    # кэш жив: catalog читается из БД, stale не разметился
    catalog = (await env.client.get(f"/api/openrouter/{prov['id']}/catalog")).json()
    assert {"fake/fast", "fake/vision"} <= {c["remote_id"] for c in catalog}
    assert all(c["stale"] is False for c in catalog)


async def test_duplicate_model_ids_deduplicated(env, monkeypatch):
    _patch_openrouter_transport(monkeypatch)
    prov = await _openrouter_provider(env)
    orig_list = openrouter_ext.OpenRouterClient.list_models

    async def duplicated(self):
        cards = await orig_list(self)
        return cards + [cards[0]]          # remote отдал тот же id дважды

    monkeypatch.setattr(openrouter_ext.OpenRouterClient, "list_models", duplicated)
    res = (await env.client.post(f"/api/openrouter/{prov['id']}/sync?force=true")).json()
    catalog = (await env.client.get(f"/api/openrouter/{prov['id']}/catalog")).json()
    ids = [c["remote_id"] for c in catalog]
    assert len(ids) == len(set(ids))       # дубль схлопнут по (provider, remote_id)
    assert res["synced"] == len(ids)


# ---------- Селектор → существующий путь исполнения ----------

async def test_pinned_model_used_by_engine_existing_path(env, monkeypatch):
    """Закреплённая модель доезжает до движка по обычному пути registry→adapter,
    без нового роутера: фабрика адаптеров получает (model, provider) и задачу
    выполняет с model=fake/fast на base_url провайдера."""
    _patch_openrouter_transport(monkeypatch)
    prov = await _openrouter_provider(env)
    await env.client.post(f"/api/openrouter/{prov['id']}/sync")
    pinned = (await env.client.post(f"/api/openrouter/{prov['id']}/pin",
                                    json={"remote_id": "fake/fast", "alias": "or-fast"})).json()

    seen = {}

    def factory(model, provider):
        seen["model_name"] = model["name"]
        seen["provider_url"] = provider["base_url"]
        from tests.conftest import FakeAdapter
        return FakeAdapter(text="42")

    env.svc.registry.adapter_factory = factory

    agent = (await env.client.post("/api/agents", json={
        "name": "or-agent", "model_id": pinned["model_id"]})).json()
    task = (await env.client.post("/api/tasks", json={
        "title": "t", "prompt": "сколько? один цифрой", "agent_id": agent["id"]})).json()
    tid = task["task"]["id"]
    rid = await env.svc.engine.claim()
    assert rid is not None
    await env.svc.engine.execute(rid)
    detail = (await env.client.get(f"/api/tasks/{tid}")).json()
    assert detail["task"]["status"] == "completed"
    assert seen["model_name"] == "fake/fast"
    assert seen["provider_url"] == "http://router/v1"
