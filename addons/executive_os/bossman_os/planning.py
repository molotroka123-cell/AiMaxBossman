"""Pure planning helpers for Executive OS; inputs are trusted host metadata.

These functions do not grant permissions, execute tools, infer successful
outcomes, mutate caller data or access Core state. Invalid inputs raise
ValueError. Numeric inputs must be finite; booleans are never numeric counts.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import json
import math


def _number(value, name, *, minimum=0.0, maximum=None):
    if type(value) not in (int, float):
        raise ValueError(f"{name} must be a finite number")
    try:
        value = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(value) or value < minimum or (maximum is not None and value > maximum):
        raise ValueError(f"{name} is outside its finite range")
    return value


def _count(value, name, *, minimum=0):
    if type(value) is not int or not minimum <= value <= 2**53 - 1:
        raise ValueError(f"{name} must be an exact integer in [{minimum}, 2**53-1]")
    _number(value, name, minimum=minimum)
    return value


def _name(value, name):
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value


def _names(values, name):
    if not isinstance(values, (list, tuple, set, frozenset)):
        raise ValueError(f"{name} must be a collection of strings")
    result = tuple(_name(value, name) for value in values)
    if len(result) != len(set(result)):
        raise ValueError(f"{name} contains duplicate identities")
    return result


def _record(value, required, optional=()):
    if not isinstance(value, Mapping):
        raise ValueError("record must be a mapping")
    if set(value) - set(required) - set(optional) or set(required) - set(value):
        raise ValueError(f"record requires {sorted(required)}; optional {sorted(optional)}")
    return value


def _graph(records):
    if any(not isinstance(row["depends_on"], (list, tuple)) for row in records.values()):
        raise ValueError("depends_on must be an ordered sequence")
    dependencies = {key: _names(row["depends_on"], "depends_on") for key, row in records.items()}
    for deps in dependencies.values():
        if set(deps) - records.keys():
            raise ValueError("unknown dependency")
    # Iterative DFS avoids recursion limits for long but valid mission plans.
    state, ordered = {}, []
    for root in records:
        stack = [(root, False)]
        while stack:
            node, exiting = stack.pop()
            if exiting:
                state[node] = 2
                ordered.append(node)
                continue
            if state.get(node) == 2:
                continue
            if state.get(node) == 1:
                raise ValueError("dependency cycle")
            state[node] = 1
            stack.append((node, True))
            stack.extend((dep, False) for dep in reversed(dependencies[node]))
    return dependencies, ordered


def ready_nodes(steps, completed):
    """Return dependency-ready IDs in input order after validating the whole DAG.

    Steps: [{id: str, depends_on: [str], resources?: {name: amount}}]. Resources
    are validated metadata, NOT admission; host resource limits remain required.
    Completed IDs must exist and be dependency-closed; no partial malformed DAG
    is silently scheduled. No completion state is inferred from model text.
    """
    if not isinstance(steps, (list, tuple)):
        raise ValueError("steps must be a sequence")
    records = {}
    for step in steps:
        _record(step, ("id", "depends_on"), ("resources",))
        key = _name(step["id"], "step id")
        if key in records:
            raise ValueError("duplicate step id")
        resources = step.get("resources", {})
        if not isinstance(resources, Mapping):
            raise ValueError("resources must be a mapping")
        for name, amount in resources.items():
            _name(name, "resource name")
            _number(amount, "resource amount")
        records[key] = step
    dependencies, _ = _graph(records)
    done = set(_names(completed, "completed"))
    if done - records.keys():
        raise ValueError("unknown completed step")
    if any(set(dependencies[key]) - done for key in done):
        raise ValueError("completed steps are not dependency-closed")
    return [key for key in records if key not in done and set(dependencies[key]) <= done]


def effective_capabilities(*sets):
    """Intersect all host/agent/task capability collections; no inputs means none.

    Returns frozenset[str]. '*' has no wildcard expansion or special authority.
    Strings are rejected as collections to avoid accidental character grants.
    """
    if not sets:
        return frozenset()
    validated = [set(_names(values, "capabilities")) for values in sets]
    return frozenset.intersection(*(frozenset(values) for values in validated))


def select_context(facts, roots, budget, now, cloud=False):
    """Return copied facts in dependency-first order, without truncating closure.

    Fact schema: id/text/source/expires_at/privacy/depends_on; privacy is LOCAL
    or PUBLIC. Expiry is an absolute numeric timestamp; expires_at <= now is
    stale. The selected closure must fit budget estimated tokens, computed as
    ceil(len(canonical JSON of selected facts)/3), including source/provenance.
    This is a deterministic estimate, not a provider tokenizer or truth check.
    Every graph edge is validated; expiry/privacy checks apply to selected facts.
    A PUBLIC root with a LOCAL dependency may never be exported to cloud.
    """
    _count(budget, "budget", minimum=1)
    now = _number(now, "now")
    if type(cloud) is not bool:
        raise ValueError("cloud must be bool")
    if not isinstance(facts, (list, tuple)):
        raise ValueError("facts must be a sequence")
    records = {}
    for fact in facts:
        _record(fact, ("id", "text", "source", "expires_at", "privacy", "depends_on"))
        key = _name(fact["id"], "fact id")
        if key in records:
            raise ValueError("duplicate fact id")
        if type(fact["text"]) is not str:
            raise ValueError("fact text must be a string")
        _name(fact["source"], "fact source")
        _number(fact["expires_at"], "expires_at")
        if fact["privacy"] not in ("LOCAL", "PUBLIC"):
            raise ValueError("unknown privacy")
        records[key] = fact
    dependencies, ordered = _graph(records)
    selected = set()
    pending = list(_names(roots, "roots"))
    while pending:
        key = pending.pop()
        if key not in records:
            raise ValueError("unknown context root")
        if key in selected:
            continue
        selected.add(key)
        pending.extend(dependencies[key])
    result = []
    for key in ordered:
        if key not in selected:
            continue
        fact = records[key]
        if fact["expires_at"] <= now:
            raise ValueError("expired required context")
        if cloud and fact["privacy"] != "PUBLIC":
            raise ValueError("private context dependency cannot be exported")
        copied = deepcopy(dict(fact))
        copied["depends_on"] = list(dependencies[key])
        result.append(copied)
    payload = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if (len(payload) + 2) // 3 > budget:
        raise ValueError("required context closure exceeds budget; replan")
    return result


def wilson(successes, total, z=1.96):
    """Wilson interval for verified outcomes; counts <=2**53-1, no trials ->(0,1)."""
    successes, total = _count(successes, "successes"), _count(total, "total")
    z = _number(z, "z", minimum=0.0)
    if z <= 0 or successes > total:
        raise ValueError("positive z and successes <= total required")
    if total == 0:
        return 0.0, 1.0
    p = successes / total
    squared = z * z
    if not math.isfinite(squared):
        raise ValueError("z is too large")
    denominator = 1 + squared / total
    center = (p + squared / (2 * total)) / denominator
    radius = z * math.sqrt(p * (1 - p) / total + squared / (4 * total * total)) / denominator
    return max(0.0, center - radius), min(1.0, center + radius)


def choose_route(candidates, budget, cloud_allowed=False):
    """Select one route using empirical Wilson lower bound, not model forecasts.

    Candidate keys: id, successes, total, cost, latency_seconds, risk, local;
    expected_retries is optional (default 0). Cost and budget share a host-chosen
    unit. Expected cost=cost*(1+retries); utility=lower*(1-risk)/
    ((1+expected_cost)*latency). Unknown evidence yields lower=0, never success.
    Output: copied candidate plus conservative_success/expected_cost/utility.
    Invalid metadata rejects the request; inadmissible routes are filtered.
    Selection is a proposal: it does not reserve money or grant cloud permission.
    """
    budget = _number(budget, "budget")
    if type(cloud_allowed) is not bool or not isinstance(candidates, (list, tuple)):
        raise ValueError("typed candidates/cloud_allowed required")
    choices, identities = [], set()
    for candidate in candidates:
        _record(candidate, ("id", "successes", "total", "cost", "latency_seconds", "risk", "local"),
                ("expected_retries",))
        key = _name(candidate["id"], "route id")
        if key in identities:
            raise ValueError("duplicate route id")
        identities.add(key)
        lower, _ = wilson(candidate["successes"], candidate["total"])
        cost = _number(candidate["cost"], "cost")
        latency = _number(candidate["latency_seconds"], "latency_seconds")
        risk = _number(candidate["risk"], "risk", maximum=1.0)
        retries = _number(candidate.get("expected_retries", 0), "expected_retries")
        if latency <= 0 or type(candidate["local"]) is not bool:
            raise ValueError("positive latency and boolean locality required")
        expected_cost = cost * (1 + retries)
        if not math.isfinite(expected_cost):
            raise ValueError("expected cost overflow")
        if expected_cost > budget or (not candidate["local"] and not cloud_allowed):
            continue
        utility = (lower * (1 - risk) / (1 + expected_cost)) / latency
        if not math.isfinite(utility):
            raise ValueError("utility overflow")
        result = deepcopy(dict(candidate))
        result.update(conservative_success=lower, expected_cost=expected_cost, utility=utility)
        choices.append(result)
    if not choices:
        raise ValueError("no admissible route")
    return min(choices, key=lambda c: (-c["utility"], c["id"]))


def checkpoint_interval(cost_seconds, failure_rate):
    """Young approximation sqrt(2*checkpoint_cost/failure_rate), in seconds.

    Assumes a stationary Poisson failure rate per second. Zero rate means an
    unbounded optimum (math.inf); JSON callers must encode that as null or text.
    This estimate never disables host-mandated checkpoints or deadline limits.
    """
    cost = _number(cost_seconds, "cost_seconds")
    rate = _number(failure_rate, "failure_rate")
    if cost <= 0:
        raise ValueError("checkpoint cost must be positive")
    if rate == 0:
        return math.inf
    try:
        result = math.exp((math.log(2) + math.log(cost) - math.log(rate)) / 2)
    except OverflowError as exc:
        raise ValueError("checkpoint interval overflow") from exc
    if not math.isfinite(result):
        raise ValueError("checkpoint interval overflow")
    return result


def evaluate_release(baseline, candidate, suite_digest):
    """Compare the same externally fixed suite and case IDs, without promotion.

    Runs: {suite_digest: str, cases: {case_id: bool}, hard_failures: int}.
    Candidate must have zero hard failures, preserve every old PASS and add at
    least one PASS. Same-suite improvement is NOT proof of generalization;
    independent holdouts and operator release policy remain authoritative.
    """
    _name(suite_digest, "suite_digest")
    for run in (baseline, candidate):
        _record(run, ("suite_digest", "cases", "hard_failures"))
        _name(run["suite_digest"], "run suite_digest")
        _count(run["hard_failures"], "hard_failures")
        if not isinstance(run["cases"], Mapping) or not run["cases"]:
            raise ValueError("nonempty cases mapping required")
        for case, result in run["cases"].items():
            _name(case, "case id")
            if type(result) is not bool:
                raise ValueError("case outcomes must be verified booleans")
    reasons = []
    if baseline["suite_digest"] != suite_digest or candidate["suite_digest"] != suite_digest:
        reasons.append("suite_digest_mismatch")
    if baseline["cases"].keys() != candidate["cases"].keys():
        reasons.append("case_set_mismatch")
    else:
        if any(ok and not candidate["cases"][key] for key, ok in baseline["cases"].items()):
            reasons.append("previous_pass_regressed")
        if sum(candidate["cases"].values()) <= sum(baseline["cases"].values()):
            reasons.append("no_strict_improvement")
    if candidate["hard_failures"]:
        reasons.append("candidate_hard_failure")
    return {"eligible": not reasons, "reasons": reasons,
            "baseline_rate": sum(baseline["cases"].values()) / len(baseline["cases"]),
            "candidate_rate": sum(candidate["cases"].values()) / len(candidate["cases"])}
