"""authored_by_lane agcloud: behavior tests for bcc.features.cache_intel (no mocks of the unit).

Observations are produced by the real engine helper and travel through the real EventBus/DB;
the unit under test (economics/intelligence + routes) is exercised through the real app.
"""
from __future__ import annotations

import pytest

from bcc.engine import cache_observation_for
from bcc.features import cache_intel
from bcc.providers import ChatResult

PRICED = {"price_in": 5.0, "price_out": 25.0, "price_cache_read": 0.5, "price_cache_write": 6.25,
          "alias": "m", "kind": "cloud"}
UNPRICED = {"price_in": 5.0, "price_out": 25.0, "alias": "m", "kind": "cloud"}


def _res() -> ChatResult:
    return ChatResult(text="", tokens_in=1000, tokens_out=10, cache_read_tokens=900,
                      provider_meta={"usage": {"input_tokens": 100, "cache_read_input_tokens": 900,
                                               "output_tokens": 10},
                                     "prompt_cache": {"applied": True}})


def test_feature_registered_with_both_routes():
    assert cache_intel.FEATURE.name == "cache_intel"
    paths = {r.path for r in cache_intel.router.routes}
    assert {"/cache/economics", "/cache/intelligence"} <= paths


async def test_empty_bus_claims_no_savings(env):
    eco = (await env.client.get("/api/cache/economics")).json()
    assert eco["available"] is True
    assert eco["measured"]["counts"]["HIT"] == 0
    assert eco["estimated"]["saved_usd"] is None          # nothing measured => nothing claimed
    assert eco["hit_rate_is_diagnostic_not_kpi"] is True
    intel = (await env.client.get("/api/cache/intelligence")).json()
    assert intel["measured"]["verified_success_rate"] is None   # no evaluations => unknown, not 0 or 1
    assert intel["learning_candidates"]["promoted"] == 0


async def test_hit_observation_is_aggregated_and_savings_are_estimated(env):
    obs = cache_observation_for(PRICED, _res(), task_id=1, run_id=1)
    await env.svc.bus.emit("cache.observation", **obs)
    await env.svc.bus.emit("task.note", prompt="not an observation")        # other kinds are ignored
    eco = (await env.client.get("/api/cache/economics")).json()
    assert eco["measured"]["counts"]["HIT"] == 1
    assert eco["measured"]["actual_cost_usd"] == pytest.approx(0.0012)
    assert eco["estimated"]["baseline_cost_usd"] == pytest.approx(0.00525)
    assert eco["estimated"]["saved_usd"] == pytest.approx(0.00405, abs=1e-6)
    assert eco["by_route"] and sum(eco["by_route"].values()) == 1


async def test_unknown_cost_blocks_savings_claim(env):
    good = cache_observation_for(PRICED, _res(), task_id=1, run_id=1)
    unpriced = cache_observation_for(UNPRICED, _res(), task_id=2, run_id=2)
    assert unpriced["actual_cost_usd"] is None
    await env.svc.bus.emit("cache.observation", **good)
    await env.svc.bus.emit("cache.observation", **unpriced)
    eco = (await env.client.get("/api/cache/economics")).json()
    assert eco["unknown"]["cost_requests"] == 1
    assert eco["estimated"]["saved_usd"] is None           # one unknown cost voids the whole claim


async def test_waste_and_advice_are_flag_gated(env, monkeypatch):
    monkeypatch.delenv("BOSSMAN_CONTEXT_WASTE_OBSERVE", raising=False)
    monkeypatch.delenv("BOSSMAN_CACHE_ADVISOR", raising=False)
    intel = (await env.client.get("/api/cache/intelligence")).json()
    assert intel["waste_signals"] is None and intel["advice"] is None
    monkeypatch.setenv("BOSSMAN_CONTEXT_WASTE_OBSERVE", "1")
    monkeypatch.setenv("BOSSMAN_CACHE_ADVISOR", "1")
    intel = (await env.client.get("/api/cache/intelligence")).json()
    assert isinstance(intel["waste_signals"], list)
    assert intel["advice"] and intel["advice"][0]["action"] == "NO_ACTION"   # advisory only, no data => no action
