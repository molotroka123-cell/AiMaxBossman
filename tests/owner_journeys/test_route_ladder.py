import asyncio
import socket

import pytest

from tools.owner_journeys import learning_supervisor as sup
from tools.owner_journeys import route_ladder as rl

CATALOG = {
    "qwen/qwen3.8-27b:free": {"prompt": "0", "completion": "0"},
    "nvidia/nemotron-3-super-120b-a12b:free": {"prompt": "0.0000001", "completion": "0"},  # not really free
    "google/gemma-4-31b-it:free": {"prompt": "0", "completion": "0"},
    "z-ai/glm-5.3-flash": {"prompt": "0.00000015", "completion": "0.0000005"},
}


def _ladder(**kw):
    base = dict(free_models=["qwen/qwen3.8-27b:free", "nvidia/nemotron-3-super-120b-a12b:free",
                             "google/gemma-4-31b-it:free"], key_env="TEST_LADDER_KEY")
    base.update(kw)
    return rl.LadderConfig(**base)


def test_config_refuses_claude_and_non_free_models():
    with pytest.raises(ValueError):
        _ladder(max_model="anthropic/claude-5-haiku").validate()
    with pytest.raises(ValueError):
        _ladder(free_models=["qwen/qwen3.8-27b"]).validate()
    with pytest.raises(ValueError):
        _ladder(cloud_base_url="https://api.anthropic.com/v1").validate()
    _ladder().validate()


def test_plan_orders_tiers_and_verifies_free_price_live(monkeypatch):
    monkeypatch.setenv("TEST_LADDER_KEY", "k")
    routes = rl.plan(_ladder(), needs_llm=True, catalog=CATALOG)
    tiers = [(r.tier, r.model) for r in routes]
    assert tiers[:2] == [("local", "bossman-fast-qwen36-35b-a3b-q5:latest"),
                         ("local", "bossman-main-qwen38-27b-q5:latest")]
    free = [m for t, m in tiers if t == "free_cloud"]
    assert free == ["qwen/qwen3.8-27b:free", "google/gemma-4-31b-it:free"]  # nemotron priced -> excluded
    assert tiers[-1] == ("max_cloud", "z-ai/glm-5.3-flash") and routes[-1].reserve_usd > 0
    assert rl.plan(_ladder(), needs_llm=False)[0].tier == "deterministic"


def test_no_key_means_local_only_and_price_ceiling(monkeypatch):
    monkeypatch.delenv("TEST_LADDER_KEY", raising=False)
    assert {r.tier for r in rl.plan(_ladder(), needs_llm=True, catalog=CATALOG)} == {"local"}
    monkeypatch.setenv("TEST_LADDER_KEY", "k")
    dear = dict(CATALOG, **{"z-ai/glm-5.3-flash": {"prompt": "0.000002", "completion": "0.00001"}})
    assert "max_cloud" not in {r.tier for r in rl.plan(_ladder(), needs_llm=True, catalog=dear)}


def test_cap_ledger_reserves_before_call_and_enforces_caps(tmp_path):
    cap = rl.CapLedger(tmp_path / "cap.json", _ladder(daily_cap_usd=0.01, per_cycle_cap_usd=0.006))
    assert cap.reserve(1, 0.005) == (True, "reserved")
    ok, why = cap.reserve(1, 0.002)
    assert not ok and "per_cycle" in why
    cap.settle(1, 0.005, 0.001)
    assert cap.spent_today() == pytest.approx(0.001)
    assert cap.reserve(2, 0.005)[0] and cap.reserve(3, 0.004)[0]
    ok, why = cap.reserve(4, 0.001)
    assert not ok and "daily" in why


def test_anthropic_egress_is_blocked_and_counted():
    rl.AnthropicBlock.install()
    before = rl.AnthropicBlock.attempts
    with pytest.raises(PermissionError):
        socket.getaddrinfo("api.anthropic.com", 443)
    assert rl.AnthropicBlock.attempts == before + 1


def _runner(statuses, seen):
    async def fake(cfg, work, index, route, ladder):
        seen.append(route.tier)
        st = statuses.get(route.tier, "completed")
        return {"task_status": st, "verifier": {"pass": st == "completed", "safety_ok": True}, "violations": []}
    return fake


def test_ladder_falls_through_local_free_then_capped_max(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_LADDER_KEY", "k")
    monkeypatch.setattr(rl, "live_catalog", lambda *a, **k: CATALOG)
    seen = []
    monkeypatch.setitem(sup.RUNNERS, "t", _runner({"local": "failed", "free_cloud": "failed"}, seen))
    ladder = _ladder(daily_cap_usd=0.5, per_cycle_cap_usd=0.05)
    cap = rl.CapLedger(tmp_path / "cap.json", ladder)
    cfg = sup.Config(state_dir=tmp_path)
    res = asyncio.run(sup.run_ladder(cfg, "t", tmp_path / "w", 0, 1, ladder, cap))
    assert seen == ["local", "local", "free_cloud", "free_cloud", "max_cloud"]
    assert res["route"]["tier"] == "max_cloud"
    # with a zero cap the paid tier is refused and the cycle ends without a route (loop keeps going)
    seen.clear()
    ladder0 = _ladder(daily_cap_usd=0.0, per_cycle_cap_usd=0.0)
    res = asyncio.run(sup.run_ladder(cfg, "t", tmp_path / "w2", 0, 2, ladder0,
                                     rl.CapLedger(tmp_path / "cap0.json", ladder0)))
    assert "max_cloud" not in seen and res["task_status"] == "NO_ROUTE"
    assert res["route"]["tried"][-1]["status"] == "CAP_BLOCKED"


def test_ladder_serves_locally_when_local_works(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_LADDER_KEY", "k")
    monkeypatch.setattr(rl, "live_catalog", lambda *a, **k: CATALOG)
    seen = []
    monkeypatch.setitem(sup.RUNNERS, "t", _runner({}, seen))
    ladder = _ladder()
    res = asyncio.run(sup.run_ladder(sup.Config(state_dir=tmp_path), "t", tmp_path / "w", 0, 1, ladder,
                                     rl.CapLedger(tmp_path / "cap.json", ladder)))
    assert seen == ["local"] and res["route"]["tier"] == "local" and res["route"]["usd"] == 0.0


def test_owner_rejection_journey_counts_as_served_not_as_a_tier_failure(tmp_path, monkeypatch):
    from tools.owner_journeys import admin_journeys as aj

    async def fake_run_journeys(work, journeys, **kw):
        return {"journeys": [{"status": "PASS", "seconds": 1.0, "tool_sequence": [],
                              "steps": [{"step": "task_finished_on_product_path", "status": "PASS",
                                         "evidence": {"status": "failed", "approve": False}}]}]}
    monkeypatch.setattr(aj, "run_journeys", fake_run_journeys)
    ladder = _ladder()
    route = rl.Route("local", "m", "http://127.0.0.1:11434/v1")
    res = asyncio.run(sup.cycle_journey(sup.Config(state_dir=tmp_path), tmp_path, 8, route, ladder))
    assert res["task_status"] == "completed" and res["product_task_status"] == "failed"
