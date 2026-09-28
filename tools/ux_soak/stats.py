"""Pure helpers for the UX soak: percentiles, growth, invariant diffs."""
from __future__ import annotations

import math
from typing import Iterable, Mapping

ACTIVE = {"queued", "leased", "running"}
TERMINAL = {"done", "completed", "failed", "stopped", "cancelled", "blocked", "rejected"}


def percentile(values: Iterable[float], pct: float) -> float | None:
    """Nearest-rank percentile; ``None`` for an empty series."""
    data = sorted(float(v) for v in values)
    if not data:
        return None
    rank = max(1, math.ceil(pct / 100.0 * len(data)))
    return data[min(rank, len(data)) - 1]


def summarize(values: Iterable[float]) -> dict:
    data = [float(v) for v in values]
    return {
        "n": len(data),
        "p50": percentile(data, 50),
        "p95": percentile(data, 95),
        "max": max(data) if data else None,
    }


def growth(samples: list[tuple[float, float]], *, warmup: int = 3) -> dict:
    """Growth of a (t, value) series after ``warmup`` samples.

    Reports first/last/max and a least-squares slope per hour so a steady leak is
    distinguishable from one-off spikes (restarts reset the backend series).
    """
    pts = samples[warmup:] if len(samples) > warmup + 2 else samples
    if not pts:
        return {"n": 0}
    first, last = pts[0][1], pts[-1][1]
    n = len(pts)
    mt = sum(t for t, _ in pts) / n
    mv = sum(v for _, v in pts) / n
    den = sum((t - mt) ** 2 for t, _ in pts)
    slope = (sum((t - mt) * (v - mv) for t, v in pts) / den) if den else 0.0
    return {"n": n, "first": first, "last": last, "max": max(v for _, v in pts),
            "delta": last - first, "slope_per_hour": slope * 3600.0}


def diff_tasks(before: Mapping[int, str], after: Mapping[int, str]) -> list[str]:
    """Problems between two {task_id: status} snapshots taken across a restart.

    * a task must never disappear;
    * a task in a terminal state must keep it (history is immutable);
    Active tasks may legitimately progress, so they are checked separately.
    """
    problems = []
    for tid, st in before.items():
        if tid not in after:
            problems.append(f"task {tid} ({st}) missing after restart")
            continue
        if st in TERMINAL and after[tid] != st:
            problems.append(f"task {tid} terminal status changed {st} -> {after[tid]}")
    return problems


def stale_active(statuses: Mapping[int, str], watched: Iterable[int]) -> list[int]:
    """Watched tasks that are still in an active state."""
    return sorted(t for t in watched if statuses.get(t) in ACTIVE)
