"""Test helpers for the autonomy Line B tests.

Line A is merged, so the tests use the REAL GoalStore, Journal, EngineeringLease,
Policy/HandBroker, StagingRunner and metrics gate. Fakes remain only for external
processes: the Claude/Codex CLIs (a scripted ProcessRunner), the hand executor
(no pytest subprocess / release command is started) and the staging launcher (no
candidate server is started). No real process, model or network is ever used.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from bcc.autonomy.goals import GoalStore
from bcc.autonomy.hands import PROTECTED_SUITE, ExecOutcome, HandBroker, RoutedPolicy
from bcc.autonomy.journal import Journal
from bcc.autonomy.lease import EngineeringLease
from bcc.autonomy.staging import StagingRunner
from bcc.autonomy.types import Budget, Goal

OK_CONSTITUTION = SimpleNamespace(ok=True, sha="c" * 64, pinned_sha="c" * 64, reason="")


# ------------------------------------------------------------------ journal helpers


def of(journal: Journal, kind: str, goal_id: str | None = None) -> list[dict]:
    return [e["payload"] for e in journal.entries(goal_id=goal_id) if e["kind"] == kind]


def kinds(journal: Journal, goal_id: str | None = None) -> list[str]:
    return [e["kind"] for e in journal.entries(goal_id=goal_id)]


# ------------------------------------------------------------------ external-process fakes


class FakeExecutor:
    """Stands in for the subprocess executor: writes the JUnit report the policy asked for.
    ``fail[(goal_id, action or action:suite)] = {"exit_code": 1}`` / ``{"no_report": True}``."""

    def __init__(self) -> None:
        self.calls: list[Any] = []
        self.fail: dict[tuple[str, str], dict] = {}

    def __call__(self, req, scope) -> ExecOutcome:
        self.calls.append(req)
        args = req.arguments
        key = req.action + (f":{args['suite']}" if "suite" in args else "")
        over = self.fail.get((req.goal_id, key)) or self.fail.get((req.goal_id, req.action)) or {}
        files = {}
        report = args.get("report_path")
        if report and not over.get("no_report"):
            Path(report).parent.mkdir(parents=True, exist_ok=True)
            Path(report).write_text("<testsuite tests='1' failures='0'/>", encoding="utf-8")
            files["report_path"] = Path(report)
        return ExecOutcome(over.get("exit_code", 0), b"1 passed", b"", files)


class FakeLauncher:
    """Staging launcher without a candidate process."""

    def __init__(self) -> None:
        self.started: list[str] = []

    def start(self, sha, port, data_dir, env):
        self.started.append(sha)
        return {"checkout": None, "sha": sha}

    def stop(self, handle) -> None:
        return None


def make_broker(root: Path, journal: Journal, executor: FakeExecutor, *, level: str = "L2",
                stop_check: Callable[[], str] | None = None) -> HandBroker:
    """Mirror of hands.build_default_broker with an explicit level (the default caps at L2)."""
    policy = RoutedPolicy([root / "cycles"], root / "reports", constitution_status=OK_CONSTITUTION, level=level,
                          time_budget_s=10 * 24 * 3600.0)
    return HandBroker(policy, journal, executor, goals=GoalStore(root, journal), level=level,
                      lease=EngineeringLease(root, journal=journal), stop_check=stop_check)


def make_staging(root: Path, journal: Journal, owner: Path, checks: tuple[str, ...],
                 results: dict[str, bool] | None = None) -> StagingRunner:
    results = results or {}
    probes = {name: (lambda h, c, n=name: (results.get(n, True), f"{n} fake probe")) for name in checks}
    owner.mkdir(parents=True, exist_ok=True)
    return StagingRunner(FakeLauncher(), probes, owner_data_dir=owner, journal=journal)


# ------------------------------------------------------------------ repos and goals


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def make_repo(root: Path) -> Path:
    repo = root / "src-repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "command-center" / "tests").mkdir(parents=True)
    (repo / "command-center" / "bcc").mkdir(parents=True)
    (repo / "docs" / "README.md").write_text("# docs\n", encoding="utf-8")
    (repo / "command-center" / "bcc" / "runtime.py").write_text("X = 1\n", encoding="utf-8")
    for rel in ("tests/test_a.py", *PROTECTED_SUITE):
        (repo / "command-center" / rel).write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    git(repo.parent, "init", "-q", str(repo))
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base")
    return repo


def make_goal(gid: str = "G-1", *, tier: str = "prompts_models", paths: tuple[str, ...] = ("docs/**",),
              turns: int = 12, minutes: int = 60, rollback: bool = True, problem: str = "docs are stale") -> Goal:
    cons = tuple(f"path:{p}" for p in paths) + (("rollback:revert the candidate commit",) if rollback else ())
    return Goal(goal_id=gid, problem=problem, desired_result="docs updated", constraints=cons,
                acceptance_tests=("pytest:command-center/tests/test_a.py",),
                budget=Budget(max_minutes=minutes, max_agent_turns=turns), risk_tier=tier,
                target_metric="m.errors", protected_metrics=("latency_ms",))


# ------------------------------------------------------------------ fake CLIs


@dataclass
class Call:
    agent: str
    role: str
    cwd: Path
    prompt: str


@dataclass
class Act:
    text: str = ""
    edits: dict = field(default_factory=dict)      # relative path -> content
    timed_out: bool = False
    exit_code: int = 0
    usage: dict = field(default_factory=lambda: {"input_tokens": 1000, "output_tokens": 100})


def parse_review_prompt(prompt: str) -> tuple[str, str]:
    s = re.search(r"Candidate sha: (\w+)", prompt)
    d = re.search(r"diff_sha256: (\w+)", prompt)
    return (s.group(1) if s else ""), (d.group(1) if d else "")


def verdict(prompt: str, v: str = "APPROVE", notes: str = "ok") -> str:
    s, d = parse_review_prompt(prompt)
    return "Review done.\n" + json.dumps({"verdict": v, "notes": notes, "sha": s, "diff_sha256": d})


def done(summary: str = "edited", **extra: Any) -> str:
    return "Work finished.\n" + json.dumps({"status": "done", "summary": summary, **extra})


def goal_of(prompt: str) -> str:
    m = re.search(r"\(goal ([\w.-]+)\)", prompt) or re.search(r"REVIEW \(read-only\) of goal ([\w.-]+)\.", prompt)
    return m.group(1) if m else ""


def default_writer(call: Call) -> Act:
    return Act(text=done(), edits={"docs/README.md": f"# docs\nchanged by {call.agent} for {goal_of(call.prompt)}\n"
                                                     f"{len(call.prompt)}\n"})


def default_reviewer(call: Call) -> Act:
    return Act(text=verdict(call.prompt))


class ScriptedCLIs:
    """ProcessRunner that plays Claude/Codex. `script(call) -> Act` decides per call."""

    def __init__(self, writer: Callable[[Call], Act] = default_writer,
                 reviewer: Callable[[Call], Act] = default_reviewer):
        self.writer, self.reviewer = writer, reviewer
        self.calls: list[Call] = []
        self.active_writers = 0
        self.max_active_writers = 0
        self.envs: list[dict] = []

    async def run(self, argv: list[str], *, cwd: Path, stdin: bytes, timeout: float, env: dict | None):
        from bcc.rave.connectors import ProcResult
        agent = "claude" if argv[0] == "fake-claude" else "codex"
        if agent == "claude":
            role = "writer" if argv[argv.index("--permission-mode") + 1] == "acceptEdits" else "reviewer"
        else:
            role = "writer" if argv[argv.index("--sandbox") + 1] == "workspace-write" else "reviewer"
        call = Call(agent, role, Path(cwd), stdin.decode("utf-8"))
        self.calls.append(call)
        self.envs.append(dict(env or {}))
        if role == "writer":
            self.active_writers += 1
            self.max_active_writers = max(self.max_active_writers, self.active_writers)
        try:
            act = (self.writer if role == "writer" else self.reviewer)(call)
            for rel, content in act.edits.items():
                p = Path(cwd) / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content, encoding="utf-8")
            if act.timed_out:
                return ProcResult(None, b"", b"killed", True)
            if agent == "claude":
                out = json.dumps({"result": act.text, "is_error": False, "usage": act.usage}).encode()
            else:
                last = Path(argv[argv.index("-o") + 1])
                last.write_text(act.text, encoding="utf-8")
                out = (json.dumps({"type": "turn", "usage": act.usage}) + "\n").encode()
            return ProcResult(act.exit_code, out, b"", False)
        finally:
            if role == "writer":
                self.active_writers -= 1


def install_fake_clis(monkeypatch: Any) -> None:
    monkeypatch.setenv("BOSSMAN_RAVE_CLAUDE_CMD", json.dumps(["fake-claude"]))
    monkeypatch.setenv("BOSSMAN_RAVE_CODEX_CMD", json.dumps(["fake-codex"]))
