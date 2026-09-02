"""The benchmark must exercise only public Python process boundaries."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from bossman.benchmark.engine import BASELINE_SHA, REQUIRED_METRICS, load_latest


CORE = Path(__file__).resolve().parents[1]
HEAD = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=CORE, text=True).strip()


def _cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "bossman.benchmark", *args], cwd=CORE, text=True, capture_output=True, check=False)


def test_smoke_is_runtime_subprocess_bound_and_writes_json_markdown_history(tmp_path: Path):
    result = _cli("run", "--tier", "smoke", "--sha", HEAD, "--output", str(tmp_path))
    assert result.returncode == 0, result.stderr
    response = json.loads(result.stdout)
    assert response["status"] == "READY"
    report = load_latest(tmp_path, HEAD)
    assert report["dataset"]["training_eligible"] is False
    assert report["execution_modes"]["LIVE"] == 0
    assert all(name in report["metrics"] for name in REQUIRED_METRICS)
    assert all(a["contract"] in ("bossman-runtime-fixture/v1", "bossman-sandbox-runtime/v1") for a in report["attempts"])
    assert (tmp_path / "history.jsonl").exists()
    assert Path(response["markdown"]).read_text(encoding="utf-8").startswith("# Bossman benchmark")


def test_pr_has_required_repeats_and_never_relabels_mock_as_live(tmp_path: Path):
    result = _cli("run", "--tier", "pr", "--sha", HEAD, "--output", str(tmp_path))
    assert result.returncode == 0, result.stderr
    report = load_latest(tmp_path, HEAD)
    repair = next(case for case in report["cases"] if case["case_id"] == "repair.teacher_boundary")
    assert repair["attempts"] >= 3
    assert {a["mode"] for a in report["attempts"]} <= {"MOCK", "SIMULATED", "REAL_SANDBOX"}
    assert report["execution_modes"]["LIVE"] == 0
    assert report["metrics"]["UnsafeActionRate"] == 0
    assert report["metrics"]["DuplicateEffectRate"] == 0


def test_compare_fails_ready_gate_on_verified_success_regression(tmp_path: Path):
    assert _cli("run", "--tier", "smoke", "--sha", HEAD, "--output", str(tmp_path)).returncode == 0
    base = load_latest(tmp_path, HEAD)
    # a stored report is data, not an attestation: mutate a copy under another directory
    candidate = json.loads(json.dumps(base)); candidate["metrics"]["VerifiedSuccessRate"] = 0.0
    (tmp_path / "candidate").mkdir(); (tmp_path / "candidate" / "smoke-x.json").write_text(json.dumps(candidate), encoding="utf-8")
    compared = _cli("compare", "--base", HEAD, "--candidate", "candidate", "--output", str(tmp_path))
    assert compared.returncode == 0, compared.stderr
    result = json.loads(compared.stdout)
    assert result["candidate_gate"]["status"] == "NO-GO"
    assert any("VerifiedSuccessRate" in why for why in result["candidate_gate"]["reasons"])


def test_live_flag_requires_two_independent_owner_attestations(tmp_path: Path, monkeypatch):
    # There are no LIVE fixtures in the deterministic CI dataset.  The runner
    # still records that --allow-live alone cannot authorize a future adapter.
    monkeypatch.delenv("BOSSMAN_BENCHMARK_OWNER_APPROVED", raising=False)
    monkeypatch.delenv("BOSSMAN_BENCHMARK_BUDGET_RESERVED", raising=False)
    result = _cli("run", "--tier", "smoke", "--allow-live", "--output", str(tmp_path))
    assert result.returncode == 0, result.stderr
    report = load_latest(tmp_path, json.loads(result.stdout)["commit_sha"])
    assert report["environment"]["live_authorized"] is False
