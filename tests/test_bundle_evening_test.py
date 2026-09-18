"""OA-01 (audit of 17 September 2026): the evening verdict is computed from
structured reasons, never from an empty ``blocking`` list.

Before the fix ``tools/bundle_evening_test.py`` said PASS when the doctor was
not shipped, printed no JSON, exited 9 with ``{}`` or was skipped. Each of
those is reproduced here against the live tool with synthetic subprocess
results and a temporary archive layout — the positive control passes, every
negative is refused with a named reason and the exit code of a failure.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / "tools" / "bundle_evening_test.py"
SHA = "a" * 40


def _load():
    spec = importlib.util.spec_from_file_location("bundle_evening_test_under_test", TOOL)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _doctor_report(blocked: tuple[str, ...] = (), *, drop: tuple[str, ...] = (),
                   schema_version: int = 1, blocked_count: int | None = None) -> dict:
    checks = [{"name": name, "status": "BLOCKED" if name in blocked else "PASS",
               "detail": f"{name} detail", "remedy": f"{name} remedy", "facts": {}}
              for name in sorted(set().union(_load().REQUIRED_DOCTOR_CHECKS, {"node", "hardware"}))
              if name not in drop]
    count = sum(check["status"] == "BLOCKED" for check in checks)
    return {"schema_version": schema_version, "platform": "synthetic", "python": "3.12",
            "checks": checks, "blocked": count if blocked_count is None else blocked_count,
            "warn": 0, "pass": len(checks) - count}


def _write_owner_run(support: Path, bodies: dict[str, str]) -> dict[str, str]:
    target = support / "owner-final-run"
    target.mkdir(parents=True, exist_ok=True)
    digests = {}
    for name, body in bodies.items():
        (target / name).write_text(body, encoding="utf-8")
        digests[name] = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return digests


class Bundle:
    """A temporary archive layout driven by fake subprocess results."""

    def __init__(self, tmp_path: Path, monkeypatch):
        self.evening = _load()
        self.home = tmp_path / "BOSSMAN-Windows-x64-synthetic"
        self.support = self.home / "app-support"
        self.support.mkdir(parents=True)
        shutil.copyfile(TOOL, self.support / "bundle_evening_test.py")
        (self.support / "bossman_doctor.py").write_text("# synthetic doctor\n", encoding="utf-8")
        (self.support / "verify_installed_product.py").write_text("# synthetic verifier\n", encoding="utf-8")
        # Настоящий архив везёт комплект владельческого прогона; синтетический
        # обязан быть таким же, иначе проверка комплекта краснела бы на всём
        # подряд и перестала бы что-либо значить.
        self.owner_run = _write_owner_run(
            self.support, {"README_RU.md": "# памятка комплекта\n",
                           "START_PROMPT_RU.md": "# короткое поручение\n"})
        self.evidence_root = tmp_path / "evidence"
        self.evidence_root.mkdir()
        self.calls: list[list[str]] = []
        self.doctor_stdout = json.dumps(_doctor_report())
        self.doctor_rc = 0
        self.doctor_raises: BaseException | None = None
        self.verifier_rc = 0
        self.verifier_status: str | None = "PASS"
        self.verifier_sha = SHA
        self.verifier_raises: BaseException | None = None
        self.verifier_writes = True
        self.required_downloads: list[dict] = []
        # OA-04 stages: (status, returncode, write?) per shipped runner
        self.stage_results = {"installed_ui_sweep.py": ("PASS", 0, True),
                              "live_openrouter_owner.py": ("OWNER_REQUIRED", 2, True)}
        monkeypatch.setattr(self.evening, "HOME", self.home)
        monkeypatch.setattr(self.evening, "SUPPORT", self.support)
        monkeypatch.setattr(self.evening, "_evidence_root", lambda _sha: self.evidence_root)
        monkeypatch.setattr(self.evening, "_console_utf8", lambda: None)
        monkeypatch.setattr(self.evening, "_run", self._fake_run)

    def write_manifest(self, *, files: bool = True, sha: str = SHA) -> None:
        listed = [{"path": f"app-support/{name}", "bytes": path.stat().st_size,
                   "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                  for name in ("bundle_evening_test.py", "bossman_doctor.py", "verify_installed_product.py")
                  for path in [self.support / name] if path.exists()]
        listed += [{"path": f"app-support/owner-final-run/{name}", "sha256": digest}
                   for name, digest in self.owner_run.items()]
        manifest = {"schema_version": 1, "artifact": "SYNTHETIC-NOT-A-PRODUCT", "source_sha": sha,
                    "required_downloads": self.required_downloads,
                    "contents": {"runtime": {"python_version": "3.12.0"},
                                 "owner_run": {"path": "app-support/owner-final-run",
                                               "files": dict(self.owner_run)}}}
        if files:
            manifest["files"] = listed
        (self.home / "MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")

    def _fake_run(self, args, **kwargs):
        self.calls.append(list(args))
        script = Path(args[2] if args[1] == "-I" else args[1]).name
        if script == "bossman_doctor.py":
            if self.doctor_raises:
                raise self.doctor_raises
            return subprocess.CompletedProcess(args, self.doctor_rc, self.doctor_stdout, "")
        if script in self.stage_results and args[1] == "-I":
            status, rc, writes = self.stage_results[script]
            if writes:
                out = Path(args[args.index("--output") + 1])
                out.write_text(json.dumps({"status": status, "source_sha": SHA,
                                           "reason": "Set BOSSMAN_OPENROUTER_API_KEY"}), encoding="utf-8")
            return subprocess.CompletedProcess(args, rc, "stage", "")
        assert script == "verify_installed_product.py", args
        if self.verifier_raises:
            raise self.verifier_raises
        if self.verifier_writes:
            out = Path(args[args.index("--out") + 1])
            body = {"status": self.verifier_status, "source_sha": self.verifier_sha}
            out.write_text(json.dumps(body) if self.verifier_status != "<broken>" else "{broken",
                           encoding="utf-8")
        return subprocess.CompletedProcess(args, self.verifier_rc, "SYNTHETIC VERIFIER", "")

    def run(self, argv=()) -> tuple[int, dict]:
        if not (self.home / "MANIFEST.json").exists():
            self.write_manifest()
        rc = self.evening.main(list(argv))
        runs = sorted(p for p in self.evidence_root.iterdir() if p.is_dir())
        result = json.loads((runs[-1] / "OWNER_EVENING_RESULT.json").read_text(encoding="utf-8"))
        return rc, result

    def scripts_called(self) -> list[str]:
        return [Path(call[2] if call[1] == "-I" else call[1]).name for call in self.calls]

    def ship_stage_runners(self) -> None:
        for name in ("installed_ui_sweep.py", "live_openrouter_owner.py"):
            (self.support / name).write_text(f"# synthetic {name}\n", encoding="utf-8")


@pytest.fixture
def bundle(tmp_path, monkeypatch, capsys) -> Bundle:
    return Bundle(tmp_path, monkeypatch)


def codes(result: dict) -> list[str]:
    return [reason["code"] for reason in result["reasons"]]


# ------------------------------------------------------------- positive control

def test_a_readable_complete_doctor_and_a_passing_verifier_pass(bundle, capsys):
    rc, result = bundle.run()
    assert (rc, result["verdict"], result["exit_code"]) == (0, "PASS", 0)
    assert result["doctor"] == "OK" and result["installed_acceptance"] == "PASS"
    assert result["reasons"] == []
    assert result["schema_version"] == 2
    assert result["source_sha"] == SHA
    assert len(result["manifest_sha256"]) == 64
    assert all(len(v) == 64 for v in result["harness"].values()), result["harness"]
    assert bundle.scripts_called() == ["bossman_doctor.py", "verify_installed_product.py"]
    out = capsys.readouterr().out
    assert "OWNER_EVENING_RESULT=PASS" in out
    assert f"OWNER_EVENING_RUN_ID={result['run_id']}" in out


# --------------------------------------------------------- the four audit rows

def test_a_doctor_that_is_not_shipped_is_a_failed_archive_not_a_pass(bundle):
    (bundle.support / "bossman_doctor.py").unlink()
    rc, result = bundle.run()
    assert (rc, result["verdict"]) == (1, "FAIL")
    assert result["doctor"] == "NOT_SHIPPED"
    assert "doctor_not_shipped" in codes(result)
    # Nothing an incomplete archive says would bind to a build: the verifier is not even started.
    assert bundle.scripts_called() == [] and result["installed_acceptance"] == "NOT_RUN"


def test_a_doctor_that_prints_no_json_fails(bundle):
    bundle.doctor_stdout = "NOT JSON"
    rc, result = bundle.run()
    assert (rc, result["verdict"], result["doctor"]) == (1, "FAIL", "UNREADABLE")
    assert "doctor_unreadable" in codes(result)


def test_exit_9_with_an_empty_object_is_not_a_diagnostic(bundle):
    bundle.doctor_stdout, bundle.doctor_rc = "{}", 9
    rc, result = bundle.run()
    assert (rc, result["verdict"], result["doctor"]) == (1, "FAIL", "INVALID_SCHEMA")
    assert "doctor_invalid_schema" in codes(result)


def test_skip_doctor_is_partial_never_pass(bundle):
    rc, result = bundle.run(["--skip-doctor"])
    assert (rc, result["verdict"], result["doctor"]) == (3, "PARTIAL", "SKIPPED")
    assert "doctor_skipped" in codes(result)
    assert result["exit_code"] == 3
    assert bundle.scripts_called() == ["verify_installed_product.py"]


def test_skip_doctor_still_fails_on_a_failing_verifier(bundle):
    bundle.verifier_status, bundle.verifier_rc = "FAIL", 1
    rc, result = bundle.run(["--skip-doctor"])
    assert (rc, result["verdict"]) == (1, "FAIL")


# ------------------------------------------------------------- doctor contract

@pytest.mark.parametrize("report,expected_problem", [
    ({"checks": []}, "checks is not a non-empty list"),
    (_doctor_report(drop=("browser",)), "required checks absent: browser"),
    (_doctor_report(blocked_count=3), "blocked count 3 does not match 0"),
    (_doctor_report(schema_version=2), "schema_version 2 is not 1"),
    ({"schema_version": 1, "checks": [{"name": "python", "status": "GREAT"}], "blocked": 0},
     "status 'GREAT'"),
])
def test_an_incomplete_or_foreign_report_is_invalid_schema(bundle, report, expected_problem):
    bundle.doctor_stdout = json.dumps(report)
    rc, result = bundle.run()
    assert (rc, result["verdict"], result["doctor"]) == (1, "FAIL", "INVALID_SCHEMA")
    reason = next(r for r in result["reasons"] if r["code"] == "doctor_invalid_schema")
    assert expected_problem in reason["detail"], reason


def test_a_doctor_whose_exit_code_contradicts_its_report_crashed(bundle):
    bundle.doctor_rc = 9  # a complete clean report, wrong exit code
    rc, result = bundle.run()
    assert (rc, result["verdict"], result["doctor"]) == (1, "FAIL", "CRASHED")
    assert "doctor_exit_code_mismatch" in codes(result)


def test_a_doctor_that_hangs_is_a_timeout_failure(bundle):
    bundle.doctor_raises = subprocess.TimeoutExpired(["python", "bossman_doctor.py"], 300, output="partial")
    rc, result = bundle.run()
    assert (rc, result["verdict"], result["doctor"]) == (1, "FAIL", "TIMEOUT")
    assert "doctor_timeout" in codes(result)


def test_a_blocked_owner_machine_check_is_owner_required_and_named(bundle):
    bundle.doctor_stdout, bundle.doctor_rc = json.dumps(_doctor_report(blocked=("port",))), 1
    rc, result = bundle.run()
    assert (rc, result["verdict"], result["doctor"]) == (2, "OWNER_REQUIRED", "BLOCKED")
    reason = next(r for r in result["reasons"] if r["code"] == "doctor_blocked_owner_machine")
    assert reason["check"] == "port" and reason["class"] == "owner"
    assert result["doctor_blocked"] == [{"name": "port", "detail": "port detail"}]


@pytest.mark.parametrize("check", ["bossman-packages", "python-packages", "browser", "evidence-key"])
def test_a_blocked_shipped_component_is_a_broken_archive(bundle, check):
    """A missing credential and a missing package must never share a verdict."""
    bundle.doctor_stdout, bundle.doctor_rc = json.dumps(_doctor_report(blocked=(check,))), 1
    rc, result = bundle.run()
    assert (rc, result["verdict"], result["doctor"]) == (1, "FAIL", "BLOCKED")
    reason = next(r for r in result["reasons"] if r["code"] == "doctor_blocked_delivery")
    assert reason["check"] == check


def test_owner_and_delivery_blocks_together_are_a_failure(bundle):
    bundle.doctor_stdout = json.dumps(_doctor_report(blocked=("port", "telemetry")))
    bundle.doctor_rc = 1
    rc, result = bundle.run()
    assert (rc, result["verdict"]) == (1, "FAIL")


# ----------------------------------------------------------- verifier contract

def test_a_verifier_that_writes_nothing_cannot_pass_on_an_old_result(bundle):
    """The previous run's PASS sits right there; this run must not read it."""
    stale = bundle.evidence_root / "20260101T000000Z-stale"
    stale.mkdir()
    (stale / "installed-acceptance.json").write_text(json.dumps({"status": "PASS", "source_sha": SHA}))
    (bundle.evidence_root / "installed-acceptance.json").write_text(json.dumps({"status": "PASS", "source_sha": SHA}))
    bundle.verifier_writes = False
    rc, result = bundle.run()
    assert (rc, result["verdict"], result["installed_acceptance"]) == (1, "FAIL", "NO_RESULT")
    assert "verifier_no_result" in codes(result)


def test_a_verifier_result_for_another_sha_is_refused(bundle):
    bundle.verifier_sha = "c" * 40
    rc, result = bundle.run()
    assert (rc, result["verdict"]) == (1, "FAIL")
    assert "verifier_sha_mismatch" in codes(result)


def test_exit_code_and_status_must_agree(bundle):
    bundle.verifier_rc = 1  # says PASS in the file, failed as a process
    rc, result = bundle.run()
    assert (rc, result["verdict"]) == (1, "FAIL")
    assert "verifier_inconsistent" in codes(result)


def test_a_failed_verifier_fails(bundle):
    bundle.verifier_status, bundle.verifier_rc = "FAIL", 1
    rc, result = bundle.run()
    assert (rc, result["verdict"], result["installed_acceptance"]) == (1, "FAIL", "FAIL")
    assert "verifier_not_passed" in codes(result)


def test_an_unreadable_verifier_result_fails(bundle):
    bundle.verifier_status = "<broken>"
    rc, result = bundle.run()
    assert (rc, result["verdict"], result["installed_acceptance"]) == (1, "FAIL", "UNREADABLE")


def test_a_hanging_verifier_is_a_timeout_failure(bundle):
    bundle.verifier_raises = subprocess.TimeoutExpired(["python", "verify_installed_product.py"], 900)
    rc, result = bundle.run()
    assert (rc, result["verdict"], result["installed_acceptance"]) == (1, "FAIL", "TIMEOUT")
    assert "verifier_timeout" in codes(result)


# ------------------------------------------------------------ payload binding

def test_a_harness_file_that_differs_from_the_manifest_is_refused(bundle):
    bundle.write_manifest()
    (bundle.support / "verify_installed_product.py").write_text("# edited after packaging\n", encoding="utf-8")
    rc, result = bundle.run()
    assert (rc, result["verdict"]) == (1, "FAIL")
    reason = next(r for r in result["reasons"] if r["code"] == "harness_not_the_shipped_one")
    assert "verify_installed_product.py" in reason["detail"]


def test_a_manifest_without_a_file_list_is_incomplete(bundle):
    bundle.write_manifest(files=False)
    rc, result = bundle.run()
    assert (rc, result["verdict"]) == (1, "FAIL")
    assert "manifest_incomplete" in codes(result)


def test_a_missing_manifest_is_a_failure_not_an_owner_request(bundle, capsys):
    """Exit 2 means OWNER_REQUIRED to the archive verifier; an incomplete archive is not that."""
    assert bundle.evening.main([]) == 1
    assert "OWNER_EVENING_RESULT=FAIL" in capsys.readouterr().err


def test_a_manifest_without_a_full_sha_is_a_failure(bundle, capsys):
    bundle.write_manifest(sha="not-a-sha")
    assert bundle.evening.main([]) == 1


def test_a_first_run_download_is_owner_required(bundle):
    bundle.required_downloads = [{"component": "ffmpeg", "reason": "not supplied", "acquired_by": "first run"}]
    rc, result = bundle.run()
    assert (rc, result["verdict"]) == (2, "OWNER_REQUIRED")
    assert "required_download" in codes(result)


# --------------------------------------------------------------- robustness

def test_an_exception_inside_the_check_writes_a_failed_result(bundle):
    bundle.doctor_raises = RuntimeError("synthetic crash")
    rc, result = bundle.run()
    assert (rc, result["verdict"]) == (1, "FAIL")
    reason = next(r for r in result["reasons"] if r["code"] == "harness_exception")
    assert "RuntimeError" in reason["detail"]


def test_every_run_gets_its_own_folder_and_result(bundle):
    bundle.run()
    bundle.run()
    runs = sorted(p for p in bundle.evidence_root.iterdir() if p.is_dir())
    assert len(runs) == 2 and runs[0].name != runs[1].name
    assert all((run / "OWNER_EVENING_RESULT.json").exists() for run in runs)
    ids = {json.loads((run / "OWNER_EVENING_RESULT.json").read_text())["run_id"] for run in runs}
    assert ids == {run.name for run in runs}


def test_exit_codes_and_verdicts_cannot_drift(bundle):
    evening = bundle.evening
    assert evening.EXIT_CODES == {"PASS": 0, "FAIL": 1, "OWNER_REQUIRED": 2, "PARTIAL": 3}
    assert evening.verdict_for([]) == "PASS"
    assert evening.verdict_for([{"class": "owner"}]) == "OWNER_REQUIRED"
    assert evening.verdict_for([{"class": "partial"}, {"class": "owner"}]) == "PARTIAL"
    assert evening.verdict_for([{"class": "fail"}, {"class": "partial"}, {"class": "owner"}]) == "FAIL"


def test_the_doctor_names_the_tool_requires_are_the_doctor_s_own():
    """A renamed check in scripts/bossman_doctor.py must fail here, not on the owner's evening."""
    source = (REPO / "scripts" / "bossman_doctor.py").read_text(encoding="utf-8")
    for name in sorted(_load().REQUIRED_DOCTOR_CHECKS | _load().OWNER_MACHINE_CHECKS):
        assert f'Check("{name}"' in source, name
    assert _load().OWNER_MACHINE_CHECKS <= _load().REQUIRED_DOCTOR_CHECKS


# ------------------------------------------------------- OA-04: shipped stages

def test_full_runs_the_shipped_sweep_and_live_smoke_from_the_archive(bundle):
    bundle.ship_stage_runners()
    rc, result = bundle.run(["--full"])
    assert bundle.scripts_called() == ["bossman_doctor.py", "verify_installed_product.py",
                                       "installed_ui_sweep.py", "live_openrouter_owner.py"]
    # No key on this machine: the live stage is OWNER_REQUIRED, never a PASS.
    assert (rc, result["verdict"]) == (2, "OWNER_REQUIRED")
    assert result["stages"] == {"installed_ui_sweep": {"status": "PASS", "returncode": 0},
                                "live_openrouter_owner": {"status": "OWNER_REQUIRED", "returncode": 2}}
    reason = next(r for r in result["reasons"] if r["code"] == "live-model_owner_required")
    assert reason["class"] == "owner" and "BOSSMAN_OPENROUTER_API_KEY" in reason["detail"]
    live_call = next(call for call in bundle.calls if call[2].endswith("live_openrouter_owner.py"))
    assert live_call[1] == "-I" and "--expected-sha" in live_call and SHA in live_call


def test_a_live_smoke_that_passed_with_a_key_makes_full_pass(bundle):
    bundle.ship_stage_runners()
    bundle.stage_results["live_openrouter_owner.py"] = ("PASS", 0, True)
    rc, result = bundle.run(["--full"])
    assert (rc, result["verdict"]) == (0, "PASS")


def test_a_sweep_that_needs_review_fails_the_full_run(bundle):
    bundle.ship_stage_runners()
    bundle.stage_results["installed_ui_sweep.py"] = ("REVIEW_REQUIRED", 0, True)
    rc, result = bundle.run(["--ui-sweep"])
    assert (rc, result["verdict"]) == (1, "FAIL")
    assert "ui-sweep_not_passed" in codes(result)


def test_a_stage_runner_that_is_not_shipped_fails(bundle):
    rc, result = bundle.run(["--live"])
    assert (rc, result["verdict"]) == (1, "FAIL")
    assert "live-model_not_shipped" in codes(result)


def test_a_stage_that_leaves_no_report_fails(bundle):
    bundle.ship_stage_runners()
    bundle.stage_results["installed_ui_sweep.py"] = ("PASS", 0, False)
    rc, result = bundle.run(["--ui-sweep"])
    assert (rc, result["verdict"]) == (1, "FAIL")
    assert "ui-sweep_no_result" in codes(result)


def test_the_default_run_starts_no_stage(bundle):
    bundle.ship_stage_runners()
    rc, result = bundle.run()
    assert (rc, result["stages"]) == (0, {})


# ------------------- комплект владельческого прогона внутри самого архива

def _owner_run_manifest(files: dict[str, str]) -> dict:
    """MANIFEST, каким его пишет сборка: комплект и в списке, и в contents."""
    return {
        "files": [{"path": f"app-support/owner-final-run/{name}", "sha256": digest}
                  for name, digest in files.items()],
        "contents": {"owner_run": {"path": "app-support/owner-final-run",
                                   "files": dict(files)}},
    }


def test_a_complete_owner_run_package_raises_nothing(tmp_path, monkeypatch) -> None:
    bundle = Bundle(tmp_path, monkeypatch)
    digests = _write_owner_run(bundle.support,
                               {"README_RU.md": "# памятка\n",
                                "START_PROMPT_RU.md": "# поручение\n"})
    monkeypatch.setattr(bundle.evening, "SUPPORT", bundle.support)
    assert bundle.evening.owner_run_problems(_owner_run_manifest(digests)) == []


def test_a_missing_owner_run_file_is_a_failure_reason(tmp_path, monkeypatch) -> None:
    """Владелец, распаковавший архив без инструкции, узнаёт об этом от нас."""
    bundle = Bundle(tmp_path, monkeypatch)
    digests = _write_owner_run(bundle.support,
                               {"README_RU.md": "# памятка\n",
                                "START_PROMPT_RU.md": "# поручение\n"})
    (bundle.support / "owner-final-run" / "START_PROMPT_RU.md").unlink()
    monkeypatch.setattr(bundle.evening, "SUPPORT", bundle.support)
    reasons = bundle.evening.owner_run_problems(_owner_run_manifest(digests))
    assert [r["code"] for r in reasons] == ["owner_run_package_incomplete"]
    assert "START_PROMPT_RU.md" in reasons[0]["detail"]
    assert reasons[0]["class"] == "fail"


def test_an_owner_run_file_that_is_not_the_shipped_one_is_a_failure(tmp_path, monkeypatch) -> None:
    """Подменённый файл — не комплект, даже если имя на месте."""
    bundle = Bundle(tmp_path, monkeypatch)
    digests = _write_owner_run(bundle.support, {"README_RU.md": "# памятка\n"})
    (bundle.support / "owner-final-run" / "README_RU.md").write_text(
        "# другое содержимое\n", encoding="utf-8")
    monkeypatch.setattr(bundle.evening, "SUPPORT", bundle.support)
    reasons = bundle.evening.owner_run_problems(_owner_run_manifest(digests))
    assert [r["code"] for r in reasons] == ["owner_run_package_not_the_shipped_one"]
    assert "README_RU.md" in reasons[0]["detail"]


def test_an_archive_without_the_package_record_fails_instead_of_passing_quietly(
        tmp_path, monkeypatch) -> None:
    """Манифест без записи о комплекте — архив собран мимо контракта."""
    bundle = Bundle(tmp_path, monkeypatch)
    monkeypatch.setattr(bundle.evening, "SUPPORT", bundle.support)
    reasons = bundle.evening.owner_run_problems({"files": [], "contents": {}})
    assert [r["code"] for r in reasons] == ["owner_run_package_not_declared"]
    assert reasons[0]["class"] == "fail"


def test_a_whole_run_fails_when_the_owner_package_is_missing(bundle) -> None:
    """Сквозная проверка: причина обязана доехать до вердикта прогона.

    Без неё owner_run_problems мог бы быть вызван и тихо выброшен — проверка,
    не влияющая на вердикт, это украшение.
    """
    bundle.write_manifest()
    (bundle.support / "owner-final-run" / "START_PROMPT_RU.md").unlink()
    rc, result = bundle.run()
    assert (rc, result["verdict"]) == (1, "FAIL")
    assert "owner_run_package_incomplete" in codes(result)
    assert result["owner_run_package"] == "FAIL"


def test_a_whole_run_records_the_package_as_passing_when_it_is_whole(bundle) -> None:
    """Пара к предыдущему: без неё «FAIL» не отличить от постоянного красного."""
    bundle.write_manifest()
    rc, result = bundle.run()
    assert result["owner_run_package"] == "PASS"
    assert not [c for c in codes(result) if c.startswith("owner_run_package")]
