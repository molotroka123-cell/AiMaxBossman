"""Control API: auth, CRUD, маскирование ключей, единый формат ошибок."""
from __future__ import annotations

import httpx

from bcc.auth import HEADER

from .conftest import FakeAdapter, client_for, make_settings, start_app

SECRET = "sk-super-secret-key-9999"


async def test_auth_required(env):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=env.app),
                                 base_url="http://test") as anon:
        resp = await anon.get("/api/models")
        assert resp.status_code == 401
        assert "error" in resp.json() and resp.json()["error"]["message"]

        bad = await anon.post("/api/login", json={"token": "нет"})
        assert bad.status_code == 401

        ok = await anon.post("/api/login", json={"token": env.svc.auth.token})
        assert ok.status_code == 200 and ok.json() == {"ok": True}

        with_header = await anon.get("/api/models", headers={HEADER: env.svc.auth.token})
        assert with_header.status_code == 200


async def test_api_key_is_masked_everywhere(env):
    created = (await env.client.post("/api/providers", json={
        "name": "облако", "kind": "anthropic", "api_key": SECRET})).json()
    assert "api_key" not in created and "api_key_enc" not in created
    assert created["api_key_masked"] == "…9999"

    listed = (await env.client.get("/api/providers")).json()
    assert SECRET not in str(listed)
    assert listed[0]["api_key_masked"] == "…9999"

    # в БД ключ лежит зашифрованным, но расшифровывается тем же хранилищем
    import sqlalchemy as sa
    from bcc.db import providers as providers_t
    async with env.svc.db.session() as s:
        row = (await s.execute(sa.select(providers_t))).first()._mapping
    assert row["api_key_enc"] and SECRET not in row["api_key_enc"]
    assert env.svc.vault.decrypt(row["api_key_enc"]) == SECRET


async def test_models_and_agents_crud(env):
    provider = (await env.client.post("/api/providers", json={
        "name": "локальный", "kind": "openai_compat",
        "base_url": "http://127.0.0.1:8080/v1"})).json()

    assert (await env.client.get("/api/providers/kinds")).json() == ["openai_compat", "anthropic"]

    model = (await env.client.post("/api/models", json={
        "provider_id": provider["id"], "name": "qwen3-8b", "alias": "qwen",
        "context_window": 32768, "caps": {"tools": True}})).json()
    assert model["status"] == "unknown" and model["alias"] == "qwen"

    patched = (await env.client.patch(f"/api/models/{model['id']}",
                                      json={"context_window": 65536})).json()
    assert patched["context_window"] == 65536

    dup = await env.client.post("/api/models", json={
        "provider_id": provider["id"], "name": "qwen3-8b", "alias": "qwen"})
    assert dup.status_code == 400 and "занят" in dup.json()["error"]["message"]

    agent = (await env.client.post("/api/agents", json={
        "name": "писатель", "role": "тексты", "system_prompt": "пиши кратко",
        "model_id": model["id"], "max_steps": 2})).json()
    assert agent["enabled"] is True and agent["max_steps"] == 2

    agent = (await env.client.patch(f"/api/agents/{agent['id']}",
                                    json={"enabled": False})).json()
    assert agent["enabled"] is False
    assert len((await env.client.get("/api/agents")).json()) == 1

    assert (await env.client.delete(f"/api/agents/{agent['id']}")).json() == {"ok": True}
    assert (await env.client.get("/api/agents")).json() == []
    assert (await env.client.delete(f"/api/models/{model['id']}")).json() == {"ok": True}
    assert (await env.client.delete(f"/api/providers/{provider['id']}")).json() == {"ok": True}


async def test_error_shape_for_missing_and_invalid(env):
    missing = await env.client.get("/api/tasks/424242")
    assert missing.status_code == 404
    assert missing.json()["error"]["message"] == "задача не найдена"

    invalid = await env.client.post("/api/providers", json={"name": "x", "kind": "битый"})
    assert invalid.status_code == 400
    assert "hint" in invalid.json()["error"]

    broken = await env.client.post("/api/agents", json={"role": "без имени"})
    assert broken.status_code == 422 and broken.json()["error"]["message"].startswith("неверный")

    unknown_action = await env.client.post("/api/tasks/424242/run")
    assert unknown_action.status_code == 404


async def test_model_check_and_test_update_status(tmp_path):
    fake = FakeAdapter("работаю")
    app, svc = await start_app(make_settings(tmp_path), start_workers=False,
                               adapter_factory=lambda m, p: fake)
    async with client_for(app, svc) as client:
        provider = (await client.post("/api/providers", json={
            "name": "локальный", "kind": "openai_compat", "base_url": "http://x/v1"})).json()
        model = (await client.post("/api/models", json={
            "provider_id": provider["id"], "name": "local-7b"})).json()

        checked = (await client.post(f"/api/models/{model['id']}/check")).json()
        assert checked["status"] == "online"

        tested = (await client.post(f"/api/models/{model['id']}/test")).json()
        assert tested["bench"]["latency_ms"] >= 0 and tested["bench"]["tested_at"]
        stored = (await client.get("/api/models")).json()[0]
        assert stored["status"] == "online" and stored["bench"]["answer"] == "работаю"
    await svc.stop()


async def test_system_activity_and_approvals(env):
    system = (await env.client.get("/api/system")).json()
    assert system["metrics"]["cpu_pct"] is not None
    assert system["metrics"]["ram_total_mb"] > 0
    assert set(system["health"]) >= {"db", "queue_worker", "scheduler"}
    assert system["health"]["db"]["status"] == "ok"
    assert isinstance(system["history"], list)

    approval = (await env.client.post("/api/approvals", json={
        "kind": "demo", "preview": "отправить письмо"})).json()
    pending = (await env.client.get("/api/approvals")).json()
    assert [a["id"] for a in pending] == [approval["id"]]

    decided = (await env.client.post(f"/api/approvals/{approval['id']}",
                                     json={"approve": True, "by": "owner"})).json()
    assert decided["status"] == "approved" and decided["decided_by"] == "owner"
    assert (await env.client.get("/api/approvals")).json() == []

    activity = (await env.client.get("/api/activity")).json()
    kinds = [e["kind"] for e in activity]
    assert "approval.created" in kinds and "approval.decided" in kinds


async def test_ws_subscriber_gets_events_with_correct_kind(env):
    """Вид события задаёт шина: одноимённое поле данных его не подменяет."""
    queue = env.svc.bus.subscribe()
    await env.client.post("/api/approvals", json={"kind": "demo", "preview": "тест"})
    msg = queue.get_nowait()
    assert msg["kind"] == "approval.created" and msg["approval_kind"] == "demo"
    env.svc.bus.unsubscribe(queue)
