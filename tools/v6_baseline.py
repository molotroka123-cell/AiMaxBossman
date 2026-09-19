"""Epoch 6 exact-SHA performance baseline harness.

Phase 0 is observational only. This module deliberately refuses to invent
resource numbers when a sampler is unavailable and binds every report to an
exact source/tree identity before it can be used as performance evidence.

Examples:
    python tools/v6_baseline.py --duration 300 --interval 1 --json-out baseline.json
    python tools/v6_baseline.py --pid 1234 --source-sha "$GITHUB_SHA" --tree-sha <tree>

GPU/model/runtime metrics are *not* inferred from process RSS. Until a real
backend supplies them they are emitted as NOT_RUN/UNAVAILABLE so unified-memory
machines are not double-counted as independent RAM + VRAM.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import platform
import statistics
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

ROOT = Path(__file__).resolve().parents[1]
HEX = set("0123456789abcdef")


class BaselineError(RuntimeError):
    """The requested baseline cannot be represented truthfully."""


def _exact_sha(value: str | None, *, field: str) -> str:
    value = (value or "").strip().lower()
    if len(value) != 40 or any(ch not in HEX for ch in value):
        raise BaselineError(f"{field} must be an exact 40-character git SHA")
    return value


def _git_rev_parse(expr: str, cwd: Path) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", expr],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def resolve_source_identity(
    *,
    repo_root: Path = ROOT,
    source_sha: str | None = None,
    tree_sha: str | None = None,
    expected_sha: str | None = None,
) -> dict[str, Any]:
    """Resolve and validate immutable source identity for a baseline artifact."""
    source_from = "argument"
    raw_source = source_sha
    if not raw_source:
        raw_source = os.environ.get("GITHUB_SHA")
        source_from = "GITHUB_SHA" if raw_source else "git"
    if not raw_source:
        raw_source = _git_rev_parse("HEAD", repo_root)

    resolved_source = _exact_sha(raw_source, field="source_sha")
    expected = _exact_sha(expected_sha, field="expected_sha") if expected_sha else None
    if expected and resolved_source != expected:
        raise BaselineError(
            f"source SHA mismatch: expected {expected}, observed {resolved_source}"
        )

    tree_from = "argument"
    raw_tree = tree_sha
    if not raw_tree:
        raw_tree = _git_rev_parse("HEAD^{tree}", repo_root)
        tree_from = "git"
    if raw_tree:
        resolved_tree: str | None = _exact_sha(raw_tree, field="tree_sha")
    else:
        resolved_tree = None
        tree_from = "unavailable"

    return {
        "source_sha": resolved_source,
        "tree_sha": resolved_tree,
        "source_sha_provenance": source_from,
        "tree_sha_provenance": tree_from,
        "expected_sha": expected,
        "exact_sha_match": expected is None or expected == resolved_source,
    }


def nearest_rank(values: Iterable[float], percentile: int) -> float:
    vals = sorted(float(v) for v in values)
    if not vals:
        raise BaselineError("cannot compute percentile of an empty sample")
    if percentile < 1 or percentile > 100:
        raise ValueError("percentile must be in [1, 100]")
    return vals[math.ceil(len(vals) * percentile / 100) - 1]


def distribution(values: Iterable[float]) -> dict[str, Any]:
    vals = [float(v) for v in values]
    if not vals:
        return {"status": "UNAVAILABLE", "samples": [], "sample_count": 0}
    return {
        "status": "MEASURED",
        "sample_count": len(vals),
        "median": round(statistics.median(vals), 4),
        "p95": round(nearest_rank(vals, 95), 4),
        "worst": round(max(vals), 4),
        "samples": [round(v, 4) for v in vals],
        "outliers_removed": 0,
    }


@dataclass(frozen=True)
class ProcessSnapshot:
    monotonic_s: float
    rss_bytes: int
    process_count: int
    cpu_seconds: float


def _psutil_module():
    try:
        import psutil  # type: ignore
    except ImportError:
        return None
    return psutil


def _snapshot_psutil(pid: int, psutil: Any, clock: Callable[[], float]) -> ProcessSnapshot:
    try:
        root = psutil.Process(pid)
        procs = [root, *root.children(recursive=True)]
    except psutil.NoSuchProcess as exc:
        raise BaselineError(f"pid {pid} does not exist") from exc

    rss = 0
    cpu_s = 0.0
    seen = 0
    for proc in procs:
        try:
            mem = proc.memory_info()
            cpu = proc.cpu_times()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
        rss += int(mem.rss)
        cpu_s += float(cpu.user) + float(cpu.system)
        seen += 1
    if seen == 0:
        raise BaselineError(f"no readable processes in tree rooted at pid {pid}")
    return ProcessSnapshot(clock(), rss, seen, cpu_s)


def summarize_process_snapshots(samples: list[ProcessSnapshot]) -> dict[str, Any]:
    if not samples:
        return {"status": "UNAVAILABLE", "reason": "no_process_samples"}

    rss_mb = [s.rss_bytes / (1024 * 1024) for s in samples]
    proc_counts = [float(s.process_count) for s in samples]
    cpu_pct: list[float] = []
    for before, after in zip(samples, samples[1:]):
        wall = after.monotonic_s - before.monotonic_s
        delta_cpu = after.cpu_seconds - before.cpu_seconds
        if wall > 0 and delta_cpu >= 0:
            # One-core scale: multi-threaded work can exceed 100%; never clamp.
            cpu_pct.append((delta_cpu / wall) * 100.0)

    return {
        "status": "MEASURED",
        "process_samples": len(samples),
        "rss_mb": distribution(rss_mb),
        "process_count": distribution(proc_counts),
        "cpu_pct_one_core_scale": distribution(cpu_pct),
        "rss_hwm_mb": round(max(rss_mb), 4),
        "raw": [
            {
                "t_monotonic_s": round(s.monotonic_s, 6),
                "rss_bytes": s.rss_bytes,
                "process_count": s.process_count,
                "cpu_seconds": round(s.cpu_seconds, 6),
            }
            for s in samples
        ],
        "outliers_removed": 0,
    }


def sample_process_tree(
    *,
    pid: int,
    duration_s: float,
    interval_s: float,
    psutil: Any | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Measure a process tree without making psutil a hard runtime dependency."""
    if pid < 1:
        raise ValueError("pid must be positive")
    if duration_s <= 0:
        raise ValueError("duration_s must be > 0")
    if interval_s <= 0:
        raise ValueError("interval_s must be > 0")

    psutil = _psutil_module() if psutil is None else psutil
    if psutil is None:
        return {
            "status": "UNAVAILABLE",
            "reason": "psutil_not_installed",
            "install_hint": "pip install -e 'bossman-core[resource]'",
            "process_samples": 0,
        }

    started = clock()
    samples = [_snapshot_psutil(pid, psutil, clock)]
    while True:
        elapsed = clock() - started
        if elapsed >= duration_s:
            break
        sleeper(min(interval_s, max(0.0, duration_s - elapsed)))
        samples.append(_snapshot_psutil(pid, psutil, clock))

    if len(samples) == 1:
        sleeper(interval_s)
        samples.append(_snapshot_psutil(pid, psutil, clock))
    return summarize_process_snapshots(samples)


def build_baseline_report(
    *,
    pid: int,
    duration_s: float,
    interval_s: float,
    source_sha: str | None = None,
    tree_sha: str | None = None,
    expected_sha: str | None = None,
    repo_root: Path = ROOT,
    process_sampler: Callable[..., dict[str, Any]] = sample_process_tree,
) -> dict[str, Any]:
    identity = resolve_source_identity(
        repo_root=repo_root,
        source_sha=source_sha,
        tree_sha=tree_sha,
        expected_sha=expected_sha,
    )
    resources = process_sampler(pid=pid, duration_s=duration_s, interval_s=interval_s)
    return {
        "schema": "bossman.performance_baseline.v6.phase0.1",
        "evidence_class": "MEASURED_OR_EXPLICITLY_UNAVAILABLE",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "phase": "V6_PHASE_0_MEASUREMENT_ONLY",
        "behavior_changed": False,
        "source": identity,
        "host": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "logical_cpu_count": os.cpu_count(),
        },
        "measurement": {
            "pid": pid,
            "duration_s": duration_s,
            "interval_s": interval_s,
            "process_tree": resources,
            "gpu": {
                "status": "NOT_RUN",
                "reason": "no_gpu_backend_configured; do not infer GPU/unified memory from RSS",
            },
            "model_runtime": {
                "status": "NOT_RUN",
                "reason": "runtime-specific residency/reload instrumentation not attached",
            },
        },
        "claims": {
            "human_comparison": "NOT_RUN",
            "speedup_vs_baseline": "NOT_APPLICABLE_BASELINE_CAPTURE",
            "ram_vram_double_counted": False,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, default=os.getpid())
    parser.add_argument("--duration", type=float, default=10.0)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--source-sha")
    parser.add_argument("--tree-sha")
    parser.add_argument("--expect-sha")
    parser.add_argument("--json-out", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        report = build_baseline_report(
            pid=args.pid,
            duration_s=args.duration,
            interval_s=args.interval,
            source_sha=args.source_sha,
            tree_sha=args.tree_sha,
            expected_sha=args.expect_sha,
        )
    except (BaselineError, ValueError) as exc:
        parser.error(str(exc))

    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
