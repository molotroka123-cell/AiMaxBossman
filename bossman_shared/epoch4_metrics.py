"""Offline arithmetic for the Epoch 4 paired, serial performance protocol.

Inputs are reported measurements, never trusted execution evidence. Even MET
means only that the supplied numbers meet targets. This module cannot certify
the system, authorize execution or promote a configuration. No I/O is performed.
Parallel wall-clock throughput and unknown-cost local resource vectors require
separate measurement adapters and are deliberately unsupported here.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import math
import random
import re
from typing import Mapping, Sequence


@dataclass(frozen=True)
class Measurement:
    pair_id: str
    family: str
    elapsed_seconds: float
    cost_usd: float | None
    verified_result: bool
    avoidable_interventions: int
    mandatory_approvals: int
    unsafe_events: int
    evidence_ref: str


@dataclass(frozen=True)
class Dataset:
    commit_sha: str
    dirty_tree: bool
    # Frozen descriptions/hashes, identical between compared configurations.
    configuration: tuple[tuple[str, str], ...]
    records: tuple[Measurement, ...]


CONFIG_FIELDS = frozenset({"hardware", "platform", "models", "permissions",
                           "resource_envelope", "workload", "execution_mode"})


def _finite(value: object, *, positive: bool = False) -> bool:
    try:
        return (type(value) in (int, float) and math.isfinite(value)
                and (value > 0 if positive else value >= 0))
    except OverflowError:
        return False


def _check(data: Dataset, manifest: Mapping[str, str]) -> dict[str, Measurement]:
    if type(data) is not Dataset or not re.fullmatch(r"[0-9a-f]{40}", data.commit_sha):
        raise ValueError("dataset requires an exact commit SHA")
    if data.dirty_tree is not False:
        raise ValueError("dirty or unknown source tree")
    config = dict(data.configuration)
    if len(config) != len(data.configuration) or set(config) != CONFIG_FIELDS:
        raise ValueError("complete unique frozen configuration required")
    if any(type(v) is not str or not v.strip() for v in config.values()):
        raise ValueError("configuration values must be explicit")
    if config["execution_mode"] != "serial":
        raise ValueError("parallel throughput requires measured workload wall time")
    found = {}
    for row in data.records:
        if type(row) is not Measurement or row.pair_id in found:
            raise ValueError("duplicate or invalid measurement")
        if row.pair_id not in manifest or manifest[row.pair_id] != row.family:
            raise ValueError("measurement differs from preregistered workload")
        if not _finite(row.elapsed_seconds, positive=True):
            raise ValueError("all attempts need finite positive elapsed time")
        if row.cost_usd is not None and not _finite(row.cost_usd):
            raise ValueError("invalid measured cost")
        if type(row.verified_result) is not bool:
            raise ValueError("explicit reported result required")
        for n in (row.avoidable_interventions, row.mandatory_approvals, row.unsafe_events):
            if type(n) is not int or n < 0:
                raise ValueError("event counts must be nonnegative integers")
        if type(row.evidence_ref) is not str or not row.evidence_ref.strip():
            raise ValueError("measurement provenance reference missing")
        found[row.pair_id] = row
    if set(found) != set(manifest):
        raise ValueError("missing pairs: failed or aborted attempts must be retained")
    return found


def _totals(rows: Sequence[Measurement]) -> dict:
    return {"attempts": len(rows), "verified": sum(r.verified_result for r in rows),
            "elapsed_seconds": math.fsum(r.elapsed_seconds for r in rows),
            "cost_usd": math.fsum(r.cost_usd for r in rows),
            "avoidable_interventions": sum(r.avoidable_interventions for r in rows),
            "mandatory_approvals": sum(r.mandatory_approvals for r in rows),
            "unsafe_events": sum(r.unsafe_events for r in rows)}


def _ratios(base: dict, candidate: dict) -> dict:
    bv, cv = base["verified"], candidate["verified"]
    return {
        "throughput": (cv / candidate["elapsed_seconds"]) / (bv / base["elapsed_seconds"]) if bv and cv else None,
        "cost_per_verified": (candidate["cost_usd"] / cv) / (base["cost_usd"] / bv)
        if bv and cv and base["cost_usd"] > 0 else None,
        "avoidable_interventions": candidate["avoidable_interventions"] / base["avoidable_interventions"]
        if base["avoidable_interventions"] else None,
        "success_delta": (cv - bv) / base["attempts"],
    }


def evaluate(manifest: Mapping[str, str], baseline: Dataset, candidate: Dataset,
             *, bootstrap_samples: int = 1000, seed: int = 0) -> dict:
    """Compare every preregistered pair with stratified paired bootstrap.

    Each family keeps its preregistered sample weight. At least 100 pairs total,
    30 per family and positive observed baseline cost/success are required.
    Family omission and configuration changes fail rather than change weights.
    Percentile intervals are numerical estimates, not universal guarantees.
    """
    report = {"verdict": "INSUFFICIENT_EVIDENCE", "certified": False,
              "source_trust": "UNVERIFIED_REPORTED_MEASUREMENTS", "reasons": []}
    try:
        if not isinstance(manifest, Mapping) or not manifest or len(manifest) > 10_000:
            raise ValueError("bounded preregistered pair manifest required")
        if any(type(k) is not str or not k or type(v) is not str or not v for k, v in manifest.items()):
            raise ValueError("pair and family identifiers must be explicit strings")
        if type(bootstrap_samples) is not int or not 200 <= bootstrap_samples <= 5000 or type(seed) is not int:
            raise ValueError("bounded bootstrap count and integer seed required")
        manifest = dict(manifest)
        b, c = _check(baseline, manifest), _check(candidate, manifest)
        if dict(baseline.configuration) != dict(candidate.configuration):
            raise ValueError("hardware/model/permissions/resource/workload mismatch")
        if baseline.commit_sha == candidate.commit_sha:
            raise ValueError("baseline and candidate must identify distinct versions")
        counts = Counter(manifest.values())
        if len(manifest) < 100 or min(counts.values()) < 30:
            raise ValueError("need >=100 pairs and >=30 observations in every family")
        if any(r.cost_usd is None for r in (*b.values(), *c.values())):
            raise ValueError("unknown monetary cost: local resource-vector adapter required")
        bt, ct = _totals(tuple(b.values())), _totals(tuple(c.values()))
        ratios = _ratios(bt, ct)
        report.update(baseline=bt, candidate=ct, ratios=ratios,
                      baseline_sha=baseline.commit_sha, candidate_sha=candidate.commit_sha,
                      family_counts=dict(counts), bootstrap_samples=bootstrap_samples, seed=seed)
        if not bt["verified"] or not ct["verified"] or bt["cost_usd"] <= 0:
            raise ValueError("zero success or cost denominator")
        groups = [[key for key, family in manifest.items() if family == group] for group in sorted(counts)]
        per_family = {}
        for group in groups:
            gb, gc = _totals([b[k] for k in group]), _totals([c[k] for k in group])
            per_family[manifest[group[0]]] = {"baseline": gb, "candidate": gc, "ratios": _ratios(gb, gc)}
        report["per_family"] = per_family
        rng = random.Random(seed)
        draws = {key: [] for key in ratios}
        for _ in range(bootstrap_samples):
            keys = [rng.choice(group) for group in groups for _ in group]
            sample = _ratios(_totals([b[k] for k in keys]), _totals([c[k] for k in keys]))
            for key, value in sample.items():
                if value is None:
                    if key != "avoidable_interventions" or bt["avoidable_interventions"]:
                        raise ValueError("bootstrap denominator too sparse")
                else:
                    if not math.isfinite(value):
                        raise ValueError("nonfinite derived ratio")
                    draws[key].append(value)
        intervals = {}
        for key, values in draws.items():
            ordered = sorted(values)
            intervals[key] = [ordered[math.floor(.025 * (len(ordered) - 1))],
                              ordered[math.ceil(.975 * (len(ordered) - 1))]] if ordered else None
        report["intervals_95"] = intervals
        intervention_ok = (ct["avoidable_interventions"] == 0 if bt["avoidable_interventions"] == 0
                           else intervals["avoidable_interventions"][1] <= 1 / 3)
        report["interventions_zero_baseline_preserved"] = bt["avoidable_interventions"] == ct["avoidable_interventions"] == 0
        gates = {"throughput_3x": intervals["throughput"][0] >= 3,
                 "cost_one_third": intervals["cost_per_verified"][1] <= 1 / 3,
                 "avoidable_interventions": intervention_ok,
                 "success_noninferior": intervals["success_delta"][0] >= -.01,
                 "reported_unsafe_events_zero": bt["unsafe_events"] == ct["unsafe_events"] == 0}
        report.update(gates=gates, verdict="MET" if all(gates.values()) else "NOT_MET")
        report["reasons"] = [key for key, passed in gates.items() if not passed]
    except (ValueError, TypeError, AttributeError, OverflowError, ZeroDivisionError) as exc:
        report["reasons"].append(str(exc))
    return report
