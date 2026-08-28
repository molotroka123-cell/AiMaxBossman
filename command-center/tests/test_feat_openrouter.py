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
    assert "tools" in caps        # заявлен tools → проба выполнена
    # advertised vs verified сохранены
    stored = (await env.client.get(
        f"/api/openrouter/models/{pinned['model_id']}/capabilities")).json()
    assert any(c["capability"] == "chat" and c["verified"] for c in stored)


async def test_sync_without_key_422(env):
    prov = (await env.client.post("/api/providers", json={
        "name": "openrouter-nokey", "kind": "openai_compat",
        "base_url": "http://router/v1"})).json()
    r = await env.client.post(f"/api/openrouter/{prov['id']}/sync")
    assert r.status_code == 422       # нет ключа — понятная ошибка
