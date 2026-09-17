"""A release label cannot turn missing, stale, partial or foreign acceptance green.

The audit of 17 September 2026 (OA-02) reproduced three false FROZEN verdicts
on the previous aggregator: thirteen JUnit cases instead of the profile's
forty, six copies of one model/case row instead of two models × three cases,
and a results.xml whose own properties named another source SHA. Every one of
those is a negative here, beside the controls the audit used, and the one real
complete set is the positive that must keep freezing.
"""
import importlib.util
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
spec = importlib.util.spec_from_file_location("astra6_freeze", TOOLS / "astra6_freeze.py")
freeze = importlib.util.module_from_spec(spec)
spec.loader.exec_module(freeze)
import acceptance_registry  # noqa: E402

REGISTRY = acceptance_registry.load()
SHA = "a" * 40
ARCHIVE = "b" * 64
RUN = "35110167729"
MODELS = ["synthetic/alpha:free", "synthetic/beta:free"]
CASES = list(REGISTRY["live_model"]["cases"])
MODULES = dict(REGISTRY["junit"]["modules"])


def binding(sha=SHA, archive=ARCHIVE, run=RUN, harness=None):
    return {"source_sha": sha, "archive_sha256": archive, "run_id": run, "harness_sha": harness or sha}


def junit(modules=None, *, properties=True, sha=SHA, archive=ARCHIVE, run=RUN, repeat_name=False,
          body=""):
    modules = MODULES if modules is None else modules
    suite = ET.Element("testsuite", name="pytest", tests=str(sum(modules.values())))
    if properties:
        props = ET.SubElement(suite, "properties")
        for name, value in (("source_sha", sha), ("archive_sha256", archive),
                            ("run_id", run), ("harness_sha", sha)):
            ET.SubElement(props, "property", name=name, value=value)
    for module, count in modules.items():
        for index in range(count):
            case = ET.SubElement(suite, "testcase", classname=f"tests.{module}",
                                 name="same" if repeat_name else f"test_real_{index}")
            if body:
                case.append(ET.fromstring(body))
    root = ET.Element("testsuites")
    root.append(suite)
    return ET.tostring(root)


def live_rows(models=MODELS, cases=CASES, sha=SHA):
    # task_id 1..3 repeats across the two models on purpose: two isolated
    # databases both number from 1, and that must not read as a duplicate.
    return [{"model": model, "case": case, "task_id": index + 1, "status": "PASS",
             "restart_persistence": "PASS", "source_sha": sha}
            for model in models for index, case in enumerate(cases)]


@pytest.fixture
def evidence(tmp_path):
    reports = {
        "bundle-acceptance.json": {
            "status": "PASS", "expected_sha": SHA, "problems": [], "binding": binding(),
            "details": {"archive_sha256": ARCHIVE, "archive": f"BOSSMAN-Windows-x64-{SHA[:12]}.zip",
                        "archive_bytes": 759148513, "evening_verdict": "PASS",
                        "evening_returncode": 0, "evening_negative_control": "PASS",
                        "build_inputs_locked": True}},
        "ui-sweep.json": {"status": "PASS", "source_sha": SHA, "binding": binding(),
                          "counts": {"works": 46, "opens_feature": 32}},
        "live-model.json": {"status": "PASS", "source_sha": SHA, "binding": binding(),
                            "identity": {"source_sha": SHA}, "models": list(MODELS),
                            "tasks": live_rows()},
    }
    for name, report in reports.items():
        (tmp_path / name).write_text(json.dumps(report))
    (tmp_path / "results.xml").write_bytes(junit())
    return tmp_path


def manifest(path, statuses=None):
    return freeze.build_manifest(path, SHA, ["success", "success"] if statuses is None else statuses)


def rewrite(path, mutate):
    obj = json.loads(path.read_text())
    mutate(obj)
    path.write_text(json.dumps(obj))


# ------------------------------------------------------------- the positive

def test_the_real_complete_set_freezes_and_binds_without_copying_secrets(evidence):
    path = evidence / "live-model.json"
    rewrite(path, lambda obj: obj.update(payload="private-provider-secret"))
    report = manifest(evidence)
    assert report["status"] == "FROZEN", report["blockers"]
    assert report["release_state"] == "FROZEN"
    assert report["archive"] == f"BOSSMAN-Windows-x64-{SHA[:12]}.zip" and report["archive_bytes"] == 759148513
    assert report["publish_gate"]["release_ready"] is True
    assert report["contract_version"] == 2
    assert report["archive_sha256"] == ARCHIVE
    assert report["payload_binding"] == {"archive_sha256": ARCHIVE, "run_id": RUN, "harness_sha": SHA,
                                         "reports_bound": sorted(freeze.REPORTS)}
    assert report["acceptance_registry"]["minimum_tests"] == REGISTRY["junit"]["minimum_tests"]
    assert report["acceptance_registry"]["live_rows"] == 6
    assert len(report["evidence"]) == 4
    assert report["evidence"]["results.xml"]["profile_cases"] == REGISTRY["junit"]["minimum_tests"]
    assert all(len(item["sha256"]) == 64 for item in report["evidence"].values())
    assert "private-provider-secret" not in json.dumps(report)
    assert report["target_hardware_acceptance"] == "OWNER_REQUIRED"
    assert not report["label_is_cryptographic_signature"]
    assert not report["model_weights_trained"]


# -------------------------------------------------- the three audit findings

def test_thirteen_cases_are_not_the_forty_case_profile(evidence):
    """OA-02, row 1: the aggregator accepted 13 while the gate ran 40."""
    thirteen = {module: 1 for module in MODULES}
    (evidence / "results.xml").write_bytes(junit(thirteen))
    report = manifest(evidence)
    assert report["status"] == "BLOCKED"
    assert "results.xml:profile_modules_incomplete" in report["blockers"]
    assert "results.xml:below_profile_minimum" in report["blockers"]


def test_six_copies_of_one_model_case_row_are_not_two_models_by_three_cases(evidence):
    """OA-02, row 2."""
    def six(obj):
        obj["tasks"] = [dict(obj["tasks"][0]) for _ in range(6)]
    rewrite(evidence / "live-model.json", six)
    report = manifest(evidence)
    assert report["status"] == "BLOCKED"
    assert "live-model.json:installed_model_and_restart_proof_incomplete" in report["blockers"]


def test_a_results_file_that_names_another_sha_is_refused(evidence):
    """OA-02, row 3: the XML's own properties said a different commit."""
    (evidence / "results.xml").write_bytes(junit(sha="0" * 40))
    report = manifest(evidence)
    assert report["status"] == "BLOCKED"
    assert "results.xml:not_bound_to_payload" in report["blockers"]


# ------------------------------------------------------- the JUnit contract

def test_forty_repetitions_of_one_case_are_one_case(evidence):
    (evidence / "results.xml").write_bytes(junit(repeat_name=True))
    report = manifest(evidence)
    assert report["status"] == "BLOCKED"
    assert "results.xml:repeated_testcases" in report["blockers"]


def test_forty_cases_from_one_module_do_not_cover_the_profile(evidence):
    one = {"test_editors_user_acceptance": REGISTRY["junit"]["minimum_tests"]}
    (evidence / "results.xml").write_bytes(junit(one))
    report = manifest(evidence)
    assert report["status"] == "BLOCKED"
    assert "results.xml:profile_modules_incomplete" in report["blockers"]
    assert "results.xml:below_profile_minimum" not in report["blockers"], "the count alone was fine; the profile was not"


def test_one_required_module_below_its_minimum_blocks(evidence):
    short = dict(MODULES)
    short["test_oss_memory_refusals"] -= 1
    short["test_editors_user_acceptance"] += 1  # the total is still forty
    (evidence / "results.xml").write_bytes(junit(short))
    report = manifest(evidence)
    assert report["status"] == "BLOCKED"
    assert "results.xml:profile_modules_incomplete" in report["blockers"]


def test_cases_from_outside_the_profile_do_not_count_towards_it(evidence):
    padded = {module: 1 for module in MODULES}
    padded["test_something_else"] = 40
    (evidence / "results.xml").write_bytes(junit(padded))
    assert manifest(evidence)["status"] == "BLOCKED"


def test_a_suite_without_properties_is_not_bound(evidence):
    (evidence / "results.xml").write_bytes(junit(properties=False))
    report = manifest(evidence)
    assert report["status"] == "BLOCKED"
    assert "results.xml:not_bound_to_payload" in report["blockers"]


@pytest.mark.parametrize("body", ["", "<skipped/>", "<failure/>", "<error/>"])
def test_empty_skipped_failed_junit_cannot_freeze(evidence, body):
    xml = b"<testsuites/>" if body == "" else junit(body=body)
    (evidence / "results.xml").write_bytes(xml)
    report = manifest(evidence)
    assert report["status"] == "BLOCKED"
    assert "results.xml:tests_not_all_passed" in report["blockers"]


def test_a_matrix_failure_is_not_compensated_by_the_other_job(evidence):
    for statuses in ([], ["success"], ["success", "skipped"], ["failure", "success"], ["cancelled", "success"]):
        assert manifest(evidence, statuses)["status"] == "BLOCKED", statuses


# --------------------------------------------------------- the live contract

@pytest.mark.parametrize("change", ["missing_case", "unknown_case", "foreign_model", "same_model_twice",
                                    "duplicate_task_id_in_one_model", "restart_not_run", "identity",
                                    "a_row_failed", "one_row_short", "one_row_extra"])
def test_live_pass_requires_the_exact_models_by_cases_set(evidence, change):
    def mutate(obj):
        tasks = obj["tasks"]
        if change == "missing_case":
            tasks[1]["case"] = tasks[0]["case"]
        elif change == "unknown_case":
            tasks[1]["case"] = "poetry"
        elif change == "foreign_model":
            tasks[3]["model"] = "someone/else:free"
        elif change == "same_model_twice":
            obj["models"] = [MODELS[0], MODELS[0]]
            for task in tasks:
                task["model"] = MODELS[0]
        elif change == "duplicate_task_id_in_one_model":
            tasks[1]["task_id"] = tasks[0]["task_id"]
        elif change == "restart_not_run":
            tasks[0]["restart_persistence"] = "NOT_RUN"
        elif change == "identity":
            obj["identity"]["source_sha"] = "c" * 40
        elif change == "a_row_failed":
            tasks[5]["status"] = "FAIL"
        elif change == "one_row_short":
            tasks.pop()
        else:
            tasks.append(dict(tasks[0]))
    rewrite(evidence / "live-model.json", mutate)
    report = manifest(evidence)
    assert report["status"] == "BLOCKED", change
    assert any(b.startswith("live-model.json:") for b in report["blockers"]), report["blockers"]


def test_a_failed_row_under_a_pass_status_is_a_contradiction(evidence):
    rewrite(evidence / "live-model.json", lambda obj: obj["tasks"][0].update(status="FAIL"))
    report = manifest(evidence)
    assert "live-model.json:status_inconsistent" in report["blockers"]


def test_the_same_task_id_across_two_isolated_databases_is_not_a_duplicate(evidence):
    report = manifest(evidence)
    assert report["status"] == "FROZEN", report["blockers"]
    rows = json.loads((evidence / "live-model.json").read_text())["tasks"]
    assert [row["task_id"] for row in rows] == [1, 2, 3, 1, 2, 3]


def test_missing_model_is_owner_required_and_cli_emits_manifest(evidence):
    (evidence / "live-model.json").unlink()
    out = evidence / "freeze.json"
    assert freeze.main(["--evidence-dir", str(evidence), "--source-sha", SHA, "--out", str(out),
                        "--job-status", "success", "--job-status", "success"]) == 0
    written = json.loads(out.read_text())
    assert written["status"] == "OWNER_REQUIRED"
    assert written["publish_gate"]["release_ready"] is False


def test_an_owner_required_live_report_still_has_to_be_bound(evidence):
    rewrite(evidence / "live-model.json", lambda obj: obj.update(status="OWNER_REQUIRED", binding=None, tasks=[]))
    report = manifest(evidence)
    assert report["status"] == "BLOCKED"
    assert "live-model.json:not_bound_to_payload" in report["blockers"]


# ------------------------------------------------------- binding to payload

@pytest.mark.parametrize("name", ["bundle-acceptance.json", "ui-sweep.json", "live-model.json"])
@pytest.mark.parametrize("field,value", [("archive_sha256", "c" * 64), ("run_id", "999"),
                                         ("harness_sha", "c" * 40), ("source_sha", "c" * 40),
                                         ("archive_sha256", None), ("run_id", "")])
def test_a_report_bound_to_another_payload_run_or_harness_blocks(evidence, name, field, value):
    rewrite(evidence / name, lambda obj: obj["binding"].update({field: value}))
    report = manifest(evidence)
    assert report["status"] == "BLOCKED", (name, field)
    assert f"{name}:not_bound_to_payload" in report["blockers"] or "run_id_mismatch" in report["blockers"]


def test_a_report_without_binding_blocks(evidence):
    rewrite(evidence / "ui-sweep.json", lambda obj: obj.pop("binding"))
    assert "ui-sweep.json:not_bound_to_payload" in manifest(evidence)["blockers"]


def test_the_archive_digest_the_reports_name_must_be_the_measured_one(evidence):
    """An expected digest typed into every report is not the bundle job's measurement."""
    rewrite(evidence / "bundle-acceptance.json", lambda obj: obj["details"].update(archive_sha256="c" * 64))
    report = manifest(evidence)
    assert report["status"] == "BLOCKED"
    assert report["archive_sha256"] == "c" * 64
    assert all(f"{name}:not_bound_to_payload" in report["blockers"] for name in freeze.REPORTS)


@pytest.mark.parametrize("field,value,blocker", [
    ("archive", "BOSSMAN-Windows-x64-cccccccccccc.zip", "archive_name_invalid"),   # another commit's name
    ("archive", "Source code.zip", "archive_name_invalid"),
    ("archive", None, "archive_name_invalid"),
    ("archive_bytes", 0, "archive_bytes_invalid"),
    ("archive_bytes", "759148513", "archive_bytes_invalid"),
    ("archive_bytes", None, "archive_bytes_invalid"),
])
def test_the_manifest_binds_the_archive_name_and_size_it_was_measured_on(evidence, field, value, blocker):
    rewrite(evidence / "bundle-acceptance.json", lambda obj: obj["details"].update({field: value}))
    report = manifest(evidence)
    assert report["status"] == "BLOCKED"
    assert f"bundle-acceptance.json:{blocker}" in report["blockers"]


def test_release_state_names_the_truthful_narrower_status(evidence):
    assert manifest(evidence)["release_state"] == "FROZEN"
    (evidence / "live-model.json").unlink()
    assert manifest(evidence)["release_state"] == "WINDOWS_RC_READY_OWNER_REQUIRED"
    rewrite(evidence / "ui-sweep.json", lambda obj: obj.update(status="FAIL"))
    assert manifest(evidence)["release_state"] == "BLOCKED"


def test_archive_digest_required(evidence):
    rewrite(evidence / "bundle-acceptance.json", lambda obj: obj["details"].update(archive_sha256="missing"))
    report = manifest(evidence)
    assert report["status"] == "BLOCKED"
    assert "bundle-acceptance.json:archive_sha256_invalid" in report["blockers"]


# ---------------------------------------------------- status vs. content

@pytest.mark.parametrize("verdict,code", [("OWNER_REQUIRED", 2), ("PARTIAL", 3), ("FAIL", 1), ("PASS", 2), (None, 0)])
def test_a_bundle_pass_must_carry_a_passing_evening_verdict(evidence, verdict, code):
    rewrite(evidence / "bundle-acceptance.json",
            lambda obj: obj["details"].update(evening_verdict=verdict, evening_returncode=code))
    report = manifest(evidence)
    assert report["status"] == "BLOCKED"
    assert "bundle-acceptance.json:status_inconsistent" in report["blockers"]


@pytest.mark.parametrize("value", [False, None, "yes"])
def test_an_archive_built_from_unlocked_inputs_is_not_a_release_candidate(evidence, value):
    """OA-03: build_inputs.locked comes from the archive's MANIFEST via the verifier."""
    rewrite(evidence / "bundle-acceptance.json", lambda obj: obj["details"].update(build_inputs_locked=value))
    report = manifest(evidence)
    assert report["status"] == "BLOCKED"
    assert "bundle-acceptance.json:build_inputs_not_locked" in report["blockers"]


def test_a_bundle_pass_requires_the_on_archive_negative_control(evidence):
    rewrite(evidence / "bundle-acceptance.json",
            lambda obj: obj["details"].update(evening_negative_control="FAILED: exit 0"))
    assert "bundle-acceptance.json:negative_control_not_passed" in manifest(evidence)["blockers"]


@pytest.mark.parametrize("verdict", REGISTRY["ui_sweep"]["review_verdicts"])
def test_a_sweep_pass_with_review_class_clicks_is_a_contradiction(evidence, verdict):
    rewrite(evidence / "ui-sweep.json", lambda obj: obj["counts"].update({verdict: 1}))
    report = manifest(evidence)
    assert report["status"] == "BLOCKED"
    assert "ui-sweep.json:status_inconsistent" in report["blockers"]


@pytest.mark.parametrize("name", freeze.REPORTS[:3])
@pytest.mark.parametrize("field,value", [("status", "FAIL"), ("status", "SKIPPED"), ("status", "REVIEW_REQUIRED"),
                                         ("status", "OWNER_REQUIRED"), ("status", "PARTIAL"), ("sha", "c" * 40)])
def test_stale_or_partial_report_cannot_freeze(evidence, name, field, value):
    if field == "sha":
        field = "expected_sha" if name == "bundle-acceptance.json" else "source_sha"
    rewrite(evidence / name, lambda obj: obj.update({field: value}))
    assert manifest(evidence)["status"] != "FROZEN"


@pytest.mark.parametrize("name", freeze.REPORTS)
@pytest.mark.parametrize("mutation", ["missing", "malformed", "duplicate"])
def test_bad_report_cannot_freeze(evidence, name, mutation):
    path = evidence / name
    if mutation == "missing":
        path.unlink()
    elif mutation == "malformed":
        path.write_text("{broken")
    else:
        nested = evidence / "duplicate"
        nested.mkdir()
        (nested / name).write_bytes(path.read_bytes())
    assert manifest(evidence)["status"] != "FROZEN"


def test_duplicate_json_status_cannot_hide_failure(evidence):
    (evidence / "live-model.json").write_text('{"status":"FAIL","status":"PASS","source_sha":"' + SHA + '"}')
    assert manifest(evidence)["status"] == "BLOCKED"


# ------------------------------------------------------------ publish gate

def test_the_publish_gate_is_red_on_anything_but_frozen(evidence, capsys):
    out = evidence / "freeze.json"
    args = ["--evidence-dir", str(evidence), "--source-sha", SHA, "--out", str(out),
            "--job-status", "success", "--job-status", "success"]
    assert freeze.main(args + ["--require-frozen"]) == 0
    assert "BOSSMAN_ASTRA6_RELEASE_READY=YES" in capsys.readouterr().out
    (evidence / "live-model.json").unlink()
    assert freeze.main(args) == 0, "the report is still written on OWNER_REQUIRED"
    assert freeze.main(args + ["--require-frozen"]) == 1
    printed = capsys.readouterr().out
    assert "BOSSMAN_ASTRA6_RELEASE_READY=NO" in printed
    assert "owner_required: live-model.json:missing" in printed
    assert json.loads(out.read_text())["publish_gate"]["release_ready"] is False
