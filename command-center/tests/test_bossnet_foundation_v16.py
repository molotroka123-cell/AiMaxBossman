from datetime import datetime, timedelta, timezone

import pytest

from bcc.features.bossnet_nodes_v16 import Node, TaskNeed, choose
from bcc.features.context_budget_v16 import ContextBudget, EvidenceItem, select_evidence
from bcc.features.knowledge_fabric_v16 import Fact, historical_view
from bcc.features.memory_retrieval_v16 import MemoryCandidate, Signals, pack
from bcc.features.model_foundry_v16 import ModelCandidate, promotable
from bcc.features.provider_fleet_v16 import ProviderClass, ProviderSlot, RentalEstimate, route, should_rent
from bcc.features.simulation_world_v16 import Scenario, summarize


def test_distributed_node_requires_owner_binding_and_fresh_heartbeat():
    now = 1000.0
    unbound = Node("remote", frozenset({"coding"}), 64, 24, last_heartbeat=995, owner_bound=False)
    bound = Node("local", frozenset({"coding"}), 128, 64, last_heartbeat=995, owner_bound=True)
    assert choose([unbound, bound], TaskNeed(frozenset({"coding"}), min_ram_gb=32), now=now) == bound
    assert choose([bound], TaskNeed(frozenset({"coding"})), now=1100) is None
    with pytest.raises(ValueError):
        Node("bad", frozenset(), -1)


def test_context_budget_fails_closed_for_bad_or_oversized_mandatory_evidence():
    b = ContextBudget(10000, 1000, 1000, 500, .30)
    assert b.evidence_budget() == 500
    chosen = select_evidence([
        EvidenceItem("must", 200, 1.0, mandatory=True),
        EvidenceItem("small", 100, .8),
        EvidenceItem("large", 400, .9),
    ], 500)
    assert [x.ref for x in chosen] == ["must", "small"]
    with pytest.raises(ValueError):
        EvidenceItem("x", -1, .5)
    with pytest.raises(ValueError):
        select_evidence([EvidenceItem("must", 501, 1, mandatory=True)], 500)


def test_temporal_fact_has_provenance_and_point_in_time_visibility():
    t0 = datetime(2026, 9, 1, tzinfo=timezone.utc)
    f = Fact("f1", "branch", "status", "green", t0, t0 + timedelta(days=2),
             t0 + timedelta(hours=1), "run:123", .9)
    assert historical_view([f], world_time=t0 + timedelta(days=1),
                           knowledge_time=t0 + timedelta(days=1)) == [f]
    assert historical_view([f], world_time=t0 + timedelta(days=3),
                           knowledge_time=t0 + timedelta(days=3)) == []
    with pytest.raises(ValueError):
        Fact("bad", "x", "y", "z", t0, None, t0, "", 2.0)


def test_memory_pack_respects_budget_and_normalized_signals():
    rows = [
        MemoryCandidate("a", 100, Signals(semantic=.9, verified=1)),
        MemoryCandidate("b", 100, Signals(semantic=.4, verified=.5)),
    ]
    assert pack(rows, 100)[0].ref == "a"
    with pytest.raises(ValueError):
        Signals(semantic=1.2)


def test_model_foundry_requires_separate_holdout_and_non_regression():
    good = ModelCandidate("m2", "m1", "train", "holdout", .8, .84, .9, .9, "m1")
    assert promotable(good, min_relative_gain=.02)
    assert not promotable(good.__class__("m2","m1","same","same",.8,.84,.9,.9,"m1"))
    assert not promotable(good.__class__("m2","m1","train","holdout",.8,.84,.9,.8,"m1"))


def slot(name, cls, **kw):
    return ProviderSlot(name, "primary", cls, True, True, kw.pop("quality_score", .9),
                        kw.pop("latency_ms", 20), kw.pop("marginal_cost_usd", 0), **kw)


def test_provider_fleet_blocks_zero_quota_secondary_and_unapproved_rental():
    assert route([slot("free", ProviderClass.OWNER_PRIMARY, quota_remaining=0)], min_quality=.5) is None
    assert route([slot("secondary", ProviderClass.OWNER_SECONDARY)], min_quality=.5) is None
    assert route([slot("gpu", ProviderClass.RENTED_GPU)], min_quality=.5) is None
    approved = slot("gpu", ProviderClass.RENTED_GPU, rental_authorized=True, marginal_cost_usd=1)
    assert route([approved], min_quality=.5) == approved


def test_rental_decision_is_economic_recommendation_but_budget_can_block():
    r = RentalEstimate(1, 2)
    assert should_rent(rental=r, api_cost_usd=10, local_hours=8, rental_hours=2,
                       required_memory_fits_local=True, owner_budget_usd=1)["rent"] is False
    assert should_rent(rental=r, api_cost_usd=10, local_hours=8, rental_hours=2,
                       required_memory_fits_local=True, owner_budget_usd=5)["rent"] is True


def test_simulation_summary_rejects_invalid_probabilities_and_labels_output_simulated():
    rows=[Scenario("a", .5, {"profit": 10}), Scenario("b", .5, {"profit": -2})]
    out=summarize(rows, "profit")
    assert out["status"] == "SIMULATED" and out["n"] == 2 and out["weighted_mean"] == pytest.approx(4)
    with pytest.raises(ValueError):
        Scenario("bad", -1, {"profit": 1})
