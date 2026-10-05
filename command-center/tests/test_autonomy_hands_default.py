"""build_default_broker: the cycle's real hands (routed worktree, suites, SHA check, level cap)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from bcc.autonomy.goals import GoalStore
from bcc.autonomy.hands import MAX_LEVEL, build_default_broker
from bcc.autonomy.journal import Journal
from bcc.autonomy.types import Budget, Goal, HandRequest

OK = SimpleNamespace(ok=True, reason="pinned")


def git(cwd: Path, *args: str) -> str:
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@example.invalid"}
    import os
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          env={**os.environ, **env}).stdout.strip()


@pytest.fixture
def setup(tmp_path):
    root = tmp_path / "autonomy"
    wt = root / "cycles" / "JEFF-0042" / "w1-a1" / "worktree"
    tests = wt / "command-center" / "tests"
    tests.mkdir(parents=True)
    (tests / "test_ok.py").write_text("def test_ok():\n    assert 1 + 1 == 2\n\n\ndef test_bad():\n    assert False\n")
    git(wt, "init", "-q")
    git(wt, "add", "-A")
    git(wt, "commit", "-q", "-m", "c")
    sha = git(wt, "rev-parse", "HEAD")
    journal = Journal(root)
    GoalStore(root, journal).create(Goal(
        goal_id="JEFF-0042", problem="p", desired_result="d", constraints=(),
        acceptance_tests=("pytest:command-center/tests/test_ok.py::test_ok",),
        budget=Budget(max_minutes=60, max_agent_turns=3), risk_tier="docs_tests", target_metric="identity.leaks",
        protected_metrics=()))
    broker = build_default_broker(root, journal, constitution_status=lambda: OK)
    return SimpleNamespace(root=root, wt=wt, sha=sha, broker=broker, journal=journal)


def run_tests(s, **args):
    base = {"suite": "acceptance", "worktree": str(s.wt), "sha": s.sha,
            "acceptance_tests": ["pytest:command-center/tests/test_ok.py::test_ok"]}
    return s.broker.execute(HandRequest(goal_id="JEFF-0042", requested_by="jev", action="run_tests",
                                        target="isolated_worktree", arguments={**base, **args},
                                        expected_evidence=("exit_code", "stdout_hash", "report_path"),
                                        risk_class="low", timeout_s=600, rollback="none (read-only test run)"))


def test_acceptance_suite_runs_pytest_in_the_worktree_with_a_report(setup):
    res = run_tests(setup)
    assert res.ok, res
    assert set(res.artifacts) >= {"stdout", "stderr", "report_path"}
    prepared = setup.journal.entries(kind="hand.prepared")[-1]["payload"]["arguments"]
    assert prepared["argv"][:3] == [sys.executable, "-m", "pytest"]
    assert Path(prepared["report_path"]).is_file()
    assert not (setup.wt / ".pytest_cache").exists()
    assert git(setup.wt, "status", "--porcelain") == ""          # the candidate stays clean


def test_failing_acceptance_test_is_not_ok(setup):
    res = run_tests(setup, acceptance_tests=["pytest:command-center/tests/test_ok.py::test_bad"])
    assert not res.ok and res.exit_code == 1


@pytest.mark.parametrize("args,needle", [
    ({"sha": "f" * 40}, "candidate SHA changed"),
    ({"suite": "vibes"}, "unknown test suite"),
    ({"acceptance_tests": ["identity leaks == 0"]}, "no executable"),
    ({"acceptance_tests": ["pytest:tests/missing.py"]}, "missing in the candidate"),
    ({"worktree": ""}, "no isolated worktree"),
])
def test_refusals(setup, args, needle):
    res = run_tests(setup, **args)
    assert not res.ok and needle in res.refused_reason, res


def test_worktree_outside_the_cycle_root_is_refused(setup, tmp_path):
    other = tmp_path / "elsewhere"
    other.mkdir()
    git(other, "init", "-q")
    res = run_tests(setup, worktree=str(other))
    assert not res.ok and "not an isolated cycle worktree" in res.refused_reason
    res = run_tests(setup, worktree=str(setup.root / "cycles"))
    assert not res.ok


def test_level_is_capped_and_release_needs_the_user(setup):
    b = build_default_broker(setup.root, setup.journal, level="L4", constitution_status=lambda: OK)
    assert b.level == MAX_LEVEL == "L2"
    for action in ("apply_candidate", "rollback"):
        res = b.execute(HandRequest(goal_id="JEFF-0042", requested_by="jev", action=action,
                                    target="release_candidate", arguments={"worktree": str(setup.wt)},
                                    expected_evidence=("exit_code",), risk_class="medium", timeout_s=60,
                                    rollback="restore base"))
        assert not res.ok and res.exit_code is None, action


def test_writer_actions_need_the_lease(setup):
    res = setup.broker.execute(HandRequest(goal_id="JEFF-0042", requested_by="claude", action="write_file",
                                           target="docs/x.md", arguments={"worktree": str(setup.wt),
                                                                          "path": "docs/x.md", "content": "x"},
                                           expected_evidence=(), risk_class="low", timeout_s=60,
                                           rollback="git restore docs/x.md"))
    assert not res.ok and "lease" in res.refused_reason
    lease = setup.broker.lease
    t = lease.acquire("JEFF-0042", "claude", ttl_s=60)
    try:
        res = setup.broker.execute(HandRequest(goal_id="JEFF-0042", requested_by="claude", action="write_file",
                                               target="docs/x.md", arguments={"worktree": str(setup.wt),
                                                                              "path": "docs/x.md", "content": "x"},
                                               expected_evidence=("written",), risk_class="low", timeout_s=60,
                                               rollback="git restore docs/x.md"))
        assert res.ok and (setup.wt / "docs" / "x.md").read_text() == "x"
    finally:
        lease.release(t)
