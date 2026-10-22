from __future__ import annotations

import base64

from .conftest import FakeAdapter, client_for, make_settings, start_app


async def test_chat_attachment_study_and_skill_injection_and_mobile_compact(tmp_path):
    fake = FakeAdapter("Изучил отчёт: выручка +42%.")
    app, svc = await start_app(
        make_settings(tmp_path),
        start_workers=False,
        adapter_factory=lambda m, p: fake,
    )
    try:
        async with client_for(app, svc) as c:
            p = (
                await c.post(
                    "/api/providers",
                    json={"name": "local-ollama-study", "kind": "openai_compat", "base_url": "http://127.0.0.1:11434/v1"},
                )
            ).json()
            # Model registered with context_window=None to verify local_default (32768) fallback in context gauge
            m = (
                await c.post(
                    "/api/models",
                    json={
                        "provider_id": p["id"],
                        "name": "gemma4:e4b",
                        "alias": "gemma-study",
                        "locality": "local",
                        "free": True,
                        "caps": {"chat": True},
                    },
                )
            ).json()
            # Probe model health so preflight accepts the local agent
            await c.post(f"/api/models/{m['id']}/check")
            a = (
                await c.post(
                    "/api/agents",
                    json={"name": "local-gemma-study", "role": "assistant", "model_id": m["id"]},
                )
            ).json()

            # 1. Create a thread (as Mobile Pult does via POST /api/chat/threads)
            th = (await c.post("/api/chat/threads", json={"title": "Изучение файла"})).json()
            tid = th["id"]

            # 2. Upload a file for study
            sample_doc = "Отчёт Q4: выручка выросла на 42%, ключевой драйвер — локальный агент Bossman."
            att_res = await c.post(
                "/api/chat/attachments",
                json={
                    "name": "q4_report.md",
                    "data_b64": base64.b64encode(sample_doc.encode("utf-8")).decode("ascii"),
                    "thread_id": tid,
                },
            )
            assert att_res.status_code == 201, att_res.text
            att_id = att_res.json()["attachment"]["id"]

            # 3. Send a message with both the attached file and a selected skill from "/"
            send_res = await c.post(
                f"/api/chat/threads/{tid}/send",
                json={
                    "text": "Применяй навык stop-latch-preflight: Изучи прикреплённый отчёт и выдели главное.",
                    "agent_id": a["id"],
                    "attachments": [att_id],
                },
            )
            assert send_res.status_code == 200, send_res.text
            task_id = send_res.json()["task"]["id"]
            task_detail = (await c.get(f"/api/tasks/{task_id}")).json()
            composed_prompt = task_detail["task"]["prompt"]
            # Verify both the file content and the skill instructions were injected into the prompt
            assert "q4_report.md" in composed_prompt
            assert "выручка выросла на 42%" in composed_prompt
            assert "stop-latch-preflight" in composed_prompt

            # 4. Verify context indicator returns a real context_window (32768 fallback) and remaining tokens
            ctx_res = await c.get(f"/api/agent/sessions/{tid}/context")
            assert ctx_res.status_code == 200, ctx_res.text
            ctx = ctx_res.json()
            assert ctx["context_window"] == 32768
            assert ctx["left_tokens"] is not None and ctx["left_tokens"] > 0
            assert ctx["used_pct"] is not None

            # 5. Verify /compact resolves the thread's local agent even without explicit PATCH /api/agent/sessions/{tid}
            compact_res = await c.post(
                f"/api/agent/sessions/{tid}/compact",
                json={"instruction": "кратко по делу"},
            )
            assert compact_res.status_code == 202, compact_res.text
            assert compact_res.json()["state"] == "running"
    finally:
        await svc.stop()
