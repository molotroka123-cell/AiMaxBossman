"""Pure helpers of the RC 1.9 UX soak harness (tools/ux_soak/stats.py)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "tools" / "ux_soak" / "stats.py"
_spec = importlib.util.spec_from_file_location("ux_soak_stats", _PATH)
stats = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(stats)


def test_percentiles_nearest_rank():
    data = list(range(1, 101))
    assert stats.percentile(data, 50) == 50
    assert stats.percentile(data, 95) == 95
    assert stats.percentile([], 50) is None
    s = stats.summarize([3, 1, 2])
    assert s == {"n": 3, "p50": 2, "p95": 3, "max": 3}


def test_growth_slope_distinguishes_leak_from_flat():
    flat = [(t * 60.0, 100.0) for t in range(20)]
    leak = [(t * 60.0, 100.0 + t) for t in range(20)]      # +1 MB per minute
    assert abs(stats.growth(flat)["slope_per_hour"]) < 1e-9
    assert round(stats.growth(leak)["slope_per_hour"]) == 60
    assert stats.growth([])["n"] == 0


def test_restart_diff_flags_lost_or_rewritten_history_only():
    before = {1: "completed", 2: "failed", 3: "running", 4: "queued"}
    after = {1: "completed", 2: "completed", 3: "completed"}
    problems = stats.diff_tasks(before, after)
    assert any("task 2" in p and "failed -> completed" in p for p in problems)
    assert any("task 4" in p and "missing" in p for p in problems)
    assert not any("task 3" in p for p in problems)          # active tasks may progress
    assert stats.stale_active({3: "running", 4: "completed"}, [3, 4]) == [3]
