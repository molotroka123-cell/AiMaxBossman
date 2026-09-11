#!/usr/bin/env python3
"""Fail-closed anti-regression gate for Bossman orchestration intelligence.

The gate compares the SAME model on the SAME benchmark items under four
execution modes: raw -> system -> context -> full.

Exit codes: 0 PASS, 1 NO_GO, 2 INSUFFICIENT_EVIDENCE.

What makes a payload evidence (handoff pack 04, "Intelligence-preservation
corrections"):

* one model, one dataset, one commit: `model`, `dataset_id` and a 40-hex
  `evaluated_sha` are required; a lane that declares its own model/dataset
  must agree; `--expect-sha` binds the file to the commit under test
  (OLD_SHA_PASS != CURRENT_SHA_PASS);
* paired items: every metric must report the same `samples` in every lane;
* 98% retention is a RELATIVE ratio and a non-inferiority claim. A point
  estimate above the bar is not proof: the gate computes a conservative
  lower confidence bound on core retention and answers INSUFFICIENT_EVIDENCE
  when the bound does not clear the bar. A genuine paired runner can report
  per-metric discordance (`paired: {lost, gained}` versus the baseline lane),
  which tightens the bound; without it the unpaired bound is used and hundreds
  of items are not enough — that is the honest answer, not a defect;
* no regression hides inside an average: each core metric is checked on its
  own, not only the core mean;
* secondary metrics (long context, memory retrieval, computer-use planning,
  task completion) need the sample floor and are reported with their
  retention; a drop is a WARNING finding, not a silent pass.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

MODES = ("raw", "system", "context", "full")
CORE_METRICS = (
    "reasoning_accuracy",
    "coding_correctness",
    "structured_output_accuracy",
    "unknown_task_adaptation",
)
TOOL_METRICS = ("tool_selection_accuracy", "schema_argument_accuracy")
CONTEXT_METRICS = ("long_context_accuracy", "memory_retrieval_accuracy")
AUTONOMY_METRICS = ("computer_use_planning_accuracy", "task_completion_rate")
SECONDARY_METRICS = CONTEXT_METRICS + AUTONOMY_METRICS
LOWER_IS_BETTER = ("hallucination_rate",)
REQUIRED_METRICS = CORE_METRICS + TOOL_METRICS + SECONDARY_METRICS + LOWER_IS_BETTER
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_Z = {0.90: 1.6448536269514722, 0.95: 1.959963984540054, 0.99: 2.5758293035489004}


@dataclass(frozen=True)
class GateConfig:
    core_retention_min: float = 0.98
    context_core_retention_min: float = 0.98
    tool_retention_min: float = 1.0
    hallucination_relative_max: float = 1.05
    min_samples_per_metric: int = 20
    confidence: float = 0.95
    expect_sha: str | None = None


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str          # BLOCKER | INSUFFICIENT | WARNING
    message: str


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _metric(block: Mapping[str, Any], name: str) -> tuple[float, int]:
    item = block.get(name)
    if not isinstance(item, Mapping):
        raise ValueError(f"missing metric: {name}")
    score = item.get("score")
    samples = item.get("samples")
    if not _is_number(score) or not isinstance(samples, int) or isinstance(samples, bool):
        raise ValueError(f"invalid metric payload: {name}")
    score = float(score)
    if not 0.0 <= score <= 1.0:
        raise ValueError(f"score outside [0,1]: {name}={score}")
    if samples < 1:
        raise ValueError(f"samples must be positive: {name}")
    return score, samples


def _paired(block: Mapping[str, Any], name: str, samples: int) -> tuple[int, int] | None:
    """Optional discordance versus the baseline lane: items the baseline got right
    and this lane lost, and items this lane gained. None when not reported."""
    item = block.get(name) or {}
    paired = item.get("paired")
    if paired is None:
        return None
    if not isinstance(paired, Mapping):
        raise ValueError(f"invalid paired block: {name}")
    lost, gained = paired.get("lost"), paired.get("gained")
    for label, value in (("lost", lost), ("gained", gained)):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0 or value > samples:
            raise ValueError(f"invalid paired.{label}: {name}")
    return int(lost), int(gained)


def wilson(successes: float, n: int, z: float) -> tuple[float, float]:
    """Wilson score interval for a proportion (successes may be fractional when
    derived from a score)."""
    if n <= 0:
        raise ValueError("n must be positive")
    p = max(0.0, min(1.0, successes / n))
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n)) / denom
    lo, hi = max(0.0, centre - half), min(1.0, centre + half)
    if successes <= 0:
        lo = 0.0
    if successes >= n:
        hi = 1.0
    return lo, hi


def _mean(mode: Mapping[str, Any], metrics: tuple[str, ...], cfg: GateConfig) -> float:
    values: list[float] = []
    for name in metrics:
        score, samples = _metric(mode, name)
        if samples < cfg.min_samples_per_metric:
            raise ValueError(f"insufficient samples: {name} has {samples}, need {cfg.min_samples_per_metric}")
        values.append(score)
    return sum(values) / len(values)


def _ratio(current: float, baseline: float) -> float:
    if baseline <= 0.0:
        raise ValueError("baseline score is zero; retention is undefined")
    return current / baseline


def _lane_floor(lane: Mapping[str, Any], baseline: Mapping[str, Any], name: str, z: float) -> float:
    """Conservative lower bound on this lane's score for `name`, given the
    baseline lane. Paired discordance (if reported) bounds the loss directly;
    otherwise the unpaired Wilson lower bound of the lane score is used."""
    base_score, n = _metric(baseline, name)
    lane_score, _ = _metric(lane, name)
    paired = _paired(lane, name, n)
    if paired is None:
        return wilson(lane_score * n, n, z)[0]
    lost, gained = paired
    implied = base_score + (gained - lost) / n
    if abs(implied - lane_score) > 1.0 / n + 1e-9:
        raise ValueError(f"paired counts contradict score: {name} implies {implied:.4f}, reported {lane_score:.4f}")
    loss_upper = wilson(lost, n, z)[1]
    gain_lower = wilson(gained, n, z)[0]
    return max(0.0, base_score + gain_lower - loss_upper)


def _core_retention_bound(lane: Mapping[str, Any], raw: Mapping[str, Any], z: float) -> float:
    """Lower bound on core retention: worst-case lane core mean over the
    upper-bounded raw core mean (unpaired) or the raw mean itself (paired)."""
    lane_floor = 0.0
    raw_ceiling = 0.0
    for name in CORE_METRICS:
        raw_score, n = _metric(raw, name)
        lane_floor += _lane_floor(lane, raw, name, z)
        raw_ceiling += raw_score if _paired(lane, name, n) is not None else wilson(raw_score * n, n, z)[1]
    lane_floor /= len(CORE_METRICS)
    raw_ceiling /= len(CORE_METRICS)
    return _ratio(lane_floor, raw_ceiling)


def _check_identity(payload: Mapping[str, Any], modes: Mapping[str, Any], cfg: GateConfig) -> dict[str, Any]:
    model = payload.get("model")
    dataset = payload.get("dataset_id")
    sha = str(payload.get("evaluated_sha") or "").strip().lower()
    if not isinstance(model, str) or not model.strip():
        raise ValueError("missing model: the gate compares ONE model with itself")
    if not isinstance(dataset, str) or not dataset.strip():
        raise ValueError("missing dataset_id: the gate compares the SAME items across lanes")
    if not _SHA_RE.match(sha):
        raise ValueError("missing or invalid evaluated_sha (40 hex): retention is measured on a commit")
    if cfg.expect_sha and cfg.expect_sha.strip().lower() != sha:
        raise ValueError(f"evaluated_sha {sha[:12]} is not the commit under test {cfg.expect_sha[:12]}: "
                         "OLD_SHA_PASS != CURRENT_SHA_PASS")
    for lane_name in MODES:
        lane = modes[lane_name]
        for key, expected in (("model", model), ("dataset_id", dataset)):
            declared = lane.get(key)
            if declared is not None and str(declared) != str(expected):
                raise ValueError(f"lane {lane_name} declares {key}={declared!r}, payload says {expected!r}: not the same experiment")
        for key in ("model_version", "quantization", "decoding"):
            if key in lane and key in payload and lane[key] != payload[key]:
                raise ValueError(f"lane {lane_name} {key} differs from payload: not the same model configuration")
    for metric_name in REQUIRED_METRICS:
        counts = {lane_name: _metric(modes[lane_name], metric_name)[1] for lane_name in MODES}
        if len(set(counts.values())) != 1:
            raise ValueError(f"unpaired items: {metric_name} samples differ across lanes {counts}")
    _check_full_lane_is_the_real_harness(payload)
    return {"model": model, "dataset_id": dataset, "evaluated_sha": sha}


def _check_full_lane_is_the_real_harness(payload: Mapping[str, Any]) -> None:
    """The FULL lane must have EXECUTED the harness, not described it.

    What this gate means by "the harness preserves the model's intelligence" is
    that the real loop — production system prompt, real tool schemas, the real
    context builder, and tools that actually run and return output to the model —
    does not cost quality. A lane that appends a LIST OF TOOL NAMES to the prompt
    and calls the model once measures none of that: there is no harness in it,
    and it would answer the question with a number that cannot be wrong. That is
    AF-04, and it is a way to close a fail-closed gate without measuring what the
    gate is for.

    The producing runner already declares this (`lanes.full.executes_tools`), so
    the check is a cheap read — but it was never enforced, which meant a payload
    from any weaker runner was accepted on equal terms. Missing declaration is a
    refusal, not a pass: evidence that does not say how it was produced is not
    evidence that the harness was exercised.
    """
    lanes = payload.get("lanes")
    if not isinstance(lanes, Mapping):
        raise ValueError(
            "missing lanes block: the payload must declare how each lane was produced, "
            "otherwise a prompt ablation and the real execution loop are indistinguishable")
    full = lanes.get("full")
    if not isinstance(full, Mapping):
        raise ValueError("missing lanes.full: the FULL lane must declare how it was produced")
    if full.get("executes_tools") is not True:
        raise ValueError(
            "lanes.full.executes_tools is not true: the FULL lane did not run the harness. "
            "Appending tool names to a prompt does not measure whether Bossman's own loop "
            "preserves the model's quality (AF-04)")
    kind = str(full.get("kind") or "")
    if kind != "production_execution_loop":
        raise ValueError(
            f"lanes.full.kind is {kind!r}, expected 'production_execution_loop': "
            "the FULL lane must be the production loop, not an ablation of it")
    observed = full.get("observed")
    if not isinstance(observed, Mapping):
        raise ValueError(
            "missing lanes.full.observed: a lane that executed tools reports what it executed")
    executed = observed.get("executed")
    if not isinstance(executed, int) or isinstance(executed, bool) or executed < 1:
        raise ValueError(
            f"lanes.full.observed.executed is {executed!r}: the FULL lane declares it executes "
            "tools but reports no tool actually executed")


def evaluate(payload: Mapping[str, Any], cfg: GateConfig | None = None) -> dict[str, Any]:
    cfg = cfg or GateConfig()
    z = _Z.get(cfg.confidence)
    if z is None:
        raise ValueError("confidence must be one of 0.90, 0.95, 0.99")
    modes = payload.get("modes")
    if not isinstance(modes, Mapping):
        raise ValueError("missing modes mapping")
    for name in MODES:
        if not isinstance(modes.get(name), Mapping):
            raise ValueError(f"missing mode: {name}")
    for mode_name in MODES:
        for metric_name in REQUIRED_METRICS:
            _metric(modes[mode_name], metric_name)
    identity = _check_identity(payload, modes, cfg)

    raw, system, context, full = (modes[name] for name in MODES)
    findings: list[Finding] = []

    raw_core = _mean(raw, CORE_METRICS, cfg)
    system_core = _mean(system, CORE_METRICS, cfg)
    context_core = _mean(context, CORE_METRICS, cfg)
    full_core = _mean(full, CORE_METRICS, cfg)
    system_retention = _ratio(system_core, raw_core)
    context_retention = _ratio(context_core, raw_core)
    full_retention = _ratio(full_core, raw_core)

    lanes = (("system", system, system_retention, cfg.core_retention_min, "SYSTEM_CORE_REGRESSION"),
             ("context", context, context_retention, cfg.context_core_retention_min, "CONTEXT_CORE_REGRESSION"),
             ("full", full, full_retention, cfg.core_retention_min, "FULL_CORE_REGRESSION"))
    core_bounds: dict[str, float] = {}
    per_metric: dict[str, dict[str, float]] = {}
    for lane_name, lane, retention, minimum, code in lanes:
        if retention < minimum:
            findings.append(Finding(code, "BLOCKER", f"{lane_name}/raw={retention:.4f} < {minimum:.4f}"))
        # no regression hides inside the average
        per_metric[lane_name] = {}
        for metric_name in CORE_METRICS:
            r = _ratio(_metric(lane, metric_name)[0], _metric(raw, metric_name)[0])
            per_metric[lane_name][metric_name] = r
            if r < minimum and retention >= minimum:
                findings.append(Finding("CORE_METRIC_REGRESSION", "BLOCKER",
                                        f"{lane_name}/raw {metric_name}={r:.4f} < {minimum:.4f} (hidden by the core mean {retention:.4f})"))
        bound = _core_retention_bound(lane, raw, z)
        core_bounds[lane_name] = bound
        if retention >= minimum and bound < minimum:
            findings.append(Finding("CORE_RETENTION_PRECISION_INSUFFICIENT", "INSUFFICIENT",
                                    f"{lane_name}/raw point={retention:.4f} but {int(cfg.confidence * 100)}% lower bound={bound:.4f} "
                                    f"< {minimum:.4f}: more paired items (or reported paired discordance) needed to claim non-inferiority"))

    tool_ratios: dict[str, float] = {}
    for metric_name in TOOL_METRICS:
        context_score, context_samples = _metric(context, metric_name)
        full_score, full_samples = _metric(full, metric_name)
        if min(context_samples, full_samples) < cfg.min_samples_per_metric:
            raise ValueError(f"insufficient samples for {metric_name}")
        ratio = _ratio(full_score, context_score)
        tool_ratios[metric_name] = ratio
        if ratio < cfg.tool_retention_min:
            findings.append(Finding("TOOL_REGRESSION", "BLOCKER", f"{metric_name}: full/context={ratio:.4f} < {cfg.tool_retention_min:.4f}"))

    secondary: dict[str, float] = {}
    for metric_name in SECONDARY_METRICS:
        raw_score, raw_n = _metric(raw, metric_name)
        full_score, full_n = _metric(full, metric_name)
        if min(raw_n, full_n) < cfg.min_samples_per_metric:
            raise ValueError(f"insufficient samples: {metric_name} has {min(raw_n, full_n)}, need {cfg.min_samples_per_metric}")
        ratio = _ratio(full_score, raw_score)
        secondary[metric_name] = ratio
        if ratio < cfg.core_retention_min:
            findings.append(Finding("SECONDARY_REGRESSION", "WARNING", f"{metric_name}: full/raw={ratio:.4f} < {cfg.core_retention_min:.4f}"))

    raw_h, raw_h_n = _metric(raw, "hallucination_rate")
    full_h, full_h_n = _metric(full, "hallucination_rate")
    if min(raw_h_n, full_h_n) < cfg.min_samples_per_metric:
        raise ValueError("insufficient hallucination samples")
    if raw_h == 0.0:
        hallucination_ratio = None
        if full_h > 0.0:
            findings.append(Finding("HALLUCINATION_REGRESSION", "BLOCKER", f"full hallucination rate rose from 0 to {full_h:.4f}"))
    else:
        hallucination_ratio = full_h / raw_h
        if hallucination_ratio > cfg.hallucination_relative_max:
            findings.append(Finding("HALLUCINATION_REGRESSION", "BLOCKER", f"full/raw={hallucination_ratio:.4f} > {cfg.hallucination_relative_max:.4f}"))

    severities = {f.severity for f in findings}
    status = "NO_GO" if "BLOCKER" in severities else "INSUFFICIENT_EVIDENCE" if "INSUFFICIENT" in severities else "PASS"
    return {
        "gate": "INTELLIGENCE_PRESERVATION",
        "version": 2,
        **identity,
        "status": status,
        "thresholds": {
            "core_retention_min": cfg.core_retention_min,
            "context_core_retention_min": cfg.context_core_retention_min,
            "tool_retention_min": cfg.tool_retention_min,
            "hallucination_relative_max": cfg.hallucination_relative_max,
            "min_samples_per_metric": cfg.min_samples_per_metric,
            "confidence": cfg.confidence,
        },
        "scores": {
            "raw_core": raw_core,
            "system_core": system_core,
            "context_core": context_core,
            "full_core": full_core,
            "system_core_retention": system_retention,
            "context_core_retention": context_retention,
            "full_core_retention": full_retention,
            "core_retention_lower_bound": core_bounds,
            "core_metric_retention": per_metric,
            "tool_retention": tool_ratios,
            "secondary_retention": secondary,
            "hallucination_ratio": hallucination_ratio,
        },
        "findings": [f.__dict__ for f in findings],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bossman intelligence-preservation release gate")
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--core-retention-min", type=float, default=0.98)
    parser.add_argument("--tool-retention-min", type=float, default=1.0)
    parser.add_argument("--min-samples", type=int, default=20)
    parser.add_argument("--confidence", type=float, default=0.95, choices=(0.90, 0.95, 0.99))
    parser.add_argument("--expect-sha", default=None, help="the commit under test; the payload's evaluated_sha must match")
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        report = evaluate(payload, GateConfig(core_retention_min=args.core_retention_min,
                                              context_core_retention_min=args.core_retention_min,
                                              tool_retention_min=args.tool_retention_min,
                                              min_samples_per_metric=args.min_samples,
                                              confidence=args.confidence, expect_sha=args.expect_sha))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        report = {"gate": "INTELLIGENCE_PRESERVATION", "status": "INSUFFICIENT_EVIDENCE", "error": str(exc)}
        text = json.dumps(report, indent=2, sort_keys=True)
        if args.output:
            args.output.write_text(text + "\n", encoding="utf-8")
        print(text)
        return 2
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return {"PASS": 0, "NO_GO": 1}.get(report["status"], 2)


if __name__ == "__main__":
    sys.exit(main())
