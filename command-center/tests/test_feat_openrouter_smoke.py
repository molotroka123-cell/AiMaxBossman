"""Optional real smoke против живого OpenRouter.

Запускается ТОЛЬКО если задан OPENROUTER_API_KEY; иначе весь модуль skipped.
Правила бюджета: один catalog fetch + максимум ОДИН inference на самой
дешёвой/бесплатной модели каталога. Никаких платных массовых прогонов.
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("OPENROUTER_API_KEY"),
    reason="OPENROUTER_API_KEY не задан — реальный smoke не выполняется")


async def test_real_connect_discover_one_cheap_call(env):
    key = os.environ["OPENROUTER_API_KEY"]
    prov = (await env.client.post("/api/providers", json={
        "name": "openrouter-live", "kind": "openai_compat",
        "base_url": "https://openrouter.ai/api/v1", "api_key": key})).json()

    # 1) Connect: валидация ключа + каталог
    r = await env.client.post(f"/api/openrouter/{prov['id']}/connect")
    assert r.status_code == 200, r.text
    assert r.json()["models"] > 0

    # 2) выбрать самую дешёвую модель с tools (цена за 1М токенов)
    catalog = (await env.client.get(
        f"/api/openrouter/{prov['id']}/catalog?limit=200&include_stale=false")).json()
    priced = sorted(catalog, key=lambda m: (m["price_in"] or 0) + (m["price_out"] or 0))
    target = next((m for m in priced if "tools" in (m["supported_parameters"] or [])),
                  priced[0])
    pinned = (await env.client.post(
        f"/api/openrouter/{prov['id']}/pin",
        json={"remote_id": target["remote_id"], "alias": "or-live-smoke"})).json()

    # 3) ОДИН inference через существующий путь registry→adapter
    seen = {}

    def factory(model, provider):
        seen["model"] = model["name"]
        seen["url"] = provider["base_url"]
        from bcc.providers import build_adapter
        real_key = env.svc.vault.decrypt(provider["api_key_enc"])
        return build_adapter(provider["kind"], base_url=provider["base_url"],
                             api_key=real_key)

    env.svc.registry.adapter_factory = factory
    agent = (await env.client.post("/api/agents", json={
        "name": "or-live-agent", "model_id": pinned["model_id"],
        "max_steps": 1, "max_tokens": 16})).json()
    task = (await env.client.post("/api/tasks", json={
        "title": "live smoke", "prompt": "Ответь ровно: OK",
        "agent_id": agent["id"]})).json()
    rid = await env.svc.engine.claim()
    await env.svc.engine.execute(rid)
    detail = (await env.client.get(f"/api/tasks/{task['task']['id']}")).json()
    assert detail["task"]["status"] == "completed", detail.get("error")
    assert seen["model"] == target["remote_id"]
    assert seen["url"] == "https://openrouter.ai/api/v1"
