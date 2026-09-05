"""CLOSURE-002 §10 — тот же агентный поток через OpenRouter-провайдер, собранный ТОЛЬКО из
окружения (ключ + список моделей как данные), с детерминированным фейковым провайдером:
Bossman → Model Broker (registry) → openai_compat-адаптер (OpenRouter base) → tool-call
(proposal) → политика → исполнение → верификация/receipt. Никакого сетевого вызова.
Живой smoke — отдельно, opt-in по OPENROUTER_API_KEY (test_feat_openrouter_smoke.py)."""
from __future__ import annotations

import sqlalchemy as sa

from bcc.db import models as models_t, task_runs as runs_t, tool_calls as tool_calls_t

from .conftest import client_for, make_settings, start_app
from .test_v21_tool_loop import FINISHED, ToolAdapter, _run_task

FAKE_KEY = "sk-or-v1-test-not-a-real-key-000000"  # ci-secret-scan: allow


async def test_env_configured_openrouter_models_drive_the_same_tool_loop(tmp_path, monkeypatch):
    monkeypatch.setenv("BOSSMAN_OPENROUTER_API_KEY", FAKE_KEY)
    monkeypatch.setenv("BOSSMAN_OPENROUTER_MODELS", "z-ai/glm-4.5-air, qwen/qwen3-coder")   # данные, не код
    settings = make_settings(tmp_path)
    app, svc = await start_app(settings, start_workers=False)
    try:
        async with svc.db.session() as s:
            models = [dict(r._mapping) for r in (await s.execute(sa.select(models_t))).fetchall()]
        aliases = sorted(m["alias"] for m in models)
        assert aliases == ["or-qwen-qwen3-coder", "or-z-ai-glm-4.5-air"] and all(m["kind"] == "cloud" for m in models)
        glm = next(m for m in models if m["alias"] == "or-z-ai-glm-4.5-air")

        # The fake provider has an explicitly known free tariff; absent prices must block.
        await svc.registry.update_model(glm["id"], price_in=0.0, price_out=0.0)

        # детерминированный «OpenRouter»: модель предлагает инструмент (proposal), потом отвечает текстом
        adapter = ToolAdapter([("tool", "terminal_run", {"command": "echo hi"}), ("text", "готово: hi")])
        svc.registry.adapter_factory = lambda m, p: adapter
        async with client_for(app, svc) as client:
            agent = (await client.post("/api/agents", json={"name": "or-agent", "system_prompt": "-", "model_id": glm["id"],
                                                           "max_steps": 3, "tools": ["terminal.run"],
                                                           "permissions": {"terminal.run": True}})).json()
            task = (await client.post("/api/tasks", json={"title": "or", "prompt": "скажи hi через терминал",
                                                         "agent_id": agent["id"], "run_now": True})).json()["task"]
            env = type("E", (), {"svc": svc, "client": client})()
            status = await _run_task(env, task["id"], timeout=30, until=FINISHED)
        assert status == "completed", adapter.seen_messages
        assert adapter.seen_tools[0] and any(t["function"]["name"] == "terminal_run" for t in adapter.seen_tools[0])
        async with svc.db.session() as s:
            run = dict((await s.execute(sa.select(runs_t).where(runs_t.c.task_id == task["id"]))).first()._mapping)
            calls = [dict(r._mapping) for r in (await s.execute(sa.select(tool_calls_t).where(
                tool_calls_t.c.task_id == task["id"]))).fetchall()]
        assert run["model_alias"] == "or-z-ai-glm-4.5-air"                     # брокер выбрал env-модель
        assert [c["tool"] for c in calls] == ["terminal.run"] and calls[0]["status"] in ("executed", "error")
        assert FAKE_KEY not in str(await svc.bus.recent(100))
    finally:
        await svc.stop()
    # повторный старт — без дублей моделей
    app, svc = await start_app(settings, start_workers=False)
    try:
        async with svc.db.session() as s:
            assert len((await s.execute(sa.select(models_t))).fetchall()) == 2
    finally:
        await svc.stop()
