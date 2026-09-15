"""Reproduce CAS latency with matched, balanced wall/thread-CPU measurements.

Only temporary local SQLite fixtures are touched. This does not certify a UI,
model, owner hardware or release. The former retained-connection comparison is
documented separately; it has not been relabelled as a passing measurement.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bossman_shared.objective_spec import ObjectiveSpec
from bossman_shared.objective_store import CompareAndSwapError, ObjectiveStore
from tools.human_speed_gate import FreshConnectionFloor, cas_latency_contract, PASS, FAIL


def measure_cas(store, state, directory: Path, *, n: int = 100):
    """Every measured operation includes commit and immediate connection close."""
    if type(n) is not int or n < 100:
        raise ValueError("at least 100 CAS samples required")
    with store._connect() as con:
        if (con.execute("PRAGMA journal_mode").fetchone()[0] != "wal"
                or con.execute("PRAGMA synchronous").fetchone()[0] != 2):
            raise RuntimeError("CAS measurement requires real WAL/FULL durability")
    floor = FreshConnectionFloor(directory)
    samples, cpu_samples, orders = [], [], []
    try:
        for i in range(n):
            # Alternate which side the control occupies; no warmup or trimmed
            # samples. The paired clocks are read inside the same operation.
            if i % 2 == 0:
                floor.tick()
            previous = state.version
            start, cpu_start = time.perf_counter_ns(), time.thread_time_ns()
            state = store.record_observation(state.objective_id, observed_at=float(i),
                                             count=1, expected_version=previous)
            cpu_elapsed = (time.thread_time_ns() - cpu_start) / 1e6
            elapsed = (time.perf_counter_ns() - start) / 1e6
            samples.append(elapsed)
            cpu_samples.append(cpu_elapsed)
            if i % 2:
                floor.tick()
            orders.append("floor-CAS" if i % 2 == 0 else "CAS-floor")
            assert state.version == previous + 1
    finally:
        floor.close()
    data = {"samples_ms": samples, "cpu_samples_ms": cpu_samples,
            "floor_samples_ms": floor.samples, "floor_cpu_samples_ms": floor.cpu_samples,
            "orders": orders}
    data["result"] = cas_latency_contract(samples, cpu_samples_ms=cpu_samples,
        floor_samples_ms=floor.samples, floor_cpu_samples_ms=floor.cpu_samples)
    return state, data


def fixture_spec() -> ObjectiveSpec:
    return ObjectiveSpec.from_dict({
        "schema_version": 1, "owner_id": "speed-owner", "scope_id": "local-fixture",
        "objective_id": "speed-check", "revision": 1, "previous_digest": None,
        "sources": [{"source_ref": "file", "source_revision": "v1", "max_age_seconds": 30}],
        "predicates": [{"predicate_id": "exists", "source_ref": "file", "field": "exists",
                        "value_type": "boolean", "operator": "eq", "expected": True}],
        "expires_at": 10000, "priority": 1, "allowed_triggers": ["source_change"],
        "permission_refs": ["local-read"], "conflict_keys": ["speed-fixture"],
        "cooldown_seconds": 0,
        "limits": {"max_observations": 1000, "max_missions": 1,
                   "max_wall_seconds": 60, "max_cost_usd": 0},
        "stop_conditions": ["owner-stop"]})


class CpuBurdenStore(ObjectiveStore):
    def record_observation(self, *args, **kwargs):
        deadline = time.thread_time_ns() + 4_000_000
        while time.thread_time_ns() < deadline:
            pass
        return super().record_observation(*args, **kwargs)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expect-sha", required=True)
    parser.add_argument("--json-out", required=True, type=Path)
    args = parser.parse_args()
    sha = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
    dirty = subprocess.check_output(["git", "-C", str(ROOT), "status", "--porcelain"], text=True)
    if not re.fullmatch(r"[a-f0-9]{40}", args.expect_sha) or sha != args.expect_sha or dirty:
        parser.error("an exact matching SHA and clean source tree are required")
    import sqlite3
    from datetime import datetime, timezone
    corpus = fixture_spec()
    report = {"code_sha": sha, "dirty": False, "python": sys.version,
              "sqlite": sqlite3.sqlite_version, "timestamp": datetime.now(timezone.utc).isoformat(),
              "corpus": corpus.to_dict(), "scope": "LOCAL_COMPONENT", "arms": {}}
    # Refuse an existing output before spending work; never overwrite evidence.
    with args.json_out.open("x", encoding="utf-8") as out:
        with tempfile.TemporaryDirectory(prefix="bossman-cas-") as td:
            for name, cls in (("baseline", ObjectiveStore), ("cpu_regression_4ms", CpuBurdenStore)):
                directory = Path(td) / name
                directory.mkdir()
                store = cls(directory / "cas.db")
                state, data = measure_cas(store, store.create(corpus), directory)
                assert store.get(state.objective_id) == state and state.observations_used == 100
                assert state.lifecycle == "DRAFT" and state.condition == "UNKNOWN"
                try:
                    store.record_observation(state.objective_id, observed_at=101, count=1,
                                             expected_version=state.version - 1)
                except CompareAndSwapError:
                    pass
                else:
                    raise AssertionError("stale CAS was accepted")
                assert ObjectiveStore(store.path).get(state.objective_id) == state
                data["final_state"] = asdict(state)
                report["arms"][name] = data
        report["status"] = PASS if (
            report["arms"]["baseline"]["result"]["status"] == PASS
            and report["arms"]["cpu_regression_4ms"]["result"]["status"] == FAIL) else FAIL
        json.dump(report, out, indent=2, allow_nan=False)
    print(json.dumps({"code_sha": sha, "status": report["status"],
                      "results": {k: v["result"] for k, v in report["arms"].items()}}, allow_nan=False))
    return 0 if report["status"] == PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
