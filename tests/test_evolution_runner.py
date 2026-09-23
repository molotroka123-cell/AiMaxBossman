"""Real Git/pytest repair experiments; fake proposer is explicitly a fixture.

These tests verify the supervisor, not a live LLM or Windows installation.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bossman-core"))
from bossman_v3.self_improvement import runner as evo


def host_run(*args, **kwargs):
    # Only deterministic fixture proposals run on the test host.
    return evo.run(*args, executor="host", **kwargs)


@pytest.fixture
def project(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "calc.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    (repo / "test_target.py").write_text("from calc import value\ndef test_target():\n    assert value() == 2\n", encoding="utf-8")
    (repo / "test_regression.py").write_text("from calc import value\ndef test_positive():\n    assert value() > 0\n", encoding="utf-8")
    suite = {"version": 1, "cases": [
        {"id": "repair", "role": "train", "goal": "Return exactly two", "tests": ["test_target.py"], "editable": ["calc.py"]},
        {"id": "positive", "role": "regression", "goal": "Remain positive", "tests": ["test_regression.py"], "editable": []},
    ]}
    (repo / "suite.json").write_text(json.dumps(suite), encoding="utf-8")
    evo.git(repo, "init", "-q")
    evo.git(repo, "add", ".")
    evo.git(repo, "-c", "user.name=test", "-c", "user.email=test@localhost", "commit", "-qm", "baseline")
    return repo, evo.load_suite(repo, repo / "suite.json"), tmp_path / "campaign"


def proposal(value=2):
    return {"summary": "Repair return value", "edits": [{"path": "calc.py", "old": "return 1", "new": f"return {value}"}]}


def test_real_repair_preserves_release_and_records_partial_memory(project):
    repo, suite, work = project
    original = evo.git(repo, "rev-parse", "HEAD")
    calls = []

    def proposer(prompt, cwd, budget, timeout):
        calls.append(prompt)
        return proposal(), 0.1

    result = host_run(repo, suite, work, proposer=proposer, iterations=1)
    assert result["status"] == "CANDIDATE_PASSES"
    assert result["champion_sha"] != original
    assert evo.git(repo, "rev-parse", "HEAD") == original
    assert (repo / "calc.py").read_text().endswith("return 1\n")
    assert len(calls) == 1 and "FAIL" in calls[0]
    assert len(result["attempts"][0]["checks"]) == 2
    store = evo.LearningStore(work / "learning")
    assert store.verified() == []
    assert store.failed()[0]["learning_status"] == "PARTIAL"
    assert evo.export_verified(store, work / "sft.jsonl", {"repair"}) == 0
    assert not list((work / "worktrees").iterdir())
    # A new invocation resumes the measured candidate, rather than spending again.
    resumed = host_run(repo, suite, work, proposer=proposer)
    assert resumed["status"] == "NEEDS_NEW_SCENARIOS" and len(calls) == 1
    assert resumed["reserved_usd"] == 0.5


def test_regression_is_rejected_even_if_target_passes(project):
    repo, suite, work = project
    # Target improvement accompanied by a new failure must never be accepted.
    (repo / "test_regression.py").write_text("from calc import value\ndef test_regression():\n    assert value() == 1\n")
    evo.git(repo, "add", ".")
    evo.git(repo, "-c", "user.name=test", "-c", "user.email=t@localhost", "commit", "-qm", "regression")
    suite = evo.load_suite(repo, repo / "suite.json")
    result = host_run(repo, suite, work, proposer=lambda *a: (proposal(), 0.0), iterations=1)
    assert result["attempts"][0]["status"] == "REJECTED"
    assert "regression" in result["attempts"][0]["reason"]
    assert result["champion_sha"] == result["base_sha"]


def test_provider_failure_keeps_budget_across_restart(project):
    repo, suite, work = project
    calls = []

    def broken(*args):
        calls.append(1)
        raise RuntimeError("provider offline")

    first = host_run(repo, suite, work, proposer=broken, iterations=1, max_usd=0.5)
    second = host_run(repo, suite, work, proposer=broken, iterations=1, max_usd=0.5)
    assert first["reserved_usd"] == 0.5
    assert second["status"] == "BUDGET_EXHAUSTED" and len(calls) == 1


def test_baseline_missing_dependency_is_blocked_not_training(project):
    repo, suite, work = project
    (repo / "test_target.py").write_text("import bossman_nonexistent_dependency\n")
    evo.git(repo, "add", ".")
    evo.git(repo, "-c", "user.name=test", "-c", "user.email=t@localhost", "commit", "-qm", "dependency")
    suite = evo.load_suite(repo, repo / "suite.json")
    result = host_run(repo, suite, work, proposer=lambda *a: pytest.fail("should not call model"))
    assert result["status"] == "BLOCKED_BASELINE"
    assert result["reserved_usd"] == 0
    assert "ModuleNotFoundError" in result["last_baseline"]["repair"]["detail"]


@pytest.mark.parametrize("name", ["../outside.py", "/tmp/outside.py", "C:/outside.py", "x\\y.py", ".git/config", "calc.py/../calc.py"])
def test_path_escape_refused(project, name):
    repo, _, _ = project
    with pytest.raises(ValueError):
        evo.safe_file(repo, name)


def test_symlink_edit_refused(project):
    repo, _, _ = project
    try:
        (repo / "link.py").symlink_to(repo / "calc.py")
    except OSError:
        pytest.skip("Symlinks unavailable in this Windows environment")
    with pytest.raises(ValueError):
        evo.safe_file(repo, "link.py")


def test_all_edits_validated_before_any_write(project):
    repo, _, _ = project
    bad = proposal()
    bad["edits"].append({"path": "test_target.py", "old": "2", "new": "1"})
    with pytest.raises(ValueError):
        evo.apply_edits(repo, bad, ["calc.py"])
    assert "return 1" in (repo / "calc.py").read_text()


@pytest.mark.parametrize("old,new", [("missing", "pass"), ("return 1", "return 1"), ("return 1", "return ("),
                                      ("return 1", "return 'BOSSMAN_TEST_SECRET_abcd'")])
def test_invalid_or_secret_patch_refused(project, old, new):
    repo, _, _ = project
    p = proposal()
    p["edits"][0].update(old=old, new=new)
    with pytest.raises((ValueError, SyntaxError)):
        evo.apply_edits(repo, p, ["calc.py"])


@pytest.mark.parametrize("xml,code", [("<testsuite/>", 0),
    ('<testsuite><testcase name="x"><skipped/></testcase></testsuite>', 0),
    ('<testsuite><testcase name="x"><error/></testcase></testsuite>', 1),
    ('<testsuite><testcase name="x"/></testsuite>', 1), ("broken", 0)])
def test_no_fake_pass_from_junit(tmp_path, xml, code):
    path = tmp_path / "junit.xml"
    path.write_text(xml)
    assert evo.parse_junit(path, code, "")['status'] == "BLOCKED"


def test_test_inventory_and_blocked_candidate_cannot_win():
    baseline = {"a": {"status": "FAIL", "tests": {"one": "FAIL", "two": "PASS"}}}
    missing = {"a": {"status": "PASS", "tests": {"one": "PASS"}}}
    assert not evo.improvement(baseline, missing)[0]
    assert not evo.improvement(baseline, baseline)[0]
    blocked = {"a": {"status": "BLOCKED", "tests": {"one": "PASS", "two": "PASS"}}}
    assert not evo.improvement(baseline, blocked)[0]


@pytest.mark.parametrize("url", ["https://example.com/v1", "http://localhost/v1", "http://127.0.0.1.evil/v1",
                                 "http://user@127.0.0.1/v1", "http://127.0.0.1/v1?x=1"])
def test_local_only_endpoint_refuses_remote_or_ambiguous_hosts(url):
    with pytest.raises(ValueError):
        evo.LocalProposer(url, "model")


def test_claude_has_no_execution_tools_and_checks_success(tmp_path, monkeypatch):
    captured = []

    def command(argv, cwd, **kw):
        captured.append(argv)
        return 0, json.dumps({"subtype": "success", "structured_output": proposal(), "total_cost_usd": 0.1})

    monkeypatch.setattr(evo, "command", command)
    assert evo.ClaudeProposer()("prompt", tmp_path, 0.5, 30)[0] == proposal()
    argv = captured[0]
    assert argv[argv.index("--tools") + 1] == ""
    assert "mcp__*" in argv and "--safe-mode" in argv
    assert not any("skip-permission" in arg for arg in argv)
    monkeypatch.setattr(evo, "command", lambda *a, **k: (0, '{"subtype":"error_max_turns"}'))
    with pytest.raises(ValueError):
        evo.ClaudeProposer()("prompt", tmp_path, 0.5, 30)


def test_nan_budget_dirty_source_and_changed_suite_refused(project):
    repo, suite, work = project
    with pytest.raises(ValueError):
        host_run(repo, suite, work, max_usd=math.nan)
    host_run(repo, suite, work)
    with pytest.raises(ValueError, match="Base/suite changed"):
        host_run(repo, {**suite, "fingerprint": "new"}, work)
    (repo / "calc.py").write_text("USER_UNCOMMITTED_WORK\n")
    with pytest.raises(ValueError, match="source changes"):
        host_run(repo, suite, work)
    assert (repo / "calc.py").read_text() == "USER_UNCOMMITTED_WORK\n"


def test_default_curriculum_paths_are_real_and_not_editable_tests():
    suite = evo.load_suite(ROOT, ROOT / "config/evolution/owner-v1.1.json")
    assert len(suite["cases"]) == 4
    assert len(suite["fingerprint"]) == 64


def test_test_environment_does_not_inherit_secrets(project, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("PYTEST_ADDOPTS", "--ignore=test_target.py")
    env = evo.test_environment(project[0])
    assert "ANTHROPIC_API_KEY" not in env and "PYTEST_ADDOPTS" not in env
    assert env["LOCAL_ONLY"] == "1"


def test_docker_uses_offline_readonly_limited_container(tmp_path):
    args = evo.docker_test_command(tmp_path / "src", tmp_path / "out", ["test_one.py"], "sha256:abc", "test-container")
    for flag in ("--network=none", "--read-only", "--pull=never", "--cap-drop=ALL", "--memory=4g", "--pids-limit=128"):
        assert flag in args
    assert any("dst=/src,readonly" in a for a in args)
    assert "sha256:abc" in args
    assert not any("docker.sock" in a for a in args)


def test_stop_file_prevents_any_evaluation(project):
    repo, suite, work = project
    work.mkdir()
    (work / "STOP").touch()
    result = host_run(repo, suite, work, proposer=lambda *a: pytest.fail("stopped"))
    assert result["status"] == "STOPPED" and not result["attempts"]


def test_export_rejects_unapproved_task_and_invalid_verified_data(tmp_path):
    class Store:
        def verified(self):
            return [{"learning_status": "VERIFIED", "task": "holdout", "teach_local_model": ["secret answer"]}]
    assert evo.export_verified(Store(), tmp_path / "train.jsonl", {"repair"}) == 0
    assert (tmp_path / "train.jsonl").read_text() == ""
