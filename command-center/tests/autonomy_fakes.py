"""Test-only fakes for the autonomy Line B tests.

* A copy of the contract types (docs/autonomy/AUTONOMY_CONTRACT.md + owner update:
  new GoalState list, HandRequest.timeout_s / rollback) installed as
  ``bcc.autonomy.types`` ONLY when Line A's real module is not present yet.
* Protocol-shaped fakes of the Line A APIs: GoalStore, EngineeringLease,
  HandBroker, Journal, StagingRunner, metrics_gate.decide, constitution.verify.
* Scripted fake Claude/Codex CLIs (a ProcessRunner) - no real process, model or
  network is ever started.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import re
import subprocess
import sys
import types as _types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

# ------------------------------------------------------------------ contract types (fallback)

try:
    importlib.import_module("bcc.autonomy.types")
except ImportError:
    _mod = _types.ModuleType("bcc.autonomy.types")
    _mod.__doc__ = "TEST FALLBACK copy of the Line A contract types (tests/autonomy_fakes.py)."

    GoalState = Literal["PROPOSED", "PLANNED", "BUILDING", "TESTING", "CLAUDE_REVIEW", "CODEX_REVIEW", "STAGING",
                        "USER_APPROVAL", "DEPLOYED", "MONITORING", "COMPLETE", "ROLLED_BACK", "BLOCKED"]
    RiskTier = Literal["docs_tests", "prompts_models", "memory_keys_telegram_services", "critical_runtime"]

    @dataclass(frozen=True)
    class Budget:
        max_minutes: int
        max_agent_turns: int
        max_cost_usd: float = 0.0

    @dataclass(frozen=True)
    class Goal:
        goal_id: str
        problem: str
        desired_result: str
        constraints: tuple
        acceptance_tests: tuple
        budget: Budget
        risk_tier: str
        target_metric: str
        protected_metrics: tuple

    @dataclass(frozen=True)
    class HandRequest:
        goal_id: str
        requested_by: str
        action: str
        target: str
        arguments: dict
        expected_evidence: tuple
        risk_class: str
        timeout_s: int = 120
        rollback: str = ""

    @dataclass(frozen=True)
    class HandResult:
        request_hash: str
        ok: bool
        exit_code: int | None
        started_at: str
        finished_at: str
        artifacts: dict
        refused_reason: str = ""

    @dataclass(frozen=True)
    class Review:
        goal_id: str
        reviewer: str
        sha: str
        diff_sha256: str
        verdict: str
        notes: str

    for _name, _obj in dict(GoalState=GoalState, RiskTier=RiskTier, Budget=Budget, Goal=Goal,
                            HandRequest=HandRequest, HandResult=HandResult, Review=Review).items():
        setattr(_mod, _name, _obj)
    sys.modules["bcc.autonomy.types"] = _mod
    import bcc.autonomy as _pkg

    _pkg.types = _mod  # type: ignore[attr-defined]

from bcc.autonomy.types import Budget, Goal, HandRequest, HandResult  # noqa: E402

LEGAL = {
    "PROPOSED": {"PLANNED", "BLOCKED"},
    "PLANNED": {"BUILDING", "BLOCKED"},
    "BUILDING": {"TESTING", "BLOCKED"},
    "TESTING": {"CLAUDE_REVIEW", "BLOCKED"},
    "CLAUDE_REVIEW": {"CODEX_REVIEW", "BUILDING", "BLOCKED"},
    "CODEX_REVIEW": {"STAGING", "BUILDING", "BLOCKED"},
    "STAGING": {"USER_APPROVAL", "DEPLOYED", "BLOCKED"},
    "USER_APPROVAL": {"DEPLOYED", "BUILDING", "COMPLETE", "BLOCKED"},
    "DEPLOYED": {"MONITORING", "BLOCKED"},
    "MONITORING": {"COMPLETE", "ROLLED_BACK", "BLOCKED"},
    "BLOCKED": set(), "COMPLETE": set(), "ROLLED_BACK": set(),
}


def sha(data: Any) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


class IllegalTransition(ValueError):
    pass


class FakeGoalStore:
    def __init__(self, journal: "FakeJournal | None" = None):
        self.records: dict[str, dict] = {}
        self.journal = journal

    def create(self, goal: Goal) -> dict:
        if goal.goal_id in self.records:
            raise ValueError("exists")
        self.records[goal.goal_id] = {"goal": goal, "state": "PROPOSED", "history": [("PROPOSED", {})]}
        return self.records[goal.goal_id]

    def get(self, goal_id: str) -> dict:
        return self.records[goal_id]

    def transition(self, goal_id: str, new_state: str, evidence: Any) -> dict:
        rec = self.records[goal_id]
        if new_state not in LEGAL[rec["state"]]:
            raise IllegalTransition(f"{rec['state']} -> {new_state}")
        rec["state"] = new_state
        rec["history"].append((new_state, evidence))
        if self.journal is not None:
            self.journal.append("goal_transition", {"goal_id": goal_id, "state": new_state})
        return rec

    def list(self) -> list[dict]:
        return list(self.records.values())


class LeaseBusy(RuntimeError):
    pass


class FakeLease:
    def __init__(self) -> None:
        self.holder: str | None = None
        self.history: list[tuple[str, str]] = []
        self.max_concurrent = 0
        self._n = 0

    def acquire(self, goal_id: str, holder: str, ttl_s: int) -> str:
        if self.holder is not None:
            raise LeaseBusy(f"held by {self.holder}")
        self.holder = holder
        self._n += 1
        self.history.append(("acquire", holder))
        self.max_concurrent = max(self.max_concurrent, 1)
        return f"lease-{self._n}"

    def release(self, token: str) -> None:
        self.history.append(("release", self.holder or "?"))
        self.holder = None

    def current(self) -> str | None:
        return self.holder


class FakeJournal:
    def __init__(self) -> None:
        self.entries: list[dict] = []
        self.head = "0" * 64

    def append(self, kind: str, payload: dict) -> str:
        body = json.dumps({"kind": kind, "payload": payload, "prev": self.head}, sort_keys=True, default=str)
        self.head = hashlib.sha256(body.encode()).hexdigest()
        self.entries.append({"kind": kind, "payload": payload, "hash": self.head})
        return self.head

    def kinds(self, goal_id: str | None = None) -> list[str]:
        return [e["kind"] for e in self.entries if goal_id is None or e["payload"].get("goal_id") == goal_id]

    def of(self, kind: str, goal_id: str | None = None) -> list[dict]:
        return [e["payload"] for e in self.entries if e["kind"] == kind
                and (goal_id is None or e["payload"].get("goal_id") == goal_id)]

    def verify(self) -> bool:
        return True


ALLOWED_ACTIONS = {"run_tests", "apply_candidate", "rollback", "read_file", "list_dir"}


class FakeHandBroker:
    """Policy-shaped: only known actions, only the active goals; everything recorded."""

    def __init__(self, active_goals: set[str] | None = None):
        self.requests: list[HandRequest] = []
        self.active_goals = active_goals
        self.fail: dict[tuple[str, str], dict] = {}        # (goal_id, action[:suite]) -> overrides
        self.refused: list[tuple[HandRequest, str]] = []

    def execute(self, req: HandRequest) -> HandResult:
        self.requests.append(req)
        h = sha({"goal": req.goal_id, "action": req.action, "target": req.target, "args": req.arguments,
                 "n": len(self.requests)})
        reason = ""
        if req.action not in ALLOWED_ACTIONS:
            reason = f"action {req.action} not allowed"
        elif self.active_goals is not None and req.goal_id not in self.active_goals:
            reason = "goal not active"
        if reason:
            self.refused.append((req, reason))
            return HandResult(h, False, None, "t0", "t1", {}, reason)
        key = (req.goal_id, req.action + (":" + str(req.arguments.get("suite")) if "suite" in req.arguments else ""))
        over = self.fail.get(key) or self.fail.get((req.goal_id, req.action)) or {}
        arts = {"stdout_hash": sha([h, "out"]), "report_path": sha([h, "rep"])}
        if over.get("no_artifacts"):
            arts = {}
        return HandResult(h, over.get("ok", True), over.get("exit_code", 0), "t0", "t1", arts)


class FakeStaging:
    def __init__(self, result: Any = None):
        self.result = {"ok": True} if result is None else result
        self.calls: list[str] = []

    def run(self, sha_: str, checks: Any) -> Any:
        self.calls.append(sha_)
        return self.result


def fake_decide(before: dict, after: dict, target: str, protected: tuple, thresholds: dict) -> dict:
    improved = after.get(target, 0) < before.get(target, 0)
    degraded = [m for m in protected if after.get(m, 0) > before.get(m, 0) + thresholds.get(m, 0)]
    return {"accepted": improved and not degraded, "degraded": degraded}


@dataclass
class ConstitutionStatus:
    ok: bool
    sha: str = "c" * 64
    pinned_sha: str = "c" * 64
    reason: str = ""


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
    (repo / "command-center" / "tests" / "test_a.py").write_text("def test_a():\n    assert True\n",
                                                                 encoding="utf-8")
    git(repo.parent, "init", "-q", str(repo))
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base")
    return repo


def make_goal(gid: str = "G-1", *, tier: str = "prompts_models", paths: tuple[str, ...] = ("docs/**",),
              turns: int = 12, minutes: int = 60, rollback: bool = True, problem: str = "docs are stale") -> Goal:
    cons = tuple(f"path:{p}" for p in paths) + (("rollback:revert the candidate commit",) if rollback else ())
    return Goal(goal_id=gid, problem=problem, desired_result="docs updated", constraints=cons,
                acceptance_tests=("pytest command-center/tests/test_a.py exits 0",),
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
    edits: dict = field(default_factory=dict)      # relative path -> content (writers; reviewers to test writes)
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
