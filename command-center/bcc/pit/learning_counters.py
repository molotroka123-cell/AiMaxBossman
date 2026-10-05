"""Cheap before/after counters per build (Jeff 1.5). Real events only, never inferred.

One small JSON file (``pit-v1.7/learning-counters.json``) written with the vault's atomic
helper: per build SHA and per event kind it counts successes, failures, owner interventions,
summed latency and spend. ``compare`` reports a delta only when both builds have enough real
samples; otherwise it says ``insufficient`` instead of producing a number. Memory hits are not
counted as learning here: this module has no notion of "recalled".
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .vault import _atomic_json

FILE_NAME = "learning-counters.json"
SCHEMA = "jeff.learning-counters/1"
MIN_SAMPLES = 5


def _path(home: Path) -> Path:
    return Path(home) / FILE_NAME


def load(home: Path) -> dict[str, Any]:
    try:
        data = json.loads(_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"schema": SCHEMA, "builds": {}}
    return data if isinstance(data, dict) and isinstance(data.get("builds"), dict) \
        else {"schema": SCHEMA, "builds": {}}


def record(home: Path, build_sha: str, kind: str, *, ok: bool, latency_ms: int | None = None,
           intervention: bool = False, cost_usd: float = 0.0) -> None:
    data = load(home)
    row = data["builds"].setdefault(str(build_sha)[:40] or "unknown", {}).setdefault(
        str(kind)[:40], {"ok": 0, "fail": 0, "interventions": 0, "latency_ms_sum": 0,
                         "latency_n": 0, "cost_usd": 0.0})
    row["ok" if ok else "fail"] += 1
    row["interventions"] += 1 if intervention else 0
    if latency_ms is not None and latency_ms >= 0:
        row["latency_ms_sum"] += int(latency_ms)
        row["latency_n"] += 1
    row["cost_usd"] = round(float(row["cost_usd"]) + max(0.0, float(cost_usd)), 6)
    data["schema"] = SCHEMA
    try:
        _atomic_json(_path(home), data)
    except OSError:
        pass


def _rates(row: dict[str, Any]) -> dict[str, Any]:
    n = row["ok"] + row["fail"]
    return {"n": n, "success_rate": round(row["ok"] / n, 4) if n else None,
            "intervention_rate": round(row["interventions"] / n, 4) if n else None,
            "avg_latency_ms": round(row["latency_ms_sum"] / row["latency_n"])
            if row["latency_n"] else None, "cost_usd": row["cost_usd"]}


def compare(home: Path, before_build: str, after_build: str, kind: str) -> dict[str, Any]:
    builds = load(home)["builds"]
    b = builds.get(str(before_build)[:40], {}).get(kind)
    a = builds.get(str(after_build)[:40], {}).get(kind)
    if not b or not a:
        return {"status": "insufficient", "reason": "missing build data"}
    rb, ra = _rates(b), _rates(a)
    if min(rb["n"], ra["n"]) < MIN_SAMPLES:
        return {"status": "insufficient", "reason": f"fewer than {MIN_SAMPLES} samples",
                "before": rb, "after": ra}
    return {"status": "measured", "before": rb, "after": ra,
            "success_rate_delta": round(ra["success_rate"] - rb["success_rate"], 4)}
