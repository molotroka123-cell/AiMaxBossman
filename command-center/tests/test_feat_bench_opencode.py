"""Feature 04 Benchmark Lab (фон, реальные замеры) + 07 OpenCode (health/attach)."""
import asyncio

from .conftest import FakeAdapter, wait_for
from .helpers import make_stack


async def test_benchmark_runs_in_background_and_stores(env):
    # TEL-001: скорость генерации — только честный замер; фейку нужны timings сервера.
    class _Timed(FakeAdapter):
        async def chat(self, model, messages, **kw):
            res = await super().chat(model, messages, **kw)
            res.provider_meta = {"timings": {"predicted_per_second": 12.5, "prompt_per_second": 80.0,
                                             "prompt_ms": 300.0, "predicted_n": 12}}
            return res
    env.svc.registry.adapter_factory = lambda m, p: _Timed("ответ", tokens=(30, 12))
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


class _PerTokenFake(FakeAdapter):
    """Отвечает со временем, пропорциональным max_tokens: дифференциальный замер
    (полный ответ минус 1-токенный зонд) получает СТРОГО положительное окно."""

    async def chat(self, model, messages, **kw):
        await asyncio.sleep(0.002 * int(kw.get("max_tokens") or 1))
        return await super().chat(model, messages, **kw)


async def _completed_benchmark(env, model_id: int) -> dict:
    r = (await env.client.post("/api/benchmarks", json={"model_id": model_id})).json()

    async def done():
        b = (await env.client.get(f"/api/benchmarks/{r['benchmark_id']}")).json()
        return b if b["status"] in ("completed", "failed") else None
    return await wait_for(done, timeout=10)


async def test_recommendations_from_stored(env):
    # Без серверных timings скорость считается дифференциально; фейк с нулевой
    # задержкой давал окно ≤ 0 и честный «unavailable» (гонка CI, py3.11).
    env.svc.registry.adapter_factory = lambda m, p: _PerTokenFake("ок", tokens=(20, 40))
    stack = await make_stack(env.client)
    b = await _completed_benchmark(env, stack["model"]["id"])
    assert b["status"] == "completed" and b["results"]["speed_method"] == "differential", b
    rec = (await env.client.get("/api/benchmarks/recommendations")).json()
    assert rec["based_on"] >= 1 and rec["for_speed"]["model_id"] == stack["model"]["id"]


async def test_recommendations_exclude_unmeasurable_speed(env):
    """Негативный контроль: ответ короче MIN_GEN_TOKENS не даёт честной скорости —
    метод unavailable, gen_tps None, и рекомендация НЕ строится на нём."""
    env.svc.registry.adapter_factory = lambda m, p: _PerTokenFake("ок", tokens=(20, 2))
    stack = await make_stack(env.client)
    b = await _completed_benchmark(env, stack["model"]["id"])
    assert b["status"] == "completed", b
    assert b["results"]["speed_method"] == "unavailable" and b["results"]["gen_tps"] is None
    rec = (await env.client.get("/api/benchmarks/recommendations")).json()
    assert rec["based_on"] == 0 and rec["for_speed"] is None


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
