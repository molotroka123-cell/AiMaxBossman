"""PASS 1 acceptance: benchmark truthfulness (BENCH-MODE-*, BENCH-SHA-*, BENCH-PROVENANCE-001)."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from bossman.benchmark.engine import BenchmarkRunner, ShaMismatch, load_latest, run_isolated

CORE = Path(__file__).resolve().parents[1]
ROOT = CORE.parent
HEAD = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=CORE, text=True).strip()
FIRST_BENCHMARK_SHA = "8a13f1d35cd68a57f1525bcc6a1a1c1b6c6d191a"     # old engine: no evidence classes, no provenance
pytestmark = pytest.mark.timeout(600)


def _cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "bossman.benchmark", *args], cwd=CORE, text=True, capture_output=True, check=False)


def _manifest_variant(tmp_path: Path, mutate) -> Path:
    src = CORE / "bossman" / "benchmark" / "datasets" / "v1" / "manifest.json"
    data = json.loads(src.read_text(encoding="utf-8"))
    mutate(data)
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_bench_mode_001_mock_success_never_enters_real_capability_score(tmp_path):
    report, _, _ = BenchmarkRunner(output_root=tmp_path / "out").run("pr", sha=HEAD)
    scores = report["scores"]
    regression = [a for a in report["attempts"] if a["mode"] in ("MOCK", "SIMULATED")]
    real = [a for a in report["attempts"] if a["mode"] == "REAL_SANDBOX"]
    assert regression and all(a["evidence_class"] == "REGRESSION" for a in regression)
    assert scores["RegressionScore"]["n"] == len(regression) and scores["RegressionScore"]["value"] == 1.0
    assert scores["RealCapabilityScore"]["n"] == len(real) and scores["RealCapabilityScore"]["status"] == "MEASURED"
    assert scores["LiveCapabilityScore"] == {"evidence_class": "LIVE", "n": 0, "value": None, "ci95": {"low": 0.0, "high": 0.0, "n": 0}, "status": "INSUFFICIENT_EVIDENCE"}
    smoke, _, _ = BenchmarkRunner(output_root=tmp_path / "out2").run("smoke", sha=HEAD)
    assert smoke["scores"]["RealCapabilityScore"]["status"] == "INSUFFICIENT_EVIDENCE"      # mocks alone: no real score


def test_bench_mode_002_fixture_cannot_self_declare_real_sandbox(tmp_path):
    # (a) manifest points REAL_SANDBOX at the deterministic fixture runtime → INVALID_SPEC, not scored
    def bad_runtime(d):
        d["tiers"] = {"smoke": ["app.higgsfield_mock"], "pr": [], "nightly": [], "release": []}
        d["cases"]["app.higgsfield_mock"] = {"mode": "REAL_SANDBOX", "repetitions": 1, "unstable": False, "runtime": "bossman.benchmark.fixture_runtime"}
    report, _, _ = BenchmarkRunner(manifest_file=_manifest_variant(tmp_path / "a", bad_runtime), output_root=tmp_path / "a" / "out").run("smoke", sha=HEAD)
    assert report["cases"][0]["status"] == "INVALID_SPEC" and report["scores"]["RealCapabilityScore"]["n"] == 0
    # (b) child claims REAL_SANDBOX for a case declared MOCK → FAIL, and the row stays REGRESSION
    def lying_child(d):
        d["tiers"] = {"smoke": ["sandbox.durable_restart"], "pr": [], "nightly": [], "release": []}
        d["cases"]["sandbox.durable_restart"] = {"mode": "MOCK", "repetitions": 1, "unstable": False, "runtime": "bossman.benchmark.sandbox_runtime"}
    report, _, _ = BenchmarkRunner(manifest_file=_manifest_variant(tmp_path / "b", lying_child), output_root=tmp_path / "b" / "out").run("smoke", sha=HEAD)
    case = report["cases"][0]
    assert case["status"] == "FAIL" and "child reported mode 'REAL_SANDBOX'" in case["reason"]
    assert report["attempts"][0]["evidence_class"] == "REGRESSION" and report["scores"]["RealCapabilityScore"]["n"] == 0


def test_bench_sha_003_label_without_matching_checkout_is_refused(tmp_path):
    other = "deadbeef" * 5
    result = _cli("run", "--tier", "smoke", "--sha", other, "--output", str(tmp_path))
    assert result.returncode == 3 and "ShaMismatch" in result.stderr
    assert not (tmp_path / other).exists() and not (tmp_path / "history.jsonl").exists()
    with pytest.raises(ShaMismatch):
        BenchmarkRunner(output_root=tmp_path).run("smoke", sha=other)


def test_bench_provenance_001_every_report_is_bound_to_actual_head_environment_dataset(tmp_path):
    report, _, _ = BenchmarkRunner(output_root=tmp_path).run("smoke", sha=HEAD)
    p = report["provenance"]
    assert p["actual_git_head"] == HEAD == report["commit_sha"] and p["requested_sha"] == HEAD
    assert p["tree_sha"] == subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=CORE, text=True).strip()
    manifest = CORE / "bossman" / "benchmark" / "datasets" / "v1" / "manifest.json"
    assert p["dataset_hash"] == hashlib.sha256(manifest.read_bytes()).hexdigest() == report["dataset"]["sha256"]
    engine = CORE / "bossman" / "benchmark" / "engine.py"
    assert p["engine_path"] == str(engine.resolve()) and len(p["benchmark_engine_hash"]) == 64 and len(p["runtime_hash"]) == 64
    assert p["python"] == sys.version.split()[0] and p["platform"] and p["environment_digest"]
    assert all(a["evidence_class"] in ("REGRESSION", "REAL_SANDBOX", "LIVE") for a in report["attempts"])


def test_bench_sha_001_002_isolated_worktrees_execute_their_own_commit(tmp_path):
    old = run_isolated(FIRST_BENCHMARK_SHA, "smoke", tmp_path)
    new = run_isolated(HEAD, "smoke", tmp_path)
    assert old["worktree_head"] == FIRST_BENCHMARK_SHA and old["child_returncode"] == 0
    assert new["worktree_head"] == HEAD and new["child_returncode"] == 0
    # genuinely different benchmark code ran: hashes differ and the old engine knows no provenance/scores
    assert old["engine_hash_in_worktree"] != new["engine_hash_in_worktree"]
    assert old["child_provenance_supported"] is False and old["scores"] is None
    assert new["child_provenance_supported"] is True and new["child_provenance"]["actual_git_head"] == HEAD
    assert new["child_provenance"]["engine_path"] != str((CORE / "bossman" / "benchmark" / "engine.py").resolve())
    assert load_latest(tmp_path, FIRST_BENCHMARK_SHA)["commit_sha"] == FIRST_BENCHMARK_SHA
    assert load_latest(tmp_path, HEAD)["commit_sha"] == HEAD
    assert not any(p.exists() for p in [Path(x) for x in [new["child_provenance"]["engine_path"]]])   # worktree removed


def test_bench_sha_003_isolated_refuses_unknown_commit(tmp_path):
    with pytest.raises(ShaMismatch):
        run_isolated("0123456789abcdef0123456789abcdef01234567", "smoke", tmp_path)
