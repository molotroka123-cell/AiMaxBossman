#!/usr/bin/env python3
"""Fail-closed anti-regression gate for Bossman orchestration intelligence.

The gate compares the same model under four execution modes:
raw -> system -> context -> full.

Exit codes: 0 PASS, 1 NO-GO, 2 INSUFFICIENT_EVIDENCE.
"""

from __future__ import annotations

import argparse
import json
import math
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
LOWER_IS_BETTER = ("hallucination_rate",)
REQUIRED_METRICS = CORE_METRICS + TOOL_METRICS + CONTEXT_METRICS + AUTONOMY_METRICS + LOWER_IS_BETTER


@dataclass(frozen=True)
class GateConfig:
    core_retention_min: float = 0.98
    context_core_retention_min: float = 0.98
    tool_retention_min: float = 1.0
    hallucination_relative_max: float = 1.05
    min_samples_per_metric: int = 20


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str
    message: str


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _metric(block: Mapping[str, Any], name: str) -> tuple[float, int]:
    item = block.get(name)
    if not isinstance(item, Mapping):
        raise ValueError(f"missing metric: {name}")
    score = item.get("score")
    samples = item.get("samples")
    if not _is_number(score) or not isinstance(samples, int):
        raise ValueError(f"invalid metric payload: {name}")
    score = float(score)
    if not 0.0 <= score <= 1.0:
        raise ValueError(f"score outside [0,1]: {name}={score}")
    if samples < 1:
        raise ValueError(f"samples must be positive: {name}")
    return score, samples


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


def evaluate(payload: Mapping[str, Any], cfg: GateConfig | None = None) -> dict[str, Any]:
    cfg = cfg or GateConfig()
    modes = payload.get("modes")
    if not isinstance(modes, Mapping):
        raise ValueError("missing modes mapping")
    for name in MODES:
        if not isinstance(modes.get(name), Mapping):
            raise ValueError(f"missing mode: {name}")

    raw, system, context, full = (modes[name] for name in MODES)

    for mode_name in MODES:
        for metric_name in REQUIRED_METRICS:
            _metric(modes[mode_name], metric_name)

    findings: list[Finding] = []
    raw_core = _mean(raw, CORE_METRICS, cfg)
    system_core = _mean(system, CORE_METRICS, cfg)
    context_core = _mean(context, CORE_METRICS, cfg)
    full_core = _mean(full, CORE_METRICS, cfg)

    system_retention = _ratio(system_core, raw_core)
    context_retention = _ratio(context_core, raw_core)
    full_retention = _ratio(full_core, raw_core)

    if system_retention < cfg.core_retention_min:
        findings.append(Finding("SYSTEM_CORE_REGRESSION", "BLOCKER", f"system/raw={system_retention:.4f} < {cfg.core_retention_min:.4f}"))
    if context_retention < cfg.context_core_retention_min:
        findings.append(Finding("CONTEXT_CORE_REGRESSION", "BLOCKER", f"context/raw={context_retention:.4f} < {cfg.context_core_retention_min:.4f}"))
    if full_retention < cfg.core_retention_min:
        findings.append(Finding("FULL_CORE_REGRESSION", "BLOCKER", f"full/raw={full_retention:.4f} < {cfg.core_retention_min:.4f}"))

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

    return {
        "gate": "INTELLIGENCE_PRESERVATION",
        "version": 1,
        "model": payload.get("model", "unknown"),
        "dataset_id": payload.get("dataset_id", "unknown"),
        "status": "PASS" if not findings else "NO_GO",
        "thresholds": {
            "core_retention_min": cfg.core_retention_min,
            "context_core_retention_min": cfg.context_core_retention_min,
            "tool_retention_min": cfg.tool_retention_min,
            "hallucination_relative_max": cfg.hallucination_relative_max,
            "min_samples_per_metric": cfg.min_samples_per_metric,
        },
        "scores": {
            "raw_core": raw_core,
            "system_core": system_core,
            "context_core": context_core,
            "full_core": full_core,
            "system_core_retention": system_retention,
            "context_core_retention": context_retention,
            "full_core_retention": full_retention,
            "tool_retention": tool_ratios,
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
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        report = evaluate(payload, GateConfig(core_retention_min=args.core_retention_min, context_core_retention_min=args.core_retention_min, tool_retention_min=args.tool_retention_min, min_samples_per_metric=args.min_samples))
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
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
