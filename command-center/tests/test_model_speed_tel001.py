"""TEL-001 (owner audit 2026-09-21, P2): UI показывал 1.6/3.3 «ток/с» у моделей
с настоящими ~10/49 — делил 2–32 токена на всю латентность запроса.

Контракт: TTFT, prefill tok/s, generation tok/s и полная латентность — раздельно;
generation — из собственных замеров llama.cpp (`timings`), иначе разностный
замер, иначе null. Никогда «токены / вся латентность».
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from bcc.model_speed import from_timings, measure_speed, median_of
from bcc.providers import ChatResult, OpenAICompatAdapter

from .conftest import wait_for
from .helpers import make_stack

# Реальный вид ответа llama-server (b10964) на /v1/chat/completions.
TIMINGS = {"prompt_n": 24, "prompt_ms": 480.0, "prompt_per_second": 50.0,
           "predicted_n": 32, "predicted_ms": 3200.0, "predicted_per_second": 10.0}


def _llama_transport(timings: dict | None, *, delay_s: float = 0.0):
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        n = int(body.get("max_tokens") or 16)
        await asyncio.sleep(delay_s)
        data = {"id": "x", "model": body["model"],
                "choices": [{"index": 0, "finish_reason": "length",
                             "message": {"role": "assistant", "content": "один, два, три"}}],
                "usage": {"prompt_tokens": 24, "completion_tokens": n, "total_tokens": 24 + n}}
        if timings is not None:
            data["timings"] = {**timings, "predicted_n": n}
        return httpx.Response(200, json=data)
    return httpx.MockTransport(handler)


async def test_model_test_uses_server_timings_not_total_latency(env):
    """Сервер медленно отвечает целиком (0.3 с на 32 токена ≈ 107 «ток/с» по старой
    формуле, а при реальном prefill было бы 1.6) — показываем его собственные 10.0."""
    transport = _llama_transport(TIMINGS, delay_s=0.3)
    env.svc.registry.adapter_factory = lambda m, p: OpenAICompatAdapter(
        base_url="http://127.0.0.1:8081/v1", transport=transport)
    stack = await make_stack(env.client)
    r = await env.client.post(f"/api/models/{stack['model']['id']}/test")
    assert r.status_code == 200, r.text
    bench = r.json()["bench"]
    assert bench["method"] == "server_timings"
    assert bench["gen_tps"] == 10.0
    assert bench["prompt_tps"] == 50.0
    assert bench["ttft_ms"] == 480.0
    assert bench["latency_ms"] >= 300
    old_formula = bench["tokens_out"] / (bench["latency_ms"] / 1000)
    assert abs(old_formula - bench["gen_tps"]) > 1, "показатель совпал со старой формулой"


async def test_model_test_without_timings_reports_null_not_fake(env):
    env.svc.registry.adapter_factory = lambda m, p: OpenAICompatAdapter(
        base_url="http://127.0.0.1:8081/v1", transport=_llama_transport(None))
    stack = await make_stack(env.client)
    bench = (await env.client.post(f"/api/models/{stack['model']['id']}/test")).json()["bench"]
    assert bench["method"] == "unavailable"
    assert bench["gen_tps"] is None and bench["prompt_tps"] is None
    assert "Bench Lab" in bench["note"]


class _PacedAdapter:
    """Модель без timings: TTFT задаётся, затем 20 ток/с (50 мс на токен)."""

    def __init__(self, ttft=0.2, per_token=0.05):
        self.ttft, self.per_token = ttft, per_token

    async def chat(self, model, messages, **kw):
        n = int(kw.get("max_tokens") or 1)
        await asyncio.sleep(self.ttft + self.per_token * (n - 1))
        return ChatResult(text="x " * n, tokens_in=24, tokens_out=n, model=model)


@pytest.mark.timeout(60)
async def test_differential_measurement_matches_true_generation_rate():
    out = await measure_speed(_PacedAdapter(ttft=1.0), "m", gen_tokens=41)
    assert out["method"] == "differential"
    assert out["gen_tps"] == pytest.approx(20.0, rel=0.2)       # истинные 20 ток/с
    assert out["ttft_ms"] == pytest.approx(1000, rel=0.2)
    naive = 41 / (out["latency_ms"] / 1000)
    assert naive < 15                                          # старая формула занижала


def test_from_timings_and_median():
    s = from_timings(TIMINGS, 4000, 32)
    assert (s["gen_tps"], s["prompt_tps"], s["ttft_ms"]) == (10.0, 50.0, 480.0)
    assert from_timings({}, 100, 3) is None
    m = median_of([{"method": "server_timings", "gen_tps": 9.0},
                   {"method": "server_timings", "gen_tps": 10.0},
                   {"method": "differential", "gen_tps": 11.0}])
    assert m["gen_tps"] == 10.0 and m["method"] == "differential" and m["runs"] == 3


async def test_bench_lab_reports_separate_phases(env):
    env.svc.registry.adapter_factory = lambda m, p: OpenAICompatAdapter(
        base_url="http://127.0.0.1:8081/v1", transport=_llama_transport(TIMINGS))
    stack = await make_stack(env.client)
    bid = (await env.client.post("/api/benchmarks",
                                 json={"model_id": stack["model"]["id"]})).json()["benchmark_id"]

    async def done():
        b = (await env.client.get(f"/api/benchmarks/{bid}")).json()
        return b if b["status"] in ("completed", "failed") else None
    b = await wait_for(done, timeout=10)
    res = b["results"]
    assert b["status"] == "completed", b
    assert res["speed_method"] == "server_timings"
    assert (res["gen_tps"], res["prompt_tps"], res["ttft_ms"]) == (10.0, 50.0, 480.0)
    rec = (await env.client.get("/api/benchmarks/recommendations")).json()
    assert rec["for_speed"]["gen_tps"] == 10.0
