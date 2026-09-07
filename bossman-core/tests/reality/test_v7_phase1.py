import time
import pytest

from bossman.reality import (
    Budget, Fact, Freshness, MissionIR, Strategy, WorldStateGraph,
    generate_strategies, shadow_route,
)


def mission(**overrides):
    data = dict(
        owner_intent="Update the project safely",
        objective="produce a verified project update",
        desired_state=("project.updated == true",),
        effect_obligations=("write project files",),
        proof_obligations=("fresh read proves expected files",),
        rollback_contract="revert produced patch",
        permissions=("workspace.write",),
        budget=Budget(money_usd=3.0, tokens=10000, time_seconds=300),
    )
    data.update(overrides)
    return MissionIR(**data)


def test_effectful_mission_requires_proof_and_rollback():
    m = mission(proof_obligations=(), rollback_contract=None)
    ready, errors = m.validate_execution_readiness()
    assert not ready
    assert len(errors) == 2


def test_unknown_freshness_never_becomes_fresh():
    graph = WorldStateGraph()
    graph.observe(Fact("repo", "head", "abc", "github", time.time()))
    fact = graph.current("repo", "head")
    assert fact.freshness() is Freshness.UNKNOWN
    assert graph.current("repo", "head", require_fresh=True) is None


def test_stale_fact_is_visible_but_not_accepted_as_fresh():
    graph = WorldStateGraph()
    graph.observe(Fact("price", "btc", 100, "api", 10, valid_until_epoch_s=20))
    assert graph.current("price", "btc", now=30).freshness(30) is Freshness.STALE
    assert graph.current("price", "btc", require_fresh=True, now=30) is None


def test_world_state_keeps_revision_history():
    graph = WorldStateGraph()
    a = graph.observe(Fact("repo", "head", "a", "github", 1, valid_until_epoch_s=2))
    b = graph.observe(Fact("repo", "head", "b", "github", 3, valid_until_epoch_s=4))
    assert (a.revision, b.revision) == (1, 2)
    assert [x.value for x in graph.history("repo", "head")] == ["a", "b"]


def test_shadow_router_is_deterministic_and_non_authoritative():
    m = mission()
    strategies = generate_strategies(m, ())
    d1 = shadow_route(m, strategies)
    d2 = shadow_route(m, tuple(reversed(strategies)))
    assert d1.shadow_only is True
    assert d1.selected == d2.selected
    assert d1.selected.strategy_id == "deterministic-tool"


def test_shadow_router_refuses_structurally_unready_effectful_mission():
    d = shadow_route(mission(proof_obligations=()), (
        Strategy("x", "tool", 1.0, 100),
    ))
    assert d.selected is None
    assert "proof obligations" in d.reason


def test_negative_budget_rejected():
    with pytest.raises(ValueError):
        Budget(money_usd=-1)
