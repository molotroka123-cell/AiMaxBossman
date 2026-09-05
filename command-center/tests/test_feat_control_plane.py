"""TZ-08 §2.5: `GET /api/control-plane` — durable-снимок для владельца. Приёмка §4.5."""
from __future__ import annotations

from .conftest import client_for, start_app


async def test_control_plane_shape_queue_and_attention(env):
    await env.svc.approvals.create("tool", "нужно решение по terminal.run")
    body = (await env.client.get("/api/control-plane")).json()
    assert set(body) >= {"now", "organization", "queue", "treasury", "fleet", "slo", "attention"}
    assert body["organization"]["enabled"] is False
    assert body["fleet"]["enabled"] is False and body["slo"]["status"] == "NOT_IMPLEMENTED"
    system_queue = (await env.client.get("/api/system")).json()["queue"]
    assert all(body["queue"].get(k, 0) == v for k, v in system_queue.items())
    assert body["treasury"]["fable"]["status"] in ("OK", "UNAVAILABLE")
    assert body["attention"] and body["attention"][0]["kind"] == "approval:tool"
    # кэш 2 с: повтор отдаёт тот же снимок
    assert (await env.client.get("/api/control-plane")).json()["now"] == body["now"]


async def test_control_plane_survives_restart(tmp_path, env):
    await env.svc.approvals.create("tool", "до рестарта")
    before = (await env.client.get("/api/control-plane")).json()
    await env.svc.stop()
    app, svc = await start_app(env.settings, start_workers=False)
    try:
        async with client_for(app, svc) as client:
            after = (await client.get("/api/control-plane")).json()
    finally:
        await svc.stop()
    for key in ("organization", "queue"):
        assert before[key] == after[key], key
    assert before["treasury"]["fable"].get("cap_usd") == after["treasury"]["fable"].get("cap_usd")
    assert [a["ref"] for a in before["attention"]] == [a["ref"] for a in after["attention"]]
