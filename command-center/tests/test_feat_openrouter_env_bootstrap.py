"""OpenRouter как временный провайдер через окружение: ключ только из env, в
репозитории/ответах API его нет; без env ничего не создаётся; без дублей."""
from __future__ import annotations

import sqlalchemy as sa

from bcc.db import providers as providers_t

from .conftest import client_for, make_settings, start_app

FAKE_KEY = "sk-or-v1-test-not-a-real-key-000000"  # ci-secret-scan: allow


async def _providers(svc):
    async with svc.db.session() as s:
        return [dict(r._mapping) for r in (await s.execute(sa.select(providers_t))).fetchall()]


async def test_no_env_no_provider(env, monkeypatch):
    monkeypatch.delenv("BOSSMAN_OPENROUTER_API_KEY", raising=False)
    assert await _providers(env.svc) == []


async def test_env_key_bootstraps_encrypted_provider_once(tmp_path, monkeypatch):
    monkeypatch.setenv("BOSSMAN_OPENROUTER_API_KEY", FAKE_KEY)
    settings = make_settings(tmp_path)
    app, svc = await start_app(settings, start_workers=False)
    try:
        rows = await _providers(svc)
        assert len(rows) == 1 and rows[0]["base_url"].startswith("https://openrouter.ai/")
        assert rows[0]["kind"] == "openai_compat" and svc.vault.decrypt(rows[0]["api_key_enc"]) == FAKE_KEY
        assert rows[0]["api_key_enc"] != FAKE_KEY
        async with client_for(app, svc) as client:
            public = (await client.get("/api/providers")).json()
        assert FAKE_KEY not in str(public) and public[0].get("has_key", True)
        events = await svc.bus.recent(50)
        assert FAKE_KEY not in str(events)
    finally:
        await svc.stop()
    # рестарт с тем же env — провайдер не дублируется
    app, svc = await start_app(settings, start_workers=False)
    try:
        assert len(await _providers(svc)) == 1
    finally:
        await svc.stop()
