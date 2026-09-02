"""P0 gate contract: READY cannot be produced by mocks; capability coverage
must come from measured REAL_SANDBOX/LIVE cases; strict tiers enforce coverage;
a NO-GO gate must fail the process (exit 1)."""
from __future__ import annotations

import hashlib
import hmac as hmac_mod
import json
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
    # Release still needs LIVE evidence, which no free tier can produce.
    assert gate["ready"] is False and gate["status"] == "NO-GO"
    assert "release requires LIVE evidence but LIVE n=0" in gate["reasons"]
    assert release["scores"]["SystemIQ"]["status"] == "MEASURED"                   # REAL rows measured, mocks excluded


def test_gate_cli_exits_nonzero_on_no_go(tmp_path: Path):
    result = subprocess.run([sys.executable, "-m", "bossman.benchmark", "run", "--tier", "release", "--sha", HEAD,
                             "--output", str(tmp_path)], cwd=CORE, text=True, capture_output=True, timeout=300)
    assert result.returncode == 1, result.stdout[-300:]
    assert '"status": "NO-GO"' in result.stdout.replace("'", '"')


def test_capability_coverage_requires_real_evidence(tmp_path: Path):
    """A passed MOCK case tagged with a capability must NOT satisfy coverage.

    The guarantee is tested against a manifest whose REAL_SANDBOX cases have been
    stripped, so it keeps holding once the shipped manifest covers everything —
    the previous version passed only because coverage happened to be incomplete.
    """
    full = json.loads((CORE / "bossman" / "benchmark" / "datasets" / "v1" / "manifest.json").read_text(encoding="utf-8"))
    stripped = json.loads(json.dumps(full))
    real = [cid for cid, spec in stripped["cases"].items() if spec["mode"] == "REAL_SANDBOX"]
    for cid in real:                                   # keep only the MOCK/SIMULATED evidence
        stripped["cases"].pop(cid)
    for name, ids in stripped["tiers"].items():
        stripped["tiers"][name] = [c for c in ids if c not in real] or ["app.higgsfield_mock"]
    report, _, _ = BenchmarkRunner(manifest_file=_write_manifest(tmp_path, stripped),
                                   output_root=tmp_path / "out").run("release", sha=HEAD)
    higgs = [c for c in report["cases"] if c["case_id"] == "app.higgsfield_mock"]
    assert higgs and higgs[0]["evidence_class"] == "REGRESSION"   # runner-assigned, never child-claimed
    assert higgs[0]["passed"] is True                             # the MOCK case itself passes ...
    gate_caps = [r for r in report["release_gate"]["reasons"] if r.startswith("required capabilities")]
    assert gate_caps, report["release_gate"]["reasons"]
    missing = gate_caps[0].split("coverage: ", 1)[1]
    # ... yet the capability it declares is still reported as uncovered.
    assert "universal_computer_apprentice" in missing
    assert "persistence" in missing                               # its REAL case was removed
    assert "no REAL_SANDBOX/LIVE evidence" in "; ".join(report["release_gate"]["reasons"])


def test_full_manifest_covers_every_required_capability_with_real_evidence(tmp_path: Path):
    """The shipped manifest reaches 18/18 measured coverage at a strict tier."""
    report, _, _ = BenchmarkRunner(output_root=tmp_path / "out").run("nightly", sha=HEAD)
    covered = {c["capability"] for c in report["cases"]
               if c.get("passed") and c.get("evidence_class") in ("REAL_SANDBOX", "LIVE") and c.get("capability")}
    assert set(REQUIRED_CAPABILITIES) <= covered, sorted(set(REQUIRED_CAPABILITIES) - covered)
    assert report["release_gate"]["status"] == "READY", report["release_gate"]["reasons"]
    assert report["scores"]["SystemIQ"]["status"] == "MEASURED"
    assert not report["scores"]["SystemIQ"]["missing_components"]


def test_fable_manifest_mac_pinning_opt_in(tmp_path: Path, monkeypatch):
    """Fable-originated: strict tiers refuse a forged manifest when MAC pinning is ON."""
    import hashlib
    import hmac as hmac_mod
    runner = BenchmarkRunner(output_root=tmp_path / "out")
    manifest = json.loads((CORE / "bossman" / "benchmark" / "datasets" / "v1" / "manifest.json").read_text(encoding="utf-8"))
    secret = "bench-mac-secret"
    payload = json.dumps({k: manifest[k] for k in sorted(manifest) if k != "mac"}, sort_keys=True)
    manifest["mac"] = hmac_mod.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    monkeypatch.setenv("BENCHMARK_MANIFEST_SECRET", secret)
    ok = BenchmarkRunner(manifest_file=_write_manifest(tmp_path, manifest), output_root=tmp_path / "ok").run("release", sha=HEAD)[0]
    assert all("MAC" not in r for r in ok["release_gate"]["reasons"])
    manifest["release_requires_live"] = False                    # forged change
    bad = BenchmarkRunner(manifest_file=_write_manifest(tmp_path, manifest), output_root=tmp_path / "bad").run("release", sha=HEAD)[0]
    assert "manifest MAC validation failed: forgery attempt detected" in bad["release_gate"]["reasons"]
    monkeypatch.delenv("BENCHMARK_MANIFEST_SECRET")
    off = BenchmarkRunner(output_root=tmp_path / "off").run("release", sha=HEAD)[0]
    assert not any("MAC" in r for r in off["release_gate"]["reasons"])   # OFF by default


def _write_manifest(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_strict_tier_refuses_ready_when_a_required_capability_regresses(tmp_path: Path):
    """A single failing REAL case must drop a strict tier back to NO-GO.

    Guards the discriminative power of the suite: with every case passing, READY
    is only meaningful if a break actually produces NO-GO.
    """
    full = json.loads((CORE / "bossman" / "benchmark" / "datasets" / "v1" / "manifest.json").read_text(encoding="utf-8"))
    broken = json.loads(json.dumps(full))
    # Point one REAL case at a capability nothing else covers, then delete the
    # case: coverage for that capability disappears and the gate must notice.
    broken["cases"].pop("sandbox.verifier")
    for name, ids in broken["tiers"].items():
        broken["tiers"][name] = [c for c in ids if c != "sandbox.verifier"]
    report, _, _ = BenchmarkRunner(manifest_file=_write_manifest(tmp_path, broken),
                                   output_root=tmp_path / "out").run("nightly", sha=HEAD)
    assert report["release_gate"]["status"] == "NO-GO"
    assert any("verifier" in r for r in report["release_gate"]["reasons"]), report["release_gate"]["reasons"]
    assert not any("P0 benchmark cases failed" in r for r in report["release_gate"]["reasons"])


def test_strict_tier_requires_duplicate_suppression_to_be_exercised(tmp_path: Path):
    """DuplicateEffectRate == 0 is trivially met by never trying; the gate demands proof."""
    from bossman.benchmark.engine import _gate, _metrics
    rows = [{"case_id": "x", "verified": True, "effects": 1, "duplicate_effects": 0}]
    cases = [{"case_id": "x", "passed": True, "p0": True, "capability": cap,
              "evidence_class": "REAL_SANDBOX"} for cap in REQUIRED_CAPABILITIES]
    manifest = {"tiers": {"nightly": ["x"], "release": ["x"]}}
    never = _gate(_metrics(rows), cases, tier="nightly", manifest=manifest,
                  evidence_classes={"REAL_SANDBOX": 1})
    assert "duplicate-suppression was never exercised: no attempt was refused by the idempotency guard" in never["reasons"]
    rows[0]["duplicate_effects_suppressed"] = 1
    proven = _gate(_metrics(rows), cases, tier="nightly", manifest=manifest,
                   evidence_classes={"REAL_SANDBOX": 1})
    assert proven["status"] == "READY", proven["reasons"]
    # An ACTUALLY executed duplicate still fails, suppression count notwithstanding.
    rows[0]["duplicate_effects"] = 1
    unsafe = _gate(_metrics(rows), cases, tier="nightly", manifest=manifest,
                   evidence_classes={"REAL_SANDBOX": 1})
    assert "DuplicateEffectRate > 0" in unsafe["reasons"]
