"""PASS3 — direct route: engine emits normalized cache observations for HIT/WRITE/
MISS/BYPASS/UNKNOWN (not only read/write), _cost splits fresh/read/write buckets,
dashboard endpoints separate measured/estimated/unknown, advisor is advisory-only."""
from __future__ import annotations

import pytest

from bcc.engine import _cost, cache_observation_for
from bcc.providers import ChatResult

from .conftest import FakeAdapter
from .helpers import make_stack


class CacheAdapter(FakeAdapter):
    def __init__(self, usage: dict | None, applied: bool = True, text="ок"):
        super().__init__(text)
        self._usage, self._applied = usage, applied

    async def chat(self, model, messages, **kw):
        u = self._usage or {}
        read, write = int(u.get("cache_read_input_tokens", 0)), int(u.get("cache_creation_input_tokens", 0))
        meta = {"usage": u} if self._usage is not None else {}
        if self._applied:
            meta["prompt_cache"] = {"applied": True, "read_tokens": read, "write_tokens": write, "hit": read > 0}
        return ChatResult(text="ок", tokens_in=int(u.get("input_tokens", 0)) + read + write,
                          tokens_out=int(u.get("output_tokens", 0)), model=model, provider_meta=meta,
                          cache_read_tokens=read, cache_write_tokens=write)


async def _run(env, adapter) -> list[dict]:
    """Один провайдер/агент на env; каждая итерация — новая задача. Возвращает
    наблюдения ТОЛЬКО этого прогона."""
    env.svc.registry.adapter_factory = lambda m, p: adapter
    stack = getattr(env, "_stack", None)
    if stack is None:
        stack = env._stack = await make_stack(env.client)
    else:
        await env.client.post("/api/tasks", json={"title": "t", "prompt": "x", "agent_id": stack["agent"]["id"],
                                                  "run_now": True})
    before = len([r for r in await env.svc.bus.recent(200) if r.get("kind") == "cache.observation"])
    rid = await env.svc.engine.claim()
    await env.svc.engine.execute(rid)
    rows = [(r.get("data") or r) for r in await env.svc.bus.recent(200) if r.get("kind") == "cache.observation"]
    rows = list(reversed(rows)) if rows and rows[0].get("timestamp", "") > rows[-1].get("timestamp", "") else rows
    return rows[before:]


@pytest.mark.parametrize("usage, expect", [
    ({"input_tokens": 12, "cache_read_input_tokens": 900, "output_tokens": 3}, "HIT"),
    ({"input_tokens": 12, "cache_creation_input_tokens": 900, "output_tokens": 3}, "WRITE"),
    ({"input_tokens": 120, "output_tokens": 3}, "MISS"),
    (None, "UNKNOWN"),
])
async def test_engine_emits_observation_for_every_state(env, usage, expect, monkeypatch):
    monkeypatch.delenv("BOSSMAN_CACHE_TELEMETRY_V2", raising=False)
    obs = await _run(env, CacheAdapter(usage))
    assert len(obs) == 1 and obs[0]["state"] == expect
    o = obs[0]
    assert o["event_version"] == 1 and o["route"] in ("direct", "local") and o["cache_control_applied"] is True
    if usage:
        assert o["fresh_input_tokens"] == usage["input_tokens"]
    assert "messages" not in o and "prompt" not in o and "content" not in o


async def test_bypass_when_cache_not_requested_and_flag_off_disables(env, monkeypatch):
    obs = await _run(env, CacheAdapter({"prompt_tokens": 50, "completion_tokens": 2}, applied=False))
    assert obs and obs[0]["state"] == "BYPASS"
    monkeypatch.setenv("BOSSMAN_CACHE_TELEMETRY_V2", "0")
    assert await _run(env, CacheAdapter({"input_tokens": 1, "cache_read_input_tokens": 5})) == []


def test_cost_splits_buckets_and_unknown_prices_are_conservative():
    r = ChatResult(text="", tokens_in=1000, tokens_out=10, cache_read_tokens=900, cache_write_tokens=0)
    model = {"price_in": 5.0, "price_out": 25.0}
    assert _cost(model, r) == pytest.approx(1000 / 1e6 * 5.0 + 10 / 1e6 * 25.0)   # неизвестная цена чтения → как fresh (верхняя граница)
    priced = {"price_in": 5.0, "price_out": 25.0, "price_cache_read": 0.5, "price_cache_write": 6.25}
    assert _cost(priced, r) == pytest.approx(100 / 1e6 * 5.0 + 900 / 1e6 * 0.5 + 10 / 1e6 * 25.0)
    obs = cache_observation_for({**model, "alias": "m", "kind": "cloud"},
                                ChatResult(text="", tokens_in=1000, tokens_out=10, cache_read_tokens=900,
                                           provider_meta={"usage": {"input_tokens": 100, "cache_read_input_tokens": 900,
                                                                    "output_tokens": 10},
                                                          "prompt_cache": {"applied": True}}),
                                task_id=1, run_id=1)
    assert obs["state"] == "HIT" and obs["actual_cost_usd"] is None and obs["baseline_is_estimate"] is True
    obs2 = cache_observation_for({**priced, "alias": "m", "kind": "cloud"},
                                 ChatResult(text="", tokens_in=1000, tokens_out=10, cache_read_tokens=900,
                                            provider_meta={"usage": {"input_tokens": 100, "cache_read_input_tokens": 900,
                                                                     "output_tokens": 10},
                                                           "prompt_cache": {"applied": True}}),
                                 task_id=1, run_id=1)
    assert obs2["actual_cost_usd"] == pytest.approx(0.0012) and obs2["baseline_cost_usd"] == pytest.approx(0.00525)


async def test_dashboard_endpoints_separate_measured_estimated_unknown(env, monkeypatch):
    await _run(env, CacheAdapter({"input_tokens": 12, "cache_read_input_tokens": 900, "output_tokens": 3}))
    await _run(env, CacheAdapter(None))
    eco = (await env.client.get("/api/cache/economics")).json()
    assert eco["available"] and eco["measured"]["counts"]["HIT"] == 1 and eco["measured"]["counts"]["UNKNOWN"] == 1
    assert eco["estimated"]["saved_usd"] is None and eco["unknown"]["cache_control_without_usage"] == 1
    assert eco["warning"] and eco["hit_rate_is_diagnostic_not_kpi"] is True
    intel = (await env.client.get("/api/cache/intelligence")).json()
    assert intel["waste_signals"] is None and intel["advice"] is None      # флаги OFF
    monkeypatch.setenv("BOSSMAN_CONTEXT_WASTE_OBSERVE", "1"); monkeypatch.setenv("BOSSMAN_CACHE_ADVISOR", "1")
    intel = (await env.client.get("/api/cache/intelligence")).json()
    assert isinstance(intel["waste_signals"], list)
    assert intel["advice"][0]["action"] == "NO_ACTION" and "insufficient" in intel["advice"][0]["text"]
    assert intel["unknown"]["false_success_rate"]                          # не выдумано
