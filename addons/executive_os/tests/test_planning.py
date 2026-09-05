"""Pure addon planning contracts: dependency, capability and evidence boundaries."""
from copy import deepcopy
import json
import math

import pytest

from bossman_os.planning import (checkpoint_interval, choose_route, effective_capabilities,
                                 evaluate_release, ready_nodes, select_context, wilson)


def fact(key, *, deps=(), text="verified observation", privacy="PUBLIC", expires=100):
    return {"id": key, "text": text, "source": "fixture:" + key,
            "expires_at": expires, "privacy": privacy, "depends_on": list(deps)}


def route(key, *, successes=90, total=100, cost=1, latency=1, risk=0, local=True, retries=0):
    return {"id": key, "successes": successes, "total": total, "cost": cost,
            "latency_seconds": latency, "risk": risk, "local": local, "expected_retries": retries}


def test_dag_ready_order_and_completed_dependencies():
    steps = [{"id": "test", "depends_on": ["edit"]}, {"id": "inspect", "depends_on": []},
             {"id": "edit", "depends_on": ["inspect"], "resources": {"gpu": 1}}]
    assert ready_nodes(steps, []) == ["inspect"]
    assert ready_nodes(steps, {"inspect"}) == ["edit"]
    assert ready_nodes(steps, {"inspect", "edit"}) == ["test"]
    assert ready_nodes(steps, {"inspect", "edit", "test"}) == []


@pytest.mark.parametrize("steps,done", [
    ([{"id": "a", "depends_on": ["missing"]}], []),
    ([{"id": "a", "depends_on": ["b"]}, {"id": "b", "depends_on": ["a"]}], []),
    ([{"id": "a", "depends_on": []}, {"id": "a", "depends_on": []}], []),
    ([{"id": "a", "depends_on": []}], ["missing"]),
    ([{"id": "a", "depends_on": []}, {"id": "b", "depends_on": ["a"]}], ["b"]),
    ([{"id": "a", "depends_on": [], "resources": {"gpu": True}}], []),
    ([{"id": "a", "depends_on": "b"}], []),
])
def test_dag_rejects_invalid_or_forged_state(steps, done):
    with pytest.raises(ValueError):
        ready_nodes(steps, done)


def test_long_dag_has_no_recursion_limit_and_no_input_mutation():
    steps = [{"id": str(i), "depends_on": [str(i - 1)] if i else []} for i in range(1500)]
    original = deepcopy(steps)
    assert ready_nodes(steps, []) == ["0"]
    assert steps == original


def test_capabilities_can_only_narrow_and_empty_means_none():
    assert effective_capabilities() == frozenset()
    assert effective_capabilities({"read", "write"}, {"read", "network"}, {"read"}) == {"read"}
    assert effective_capabilities({"read"}, []) == frozenset()
    assert effective_capabilities({"*"}, {"write"}) == frozenset()
    with pytest.raises(ValueError):
        effective_capabilities("read")


def test_context_returns_full_dependency_first_closure_with_provenance_copy():
    facts = [fact("decision", deps=["evidence"]), fact("evidence"), fact("unused", privacy="LOCAL", expires=0)]
    original = deepcopy(facts)
    selected = select_context(facts, ["decision"], 1000, 10, cloud=True)
    assert [f["id"] for f in selected] == ["evidence", "decision"]
    assert selected[0]["source"] == "fixture:evidence"
    selected[1]["depends_on"].append("mutated")
    assert facts == original


def test_public_root_cannot_export_local_dependency():
    with pytest.raises(ValueError, match="private"):
        select_context([fact("public", deps=["secret"]), fact("secret", privacy="LOCAL")],
                       ["public"], 1000, 10, cloud=True)


@pytest.mark.parametrize("facts,roots", [
    ([fact("a", expires=10)], ["a"]),
    ([fact("a", deps=["b"])], ["a"]),
    ([fact("a", deps=["b"]), fact("b", deps=["a"])], ["a"]),
    ([fact("a")], ["missing"]),
    ([fact("a"), fact("a")], ["a"]),
    ([fact("a", privacy="UNKNOWN")], ["a"]),
])
def test_context_rejects_missing_stale_or_ambiguous_closure(facts, roots):
    with pytest.raises(ValueError):
        select_context(facts, roots, 1000, 10)


def test_context_budget_includes_provenance_and_never_truncates_dependency():
    facts = [fact("a", deps=["b"]), fact("b", text="long evidence " * 100)]
    full = select_context(facts, ["a"], 10000, 10)
    exact = (len(json.dumps(full, sort_keys=True, ensure_ascii=False, separators=(",", ":"))) + 2) // 3
    assert select_context(facts, ["a"], exact, 10) == full
    with pytest.raises(ValueError, match="budget"):
        select_context(facts, ["a"], exact - 1, 10)


def test_route_uses_lower_bound_not_one_lucky_success():
    chosen = choose_route([route("one", successes=1, total=1), route("proven", successes=90, total=100)], 10)
    assert chosen["id"] == "proven"
    assert 0 < chosen["conservative_success"] < 0.9


def test_route_budget_includes_retries_and_cloud_is_denied_by_default():
    candidates = [route("cloud", local=False, cost=0), route("expensive", cost=3, retries=2), route("local", cost=2)]
    assert choose_route(candidates, 5)["id"] == "local"
    assert choose_route(candidates, 5, cloud_allowed=True)["id"] == "cloud"
    with pytest.raises(ValueError, match="no admissible"):
        choose_route([route("cloud", local=False)], 10)


def test_route_determinism_and_no_mutation():
    candidates = [route("z"), route("a")]
    before = deepcopy(candidates)
    assert choose_route(candidates, 10)["id"] == "a"
    assert candidates == before


@pytest.mark.parametrize("key,bad", [("cost", float("nan")), ("latency_seconds", 0),
                                   ("risk", 1.1), ("total", True), ("successes", 101),
                                   ("local", 1), ("expected_retries", float("inf"))])
def test_route_invalid_metadata_is_rejected_not_silently_filtered(key, bad):
    candidate = route("bad")
    candidate[key] = bad
    with pytest.raises(ValueError):
        choose_route([route("valid"), candidate], 10)


def test_wilson_known_values_and_unknown_evidence():
    lower, upper = wilson(5, 10)
    assert lower == pytest.approx(0.23658959, abs=1e-7)
    assert upper == pytest.approx(0.76341041, abs=1e-7)
    assert wilson(0, 0) == (0.0, 1.0)
    assert wilson(0, 10)[0] == 0
    assert wilson(10, 10)[1] == 1


@pytest.mark.parametrize("successes,total,z", [(True, 10, 1.96), (1, 0, 1.96), (-1, 10, 1.96),
                                              (1, 2, float("nan")), (1, 2, 0), (1, 10**100, 1.96)])
def test_wilson_rejects_nonnumeric_or_impossible_counts(successes, total, z):
    with pytest.raises(ValueError):
        wilson(successes, total, z)


def test_checkpoint_interval_formula_units_and_unknown_failure_rate():
    assert checkpoint_interval(2, 0.01) == pytest.approx(20)
    assert checkpoint_interval(8, 0.01) == pytest.approx(40)
    assert math.isinf(checkpoint_interval(2, 0))


@pytest.mark.parametrize("cost,rate", [(True, 1), (1, False), (0, 1), (1, -1),
                                      (float("inf"), 1), (1, float("nan"))])
def test_checkpoint_rejects_invalid_measurements(cost, rate):
    with pytest.raises(ValueError):
        checkpoint_interval(cost, rate)


def test_release_requires_same_suite_cases_and_strict_nonregression():
    baseline = {"suite_digest": "fixed-suite", "cases": {"a": True, "b": False}, "hard_failures": 0}
    improved = {"suite_digest": "fixed-suite", "cases": {"a": True, "b": True}, "hard_failures": 0}
    assert evaluate_release(baseline, improved, "fixed-suite")["eligible"] is True
    assert evaluate_release(baseline, baseline, "fixed-suite")["reasons"] == ["no_strict_improvement"]
    regressed = {**improved, "cases": {"a": False, "b": True}}
    assert "previous_pass_regressed" in evaluate_release(baseline, regressed, "fixed-suite")["reasons"]
    assert "candidate_hard_failure" in evaluate_release(baseline, {**improved, "hard_failures": 1}, "fixed-suite")["reasons"]
    assert "suite_digest_mismatch" in evaluate_release(baseline, improved, "changed-suite")["reasons"]
    assert "case_set_mismatch" in evaluate_release(baseline, {**improved, "cases": {"a": True}}, "fixed-suite")["reasons"]


def test_release_rejects_unverified_truthy_outcomes_and_bool_counts():
    good = {"suite_digest": "s", "cases": {"a": True}, "hard_failures": 0}
    for bad in ({**good, "cases": {"a": "PASS"}}, {**good, "hard_failures": False}, {**good, "cases": {}}):
        with pytest.raises(ValueError):
            evaluate_release(good, bad, "s")
