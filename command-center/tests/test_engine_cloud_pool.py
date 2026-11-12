"""Owner 12.11: cloud-provider agents must not be starved by long local runs (separate worker pool)."""
from __future__ import annotations

import asyncio

from .conftest import FakeAdapter, client_for, make_settings, start_app, wait_for

FAST = {"poll_interval": 0.02, "recover_every": 5.0, "retry_base_delay": 0.01, "workers": 1}


async def _agent(client, name, base_url, prompt):
    p = (await client.post("/api/providers", json={"name": name, "kind": "openai_compat",
                                                   "base_url": base_url, "api_key": "sk-test-abcd"})).json()
    m = (await client.post("/api/models", json={"provider_id": p["id"], "name": name, "alias": name})).json()
    a = (await client.post("/api/agents", json={"name": name, "system_prompt": "x", "model_id": m["id"],
                                                "max_steps": 1})).json()
    return a, prompt


async def _task(client, agent, prompt):
    return (await client.post("/api/tasks", json={"title": prompt, "prompt": prompt, "agent_id": agent["id"],
                                                  "run_now": True})).json()["task"]


async def test_cloud_task_starts_while_local_pool_is_saturated(tmp_path):
    gate = asyncio.Event()
    local_running = {"now": 0, "max": 0}

    async def block_local(call, messages):
        local_running["now"] += 1
        local_running["max"] = max(local_running["max"], local_running["now"])
        try:
            await gate.wait()
        finally:
            local_running["now"] -= 1

    local = FakeAdapter("local done", on_chat=block_local)
    cloud = FakeAdapter("cloud done")
    app, svc = await start_app(make_settings(tmp_path), start_workers=True, engine_options=FAST,
                               adapter_factory=lambda m, p: cloud if "openrouter" in p["base_url"] else local)
    async with client_for(app, svc) as client:
        la, _ = await _agent(client, "loc", "http://127.0.0.1:11434/v1", "l")
        ca, _ = await _agent(client, "cld", "https://openrouter.ai/api/v1", "c")
        l1 = await _task(client, la, "local-1")
        l2 = await _task(client, la, "local-2")
        await wait_for(lambda: asyncio.sleep(0, local_running["now"] == 1 or None), timeout=10)
        c1 = await _task(client, ca, "cloud-1")

        async def cloud_started():
            d = (await client.get(f"/api/tasks/{c1['id']}")).json()
            return d if d["runs"] and d["runs"][-1]["status"] != "queued" else None

        # the cloud run is claimed (leaves "queued") although the single local slot is busy; whether the
        # paid-cloud policy then lets it finish is a separate gate and not what this test is about
        await wait_for(cloud_started, timeout=10)
        d2 = (await client.get(f"/api/tasks/{l2['id']}")).json()
        assert d2["runs"][-1]["status"] == "queued"          # 2nd local task still waits: local slots stay 1
        assert local_running["now"] == 1 and local_running["max"] == 1
        gate.set()
        for t in (l1, l2):
            async def done(t=t):
                d = (await client.get(f"/api/tasks/{t['id']}")).json()
                return d if d["task"]["status"] == "completed" else None
            await wait_for(done, timeout=10)
        assert local_running["max"] == 1
    await svc.stop()
