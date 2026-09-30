"""Authority matrix: constitution + level + release tier + scope; always-user actions; fail closed."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from bcc.autonomy.policy import ALWAYS_USER, Policy, Scope, classify_path
from bcc.autonomy.types import Budget, Goal, HandRequest

OK = SimpleNamespace(ok=True, reason="pinned")


def goal(tier="docs_tests", minutes=60) -> Goal:
    return Goal(goal_id="JEFF-0042", problem="p", desired_result="d", constraints=(),
                acceptance_tests=("pytest:tests/x.py",), budget=Budget(max_minutes=minutes, max_agent_turns=3),
                risk_tier=tier, target_metric="identity.leaks", protected_metrics=())


def req(action="run_tests", target="worktree", by="claude", risk="low", timeout=60, rollback="git restore", **args):
    return HandRequest(goal_id="JEFF-0042", requested_by=by, action=action, target=target, arguments=args,
                       expected_evidence=("exit_code",), risk_class=risk, timeout_s=timeout, rollback=rollback)


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


@pytest.fixture
def wt(tmp_path):
    w = tmp_path / "wt"
    (w / "docs").mkdir(parents=True)
    return w


def pol(wt, level="L2", status=OK, clock=None, **scope):
    return Policy(Scope(worktree=wt, **scope), constitution_status=status, level=level, clock=clock or Clock())


def test_unpinned_constitution_blocks_everything(wt):
    p = pol(wt, status=SimpleNamespace(ok=False, reason="constitution is not pinned"))
    d = p.check(req(argv=["python", "-m", "pytest"]), goal())
    assert not d.allowed and d.needs_user and d.reason.startswith("BLOCKED")


@pytest.mark.parametrize("action", sorted(ALWAYS_USER))
def test_always_user_actions_need_the_user_at_every_level(wt, action):
    for level in ("L0", "L4"):
        d = pol(wt, level).check(req(action=action), goal())
        assert not d.allowed and d.needs_user, (action, level)


def test_unknown_action_fails_closed(wt):
    d = pol(wt, "L4").check(req(action="teleport"), goal())
    assert not d.allowed and not d.needs_user and "fail closed" in d.reason


def test_foreign_goal_and_malformed_request(wt):
    assert not pol(wt).check(req(argv=["pytest"]), None).allowed
    other = Goal(**{**goal().__dict__, "goal_id": "JEFF-0001"})
    assert "outside the active goal" in pol(wt).check(req(argv=["pytest"]), other).reason
    assert "malformed" in pol(wt).check(req(timeout=0, argv=["pytest"]), goal()).reason
    assert "malformed" in pol(wt).check(req(rollback="", argv=["pytest"]), goal()).reason


def test_levels(wt):
    assert not pol(wt, "L0").check(req(argv=["pytest"]), goal()).allowed
    assert pol(wt, "L0").check(req(action="read_file", path="docs/a.md"), goal()).allowed
    assert pol(wt, "L1").check(req(argv=["pytest", "-q"]), goal()).allowed
    assert not pol(wt, "L1").check(req(action="prepare_candidate"), goal()).allowed
    assert pol(wt, "L2").check(req(action="prepare_candidate"), goal()).allowed
    assert not pol(wt, "L9").check(req(argv=["pytest"]), goal()).allowed


def test_release_tiers(wt):
    assert not pol(wt, "L2").check(req(action="apply_release"), goal()).allowed
    assert pol(wt, "L3").check(req(action="apply_release"), goal("docs_tests")).allowed
    for tier in ("prompts_models", "memory_keys_telegram_services", "critical_runtime"):
        d = pol(wt, "L4").check(req(action="apply_release"), goal(tier))
        assert not d.allowed and d.needs_user, tier
    assert not pol(wt, "L3").check(req(action="rollback_release"), goal()).allowed
    assert pol(wt, "L4").check(req(action="rollback_release"), goal()).allowed


@pytest.mark.parametrize("path", ["../outside.txt", "/etc/passwd", "C:/Windows/system.ini", "docs/../../x",
                                  "..\\outside.txt", "C:\\Windows\\system.ini"])
def test_paths_outside_the_worktree_are_refused(wt, path):
    d = pol(wt).check(req(action="write_file", path=path), goal())
    assert not d.allowed and "outside" in d.reason


@pytest.mark.parametrize("path", ["docs/constitution/BOSSMAN_CONSTITUTION.md", "command-center/bcc/autonomy/policy.py",
                                  ".github/workflows/ci.yml", ".git/hooks/pre-commit",
                                  "docs\\constitution\\BOSSMAN_CONSTITUTION.md"])
def test_gate_files_need_the_user(wt, path):
    d = pol(wt).check(req(action="write_file", path=path), goal("critical_runtime"))
    assert not d.allowed and d.needs_user


def test_extra_protected_paths(wt):
    pin = wt / "local" / "pin"
    d = pol(wt, extra_protected=(pin.parent,)).check(req(action="write_file", path="local/pin"), goal("critical_runtime"))
    assert d.needs_user


def test_write_above_goal_tier_is_refused(wt):
    p = pol(wt)
    assert p.check(req(action="write_file", path="docs/guide.md"), goal("docs_tests")).allowed
    assert p.check(req(action="write_file", path="docs\\guide.md"), goal("docs_tests")).allowed
    assert p.check(req(action="write_file", path="command-center/tests/test_x.py"), goal("docs_tests")).allowed
    d = p.check(req(action="write_file", path="command-center/bcc/engine.py"), goal("docs_tests"))
    assert not d.allowed and "critical_runtime" in d.reason
    assert not p.check(req(action="write_file", path="bcc/telegram_companion/bot.py"), goal("prompts_models")).allowed
    assert p.check(req(action="write_file", path="bcc/jev/prompts/system.txt"), goal("prompts_models")).allowed


def test_classify_path():
    assert classify_path("docs/a.md") == "docs_tests"
    assert classify_path("command-center/tests/test_telegram.py") == "docs_tests"
    assert classify_path("bcc/telegram_companion/x.py") == "memory_keys_telegram_services"
    assert classify_path("config/models.json") == "prompts_models"
    assert classify_path("bcc/engine.py") == "critical_runtime"


@pytest.mark.parametrize("argv,needs_user", [
    (["bash", "-c", "pytest"], False),
    (["cmd.exe", "/c", "dir"], False),
    (["powershell", "-Command", "x"], False),
    (["rm", "-rf", "."], True),
    (["curl", "https://example.com"], True),
    (["git", "push", "origin", "main"], True),
    (["git", "reset", "--hard"], True),
    (["git", "clean", "-fdx"], True),
    (["git", "branch", "-D", "x"], True),
    (["git", "-c", "core.hooksPath=/x", "commit"], False),
    (["git", "bisect"], False),
    (["python", "-c", "print(1)"], False),
    (["python", "-m", "pip", "install", "x"], False),
    (["python", "../escape.py"], False),
    (["node", "C:/other/tool.js"], False),
    (["make", "all"], False),
    ("pytest -q", False),
    ([], False),
])
def test_argv_refusals(wt, argv, needs_user):
    d = pol(wt).check(req(argv=argv), goal())
    assert not d.allowed and d.needs_user is needs_user, (argv, d)


@pytest.mark.parametrize("argv", [["python", "-m", "pytest", "-q", "tests"], ["pytest", "-q"],
                                  ["git", "status"], ["git", "diff", "--stat"], ["npm", "test"],
                                  [r"C:\Python312\python.exe", "-m", "pytest"]])
def test_argv_allowed(wt, argv):
    assert pol(wt).check(req(argv=argv), goal()).allowed, argv


def test_git_commit_action_shape(wt):
    assert pol(wt).check(req(action="git_commit", argv=["git", "commit", "-m", "x"]), goal()).allowed
    assert not pol(wt).check(req(action="git_commit", argv=["git", "status"]), goal()).allowed


def test_time_budget_and_goal_budget(wt):
    clock = Clock()
    p = pol(wt, clock=clock, time_budget_s=100)
    assert p.check(req(argv=["pytest"], timeout=90), goal()).allowed
    clock.t = 50
    assert "time budget" in p.check(req(argv=["pytest"], timeout=90), goal()).reason
    assert "goal budget" in pol(wt).check(req(argv=["pytest"], timeout=600), goal(minutes=5)).reason


def test_high_risk_and_jeff(wt):
    assert pol(wt).check(req(argv=["pytest"], risk="high"), goal()).needs_user
    assert not pol(wt).check(req(argv=["pytest"], by="jeff"), goal()).allowed
    assert pol(wt, "L0").check(req(action="read_logs", by="jeff", path="docs"), goal()).allowed
