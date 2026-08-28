"""Feature 04 Benchmark Lab (фон, реальные замеры) + 07 OpenCode (health/attach)."""
import asyncio

from .conftest import FakeAdapter, wait_for
from .helpers import make_stack


async def test_benchmark_runs_in_background_and_stores(env):
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter("ответ", tokens=(30, 12))
    stack = await make_stack(env.client)
    r = (await env.client.post("/api/benchmarks",
                               json={"model_id": stack["model"]["id"]})).json()
    assert r["status"] == "queued"
    bid = r["benchmark_id"]

    # API остаётся живым, пока идёт benchmark
    live = (await env.client.get("/api/system")).json()
    assert "metrics" in live

    async def done():
        b = (await env.client.get(f"/api/benchmarks/{bid}")).json()
        return b if b["status"] in ("completed", "failed") else None
    b = await wait_for(done, timeout=10)
    assert b["status"] == "completed"
    res = b["results"]
    # реальные измерения, не хардкод
    assert res["gen_tps"] is not None and res["gen_tps"] > 0
    assert res["stability"]["success_rate"] == 1.0
    assert "measured_at" in res


async def test_second_benchmark_new_record(env):
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter("ок", tokens=(10, 5))
    stack = await make_stack(env.client)
    b1 = (await env.client.post("/api/benchmarks", json={"model_id": stack["model"]["id"]})).json()
    b2 = (await env.client.post("/api/benchmarks", json={"model_id": stack["model"]["id"]})).json()
    assert b1["benchmark_id"] != b2["benchmark_id"]     # новая запись, не перезапись

    async def both_done():
        rows = (await env.client.get(f"/api/benchmarks?model_id={stack['model']['id']}")).json()
        return rows if all(r["status"] in ("completed", "failed") for r in rows) and len(rows) == 2 else None
    rows = await wait_for(both_done, timeout=12)
    assert len(rows) == 2


async def test_benchmark_failed_endpoint(env):
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter(fail_times=99, error="dead")
    stack = await make_stack(env.client)
    r = (await env.client.post("/api/benchmarks", json={"model_id": stack["model"]["id"]})).json()

    async def done():
        b = (await env.client.get(f"/api/benchmarks/{r['benchmark_id']}")).json()
        return b if b["status"] in ("completed", "failed") else None
    b = await wait_for(done, timeout=10)
    assert b["status"] == "failed" and b["error"]      # честная ошибка, фон не завис


async def test_recommendations_from_stored(env):
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter("ок", tokens=(20, 40))
    stack = await make_stack(env.client)
    r = (await env.client.post("/api/benchmarks", json={"model_id": stack["model"]["id"]})).json()

    async def done():
        b = (await env.client.get(f"/api/benchmarks/{r['benchmark_id']}")).json()
        return b["status"] == "completed"
    await wait_for(done, timeout=10)
    rec = (await env.client.get("/api/benchmarks/recommendations")).json()
    assert rec["based_on"] >= 1 and rec["for_speed"]["model_id"] == stack["model"]["id"]


# ---------- OpenCode ----------

async def test_opencode_health_unavailable_not_error(env):
    """Без запущенного opencode serve — honest unavailable, не 500."""
    r = await env.client.get("/api/opencode/health")
    assert r.status_code == 200
    assert r.json()["status"] == "unavailable" and r.json()["hint"]


async def test_opencode_attach_session_record(env):
    stack = await make_stack(env.client)
    r = (await env.client.post("/api/opencode/attach",
                               json={"session_id": "oc-abc123", "task_id": stack["task"]["id"],
                                     "project_path": "/tmp/proj"})).json()
    assert r["session_id"] == "oc-abc123"
    sessions = (await env.client.get("/api/opencode/sessions")).json()
    assert any(s["session_id"] == "oc-abc123" for s in sessions)
