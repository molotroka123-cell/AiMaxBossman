"""Real files/process restarts against the canonical promotion evidence ledger.

No live service, skill activation, real retention evidence or desktop access.
The tests exercise the real ledger and authorize(), not an application caller.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from dataclasses import asdict

import pytest
from bossman.learning_guard import evidence_ledger as ledger_module
from bossman_shared import objective_promotion as promotion


@pytest.fixture(autouse=True)
def tier(record_property):
    record_property("evidence_tier", "REAL_COMPONENT")
    record_property("scope", "canonical_ledger_real_files_and_subprocesses_not_live_activation")


def _measurement_file(tmp_path):
    tasks = [promotion.Task(f"case-{n}", "fixture.arithmetic") for n in range(30)]
    def verifier(variant, task):
        n = int(task.task_id.split("-")[1])
        output = n + (1 if variant == promotion.CANDIDATE else 0)
        passed = output == n + 1  # independent expected result
        return promotion.Outcome(task.task_id, passed, float(passed))
    m = promotion.measure(tasks, candidate_id="fixture-skill", candidate_version="v2",
                          baseline_version="v1", applicability_version="scope1", run=verifier)
    path = tmp_path / "measurement.json"
    path.write_text(json.dumps(asdict(m)), encoding="utf-8")
    return path


CHILD = r'''
import json, sys
from dataclasses import replace
from pathlib import Path
from bossman.learning_guard import evidence_ledger as ledger
from bossman_shared import objective_promotion as p
from bossman_shared.objective_improvement import CandidateImprovement, REQUIRED_STAGES
raw = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
s = raw.pop("split")
raw["split"] = p.Split(tuple(s["measured"]), tuple(s["holdout"]), s["material"])
raw["applicability"] = tuple(raw["applicability"])
for lane in ("measured", "holdout"):
    raw[lane] = {key: p.LaneResult(**value) for key, value in raw[lane].items()}
m = p.MeasuredPromotion(**raw)
# Replay the identical measurements, relabeling only the consuming candidate version.
m = replace(m, candidate_version=sys.argv[3])
candidate = CandidateImprovement("skill", "v1", sys.argv[3], "fixture", REQUIRED_STAGES)
v = p.authorize(m, candidate, ledger=ledger.DurableEvidenceLedger(sys.argv[2]),
                applicability_version="scope1", applicability_scope=("fixture.arithmetic",),
                retention=1.0, retention_evidence_ref="intelligence_preservation/paired/" + "a"*64,
                security_pass=True, rollback_available=True)
print(json.dumps({"authorized":v.authorized,"reason":v.reason,"key":m.evidence_key,
                  "pid":__import__("os").getpid(),
                  "origins":{"ledger":ledger.__file__,"promotion":p.__file__}}))
'''


def _run(measurement, path, version):
    result = subprocess.run([sys.executable, "-c", CHILD, str(measurement), str(path), version],
                            text=True, capture_output=True, timeout=15, check=False)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_replayed_measurement_for_another_version_is_refused_after_real_restart(tmp_path):
    measurement = _measurement_file(tmp_path)
    path = tmp_path / "spent.json"
    first = _run(measurement, path, "v2")
    second = _run(measurement, path, "v3")
    assert first["pid"] != second["pid"]
    assert first["authorized"] and not second["authorized"], (first, second)
    assert second["reason"] == "evidence_already_spent"
    assert first["key"] == second["key"]
    assert first["origins"] == second["origins"]
    (tmp_path / "restart-proof.json").write_text(json.dumps([first, second], indent=2))


def test_same_consumer_retry_is_idempotent_after_real_restart(tmp_path):
    measurement = _measurement_file(tmp_path)
    path = tmp_path / "spent.json"
    first = _run(measurement, path, "v2")
    second = _run(measurement, path, "v2")
    assert first["pid"] != second["pid"]
    assert first["authorized"] and second["authorized"]
    assert first["key"] == second["key"]


def test_four_processes_cannot_spend_same_evidence_on_four_consumers(tmp_path):
    measurement = _measurement_file(tmp_path)
    path = tmp_path / "spent.json"
    processes = [subprocess.Popen([sys.executable, "-c", CHILD, str(measurement), str(path), f"v{n}"],
                                  text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                 for n in range(2, 6)]
    results = []
    try:
        for proc in processes:
            stdout, stderr = proc.communicate(timeout=15)
            assert proc.returncode == 0, stderr
            results.append(json.loads(stdout))
    finally:
        for proc in processes:
            if proc.poll() is None:
                proc.kill()  # only these explicitly created child processes
                proc.wait(timeout=5)
    assert len({r["pid"] for r in results}) == 4
    assert len({r["key"] for r in results}) == 1
    assert sum(r["authorized"] for r in results) == 1, results
    (tmp_path / "concurrent-proof.json").write_text(json.dumps(results, indent=2))


def test_missing_initialized_ledger_refuses_new_spend(tmp_path):
    path = tmp_path / "spent.json"
    assert ledger_module.DurableEvidenceLedger(path).consume("evidence1", "v2") is None
    path.unlink()
    result = ledger_module.DurableEvidenceLedger(path).consume("evidence1", "v3")
    assert result is not None and "unavailable" in result


def test_corrupt_ledger_is_not_reinitialized_as_empty(tmp_path):
    path = tmp_path / "spent.json"
    assert ledger_module.DurableEvidenceLedger(path).consume("evidence1", "v2") is None
    path.write_text("{broken", encoding="utf-8")
    assert ledger_module.DurableEvidenceLedger(path).consume("evidence1", "v3") is not None
    assert path.read_text(encoding="utf-8") == "{broken"


def test_write_failure_never_returns_authorization(tmp_path, monkeypatch):
    def fail_replace(*args, **kwargs):
        raise OSError("injected atomic-replace failure")
    with monkeypatch.context() as patch:
        patch.setattr(ledger_module.os, "replace", fail_replace)
        verdict = ledger_module.DurableEvidenceLedger(tmp_path / "spent.json").consume("key", "v2")
    assert verdict is not None
    assert ledger_module.DurableEvidenceLedger(tmp_path / "spent.json").consume("key", "v3") is not None


def test_capacity_does_not_evict_spent_evidence_on_restart(tmp_path):
    path = tmp_path / "spent.json"
    ledger = ledger_module.DurableEvidenceLedger(path, capacity=1)
    assert ledger.consume("e1", "v2") is None
    assert ledger.consume("e2", "v3") is not None
    reopened = ledger_module.DurableEvidenceLedger(path, capacity=1)
    assert reopened.consume("e1", "v2") is None
    assert reopened.consume("e1", "v3") is not None
