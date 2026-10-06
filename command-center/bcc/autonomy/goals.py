"""Persistent goal store and state machine (docs/autonomy/AUTONOMY_CONTRACT.md "State machine").

One JSON file per goal (``<root>/goals/<GOAL-ID>.json``), written atomically;
every mutation is serialised on a cross-process lock and journaled. A new
``GoalStore`` over the same root resumes exactly where the previous process
stopped. Illegal transitions raise ``TransitionError``; unmet guards raise
``GuardError``. Owner rule: any rejection / timeout / disagreement / changed
SHA / missing evidence / ambiguity puts the goal in BLOCKED, and any revision
invalidates both approvals.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from . import schemas
from .journal import Journal, atomic_write_bytes, canonical, file_lock, sha256_bytes, utc_now
from .policy import CANONICAL_TARGET_BRANCH, scope_violations
from .types import GOAL_STATES, Goal, Review

TERMINAL = frozenset({"COMPLETE"})
REVISION_SOURCES = frozenset({"TESTING", "CLAUDE_REVIEW", "CODEX_REVIEW", "STAGING", "USER_APPROVAL"})

TRANSITIONS: dict[str, frozenset[str]] = {
    "PROPOSED": frozenset({"PLANNED", "BLOCKED"}),
    "PLANNED": frozenset({"BUILDING", "BLOCKED"}),
    "BUILDING": frozenset({"TESTING", "BLOCKED"}),
    "TESTING": frozenset({"CLAUDE_REVIEW", "BUILDING", "BLOCKED"}),
    "CLAUDE_REVIEW": frozenset({"CODEX_REVIEW", "BUILDING", "BLOCKED"}),
    "CODEX_REVIEW": frozenset({"STAGING", "BUILDING", "BLOCKED"}),
    "STAGING": frozenset({"USER_APPROVAL", "DEPLOYED", "BUILDING", "BLOCKED"}),   # DEPLOYED: docs_tests auto tier
    "USER_APPROVAL": frozenset({"DEPLOYED", "BUILDING", "COMPLETE", "BLOCKED"}),   # COMPLETE = user Reject
    "DEPLOYED": frozenset({"MONITORING", "ROLLED_BACK", "BLOCKED"}),
    "MONITORING": frozenset({"COMPLETE", "ROLLED_BACK", "BLOCKED"}),
    "ROLLED_BACK": frozenset({"PLANNED", "BLOCKED"}),
    "COMPLETE": frozenset(),
    "BLOCKED": frozenset({"PLANNED"}),          # + resume to the state it was blocked from
}
assert set(TRANSITIONS) == set(GOAL_STATES)

_EXEC_REF = re.compile(r"^(pytest|check|cmd|probe|metric|script|test):\S+")
_MEASURABLE = re.compile(r"(\d|<=|>=|==|!=|<|>|\b(zero|none|all|every|no)\b)", re.I)
_HISTORY_KEEP = 500
_SHA = re.compile(r"[0-9a-f]{40}([0-9a-f]{24})?")
_HEX64 = re.compile(r"[0-9a-f]{64}")


class GoalError(ValueError):
    pass


class GoalNotFound(KeyError):
    pass


class TransitionError(GoalError):
    pass


class GuardError(TransitionError):
    pass


def measurable(test: str) -> bool:
    """An acceptance test is an executable check reference (``pytest:...``,
    ``check:...``, ``cmd:...``) or a statement with a measurable criterion."""
    t = (test or "").strip()
    return bool(t) and (bool(_EXEC_REF.match(t)) or bool(_MEASURABLE.search(t)))


def validate_goal(goal: Goal) -> None:
    if not isinstance(goal, Goal):
        raise GoalError("not a Goal")
    if goal.budget is None:
        raise GoalError("refused: goal has no budget")
    try:
        schemas.validate("task", schemas.to_json(goal))
    except schemas.SchemaError as exc:
        raise GoalError(f"refused: {exc}") from None
    bad = [t for t in goal.acceptance_tests if not measurable(t)]
    if bad:
        raise GoalError(f"refused: acceptance tests are not measurable: {bad}")
    if goal.target_metric in goal.protected_metrics:
        raise GoalError("refused: the target metric cannot also be a protected metric")
    scope = [c.split(":", 1)[1].strip() for c in goal.constraints if c.lower().startswith("path:")]
    problems = scope_violations(scope)
    if problems:
        raise GoalError("refused: goal scope touches the loop's own rules: " + "; ".join(problems))


class GoalStore:
    def __init__(self, root: str | os.PathLike, journal: Journal | None = None, *,
                 clock: Callable[[], float] = time.time):
        self.root = Path(root)
        self.dir = self.root / "goals"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.journal = journal if journal is not None else Journal(self.root)
        self._clock = clock
        self._lock = self.dir / ".lock"

    # .......................................................... io
    def _path(self, goal_id: str) -> Path:
        if not isinstance(goal_id, str) or not re.fullmatch(r"[A-Z][A-Z0-9]*(-[A-Z0-9]+)+", goal_id) \
                or len(goal_id) > 64:
            raise GoalNotFound(goal_id)
        return self.dir / f"{goal_id}.json"

    def _load(self, goal_id: str) -> dict:
        p = self._path(goal_id)
        if not p.exists():
            raise GoalNotFound(goal_id)
        return json.loads(p.read_text(encoding="utf-8"))

    def _save(self, rec: dict) -> None:
        rec["version"] = int(rec.get("version", 0)) + 1
        rec["updated_at"] = utc_now()
        atomic_write_bytes(self._path(rec["goal"]["goal_id"]),
                           json.dumps(rec, ensure_ascii=False, indent=1, sort_keys=True).encode("utf-8"))

    # .......................................................... read
    def get(self, goal_id: str) -> dict:
        return self._load(goal_id)

    def goal(self, goal_id: str) -> Goal:
        return schemas.goal_from_json(self._load(goal_id)["goal"])

    def list(self, state: str | Iterable[str] | None = None) -> list[dict]:
        wanted = {state} if isinstance(state, str) else (set(state) if state else None)
        out = []
        for p in sorted(self.dir.glob("*.json")):
            try:
                rec = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if wanted is None or rec.get("state") in wanted:
                out.append(rec)
        return out

    def resumable(self) -> list[dict]:
        """Goals a restarted loop continues (everything not terminal and not BLOCKED)."""
        return [r for r in self.list() if r["state"] not in TERMINAL and r["state"] != "BLOCKED"]

    # .......................................................... create
    def create(self, goal: Goal) -> dict:
        validate_goal(goal)
        with file_lock(self._lock):
            p = self._path(goal.goal_id)
            if p.exists():
                raise GoalError(f"goal {goal.goal_id} already exists")
            now = utc_now()
            rec = {"goal": schemas.to_json(goal), "state": "PROPOSED", "version": 0, "created_at": now,
                   "created_ts": self._clock(), "blocked_from": None, "blocked_reason": "", "candidate": None,
                   "approvals": {}, "tests": None, "staging": None, "user_decision": None, "revisions": 0,
                   "usage": {"agent_turns": 0, "cost_usd": 0.0}, "history": []}
            self._save(rec)
            self.journal.append("goal.created", {"goal_id": goal.goal_id, "goal": rec["goal"],
                                                 "goal_sha256": sha256_bytes(canonical(rec["goal"]))})
            return rec

    # .......................................................... helpers
    @staticmethod
    def _bound(rec: dict, sha: str, diff: str) -> bool:
        c = rec.get("candidate") or {}
        return bool(c) and c.get("sha") == sha and c.get("diff_sha256") == diff

    @classmethod
    def _approved_by(cls, rec: dict, reviewer: str) -> bool:
        r = (rec.get("approvals") or {}).get(reviewer)
        return bool(r) and r.get("verdict") == "APPROVE" and cls._bound(rec, r.get("sha"), r.get("diff_sha256"))

    @classmethod
    def approvals_valid(cls, rec: dict) -> bool:
        return cls._approved_by(rec, "claude") and cls._approved_by(rec, "codex")

    @classmethod
    def staging_passed(cls, rec: dict) -> bool:
        s = rec.get("staging") or {}
        return bool(s.get("passed")) and cls._bound(rec, s.get("sha"), s.get("diff_sha256"))

    @classmethod
    def apply_decided(cls, rec: dict) -> bool:
        d = rec.get("user_decision") or {}
        return d.get("action") == "apply" and cls._bound(rec, d.get("sha"), d.get("diff_sha256"))

    def _invalidate(self, rec: dict, why: str) -> None:
        if rec.get("approvals") or rec.get("staging") or rec.get("user_decision") or rec.get("tests"):
            self.journal.append("goal.approvals_invalidated", {"goal_id": rec["goal"]["goal_id"], "reason": why,
                                                               "had": sorted((rec.get("approvals") or {}).keys())})
        rec["approvals"] = {}
        rec["staging"] = None
        rec["user_decision"] = None
        rec["tests"] = None

    def _guard(self, rec: dict, new: str, evidence: Mapping[str, Any]) -> None:
        c = rec.get("candidate")
        if new == "BLOCKED":
            if not str(evidence.get("reason", "")).strip():
                raise GuardError("BLOCKED needs a reason")
            return
        if new == "TESTING" and not c:
            raise GuardError("missing evidence: no candidate SHA/diff recorded")
        if new == "CLAUDE_REVIEW":
            t = rec.get("tests") or {}
            if not (t.get("passed") and self._bound(rec, t.get("sha"), t.get("diff_sha256"))):
                raise GuardError("missing evidence: tests did not pass for the current candidate")
        if new == "CODEX_REVIEW" and not self._approved_by(rec, "claude"):
            raise GuardError("Claude's APPROVE for the current SHA+diff is missing")
        if new == "STAGING" and not self.approvals_valid(rec):
            raise GuardError("both approvals for the current SHA+diff are required")
        if new == "USER_APPROVAL" and not (self.approvals_valid(rec) and self.staging_passed(rec)):
            raise GuardError("staging did not pass for the approved SHA")
        if new == "DEPLOYED":
            auto = (rec["goal"]["risk_tier"] == "docs_tests" and evidence.get("auto_tier") is True
                    and self.approvals_valid(rec) and self.staging_passed(rec))
            if rec["state"] == "STAGING" and not auto:
                raise GuardError("STAGING -> DEPLOYED only in the automatic docs/tests tier with both approvals "
                                 "and a passed staging")
            if not auto:
                if not self.apply_decided(rec):
                    raise GuardError("the user's Apply decision for the current SHA is missing")
                if evidence.get("owner_confirmed") is not True:
                    raise GuardError("the owner has not confirmed the release")
        if new == "COMPLETE":
            if rec["state"] == "USER_APPROVAL":
                if evidence.get("outcome") != "rejected_by_user":
                    raise GuardError("USER_APPROVAL -> COMPLETE only as the user's Reject (outcome rejected_by_user)")
            elif "ACCEPT" not in (evidence.get("gate"), evidence.get("verdict")):
                raise GuardError("COMPLETE needs a metrics gate ACCEPT")

    def _bind_from_evidence(self, rec: dict, new: str, evidence: Mapping[str, Any]) -> None:
        """A cycle that keeps its own review gate (Line B) may carry the binding facts in the
        transition evidence instead of calling record_*; they are recorded with the same checks."""
        gid = rec["goal"]["goal_id"]
        c = rec.get("candidate") or {}
        sha = evidence.get("sha")
        if new == "TESTING" and isinstance(sha, str) and isinstance(evidence.get("diff_sha256"), str):
            if not (_SHA.fullmatch(sha) and _HEX64.fullmatch(evidence["diff_sha256"])):
                raise GuardError("candidate evidence needs a full commit SHA and a sha256 diff hash")
            if c.get("sha") != sha or c.get("diff_sha256") != evidence["diff_sha256"]:
                self._invalidate(rec, "candidate SHA/diff changed")
            rec["candidate"] = {**c, "sha": sha, "diff_sha256": evidence["diff_sha256"],
                                "author": str(evidence.get("author") or evidence.get("writer") or c.get("author", "")),
                                "set_at": utc_now()}
            return
        if not c or sha != c.get("sha"):
            return
        bound = {"sha": c["sha"], "diff_sha256": c["diff_sha256"]}
        if new == "CLAUDE_REVIEW" and _HEX64.fullmatch(str(evidence.get("evidence_sha256", "")))                 and evidence.get("tests_passed", True) is True and not (rec.get("tests") or {}).get("passed"):
            rec["tests"] = {**bound, "passed": True, "evidence_sha256": evidence["evidence_sha256"]}
        if new == "CODEX_REVIEW" and evidence.get("claude") == "APPROVE":
            rec.setdefault("approvals", {}).setdefault("claude", {"goal_id": gid, "reviewer": "claude", **bound,
                                                                  "verdict": "APPROVE", "notes": "via transition"})
        if new in ("USER_APPROVAL", "DEPLOYED") and evidence.get("staging_ok") is True                 and rec["state"] == "STAGING" and not self.staging_passed(rec):
            rec["staging"] = {**bound, "passed": True, "via": "transition evidence"}

    def _bind_gate_approvals(self, rec: dict, evidence: Mapping[str, Any]) -> None:
        g = evidence.get("approvals")
        c = rec.get("candidate") or {}
        if not isinstance(g, Mapping) or not c:
            return
        for reviewer, v in (g.get("verdicts") or {}).items():
            key = list(v.get("key") or []) if isinstance(v, Mapping) else []
            if reviewer in ("claude", "codex") and v.get("verdict") == "APPROVE" and key[:2] == [c["sha"],
                                                                                              c["diff_sha256"]]:
                rec.setdefault("approvals", {})[reviewer] = {
                    "goal_id": rec["goal"]["goal_id"], "reviewer": reviewer, "sha": c["sha"],
                    "diff_sha256": c["diff_sha256"], "verdict": "APPROVE", "notes": str(v.get("notes", ""))[:2000],
                    "evidence_sha256": key[2] if len(key) > 2 else ""}

    def _move(self, rec: dict, new: str, evidence: Mapping[str, Any]) -> None:
        old = rec["state"]
        if new not in GOAL_STATES:
            raise TransitionError(f"unknown state {new!r}")
        allowed = set(TRANSITIONS[old])
        if old == "BLOCKED" and rec.get("blocked_from"):
            allowed.add(rec["blocked_from"])
        if new not in allowed:
            raise TransitionError(f"illegal transition {old} -> {new}")
        if new == "STAGING":
            self._bind_gate_approvals(rec, evidence)
        self._bind_from_evidence(rec, new, evidence)
        self._guard(rec, new, evidence)
        if new == "BUILDING" and old in REVISION_SOURCES:
            rec["revisions"] = int(rec.get("revisions", 0)) + 1
            self._invalidate(rec, f"revision from {old}")
        if new in ("PLANNED",) and old in ("BLOCKED", "ROLLED_BACK"):
            self._invalidate(rec, f"re-plan from {old}")
        if new == "BLOCKED":
            rec["blocked_from"] = old
            rec["blocked_reason"] = str(evidence.get("reason"))[:2000]
        elif old == "BLOCKED":
            rec["blocked_from"] = None
            rec["blocked_reason"] = ""
        rec["state"] = new
        if old == "PROPOSED" and new == "PLANNED" and rec.get("started_ts") is None:
            rec["started_ts"] = self._clock()                  # the time budget starts when work starts
        if new == "COMPLETE":
            rec["outcome"] = str(evidence.get("outcome") or "accepted")
        ev = dict(evidence)
        entry_hash = self.journal.append("goal.transition", {"goal_id": rec["goal"]["goal_id"], "from": old,
                                                             "to": new, "evidence": ev})
        rec["history"] = (rec.get("history") or [])[-(_HISTORY_KEEP - 1):] + [
            {"from": old, "to": new, "at": utc_now(), "journal": entry_hash,
             "reason": str(ev.get("reason", ""))[:500]}]

    def _mutate(self, goal_id: str, fn: Callable[[dict], Any]) -> dict:
        with file_lock(self._lock):
            rec = self._load(goal_id)
            fn(rec)
            self._save(rec)
            return rec

    # .......................................................... transitions
    def transition(self, goal_id: str, new_state: str, evidence: Mapping[str, Any] | None = None) -> dict:
        if evidence is not None and not isinstance(evidence, Mapping):
            raise TransitionError("evidence must be a mapping")
        return self._mutate(goal_id, lambda rec: self._move(rec, new_state, evidence or {}))

    def block(self, goal_id: str, reason: str, **evidence: Any) -> dict:
        def fn(rec: dict) -> None:
            if rec["state"] != "BLOCKED":
                self._move(rec, "BLOCKED", {**evidence, "reason": reason})
        return self._mutate(goal_id, fn)

    def resume(self, goal_id: str, evidence: Mapping[str, Any] | None = None) -> dict:
        """BLOCKED -> the state it was blocked from (guards re-checked)."""
        def fn(rec: dict) -> None:
            if rec["state"] != "BLOCKED" or not rec.get("blocked_from"):
                raise TransitionError("only a BLOCKED goal resumes")
            self._move(rec, rec["blocked_from"], {**(evidence or {}), "resume": True})
        return self._mutate(goal_id, fn)

    # .......................................................... candidate / evidence
    def set_candidate(self, goal_id: str, sha: str, diff_sha256: str, *, author: str = "",
                      branch: str = "", base_sha: str = "", target_branch: str = "") -> dict:
        if not re.fullmatch(r"[0-9a-f]{40}([0-9a-f]{24})?", sha or "") or \
                not re.fullmatch(r"[0-9a-f]{64}", diff_sha256 or ""):
            raise GoalError("candidate needs a full commit SHA and a sha256 diff hash")
        if target_branch and target_branch != CANONICAL_TARGET_BRANCH:
            raise GoalError(f"refused: a candidate is released only to {CANONICAL_TARGET_BRANCH}, "
                            f"not {target_branch!r}")

        def fn(rec: dict) -> None:
            old = rec.get("candidate") or {}
            changed = old.get("sha") != sha or old.get("diff_sha256") != diff_sha256
            rec["candidate"] = {"sha": sha, "diff_sha256": diff_sha256, "author": author, "branch": branch,
                                "base_sha": base_sha, "target_branch": target_branch, "set_at": utc_now()}
            self.journal.append("goal.candidate", {"goal_id": goal_id, "sha": sha, "diff_sha256": diff_sha256,
                                                   "author": author, "previous_sha": old.get("sha", "")})
            if changed:
                self._invalidate(rec, "candidate SHA/diff changed")
                if rec["state"] not in ("PLANNED", "BUILDING", "BLOCKED"):
                    self._move(rec, "BLOCKED", {"reason": f"candidate changed during {rec['state']}",
                                                "sha": sha})
        return self._mutate(goal_id, fn)

    def record_tests(self, goal_id: str, sha: str, diff_sha256: str, passed: bool, report: Mapping | None = None
                     ) -> dict:
        def fn(rec: dict) -> None:
            if rec["state"] != "TESTING":
                raise TransitionError("tests are recorded in TESTING")
            if not self._bound(rec, sha, diff_sha256):
                self._move(rec, "BLOCKED", {"reason": "tests ran on a different SHA than the candidate", "sha": sha})
                return
            rec["tests"] = {"sha": sha, "diff_sha256": diff_sha256, "passed": bool(passed),
                            "report": dict(report or {})}
            self.journal.append("goal.tests", {"goal_id": goal_id, "sha": sha, "passed": bool(passed),
                                               "report": dict(report or {})})
        return self._mutate(goal_id, fn)

    def record_review(self, review: Review) -> dict:
        try:
            schemas.validate("review", schemas.to_json(review))
        except schemas.SchemaError as exc:
            raise GoalError(str(exc)) from None

        def fn(rec: dict) -> None:
            if rec["state"] not in ("CLAUDE_REVIEW", "CODEX_REVIEW"):
                raise TransitionError(f"reviews are recorded in CLAUDE_REVIEW/CODEX_REVIEW, not {rec['state']}")
            payload = {"goal_id": review.goal_id, **asdict(review)}
            if not self._bound(rec, review.sha, review.diff_sha256):
                self.journal.append("goal.review_stale", payload)
                self._move(rec, "BLOCKED", {"reason": f"{review.reviewer} reviewed a different SHA/diff",
                                            "sha": review.sha})
                return
            author = (rec.get("candidate") or {}).get("author")
            other = "codex" if review.reviewer == "claude" else "claude"
            if review.verdict == "APPROVE" and author == review.reviewer and not self._approved_by(rec, other):
                raise GuardError(f"{review.reviewer} cannot approve its own unreviewed candidate")
            rec.setdefault("approvals", {})[review.reviewer] = asdict(review)
            self.journal.append("goal.review", payload)
            if review.verdict == "REJECT":
                self._move(rec, "BLOCKED", {"reason": f"{review.reviewer} rejected the candidate",
                                            "sha": review.sha})
            elif review.verdict == "APPROVE" and self._disagree(rec):
                self._move(rec, "BLOCKED", {"reason": "reviewers disagree on the same SHA", "sha": review.sha})
        return self._mutate(review.goal_id, fn)

    def _disagree(self, rec: dict) -> bool:
        verdicts = {r.get("verdict") for r in (rec.get("approvals") or {}).values()
                    if self._bound(rec, r.get("sha"), r.get("diff_sha256"))}
        return "APPROVE" in verdicts and "REJECT" in verdicts

    def record_staging(self, goal_id: str, report: Mapping[str, Any]) -> dict:
        def fn(rec: dict) -> None:
            if rec["state"] != "STAGING":
                raise TransitionError("staging reports are recorded in STAGING")
            c = rec.get("candidate") or {}
            if report.get("sha") != c.get("sha"):
                self._move(rec, "BLOCKED", {"reason": "staging ran a different SHA", "sha": report.get("sha")})
                return
            rec["staging"] = {**dict(report), "diff_sha256": c.get("diff_sha256")}
            self.journal.append("goal.staging", {"goal_id": goal_id, **dict(report)})
            if not report.get("passed"):
                self._move(rec, "BLOCKED", {"reason": "staging failed: " + str(report.get("reason", ""))[:300]})
        return self._mutate(goal_id, fn)

    def record_user_decision(self, goal_id: str, action: str, *, sha: str, diff_sha256: str, note: str = "",
                             by: str = "owner") -> dict:
        if action not in ("apply", "reject", "revise"):
            raise GoalError(f"unknown user decision {action!r}")

        def fn(rec: dict) -> None:
            if rec["state"] != "USER_APPROVAL":
                raise TransitionError(f"the user decides in USER_APPROVAL, the goal is {rec['state']}")
            if not self._bound(rec, sha, diff_sha256):
                raise GuardError("the decision names a different SHA/diff than the candidate")
            if action == "apply" and not (self.approvals_valid(rec) and self.staging_passed(rec)):
                raise GuardError("Apply needs both approvals and a passed staging for this SHA")
            decision = {"action": action, "sha": sha, "diff_sha256": diff_sha256, "note": note[:2000], "by": by,
                        "at": utc_now()}
            self.journal.append("goal.user_decision", {"goal_id": goal_id, **decision})
            if action == "apply":
                rec["user_decision"] = decision
            elif action == "reject":
                self._move(rec, "COMPLETE", {"outcome": "rejected_by_user", "applied": False, "note": note})
            else:
                self._move(rec, "BUILDING", {"reason": "revision requested by the user", "note": note})
        return self._mutate(goal_id, fn)

    # .......................................................... budget
    def charge(self, goal_id: str, *, agent_turns: int = 0, cost_usd: float = 0.0) -> dict:
        """Account usage; an exhausted budget (turns, cost or time) blocks the goal."""
        def fn(rec: dict) -> None:
            u = rec.setdefault("usage", {"agent_turns": 0, "cost_usd": 0.0})
            u["agent_turns"] = int(u.get("agent_turns", 0)) + int(agent_turns)
            u["cost_usd"] = round(float(u.get("cost_usd", 0.0)) + float(cost_usd), 6)
            reason = self.budget_exceeded(rec)
            if reason and rec["state"] not in TERMINAL and rec["state"] != "BLOCKED":
                self._move(rec, "BLOCKED", {"reason": f"budget exhausted: {reason}"})
        return self._mutate(goal_id, fn)

    def sweep_budgets(self) -> list[str]:
        """Timeout sweep: block every live goal whose budget ran out. Returns the blocked ids. A goal waiting for
        the owner (USER_APPROVAL) is not spending anything, so it is never swept."""
        blocked = []
        for rec in self.resumable():
            if rec["state"] == "USER_APPROVAL":
                continue
            reason = self.budget_exceeded(rec)
            if reason:
                self.block(rec["goal"]["goal_id"], f"budget exhausted: {reason}")
                blocked.append(rec["goal"]["goal_id"])
        return blocked

    def budget_exceeded(self, rec: dict) -> str:
        b = rec["goal"]["budget"]
        u = rec.get("usage") or {}
        if int(u.get("agent_turns", 0)) > int(b["max_agent_turns"]):
            return "agent turns"
        if float(u.get("cost_usd", 0.0)) > float(b["max_cost_usd"]) + 1e-9:
            return "cost"
        started = rec.get("started_ts")
        if started is None and rec.get("state") != "PROPOSED":
            started = rec.get("created_ts")                   # goals stored before the start clock existed
        if started is not None and self._clock() - float(started) > float(b["max_minutes"]) * 60:
            return "time"                                      # the clock runs from the start, not from creation
        return ""


__all__ = ["GoalError", "GoalNotFound", "GoalStore", "GuardError", "TERMINAL", "TRANSITIONS", "TransitionError",
           "measurable", "validate_goal"]
