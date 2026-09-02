"""PASS3 observe-only waste detector, advisory-only advisor, local reuse gate."""
from __future__ import annotations

from bossman_shared.cache_intelligence import (ReuseOutcome, allow_local_cognitive_reuse, cache_advice,
                                               detect_context_waste, fresh_observation_wins)


def test_waste_detector_signals_without_acting():
    obs = [{"prefix_hash": h} for h in ("a", "b", "c", "d")]
    layout = {"blocks": [{"kind": "policy", "hash": "p"}, {"kind": "task", "hash": "t"},
                         {"kind": "tools", "hash": "x"}, {"kind": "tools", "hash": "x"},
                         {"kind": "live_state", "cacheable": True}, {"kind": "credential", "cacheable": True}]}
    eps = [{"strategy_hash": "s1", "new_evidence": False}, {"strategy_hash": "s1", "new_evidence": False},
           {"context_tokens": 60000, "confidence": 0.5}, {"context_tokens": 70000, "confidence": 0.51},
           {"reused": True, "verified_success": 0.6}, {"reused": False, "verified_success": 0.9},
           {"retrieval_full_repeat": True}, {"retrieval_full_repeat": True}]
    kinds = {s.kind: s for s in detect_context_waste(obs, layout=layout, episodes=eps)}
    for k in ("stable_prefix_churn", "dynamic_before_stable_prefix", "duplicate_schemas",
              "security_or_live_state_cached", "repeated_strategy_without_evidence",
              "large_context_no_confidence_gain", "reuse_degrades_quality", "repeated_full_retrieval"):
        assert k in kinds, k
    assert kinds["security_or_live_state_cached"].severity == "red" and kinds["reuse_degrades_quality"].severity == "red"
    assert layout["blocks"][1]["kind"] == "task"                     # ничего не переставлено
    assert detect_context_waste([], layout={"blocks": [{"kind": "policy"}, {"kind": "tools", "hash": "x"},
                                                       {"kind": "task"}]}) == []


def test_advisor_blocks_security_move_and_never_recommends_forbidden_actions():
    blocked = cache_advice({"eligible_requests": 500}, security_context_moved=True)
    assert blocked[0].action == "BLOCK" and blocked[0].security_check == "violation"
    assert cache_advice({"eligible_requests": 3})[0].action == "NO_ACTION"
    adv = cache_advice({"eligible_requests": 100, "hit_rate_percent": 5.0, "cache_control_without_usage": 2,
                        "degraded_events": 1}, prefix_stability=0.5)
    actions = {a.action for a in adv}
    assert actions <= {"EXPERIMENT", "CHECK"} and all(a.rollback for a in adv)
    assert not any(w in a.text.lower() for a in adv for w in ("move policy", "reorder security", "enable cache"))


def test_local_reuse_gate_requires_same_model_ab_and_non_inferiority():
    base = dict(verified_success_on=0.91, verified_success_off=0.90, continuity_delta=0.2, compute_delta=-0.1,
                samples_on=30, samples_off=30)
    assert allow_local_cognitive_reuse(ReuseOutcome(**base))[0]
    assert not allow_local_cognitive_reuse(ReuseOutcome(**{**base, "verified_success_on": 0.8}))[0]
    assert "INSUFFICIENT" in allow_local_cognitive_reuse(ReuseOutcome(**{**base, "samples_on": 5}))[1]
    assert not allow_local_cognitive_reuse(ReuseOutcome(**{**base, "holdout_isolated": False}))[0]
    assert not allow_local_cognitive_reuse(ReuseOutcome(**{**base, "same_model": False}))[0]
    assert not allow_local_cognitive_reuse(ReuseOutcome(**{**base, "stale_error_delta": 0.01}))[0]
    assert not allow_local_cognitive_reuse(ReuseOutcome(**{**base, "false_success_delta": 0.01}))[0]
    assert not allow_local_cognitive_reuse(ReuseOutcome(**{**base, "continuity_delta": 0, "compute_delta": 0}))[0]
    assert fresh_observation_wins({"cached": 1}, {"fresh": 1}) == {"fresh": 1}
    assert fresh_observation_wins({"cached": 1}, None) == {"cached": 1}
