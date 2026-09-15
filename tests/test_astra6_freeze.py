"""A release label cannot turn missing, stale or partial acceptance green."""
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("astra6_freeze", Path(__file__).resolve().parents[1] / "tools/astra6_freeze.py")
freeze = importlib.util.module_from_spec(spec)
spec.loader.exec_module(freeze)
SHA = "a" * 40


@pytest.fixture
def evidence(tmp_path):
    reports = {
        "bundle-acceptance.json": {"status": "PASS", "expected_sha": SHA, "problems": [], "details": {"archive_sha256": "b" * 64}},
        "ui-sweep.json": {"status": "PASS", "source_sha": SHA},
        "live-model.json": {"status": "PASS", "source_sha": SHA, "identity": {"source_sha": SHA}, "models": ["a:free", "b:free"],
                            "tasks": [{"model": model, "status": "PASS", "restart_persistence": "PASS", "source_sha": SHA} for model in ["a:free", "b:free"] for _ in range(3)]},
    }
    for name, report in reports.items():
        (tmp_path / name).write_text(json.dumps(report))
    (tmp_path / "results.xml").write_text('<testsuites><testsuite>' + "".join(f'<testcase name="real-ui-{i}"/>' for i in range(13)) + '</testsuite></testsuites>')
    return tmp_path


def manifest(path, statuses=None):
    return freeze.build_manifest(path, SHA, ["success", "success"] if statuses is None else statuses)


def test_freeze_binds_reports_without_copying_secrets(evidence):
    path = evidence / "live-model.json"
    obj = json.loads(path.read_text()); obj["payload"] = "private-provider-secret"
    path.write_text(json.dumps(obj))
    report = manifest(evidence)
    assert report["status"] == "FROZEN"
    assert report["archive_sha256"] == "b" * 64
    assert len(report["evidence"]) == 4
    assert all(len(item["sha256"]) == 64 for item in report["evidence"].values())
    assert "private-provider-secret" not in json.dumps(report)
    assert report["target_hardware_acceptance"] == "OWNER_REQUIRED"
    assert not report["label_is_cryptographic_signature"]
    assert not report["model_weights_trained"]


@pytest.mark.parametrize("name", freeze.REPORTS)
@pytest.mark.parametrize("mutation", ["missing", "malformed", "duplicate"])
def test_bad_report_cannot_freeze(evidence, name, mutation):
    path = evidence / name
    if mutation == "missing":
        path.unlink()
    elif mutation == "malformed":
        path.write_text("{broken")
    else:
        nested = evidence / "duplicate"; nested.mkdir(); (nested / name).write_bytes(path.read_bytes())
    assert manifest(evidence)["status"] != "FROZEN"


@pytest.mark.parametrize("name", freeze.REPORTS[:3])
@pytest.mark.parametrize("field,value", [("status", "FAIL"), ("status", "SKIPPED"), ("status", "REVIEW_REQUIRED"), ("status", "OWNER_REQUIRED"), ("sha", "c" * 40)])
def test_stale_or_partial_report_cannot_freeze(evidence, name, field, value):
    path = evidence / name; obj = json.loads(path.read_text())
    if field == "sha":
        field = "expected_sha" if name == "bundle-acceptance.json" else "source_sha"
    obj[field] = value; path.write_text(json.dumps(obj))
    assert manifest(evidence)["status"] != "FROZEN"


@pytest.mark.parametrize("body", ["", "<skipped/>", "<failure/>", "<error/>"])
def test_empty_skipped_failed_junit_cannot_freeze(evidence, body):
    xml = '<testsuites/>' if body == "" else f'<testsuites><testsuite><testcase>{body}</testcase></testsuite></testsuites>'
    (evidence / "results.xml").write_text(xml)
    assert manifest(evidence)["status"] == "BLOCKED"


@pytest.mark.parametrize("statuses", [[], ["success"], ["success", "skipped"], ["failure", "success"], ["cancelled", "success"]])
def test_job_completion_is_required(evidence, statuses):
    assert manifest(evidence, statuses)["status"] == "BLOCKED"


def test_duplicate_json_status_cannot_hide_failure(evidence):
    (evidence / "live-model.json").write_text('{"status":"FAIL","status":"PASS","source_sha":"' + SHA + '"}')
    assert manifest(evidence)["status"] == "BLOCKED"


def test_missing_model_is_owner_required_and_cli_emits_manifest(evidence):
    (evidence / "live-model.json").unlink()
    out = evidence / "freeze.json"
    assert freeze.main(["--evidence-dir", str(evidence), "--source-sha", SHA, "--out", str(out), "--job-status", "success", "--job-status", "success"]) == 0
    assert json.loads(out.read_text())["status"] == "OWNER_REQUIRED"


def test_archive_digest_required(evidence):
    path = evidence / "bundle-acceptance.json"; obj = json.loads(path.read_text())
    obj["details"]["archive_sha256"] = "missing"; path.write_text(json.dumps(obj))
    assert manifest(evidence)["status"] == "BLOCKED"


@pytest.mark.parametrize("change", ["identity", "restart", "tasks", "models"])
def test_live_pass_requires_actual_installed_tasks_and_restart(evidence, change):
    path = evidence / "live-model.json"; obj = json.loads(path.read_text())
    if change == "identity":
        obj["identity"]["source_sha"] = "c" * 40
    elif change == "restart":
        obj["tasks"][0]["restart_persistence"] = "NOT_RUN"
    elif change == "tasks":
        obj["tasks"].pop()
    else:
        obj["models"].pop()
    path.write_text(json.dumps(obj))
    assert manifest(evidence)["status"] == "BLOCKED"


def test_duplicate_testcases_cannot_pad_acceptance_count(evidence):
    (evidence / "results.xml").write_text('<testsuites><testsuite>' + '<testcase name="same"/>' * 13 + '</testsuite></testsuites>')
    assert manifest(evidence)["status"] == "BLOCKED"
