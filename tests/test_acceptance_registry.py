"""One acceptance profile for the workflow, the JUnit check and the freeze (OA-02)."""
from __future__ import annotations

import importlib.util
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
TOOLS = REPO / "tools"
sys.path.insert(0, str(TOOLS))
import acceptance_registry  # noqa: E402

spec = importlib.util.spec_from_file_location("require_acceptance_results", TOOLS / "require_acceptance_results.py")
require = importlib.util.module_from_spec(spec)
spec.loader.exec_module(require)

REGISTRY = acceptance_registry.load()
MODULES = REGISTRY["junit"]["modules"]
WORKFLOW = (REPO / ".github" / "workflows" / "windows-bundle.yml").read_text(encoding="utf-8")
SHA = "a" * 40


def results(modules=None, *, sha=None, repeat=False, body="", extra_module=None):
    modules = dict(MODULES if modules is None else modules)
    if extra_module:
        modules[extra_module[0]] = extra_module[1]
    suite = ET.Element("testsuite")
    if sha:
        props = ET.SubElement(suite, "properties")
        ET.SubElement(props, "property", name="source_sha", value=sha)
    for module, count in modules.items():
        for index in range(count):
            case = ET.SubElement(suite, "testcase", classname=f"tests.{module}",
                                 name="same" if repeat else f"test_{index}")
            if body:
                case.append(ET.fromstring(body))
    return ET.tostring(suite)


def write(tmp_path, xml: bytes) -> Path:
    path = tmp_path / "results.xml"
    path.write_bytes(xml)
    return path


# ------------------------------------------------------------- the registry

def test_the_registry_is_one_consistent_profile():
    assert REGISTRY["profile"] == "windows-installed"
    assert sum(MODULES.values()) == REGISTRY["junit"]["minimum_tests"] == 40
    assert len(MODULES) == 13
    assert REGISTRY["live_model"] == {"models": 2, "cases": ["arithmetic", "structured_data", "instruction_following"]}


@pytest.mark.parametrize("module", sorted(MODULES))
def test_every_registry_module_exists_where_the_workflow_runs_it(module):
    path = REPO / REGISTRY["junit"]["root"] / f"{module}.py"
    assert path.is_file(), path
    assert path.read_text(encoding="utf-8").count("def test_") >= 1


def test_pytest_paths_and_module_names_round_trip():
    paths = acceptance_registry.pytest_paths(REGISTRY)
    assert paths[0] == "command-center/tests/test_editors_user_acceptance.py"
    assert acceptance_registry.module_of("tests.test_editors_user_acceptance") == "test_editors_user_acceptance"
    assert acceptance_registry.module_of("tests.test_x.TestY") == "test_x"
    assert acceptance_registry.module_of("") is None


@pytest.mark.parametrize("broken", [
    {"schema_version": 2},
    {"junit": {"root": "x", "minimum_tests": 1, "modules": {}}},
    {"junit": {"root": "x", "minimum_tests": 3, "modules": {"test_a": 1}}},
    {"junit": {"root": "x", "minimum_tests": 1, "modules": {"not_a_test": 1}}},
    {"junit": {"root": "x", "minimum_tests": 0, "modules": {"test_a": 0}}},
    {"live_model": {"models": 0, "cases": ["a"]}},
    {"live_model": {"models": 2, "cases": ["a", "a"]}},
])
def test_a_registry_that_is_not_a_profile_is_refused(tmp_path, broken):
    import json
    registry = json.loads((TOOLS / "acceptance_registry.json").read_text(encoding="utf-8"))
    registry.update(broken)
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(registry), encoding="utf-8")
    with pytest.raises(ValueError):
        acceptance_registry.load(path)


def test_the_cli_prints_the_paths_the_workflow_consumes(capsys):
    assert acceptance_registry.main(["--paths"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines == acceptance_registry.pytest_paths(REGISTRY)
    assert acceptance_registry.main(["--minimum-tests"]) == 0
    assert capsys.readouterr().out.strip() == "40"


# ------------------------------------------------------------- the workflow

def test_the_windows_gate_runs_the_registry_not_a_typed_list():
    assert "python tools/acceptance_registry.py --paths" in WORKFLOW
    assert re.search(r"require_acceptance_results\.py .*--source-sha", WORKFLOW), "results must be bound to the SHA"
    assert "--minimum-tests 40" not in WORKFLOW, "the floor lives in the registry"


@pytest.mark.parametrize("module", sorted(MODULES))
def test_the_push_filter_names_every_profile_module(module):
    """A gate test that cannot trigger the gate is a silent gate (run 105)."""
    assert f"      - 'command-center/tests/{module}.py'\n" in WORKFLOW, module


@pytest.mark.parametrize("path", ["tools/astra6_freeze.py", "tools/require_acceptance_results.py",
                                  "tools/acceptance_registry.py", "tools/acceptance_registry.json",
                                  "tools/bundle_evening_test.py", "tools/verify_windows_bundle.py",
                                  "command-center/tests/conftest.py"])
def test_the_validators_themselves_trigger_the_gate(path):
    assert f"      - '{path}'\n" in WORKFLOW, path


def test_the_freeze_job_has_a_publish_gate_that_is_not_the_report_step():
    assert WORKFLOW.count("python tools/astra6_freeze.py") == 2
    assert "--require-frozen" in WORKFLOW


def test_the_owner_job_binds_evidence_to_the_measured_archive():
    for name in ("BOSSMAN_ARCHIVE_SHA256", "BOSSMAN_HARNESS_SHA"):
        assert f"'{name}'" in WORKFLOW, name
    assert "accepted['details']['archive_sha256'] == archive_sha256" in WORKFLOW


# ----------------------------------------------------- require_acceptance

def test_the_real_profile_passes_the_junit_check(tmp_path):
    counts = require.verify(write(tmp_path, results(sha=SHA)), source_sha=SHA)
    assert counts["tests"] == 40 and counts["per_module"] == MODULES


@pytest.mark.parametrize("case", ["thirteen", "repeat", "one_module", "module_short", "failure", "error",
                                  "skipped", "wrong_sha", "no_sha", "padding_outside_profile"])
def test_everything_that_is_not_the_profile_is_refused(tmp_path, case):
    sha = SHA
    if case == "thirteen":
        xml = results({m: 1 for m in MODULES}, sha=sha)
    elif case == "repeat":
        xml = results(sha=sha, repeat=True)
    elif case == "one_module":
        xml = results({"test_editors_user_acceptance": 40}, sha=sha)
    elif case == "module_short":
        short = dict(MODULES)
        short["test_unload_flush"] -= 1
        short["test_web_designer_recovery_ui"] += 1
        xml = results(short, sha=sha)
    elif case in ("failure", "error", "skipped"):
        xml = results(sha=sha, body=f"<{case}/>")
    elif case == "wrong_sha":
        xml = results(sha="0" * 40)
    elif case == "no_sha":
        xml = results()
    else:
        xml = results({m: 1 for m in MODULES}, sha=sha, extra_module=("test_other", 40))
    with pytest.raises(SystemExit) as failed:
        require.verify(write(tmp_path, xml), source_sha=sha)
    assert "Required acceptance did not pass" in str(failed.value)


def test_a_command_line_floor_can_only_raise_the_registry_floor(tmp_path):
    path = write(tmp_path, results(sha=SHA))
    assert require.verify(path, minimum_tests=1, source_sha=SHA)["tests"] == 40
    with pytest.raises(SystemExit):
        require.verify(path, minimum_tests=41, source_sha=SHA)


def test_review_verdicts_are_the_sweep_s_own():
    spec = importlib.util.spec_from_file_location("installed_ui_sweep", TOOLS / "installed_ui_sweep.py")
    sweep = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sweep)
    assert list(sweep.REVIEW_VERDICTS) == REGISTRY["ui_sweep"]["review_verdicts"]
