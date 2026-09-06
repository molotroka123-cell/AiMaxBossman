"""Exact-SHA release certification is falsifiable: only completed success runs on
the same commit certify; skipped/cancelled/in-progress/other-SHA never do."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import exact_sha_certify as esc  # noqa: E402

SHA = "a" * 40
OTHER = "b" * 40
REQ = ("root-ci", "Core CI", "CC CI")


def _run(name, *, sha=SHA, status="completed", conclusion="success", number=1, attempt=1, rid=None):
    return {"name": name, "head_sha": sha, "status": status, "conclusion": conclusion,
            "run_number": number, "run_attempt": attempt, "id": rid or number * 10 + attempt,
            "html_url": f"https://example.invalid/{name}/{number}"}


def test_all_required_green_on_exact_sha_is_certified():
    report = esc.certify(SHA, [_run(n) for n in REQ], REQ)
    assert report["verdict"] == esc.CERTIFIED and report["final"] is True
    assert all(w["result"] == "PASS" for w in report["workflows"].values())


@pytest.mark.parametrize("conclusion", ["skipped", "cancelled", "neutral", "timed_out", "failure",
                                        "action_required", "stale", "startup_failure", ""])
def test_non_success_conclusion_is_not_pass(conclusion):
    runs = [_run("root-ci"), _run("Core CI"), _run("CC CI", conclusion=conclusion)]
    report = esc.certify(SHA, runs, REQ)
    assert report["verdict"] == esc.NOT_CERTIFIED
    assert report["workflows"]["CC CI"]["result"] == "NOT_PASS"
    assert conclusion in report["workflows"]["CC CI"]["reason"] or "none" in report["workflows"]["CC CI"]["reason"]


def test_green_run_on_another_sha_never_transfers():
    runs = [_run("root-ci"), _run("Core CI"), _run("CC CI", sha=OTHER)]
    report = esc.certify(SHA, runs, REQ)
    assert report["verdict"] == esc.NOT_CERTIFIED
    assert report["workflows"]["CC CI"]["result"] == "MISSING"
    assert report["ignored_other_sha"] == 1


def test_in_progress_run_is_not_final_and_not_pass():
    runs = [_run("root-ci"), _run("Core CI"), _run("CC CI", status="in_progress", conclusion="")]
    report = esc.certify(SHA, runs, REQ)
    assert report["verdict"] == esc.NOT_CERTIFIED and report["final"] is False
    assert report["workflows"]["CC CI"]["result"] == "NOT_FINAL"


def test_rerun_supersedes_earlier_attempt_on_same_sha_only():
    runs = [_run("root-ci"), _run("Core CI"),
            _run("CC CI", conclusion="cancelled", number=5, attempt=1),
            _run("CC CI", conclusion="success", number=5, attempt=2)]
    assert esc.certify(SHA, runs, REQ)["verdict"] == esc.CERTIFIED
    # a later, green attempt on ANOTHER commit does not rescue this one
    runs[-1] = _run("CC CI", conclusion="success", number=6, attempt=1, sha=OTHER)
    report = esc.certify(SHA, runs, REQ)
    assert report["verdict"] == esc.NOT_CERTIFIED
    assert report["workflows"]["CC CI"]["conclusion"] == "cancelled"


def test_missing_required_workflow_is_never_assumed():
    report = esc.certify(SHA, [_run("root-ci"), _run("Core CI")], REQ)
    assert report["verdict"] == esc.NOT_CERTIFIED
    assert report["workflows"]["CC CI"]["result"] == "MISSING"


def test_no_runs_is_insufficient_evidence_not_pass():
    report = esc.certify(SHA, [], REQ)
    assert report["verdict"] == esc.INSUFFICIENT and report["final"] is False


@pytest.mark.parametrize("sha", ["abc123", "", "a" * 39, "g" * 40])
def test_abbreviated_or_invalid_sha_cannot_be_certified(sha):
    with pytest.raises(esc.CertificationError):
        esc.certify(sha, [_run(n, sha=sha) for n in REQ], REQ)


def test_sha_case_is_normalized_not_a_different_commit():
    report = esc.certify("A" * 40, [_run(n) for n in REQ], REQ)
    assert report["sha"] == SHA and report["verdict"] == esc.CERTIFIED


def test_empty_requirement_set_certifies_nothing():
    with pytest.raises(esc.CertificationError):
        esc.certify(SHA, [_run("root-ci")], ())


def test_default_required_names_match_workflow_files():
    root = Path(__file__).resolve().parents[1]
    names = set()
    for wf in (root / ".github" / "workflows").glob("*.yml"):
        for line in wf.read_text(encoding="utf-8").splitlines():
            if line.startswith("name:"):
                names.add(line[len("name:"):].strip().strip("'\""))
                break
    missing = set(esc.DEFAULT_REQUIRED) - names
    assert not missing, f"required workflow names not found in .github/workflows: {missing}"


def test_scorecard_pass_claim_requires_certification_of_that_sha():
    good = esc.certify(SHA, [_run(n) for n in REQ], REQ)
    assert esc.check_scorecard(good, {"exact_sha_ci": "PASS", "last_evidence_sha": SHA}) == ""
    assert esc.check_scorecard(good, {"exact_sha_ci": "UNPROVEN", "last_evidence_sha": OTHER}) == ""
    stale = esc.check_scorecard(good, {"exact_sha_ci": "PASS", "last_evidence_sha": OTHER})
    assert "OLD_SHA_PASS" in stale
    bad = esc.certify(SHA, [_run("root-ci"), _run("Core CI"), _run("CC CI", conclusion="cancelled")], REQ)
    assert "NOT_CERTIFIED" in esc.check_scorecard(bad, {"exact_sha_ci": "PASS", "last_evidence_sha": SHA})


def test_cli_exit_codes_and_report(tmp_path):
    tool = Path(__file__).resolve().parents[1] / "tools" / "exact_sha_certify.py"
    runs = tmp_path / "runs.json"
    out = tmp_path / "report.json"
    runs.write_text(json.dumps({"workflow_runs": [_run(n) for n in REQ]}), encoding="utf-8")
    base = [sys.executable, str(tool), "--sha", SHA, "--runs-json", str(runs), "--required", *REQ]
    assert subprocess.run(base + ["--output", str(out)], capture_output=True, text=True).returncode == 0
    assert json.loads(out.read_text(encoding="utf-8"))["verdict"] == "CERTIFIED"
    runs.write_text(json.dumps({"workflow_runs": [_run(n, conclusion="skipped") for n in REQ]}), encoding="utf-8")
    res = subprocess.run(base, capture_output=True, text=True)
    assert res.returncode == 1 and "NOT_PASS" in res.stdout and "skipped" in res.stdout
    runs.write_text(json.dumps({"workflow_runs": []}), encoding="utf-8")
    assert subprocess.run(base, capture_output=True, text=True).returncode == 2
    runs.write_text("{not json", encoding="utf-8")
    assert subprocess.run(base, capture_output=True, text=True).returncode == 2
    # a scorecard PASS claim for an uncertified SHA is a contradiction (exit 1)
    runs.write_text(json.dumps({"workflow_runs": [_run(n, conclusion="cancelled") for n in REQ]}), encoding="utf-8")
    card = tmp_path / "card.json"
    card.write_text(json.dumps({"exact_sha_ci": "PASS", "last_evidence_sha": SHA}), encoding="utf-8")
    res = subprocess.run(base + ["--scorecard", str(card)], capture_output=True, text=True)
    assert res.returncode == 1 and "SCORECARD_CONTRADICTION" in res.stdout
