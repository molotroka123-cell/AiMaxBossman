#!/usr/bin/env python3
"""Bossman real-workload and hardware scaling audit.

Consumes JSON/JSONL task records produced by real end-to-end runs, summarizes
verified outcomes, latency/throughput and operator burden, captures basic host
hardware/topology, and emits both JSON and Markdown reports.

This intentionally does not infer that a larger machine is required merely
because a model is large. Recommendations are evidence-gated by measured
workload pressure.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import subprocess
import time
from pathlib import Path
from typing import Any, Iterable


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * p
    lo, hi = int(pos), min(int(pos) + 1, len(xs) - 1)
    frac = pos - lo
    return xs[lo] * (1 - frac) + xs[hi] * frac


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def load_records(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    if text.startswith("["):
        data = json.loads(text)
        if not isinstance(data, list):
            raise ValueError("JSON input must be an array of task records")
        return [x for x in data if isinstance(x, dict)]
    records: list[dict[str, Any]] = []
    for line_no, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        item = json.loads(line)
        if not isinstance(item, dict):
            raise ValueError(f"line {line_no}: task record must be an object")
        records.append(item)
    return records


def validate_record(record: dict[str, Any], index: int) -> list[str]:
    errors: list[str] = []
    if not str(record.get("task_id", "")).strip():
        errors.append(f"record[{index}] missing task_id")
    if "duration_s" not in record or _safe_float(record.get("duration_s"), -1.0) < 0:
        errors.append(f"record[{index}] duration_s must be >= 0")
    if record.get("status") not in {"passed", "failed", "blocked"}:
        errors.append(f"record[{index}] status must be passed|failed|blocked")
    if record.get("verified") not in {True, False}:
        errors.append(f"record[{index}] verified must be boolean")
    return errors


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    durations = [_safe_float(r.get("duration_s")) for r in records]
    passed = [r for r in records if r.get("status") == "passed"]
    verified_passed = [r for r in passed if r.get("verified") is True]
    failed = [r for r in records if r.get("status") == "failed"]
    blocked = [r for r in records if r.get("status") == "blocked"]
    interventions = sum(_safe_int(r.get("human_interventions")) for r in records)
    retries = sum(_safe_int(r.get("retries")) for r in records)
    oom = sum(1 for r in records if r.get("oom") is True)
    total_wall = sum(durations)
    observed_span = None
    starts = [_safe_float(r.get("started_at"), -1) for r in records if r.get("started_at") is not None]
    ends = [_safe_float(r.get("ended_at"), -1) for r in records if r.get("ended_at") is not None]
    if starts and ends and min(starts) >= 0 and max(ends) >= min(starts):
        observed_span = max(ends) - min(starts)
    throughput_basis = observed_span if observed_span and observed_span > 0 else total_wall
    throughput_h = (len(records) / throughput_basis * 3600.0) if throughput_basis > 0 else None
    concurrency = max([_safe_int(r.get("concurrency"), 1) for r in records] or [0])
    peak_memory_gb = max([_safe_float(r.get("peak_memory_gb"), 0) for r in records] or [0.0])
    return {
        "tasks": len(records),
        "passed": len(passed),
        "verified_passed": len(verified_passed),
        "failed": len(failed),
        "blocked": len(blocked),
        "verified_success_rate": (len(verified_passed) / len(records)) if records else None,
        "p50_latency_s": _percentile(durations, 0.50),
        "p95_latency_s": _percentile(durations, 0.95),
        "mean_latency_s": statistics.fmean(durations) if durations else None,
        "throughput_tasks_per_hour": throughput_h,
        "human_interventions": interventions,
        "interventions_per_task": (interventions / len(records)) if records else None,
        "retries": retries,
        "oom_events": oom,
        "max_observed_concurrency": concurrency,
        "peak_observed_memory_gb": peak_memory_gb,
    }


def _linux_topology() -> dict[str, Any]:
    out: dict[str, Any] = {}
    try:
        raw = subprocess.check_output(["lscpu", "-J"], text=True, timeout=2)
        parsed = json.loads(raw)
        kv = {x.get("field", "").rstrip(":"): x.get("data") for x in parsed.get("lscpu", [])}
        out["sockets"] = _safe_int(kv.get("Socket(s)"), 0) or None
        out["numa_nodes"] = _safe_int(kv.get("NUMA node(s)"), 0) or None
        out["cores_per_socket"] = _safe_int(kv.get("Core(s) per socket"), 0) or None
        out["threads_per_core"] = _safe_int(kv.get("Thread(s) per core"), 0) or None
        out["cpu_model"] = kv.get("Model name")
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        pass
    return out


def detect_hardware() -> dict[str, Any]:
    hw: dict[str, Any] = {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "logical_cpus": os.cpu_count(),
    }
    if platform.system() == "Linux":
        hw.update(_linux_topology())
        try:
            mem_kb = None
            for line in Path("/proc/meminfo").read_text().splitlines():
                if line.startswith("MemTotal:"):
                    mem_kb = int(line.split()[1])
                    break
            if mem_kb:
                hw["ram_gb"] = round(mem_kb / 1024 / 1024, 2)
        except (OSError, ValueError):
            pass
    return hw


def recommend(summary: dict[str, Any], hardware: dict[str, Any], sla_p95_s: float | None = None) -> dict[str, Any]:
    """Return an evidence-based scaling verdict, never a topology guess."""
    reasons: list[str] = []
    tasks = summary["tasks"]
    if tasks < 10:
        return {
            "verdict": "INSUFFICIENT_EVIDENCE",
            "reasons": ["Need at least 10 representative real end-to-end task runs before hardware escalation."],
        }

    if summary["verified_success_rate"] is not None and summary["verified_success_rate"] < 0.90:
        reasons.append("Verified task success is below 90%; fix reliability before buying hardware.")
    if summary["human_interventions"] > 0:
        reasons.append("Human intervention remains in the critical path; hardware alone will not remove it.")
    if summary["oom_events"] > 0:
        reasons.append("OOM events were observed; memory capacity/admission is a measured bottleneck.")
    if sla_p95_s is not None and summary["p95_latency_s"] is not None and summary["p95_latency_s"] > sla_p95_s:
        reasons.append(f"Measured p95 {summary['p95_latency_s']:.2f}s exceeds SLA {sla_p95_s:.2f}s.")

    ram = _safe_float(hardware.get("ram_gb"), 0)
    peak = _safe_float(summary.get("peak_observed_memory_gb"), 0)
    memory_pressure = bool(ram and peak and peak / ram >= 0.85) or summary["oom_events"] > 0
    latency_pressure = bool(sla_p95_s is not None and summary["p95_latency_s"] is not None and summary["p95_latency_s"] > sla_p95_s)
    concurrency_pressure = summary["max_observed_concurrency"] >= 4 and latency_pressure

    reliability_ok = (summary["verified_success_rate"] or 0) >= 0.90
    if not reliability_ok:
        verdict = "FIX_SOFTWARE_FIRST"
    elif memory_pressure and concurrency_pressure:
        verdict = "BENCHMARK_SCALE_UP_AND_SCALE_OUT"
        reasons.append("Test a larger/NUMA-aware host and a multi-node topology with the same replay corpus; choose by measured p95/throughput/cost.")
    elif memory_pressure:
        verdict = "BENCHMARK_SCALE_UP"
        reasons.append("Capacity pressure is measured. Benchmark a larger-memory host; dual-socket is justified only if NUMA-aware replay beats a single host on p95/throughput/cost.")
    elif concurrency_pressure:
        verdict = "BENCHMARK_SCALE_OUT"
        reasons.append("Concurrency/SLA pressure is measured. Benchmark additional workers/nodes before attempting to split one model across nodes.")
    elif latency_pressure:
        verdict = "OPTIMIZE_THEN_REPLAY"
        reasons.append("Latency misses SLA without measured capacity pressure; profile model/runtime before changing topology.")
    else:
        verdict = "SINGLE_HOST_SUFFICIENT_FOR_OBSERVED_LOAD"
        reasons.append("Observed workload provides no evidence that multi-socket or cluster hardware is required.")

    return {"verdict": verdict, "reasons": reasons}


def markdown(report: dict[str, Any]) -> str:
    s, h, d = report["summary"], report["hardware"], report["decision"]
    rate = "n/a" if s["verified_success_rate"] is None else f"{s['verified_success_rate'] * 100:.1f}%"
    p50 = "n/a" if s["p50_latency_s"] is None else f"{s['p50_latency_s']:.2f}s"
    p95 = "n/a" if s["p95_latency_s"] is None else f"{s['p95_latency_s']:.2f}s"
    tph = "n/a" if s["throughput_tasks_per_hour"] is None else f"{s['throughput_tasks_per_hour']:.2f}"
    lines = [
        "# Bossman Real-Workload Hardware Audit",
        "",
        "> Decision rule: benchmark real, verified outcomes first; buy topology only for a measured bottleneck.",
        "",
        "## Real workload result",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Tasks | {s['tasks']} |",
        f"| Verified success | {rate} |",
        f"| p50 latency | {p50} |",
        f"| p95 latency | {p95} |",
        f"| Throughput, tasks/hour | {tph} |",
        f"| Human interventions | {s['human_interventions']} |",
        f"| Retries | {s['retries']} |",
        f"| OOM events | {s['oom_events']} |",
        f"| Max concurrency | {s['max_observed_concurrency']} |",
        f"| Peak observed memory | {s['peak_observed_memory_gb']:.2f} GB |",
        "",
        "## Host snapshot",
        "",
        f"- Platform: `{h.get('platform', 'unknown')}`",
        f"- CPU: `{h.get('cpu_model') or 'unknown'}`",
        f"- Logical CPUs: `{h.get('logical_cpus')}`",
        f"- Sockets / NUMA nodes: `{h.get('sockets', 'unknown')}` / `{h.get('numa_nodes', 'unknown')}`",
        f"- RAM: `{h.get('ram_gb', 'unknown')} GB`",
        "",
        "## Scaling verdict",
        "",
        f"**{d['verdict']}**",
        "",
    ]
    lines.extend(f"- {reason}" for reason in d["reasons"])
    lines += [
        "",
        "## Interpretation",
        "",
        "Dual-socket is not a default upgrade. It must win a replay of the same workload after NUMA-aware placement is enabled. A cluster is not a default upgrade either: use it when aggregate concurrency/capacity/resilience requires more workers. Splitting one model across nodes is a separate decision and should be accepted only when interconnect overhead still meets the target SLA.",
        "",
        "Synthetic/model benchmarks may be attached as diagnostics, but they never substitute for verified end-to-end task success.",
        "",
    ]
    return "\n".join(lines)


def build_report(records: list[dict[str, Any]], sla_p95_s: float | None = None, hardware: dict[str, Any] | None = None) -> dict[str, Any]:
    errors = [err for i, record in enumerate(records) for err in validate_record(record, i)]
    if errors:
        raise ValueError("; ".join(errors))
    hw = hardware or detect_hardware()
    s = summarize(records)
    return {
        "schema_version": 1,
        "generated_at_unix": int(time.time()),
        "summary": s,
        "hardware": hw,
        "decision": recommend(s, hw, sla_p95_s),
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="JSON array or JSONL of real task runs")
    parser.add_argument("--json-out", type=Path, default=Path("real_workload_audit.json"))
    parser.add_argument("--md-out", type=Path, default=Path("real_workload_audit.md"))
    parser.add_argument("--sla-p95-s", type=float, default=None)
    args = parser.parse_args(argv)
    try:
        report = build_report(load_records(args.input), args.sla_p95_s)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        parser.error(str(exc))
    args.json_out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    args.md_out.write_text(markdown(report), encoding="utf-8")
    print(f"verdict={report['decision']['verdict']}")
    print(f"json={args.json_out}")
    print(f"markdown={args.md_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
