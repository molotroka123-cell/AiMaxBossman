from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "real_workload_audit.py"
spec = spec_from_file_location("real_workload_audit", SCRIPT)
assert spec and spec.loader
mod = module_from_spec(spec)
spec.loader.exec_module(mod)


def _records(n=10, *, duration=10.0, verified=True, oom=False, concurrency=1, memory=20.0):
    return [
        {
            "task_id": f"task-{i}",
            "status": "passed",
            "verified": verified,
            "duration_s": duration + i,
            "human_interventions": 0,
            "retries": 0,
            "oom": oom,
            "concurrency": concurrency,
            "peak_memory_gb": memory,
        }
        for i in range(n)
    ]


def test_small_sample_refuses_hardware_recommendation():
    report = mod.build_report(_records(3), hardware={"ram_gb": 128})
    assert report["decision"]["verdict"] == "INSUFFICIENT_EVIDENCE"


def test_healthy_real_workload_keeps_single_host():
    report = mod.build_report(_records(), sla_p95_s=30, hardware={"ram_gb": 128})
    assert report["summary"]["verified_success_rate"] == 1.0
    assert report["decision"]["verdict"] == "SINGLE_HOST_SUFFICIENT_FOR_OBSERVED_LOAD"


def test_reliability_failure_beats_hardware_upgrade():
    records = _records()
    records[0]["status"] = "failed"
    records[0]["verified"] = False
    records[1]["verified"] = False
    report = mod.build_report(records, hardware={"ram_gb": 128})
    assert report["decision"]["verdict"] == "FIX_SOFTWARE_FIRST"


def test_memory_and_concurrency_pressure_demands_comparative_benchmark():
    report = mod.build_report(
        _records(10, duration=40, oom=True, concurrency=5, memory=120),
        sla_p95_s=20,
        hardware={"ram_gb": 128},
    )
    assert report["decision"]["verdict"] == "BENCHMARK_SCALE_UP_AND_SCALE_OUT"


def test_bad_record_is_rejected():
    try:
        mod.build_report([{"task_id": "x", "status": "passed", "verified": True}], hardware={})
    except ValueError as exc:
        assert "duration_s" in str(exc)
    else:
        raise AssertionError("invalid record was accepted")
