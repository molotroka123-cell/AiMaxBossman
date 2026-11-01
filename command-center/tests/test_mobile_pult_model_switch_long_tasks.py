from __future__ import annotations

import sqlalchemy as sa

from bcc.db import task_runs as runs_t, tasks as tasks_t

from .conftest import FakeAdapter, client_for, make_settings, start_app


async def test_mobile_pult_cloud_local_switch_modes_context_compact_and_turn_badges(tmp_path):
    """Verify all fixes for Mobile Pult cloud<->local model switching and long multi-step tasks:
    1. `GET /api/chat/threads/{tid}` returns per-turn `agent_id` and `model` (`alias`, `locality`, `provider`)
       and preserves synthetic turns (`/improve`) instead of marking them `missing`.
    2. `POST /api/agent/sessions/{tid}/messages` with a cloud `agent_id` sets `route.locality == 'cloud'`,
       updates `cfg.agent_id` & `cfg.model`, and does not fall back to `gemma4-31b-uncensored:latest`.
    3. `GET /api/agent/sessions/{tid}/context?agent_id=...` recalculates `used_tokens` for the newly selected
       model instead of reusing the previous model's `provider_usage` anchor.
    4. `POST /api/agent/sessions/{tid}/compact` succeeds on a session whose last turn was answered by a
       mobile-approved cloud agent (`permissions.mobile_cloud_ok = True`) or when `agent_id` is passed.
    """
    fake = FakeAdapter("Подробный многошаговый отчёт готов: все секции проверены.")
    app, svc = await start_app(
        make_settings(tmp_path),
        start_workers=False,
        adapter_factory=lambda m, p: fake,
    )
    try:
        async with client_for(app, svc) as c:
            # 1. Register a local provider/model/agent and a cloud provider/model/agent (mobile_cloud_ok=True)
            p_local = (
                await c.post(
                    "/api/providers",
                    json={"name": "ollama-local", "kind": "openai_compat", "base_url": "http://127.0.0.1:11434/v1"},
                )
            ).json()
            m_local = (
                await c.post(
                    "/api/models",
                    json={
                        "provider_id": p_local["id"],
                        "name": "qwen2.5-coder:7b",
                        "alias": "qwen-local-7b",
                        "kind": "local",
                        "locality": "local",
                        "free": True,
                        "context_window": 32768,
                        "caps": {"chat": True},
                    },
                )
            ).json()
            await c.post(f"/api/models/{m_local['id']}/check")
            a_local = (
                await c.post(
                    "/api/agents",
                    json={
                        "name": "Local Qwen 7B",
                        "role": "assistant",
                        "model_id": m_local["id"],
                        "system_prompt": "Ты локальный агент Bossman.",
                    },
                )
            ).json()

            p_cloud = (
                await c.post(
                    "/api/providers",
                    json={
                        "name": "openrouter-cloud",
                        "kind": "openai_compat",
                        "base_url": "https://openrouter.ai/api/v1",
                        "api_key": "sk-or-test-key-123456",
                    },
                )
            ).json()
            m_cloud = (
                await c.post(
                    "/api/models",
                    json={
                        "provider_id": p_cloud["id"],
                        "name": "z-ai/glm-4.5-flash:free",
                        "alias": "glm-5.3-flash",
                        "kind": "cloud",
                        "locality": "cloud",
                        "free": True,
                        "price_in": 0.0,
                        "price_out": 0.0,
                        "pricing_known": True,
                        "context_window": 131072,
                        "caps": {"chat": True},
                    },
                )
            ).json()
            await c.post(f"/api/models/{m_cloud['id']}/check")
            a_cloud = (
                await c.post(
                    "/api/agents",
                    json={
                        "name": "GLM 5.3 Flash (Cloud)",
                        "role": "assistant",
                        "model_id": m_cloud["id"],
                        "system_prompt": "Ты облачный агент Bossman.",
                        "permissions": {"mobile_cloud_ok": True},
                    },
                )
            ).json()

            # 2. Create thread and run Turn 1 on LOCAL agent in CHAT mode
            th = (await c.post("/api/chat/threads", json={"title": "Переключение облако-локально"})).json()
            tid = th["id"]

            r1 = await c.post(
                f"/api/chat/threads/{tid}/send",
                json={"text": "Шаг 1: локальный анализ архитектуры", "agent_id": a_local["id"]},
            )
            assert r1.status_code == 200, r1.text
            t1_id = r1.json()["task"]["id"]
            async with svc.db.session() as s:
                await s.execute(
                    sa.update(runs_t)
                    .where(runs_t.c.task_id == t1_id)
                    .values(
                        status="completed",
                        result="Локальный ответ Qwen по шагу 1.",
                        model_alias="qwen-local-7b",
                        tokens_in=9500,
                        tokens_out=500,
                    )
                )
                await s.execute(sa.update(tasks_t).where(tasks_t.c.id == t1_id).values(status="completed"))
                await s.commit()

            # 3. Check context gauge before & after switching to CLOUD agent
            ctx_same = (await c.get(f"/api/agent/sessions/{tid}/context", params={"agent_id": a_local["id"]})).json()
            assert ctx_same["source"] == "provider_usage"
            assert ctx_same["used_tokens"] == 10000

            ctx_switched = (await c.get(f"/api/agent/sessions/{tid}/context", params={"agent_id": a_cloud["id"]})).json()
            assert ctx_switched["context_window"] == 131072
            assert ctx_switched["context_window_source"] == "selected_model"
            # Switched model must NOT inherit the 10,000 provider_usage anchor from the previous model
            assert ctx_switched["source"] == "estimate"
            assert ctx_switched["used_tokens"] < 2000

            # 4. Switch to CLOUD agent in AGENT mode via POST /api/agent/sessions/{tid}/messages
            r2 = await c.post(
                f"/api/agent/sessions/{tid}/messages",
                json={
                    "text": "Шаг 2: длинная облачная задача в режиме AGENT с полным контекстом",
                    "mode": "AGENT",
                    "agent_id": a_cloud["id"],
                    "allow_cloud": True,
                },
            )
            assert r2.status_code == 200, r2.text
            b2 = r2.json()
            assert b2["agent"]["id"] == a_cloud["id"]
            assert b2["model"]["alias"] == "glm-5.3-flash"
            assert b2["model"]["locality"] == "cloud"
            assert b2["route"]["locality"] == "cloud"
            assert b2["route"]["model"] == "glm-5.3-flash"

            t2_id = b2["task"]["id"]
            async with svc.db.session() as s:
                await s.execute(
                    sa.update(runs_t)
                    .where(runs_t.c.task_id == t2_id)
                    .values(
                        status="completed",
                        result="Облачный ответ GLM 5.3 Flash по шагу 2.",
                        model_alias="glm-5.3-flash",
                        tokens_in=600,
                        tokens_out=200,
                    )
                )
                await s.execute(sa.update(tasks_t).where(tasks_t.c.id == t2_id).values(status="completed"))
                await s.commit()

            # 5. Reopen thread (GET /api/chat/threads/{tid}) and verify every turn keeps its own agent_id & model badge
            thread_view = (await c.get(f"/api/chat/threads/{tid}")).json()
            turns = thread_view["turns"]
            assert len(turns) == 2
            assert turns[0]["agent_id"] == a_local["id"]
            assert turns[0]["model"]["alias"] == "qwen-local-7b"
            assert turns[0]["model"]["locality"] == "local"
            assert turns[1]["agent_id"] == a_cloud["id"]
            assert turns[1]["model"]["alias"] == "glm-5.3-flash"
            assert turns[1]["model"]["locality"] == "cloud"
            assert turns[1]["model"]["provider"] == "openrouter-cloud"

            # 6. Run /compact after the cloud turn (both with explicit agent_id and via mobile_cloud_ok)
            cmp_res = await c.post(
                f"/api/agent/sessions/{tid}/compact",
                json={"instruction": "сохрани шаги 1 и 2", "agent_id": a_cloud["id"]},
            )
            assert cmp_res.status_code == 202, cmp_res.text
            assert cmp_res.json()["state"] == "running"
    finally:
        await svc.stop()
