"""P0 gate contract: READY cannot be produced by mocks; capability coverage
must come from measured REAL_SANDBOX/LIVE cases; strict tiers enforce coverage;
a NO-GO gate must fail the process (exit 1)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from bossman.benchmark.engine import BenchmarkRunner, REQUIRED_CAPABILITIES

CORE = Path(__file__).resolve().parents[1]
ROOT = CORE.parent
HEAD = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=CORE, text=True).strip()
pytestmark = pytest.mark.timeout(600)


def test_gate_smoke_stays_ready_and_release_is_honestly_no_go(tmp_path: Path):
    report, _, _ = BenchmarkRunner(output_root=tmp_path / "out").run("smoke", sha=HEAD)
    assert report["release_gate"]["ready"] is True and report["release_gate"]["status"] == "READY"
    assert report["scores"]["SystemIQ"]["status"] == "INSUFFICIENT_EVIDENCE"       # smoke has no REAL rows
    release, _, _ = BenchmarkRunner(output_root=tmp_path / "out2").run("release", sha=HEAD)
    gate = release["release_gate"]
    assert gate["ready"] is False and gate["status"] == "NO-GO"
    joined = "; ".join(gate["reasons"])
    assert "required capabilities" in joined and "model_selection" in joined
    assert "LIVE n=0" in joined
    assert release["scores"]["SystemIQ"]["status"] == "MEASURED"                   # REAL rows measured, mocks excluded


def test_gate_cli_exits_nonzero_on_no_go(tmp_path: Path):
    result = subprocess.run([sys.executable, "-m", "bossman.benchmark", "run", "--tier", "release", "--sha", HEAD,
                             "--output", str(tmp_path)], cwd=CORE, text=True, capture_output=True, timeout=300)
    assert result.returncode == 1, result.stdout[-300:]
    assert '"status": "NO-GO"' in result.stdout.replace("'", '"')


def test_capability_coverage_requires_real_evidence(tmp_path: Path):
    """A passed MOCK case tagged with a capability must NOT satisfy coverage."""
    runner = BenchmarkRunner(output_root=tmp_path / "out")
    report, _, _ = runner.run("release", sha=HEAD)
    higgs = [c for c in report["cases"] if c["case_id"] == "app.higgsfield_mock"]
    assert not higgs or higgs[0]["evidence_class"] == "REGRESSION"                 # runner-assigned, never child-claimed
    gate_caps = [r for r in report["release_gate"]["reasons"] if r.startswith("required capabilities")]
    assert gate_caps
    missing = gate_caps[0].split("coverage: ", 1)[1]
    assert "universal_computer_apprentice" in missing       # its MOCK case cannot satisfy coverage
    assert "persistence" not in missing                     # covered by the REAL_SANDBOX durable-restart case


def test_gate_no_go_never_claims_ready_without_baseline(tmp_path: Path):
    runner = BenchmarkRunner(output_root=tmp_path / "out")
    report, _, _ = runner.run("nightly", sha=HEAD)
    assert report["release_gate"]["status"] == "NO-GO"
    assert any("nightly tier manifest is empty" not in r for r in report["release_gate"]["reasons"])
    assert not any("P0 benchmark cases failed" in r for r in report["release_gate"]["reasons"])
