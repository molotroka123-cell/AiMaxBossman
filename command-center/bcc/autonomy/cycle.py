"""The supervised self-improvement cycle as a resumable state machine.

States are the Line A GoalStore states (`bcc.autonomy.types.GoalState`):

    PROPOSED -> PLANNED -> BUILDING -> TESTING -> CLAUDE_REVIEW -> CODEX_REVIEW -> STAGING
      -> USER_APPROVAL (stop; the user presses Apply / Reject / Revise)
      -> DEPLOYED -> MONITORING -> COMPLETE | ROLLED_BACK
    any state -> BLOCKED (pause with a reason; the goal keeps its evidence)

Sequential and single-writer: the writer runs under the engineering lease in its
own clone (`workers.WriterSession`); tests and every other privileged action go
through the HandBroker; Claude then Codex review read-only, bound to the same
(sha, diff hash, test-evidence hash) (`review.ReviewGate`); a revision is a new
writer turn and invalidates both approvals. Auto-deploy happens only for the
`docs_tests` tier at autonomy level 3+, everything else stops at USER_APPROVAL.
After deploy the metrics gate decides; a regression is rolled back.

Stop conditions -> BLOCKED: constitution not pinned/mismatching, missing budget,
scope or rollback condition, budget exhausted, lease busy, writer timeout /
malformed output / scope or tamper violation, failing tests or missing evidence,
any reviewer rejection / timeout / malformed verdict / write, repeated
disagreement, revision limit, candidate SHA changed, ambiguous state or report,
rollback not guaranteed.

The cycle context is persisted after every step (`<root>/<goal>/cycle.json`), so a
crash resumes from the persisted GoalStore state instead of starting over.

    python -m bcc.autonomy.cycle --goal JEFF-0042 --real   # OWNER_REQUIRED: real CLIs, after Line A merge
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from ..rave import workspace as rws
from .planner import ModelFacts, facts_dict, planner_model_allowed
from .review import Candidate, ReviewGate, parse_review
from .savings import SavingsLedger
from .skills import SkillStore, capture_trace
from .goals import GoalError, TransitionError
from .probes import STAGING_PROBES
from .types import Budget, Goal, HandRequest
from .workers import (HandsPort, JournalPort, LeasePort, NemotronWriter, ProcessRunner, ReviewerSession,
                      WriterSession, assign_roles, full_diff, goal_rollback, goal_scope, hand_result_dict,
                      maybe_await, sha256_bytes, sha256_json)

GOAL_STATES = ("PROPOSED", "PLANNED", "BUILDING", "TESTING", "CLAUDE_REVIEW", "CODEX_REVIEW", "STAGING",
               "USER_APPROVAL", "DEPLOYED", "MONITORING", "COMPLETE", "ROLLED_BACK", "BLOCKED")
TERMINAL = frozenset({"COMPLETE", "ROLLED_BACK"})
STOPS = TERMINAL | {"BLOCKED", "USER_APPROVAL"}
_HEAVY = re.compile(r"(?i)architect|security|auth|credential|secret|runtime|migration|memory|telegram|service"
                    r"|release|payment")


class GoalStorePort(Protocol):
    def create(self, goal: Goal) -> Any: ...
    def get(self, goal_id: str) -> Any: ...
    def transition(self, goal_id: str, new_state: str, evidence: Any) -> Any: ...


class StagingPort(Protocol):
    def run(self, sha: str, checks: Any) -> Any: ...


def default_root() -> Path:
    explicit = os.environ.get("BOSSMAN_AUTONOMY_ROOT", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    from ..config import _data_dir           # the same data dir as the dashboard / CLI / service
    return Path(_data_dir()) / "autonomy"


def state_of(record: Any) -> str | None:
    if isinstance(record, str):
        return record
    if isinstance(record, dict):
        value = record.get("state")
    else:
        value = getattr(record, "state", None)
    return str(value) if value is not None else None


def verdict_flag(obj: Any) -> bool | None:
    """True / False / None (ambiguous) from a StagingReport or GateVerdict."""
    if obj is None:
        return None
    if isinstance(obj, bool):
        return obj
    for name in ("ok", "accepted", "passed", "allowed"):
        v = obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)
        if isinstance(v, bool):
            return v
    for name in ("verdict", "decision", "status"):
        v = obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)
        if isinstance(v, str):
            u = v.upper()
            if u in ("ACCEPT", "ACCEPTED", "PASS", "PASSED", "OK", "IMPROVED"):
                return True
            if u in ("REJECT", "REJECTED", "REGRESSION", "ROLLBACK", "FAIL", "FAILED", "DEGRADED"):
                return False
    return None


def goal_to_dict(goal: Goal) -> dict:
    return asdict(goal)


def goal_from_dict(d: dict) -> Goal:
    d = dict(d)
    d["budget"] = Budget(**d["budget"])
    for k in ("constraints", "acceptance_tests", "protected_metrics"):
        d[k] = tuple(d[k])
    return Goal(**d)


def route_writer(goal: Goal, *, seed: int | str, nemotron_ok: bool, nemotron_reason: str = "") -> dict:
    """Light tasks (docs_tests tier, <= 2 paths, no heavy topic) -> free Nemotron diff
    writer; everything else -> seeded Claude/Codex writer. Recorded with a reason."""
    roles = assign_roles(goal.goal_id, seed)
    light = goal.risk_tier == "docs_tests" and len(goal_scope(goal)) <= 2 and not _HEAVY.search(goal.problem)
    if light and nemotron_ok:
        return {**roles, "writer": "nemotron", "light": True,
                "reason": "light task (docs_tests, <=2 paths): free Nemotron diff writer"}
    if light:
        return {**roles, "light": True,
                "reason": f"light task, Nemotron unavailable ({nemotron_reason or 'not configured'}): seeded writer"}
    return {**roles, "light": False, "reason": "heavy task: seeded Claude/Codex writer"}


@dataclass
class CycleConfig:
    level: int = 2
    seed: int | str = 0
    max_revisions: int = 2
    max_disagreements: int = 1
    writer_timeout_s: int = 900
    reviewer_timeout_s: int = 600
    max_hand_rounds: int = 3
    test_suites: tuple[str, ...] = ("acceptance", "protected")
    staging_checks: tuple[str, ...] = ("health", "ready", *STAGING_PROBES)
    thresholds: dict = field(default_factory=dict)
    auto_deploy_min_level: int = 3


@dataclass
class CycleDeps:
    goals: GoalStorePort
    lease: LeasePort
    hands: HandsPort
    journal: JournalPort
    staging: StagingPort
    gate_decide: Callable[..., Any]
    constitution_verify: Callable[[], Any]
    metrics_probe: Callable[[Goal], Any]
    source_repo: Path
    work_root: Path
    runner: ProcessRunner | None = None
    nemotron: NemotronWriter | None = None
    nemotron_facts: ModelFacts | None = None
    savings: SavingsLedger | None = None
    skills: SkillStore | None = None
    wall: Callable[[], float] = time.time


@dataclass
class CycleOutcome:
    goal_id: str
    state: str
    reason: str = ""
    evidence: dict = field(default_factory=dict)


class Blocked(Exception):
    def __init__(self, reason: str, **evidence: Any):
        super().__init__(reason)
        self.reason, self.evidence = reason, evidence


class AutonomyCycle:
    def __init__(self, deps: CycleDeps, config: CycleConfig | None = None):
        self.d, self.c = deps, config or CycleConfig()

    # ------------------------------------------------------------ persistence
    def _dir(self, goal_id: str) -> Path:
        return Path(self.d.work_root) / re.sub(r"[^A-Za-z0-9._-]", "_", goal_id)

    def _load(self, goal_id: str) -> dict | None:
        p = self._dir(goal_id) / "cycle.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None

    def _save(self, ctx: dict) -> None:
        d = self._dir(ctx["goal"]["goal_id"])
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / "cycle.json.tmp"
        tmp.write_text(json.dumps(ctx, ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding="utf-8")
        os.replace(tmp, d / "cycle.json")

    def _log(self, kind: str, ctx: dict, payload: dict) -> str:
        return self.d.journal.append(kind, {"goal_id": ctx["goal"]["goal_id"], **payload})

    def _to(self, ctx: dict, state: str, evidence: dict) -> None:
        """GoalStore transition; a refused transition (illegal or failed guard) is a stop
        condition: journaled and turned into BLOCKED by the driver."""
        try:
            self.d.goals.transition(ctx["goal"]["goal_id"], state, evidence)
        except TransitionError as exc:
            self._log("cycle_transition_refused", ctx, {"from_state": ctx["state"], "to": state,
                                                        "reason": str(exc)[:500]})
            raise Blocked(f"transition {ctx['state']} -> {state} refused: {exc}"[:500]) from None
        ctx["state"] = state
        ctx["steps"].append({"state": state, "at": self.d.wall()})
        self._save(ctx)

    # ------------------------------------------------------------ entry points
    async def run_goal(self, goal: Goal) -> CycleOutcome:
        ctx = self._load(goal.goal_id)
        if ctx is None:
            try:
                self.d.goals.create(goal)
            except GoalError as exc:
                existing = self._store_state(goal.goal_id)
                if existing != "PROPOSED":           # created elsewhere (panel/CLI) and untouched -> adopt
                    self.d.journal.append("cycle_refused", {"goal_id": goal.goal_id, "reason": str(exc)[:500]})
                    return CycleOutcome(goal.goal_id, "REFUSED", str(exc)[:500])
            ctx = {"goal": goal_to_dict(goal), "state": "PROPOSED", "steps": [], "round": 0, "attempt": 0,
                   "revisions": 0, "turns_used": 0, "started_at": self.d.wall(), "writer_runs": [],
                   "evidence": [], "gate": None, "feedback": "", "user_decision": None, "deploys": []}
            self._save(ctx)
            self._log("cycle_started", ctx, {"goal": ctx["goal"]})
        return await self._drive(ctx)

    async def resume(self, goal_id: str) -> CycleOutcome:
        ctx = self._load(goal_id)
        if ctx is None:
            raise KeyError(f"no persisted cycle for {goal_id}")
        return await self._drive(ctx)

    async def user_decision(self, goal_id: str, decision: str, notes: str = "") -> CycleOutcome:
        """Reject / Revise at USER_APPROVAL. Apply is NOT a loop action: it goes through the
        release panel (``AutonomyService.apply`` -> the owner runs the commands ->
        ``confirm_released``), after which ``resume()`` continues from DEPLOYED."""
        if decision == "apply":
            raise ValueError("Apply goes through the release panel (Apply -> owner Confirm), then resume()")
        if decision not in ("reject", "revise"):
            raise ValueError("decision must be reject or revise (apply: release panel)")
        ctx = self._load(goal_id)
        if ctx is None or self._store_state(goal_id) != "USER_APPROVAL":
            raise ValueError("goal is not waiting for the user")
        entry = self._log("user_decision", ctx, {"decision": decision, "notes": notes[:2000]})
        ctx["user_decision"] = {"decision": decision, "journal_entry": entry}
        if decision == "revise":
            ctx["revisions"] += 1
            ctx["feedback"] = f"user: {notes}"
        self._save(ctx)
        record = getattr(self.d.goals, "record_user_decision", None)
        try:
            if record is not None:
                record(goal_id, decision, sha=ctx["sha"], diff_sha256=ctx["diff_sha256"], note=notes)
            elif decision == "reject":
                self.d.goals.transition(goal_id, "COMPLETE", {"outcome": "rejected_by_user", "applied": False})
            else:
                self.d.goals.transition(goal_id, "BUILDING", {"reason": "revision requested by the user"})
        except (TransitionError, GoalError) as exc:
            return self._block(ctx, Blocked(f"user decision refused: {exc}"[:500]))
        if decision == "reject":
            self._capture_trace(ctx, "COMPLETE")
            ctx["state"] = "COMPLETE"
            self._save(ctx)
            return CycleOutcome(goal_id, "COMPLETE", "rejected by the user")
        return await self._drive(ctx)

    async def run_queue(self, goals: list[Goal], *, stop_on_blocked: bool = False) -> list[CycleOutcome]:
        out = []
        for goal in goals:
            res = await self.run_goal(goal)
            out.append(res)
            if stop_on_blocked and res.state == "BLOCKED":
                break
        return out

    # ------------------------------------------------------------ driver
    async def _drive(self, ctx: dict) -> CycleOutcome:
        gid = ctx["goal"]["goal_id"]
        while True:
            # the GoalStore is the source of truth; a crash between saving the context and the
            # transition simply repeats that step (every step is idempotent or re-runs isolated)
            store_state = self._store_state(gid)
            if store_state not in GOAL_STATES:
                return self._block(ctx, Blocked("ambiguous state", store=store_state, cycle=ctx["state"]))
            ctx["state"] = store_state
            if store_state in STOPS:
                if store_state in TERMINAL and not ctx.get("trace_hash") and ctx.get("sha"):
                    self._capture_trace(ctx, store_state)
                return CycleOutcome(gid, store_state, ctx.get("blocked_reason", "") if store_state == "BLOCKED"
                                    else "", {"cycle": str(self._dir(gid) / "cycle.json")})
            try:
                await getattr(self, "_s_" + store_state.lower())(ctx)
            except Blocked as exc:
                return self._block(ctx, exc)

    def _store_state(self, goal_id: str) -> str | None:
        try:
            return state_of(self.d.goals.get(goal_id))
        except (KeyError, ValueError, OSError):
            return None

    def _block(self, ctx: dict, exc: Blocked) -> CycleOutcome:
        gid = ctx["goal"]["goal_id"]
        ctx["blocked_reason"] = exc.reason
        evidence = json.loads(json.dumps(exc.evidence, default=str))
        self._log("cycle_blocked", ctx, {"reason": exc.reason, "from_state": ctx["state"], **evidence})
        if self._store_state(gid) != "BLOCKED":
            try:
                self.d.goals.transition(gid, "BLOCKED", {"reason": exc.reason, **evidence})
            except (TransitionError, KeyError, ValueError) as err:
                self._log("cycle_transition_refused", ctx, {"to": "BLOCKED", "reason": str(err)[:500]})
        ctx["state"] = "BLOCKED"
        self._save(ctx)
        return CycleOutcome(gid, "BLOCKED", exc.reason, evidence)

    def _goal(self, ctx: dict) -> Goal:
        return goal_from_dict(ctx["goal"])

    def _check_budget(self, ctx: dict, *, need_turns: int = 1) -> None:
        b = self._goal(ctx).budget
        if ctx["turns_used"] + need_turns > b.max_agent_turns:
            raise Blocked("budget exhausted: agent turns", turns_used=ctx["turns_used"], max=b.max_agent_turns)
        if (self.d.wall() - ctx["started_at"]) / 60.0 > b.max_minutes:
            raise Blocked("budget exhausted: minutes", max_minutes=b.max_minutes)

    # ------------------------------------------------------------ states
    async def _s_proposed(self, ctx: dict) -> None:
        goal = self._goal(ctx)
        status = await maybe_await(self.d.constitution_verify())
        if not bool(getattr(status, "ok", status.get("ok") if isinstance(status, dict) else False)):
            reason = getattr(status, "reason", None) or (status.get("reason") if isinstance(status, dict) else "")
            raise Blocked(f"constitution not verified: {reason or 'unknown'}")
        b = goal.budget
        if not b or b.max_minutes <= 0 or b.max_agent_turns <= 0:
            raise Blocked("goal without a budget is refused")
        if not goal.acceptance_tests or not goal.target_metric:
            raise Blocked("goal without measurable acceptance tests is not started")
        if not goal_scope(goal):
            raise Blocked("goal has no allowed paths (path:<glob>)")
        if not goal_rollback(goal):
            raise Blocked("rollback cannot be guaranteed: no rollback condition")
        nemo_ok, nemo_why = planner_model_allowed(self.d.nemotron_facts) if self.d.nemotron else (False,
                                                                                                "not configured")
        route = route_writer(goal, seed=self.c.seed, nemotron_ok=nemo_ok, nemotron_reason=nemo_why)
        ctx["route"] = route
        self._log("roles_assigned", ctx, {**route, "nemotron": facts_dict(self.d.nemotron_facts)})
        before = await maybe_await(self.d.metrics_probe(goal))
        if not isinstance(before, dict):
            raise Blocked("ambiguous state: metrics probe returned no baseline")
        ctx["metrics_before"] = before
        ctx["base_sha"] = rws.head_commit(Path(self.d.source_repo))
        self._to(ctx, "PLANNED", {"route": route, "base_sha": ctx["base_sha"], "metrics_before": before})

    async def _s_planned(self, ctx: dict) -> None:
        self._to(ctx, "BUILDING", {"writer": ctx["route"]["writer"], "reason": ctx["route"]["reason"]})

    async def _s_building(self, ctx: dict) -> None:
        goal = self._goal(ctx)
        writer = ctx["route"]["writer"]
        self._check_budget(ctx)
        ctx["round"] += 1 if not ctx.get("building_started") else 0
        ctx["attempt"] += 1
        ctx["building_started"] = True
        self._save(ctx)
        prev = ctx.get("worktree") if ctx.get("sha") else None
        source, clone_at = (Path(prev), ctx["sha"]) if prev else (Path(self.d.source_repo), ctx["base_sha"])
        budget_turns = goal.budget.max_agent_turns
        session = WriterSession(
            goal=goal, agent=writer, source_repo=source, base_sha=clone_at,
            session_dir=self._dir(goal.goal_id) / f"w{ctx['round']}-a{ctx['attempt']}", lease=self.d.lease,
            hands=self.d.hands, journal=self.d.journal, runner=self.d.runner, timeout_s=self.c.writer_timeout_s,
            max_hand_rounds=self.c.max_hand_rounds, feedback=ctx.get("feedback", ""), turn=ctx["round"],
            nemotron=self.d.nemotron, turns_left=lambda: budget_turns - ctx["turns_used"])
        res = await session.run()
        ctx["turns_used"] += res.turns_used
        ctx["building_started"] = False
        ctx["writer_runs"].append({k: v for k, v in res.as_dict().items() if k != "hand_results"})
        self._save(ctx)
        if res.status != "ok":
            reasons = {"lease_busy": "engineering lease busy", "timeout": "writer timed out",
                       "malformed": "writer output malformed", "violation": "boundary violation",
                       "budget": "budget exhausted: agent turns", "no_change": "writer produced no change",
                       "unavailable": "writer unavailable"}
            raise Blocked(f"{reasons.get(res.status, 'writer failed')}: {res.summary}"[:400],
                          violations=res.violations)
        if self.d.savings is not None:
            f = self.d.nemotron_facts if writer == "nemotron" else None
            self.d.savings.record(goal_id=goal.goal_id, risk_tier=goal.risk_tier, writer=writer,
                                  turns=res.turns_used, tokens_in=res.tokens_in, tokens_out=res.tokens_out,
                                  model=f.model if f else "", provider=f.provider if f else "subscription")
        # the candidate diff is always against the goal's base, whatever the revision
        diff = full_diff(Path(res.worktree), ctx["base_sha"], res.sha)
        ctx.update(sha=res.sha, worktree=res.worktree, author=writer, author_session=res.task_id,
                   diff_sha256=sha256_bytes(diff), evidence=[], evidence_sha256=None, feedback="")
        self._to(ctx, "TESTING", {"sha": res.sha, "diff_sha256": ctx["diff_sha256"], "writer": writer,
                                  "transcripts": [t["sha256"] for t in res.transcripts]})

    def _head_unchanged(self, ctx: dict) -> None:
        try:
            head = rws.head_commit(Path(ctx["worktree"]))
        except (rws.WorkspaceError, KeyError, TypeError):
            raise Blocked("ambiguous state: candidate worktree missing") from None
        if head != ctx.get("sha"):
            raise Blocked("candidate SHA changed", expected=ctx.get("sha"), found=head)

    async def _s_testing(self, ctx: dict) -> None:
        goal = self._goal(ctx)
        if not ctx.get("sha"):
            raise Blocked("ambiguous state: no candidate to test")
        self._head_unchanged(ctx)
        evidence = []
        for suite in self.c.test_suites:
            req = HandRequest(goal_id=goal.goal_id, requested_by="jev", action="run_tests",
                              target="isolated_worktree",
                              arguments={"suite": suite, "worktree": ctx["worktree"], "sha": ctx["sha"],
                                         "acceptance_tests": list(goal.acceptance_tests)},
                              expected_evidence=("exit_code", "stdout_hash", "report_path"), risk_class="low",
                              timeout_s=self._action_timeout(goal, 1800),
                              rollback="none: read-only test run in the isolated worktree")
            res = await maybe_await(self.d.hands.execute(req))
            row = {"suite": suite, **hand_result_dict(res)}
            evidence.append(row)
            missing = [e for e in req.expected_evidence if e != "exit_code" and e not in res.artifacts
                       and not (e == "stdout_hash" and "stdout" in res.artifacts)]
            if "missing evidence" in res.refused_reason or (res.exit_code is not None and missing):
                raise Blocked("missing test evidence", suite=suite, missing=missing or res.refused_reason)
            if res.exit_code is None and res.refused_reason:
                raise Blocked(f"hand request refused: {res.refused_reason}", suite=suite)
            if res.exit_code is None:
                raise Blocked("missing test evidence", suite=suite, missing=["exit_code"])
            if not res.ok or res.exit_code != 0:
                raise Blocked(f"protected tests fail: {suite}", exit_code=res.exit_code)
        ctx["evidence"] = evidence
        ctx["evidence_sha256"] = sha256_json(evidence)
        gate = ReviewGate(goal.goal_id, max_disagreements=self.c.max_disagreements, journal=self.d.journal,
                          state=ctx.get("gate"))
        gate.set_candidate(Candidate(sha=ctx["sha"], diff_sha256=ctx["diff_sha256"],
                                     evidence_sha256=ctx["evidence_sha256"], author=ctx["author"],
                                     author_session=ctx["author_session"]))
        ctx["gate"] = gate.to_dict()
        self._to(ctx, "CLAUDE_REVIEW", {"sha": ctx["sha"], "evidence_sha256": ctx["evidence_sha256"]})

    @staticmethod
    def _action_timeout(goal: Goal, wanted: int) -> int:
        return max(1, min(int(wanted), int(goal.budget.max_minutes) * 60))

    def _hand_with_lease(self, ctx: dict, req: HandRequest):
        """Release-kind hands (apply/rollback) are writing actions: they run under the
        engineering lease for this goal, never beside a writer."""
        gid = ctx["goal"]["goal_id"]
        try:
            token = self.d.lease.acquire(gid, f"jev-{req.action}:{gid}", float(req.timeout_s) + 60.0)
        except Exception as exc:  # noqa: BLE001 - busy / refused lease
            raise Blocked(f"engineering lease busy for {req.action}: {exc}"[:300]) from None
        try:
            return self.d.hands.execute(req)
        finally:
            self.d.lease.release(token)

    async def _s_claude_review(self, ctx: dict) -> None:
        await self._review(ctx, "claude")

    async def _s_codex_review(self, ctx: dict) -> None:
        await self._review(ctx, "codex")

    async def _review(self, ctx: dict, agent: str) -> None:
        goal = self._goal(ctx)
        if not ctx.get("gate") or not ctx.get("evidence_sha256"):
            raise Blocked("ambiguous state: review without tested candidate")
        self._check_budget(ctx)
        self._head_unchanged(ctx)
        gate = ReviewGate(goal.goal_id, max_disagreements=self.c.max_disagreements, journal=self.d.journal,
                          state=ctx["gate"])
        cand = gate.candidate
        if cand is None or cand.sha != ctx["sha"] or cand.evidence_sha256 != ctx["evidence_sha256"]:
            raise Blocked("ambiguous state: gate not bound to the candidate")
        diff = full_diff(Path(ctx["worktree"]), ctx["base_sha"], ctx["sha"]).decode("utf-8", "replace")
        sess = ReviewerSession(goal=goal, reviewer=agent, source_worktree=Path(ctx["worktree"]), sha=ctx["sha"],
                               diff_sha256=ctx["diff_sha256"], evidence_sha256=ctx["evidence_sha256"],
                               evidence=ctx["evidence"], session_dir=self._dir(goal.goal_id)
                               / f"r{ctx['round']}-{agent}-a{ctx['attempt']}", journal=self.d.journal,
                               runner=self.d.runner, timeout_s=self.c.reviewer_timeout_s,
                               base_sha=ctx["base_sha"], diff_text=diff)
        out = await sess.run()
        ctx["turns_used"] += 1
        if out.status != "ok":
            gate.record_failure(agent, f"{out.status}: {out.reason}")
            ctx["gate"] = gate.to_dict()
            raise Blocked(gate.blocked_reason)
        review, why = parse_review(out.text, goal_id=goal.goal_id, reviewer=agent, candidate=cand)
        if review is None:
            gate.record_failure(agent, f"malformed verdict: {why}")
            ctx["gate"] = gate.to_dict()
            raise Blocked(gate.blocked_reason)
        verdict = gate.submit(review, evidence_sha256=ctx["evidence_sha256"], session_id=out.session_id)
        ctx["gate"] = gate.to_dict()
        self._save(ctx)
        if verdict != "accepted":
            raise Blocked(f"ambiguous state: review {verdict}")
        status = gate.status()
        if status == "BLOCKED":
            raise Blocked(gate.blocked_reason, disagreements=gate.disagreements)
        if status == "CHANGES_REQUESTED":
            ctx["revisions"] += 1
            if ctx["revisions"] > self.c.max_revisions:
                raise Blocked("revision limit reached", revisions=ctx["revisions"])
            ctx["feedback"] = gate.change_notes()
            self._to(ctx, "BUILDING", {"reason": "changes requested", "by": agent, "revision": ctx["revisions"]})
            return
        if agent == "claude":
            self._to(ctx, "CODEX_REVIEW", {"claude": review.verdict, "sha": ctx["sha"]})
            return
        if status != "APPROVED":
            raise Blocked("ambiguous state: review sequence finished without dual approval")
        self._to(ctx, "STAGING", {"approvals": gate.evidence()})

    async def _s_staging(self, ctx: dict) -> None:
        goal = self._goal(ctx)
        self._head_unchanged(ctx)
        gate = ReviewGate(goal.goal_id, state=ctx["gate"])
        if gate.status() != "APPROVED" or gate.candidate is None or gate.candidate.sha != ctx["sha"]:
            raise Blocked("ambiguous state: staging without dual approval of this sha")
        report = await maybe_await(self.d.staging.run(ctx["sha"], list(self.c.staging_checks)))
        ok = verdict_flag(report)
        ctx["staging"] = report if isinstance(report, dict) else getattr(report, "__dict__", str(report))
        self._log("staging_report", ctx, {"sha": ctx["sha"], "ok": ok})
        if ok is None:
            raise Blocked("ambiguous state: staging report")
        if not ok:
            raise Blocked("staging failed")
        if goal.risk_tier == "docs_tests" and self.c.level >= self.c.auto_deploy_min_level:
            await self._deploy(ctx)
            return
        self._to(ctx, "USER_APPROVAL", {"sha": ctx["sha"], "rollback": goal_rollback(goal),
                                        "approvals": gate.approvals(), "staging_ok": True})

    async def _deploy(self, ctx: dict) -> None:
        """Automatic tier only (docs_tests at L3+). Every other release is the user's:
        release panel Apply -> the owner runs the commands -> Confirm -> resume()."""
        goal = self._goal(ctx)
        req = HandRequest(goal_id=goal.goal_id, requested_by="jev", action="apply_candidate",
                          target="release_candidate",
                          arguments={"sha": ctx["sha"], "worktree": ctx["worktree"], "auto_tier": True,
                                     "risk_tier": goal.risk_tier},
                          expected_evidence=("exit_code",), risk_class="medium",
                          timeout_s=self._action_timeout(goal, 900),
                          rollback=f"restore {ctx['base_sha']} ({goal_rollback(goal)})")
        res = await maybe_await(self._hand_with_lease(ctx, req))
        ctx["deploys"].append(hand_result_dict(res))
        self._save(ctx)
        if not res.ok:
            raise Blocked(f"deploy refused or failed: {res.refused_reason or res.exit_code}"[:400])
        self._to(ctx, "DEPLOYED", {"sha": ctx["sha"], "auto_tier": True, "staging_ok": True,
                                   "request_hash": res.request_hash})

    async def _s_deployed(self, ctx: dict) -> None:
        self._to(ctx, "MONITORING", {"sha": ctx["sha"]})

    async def _s_monitoring(self, ctx: dict) -> None:
        goal = self._goal(ctx)
        after = await maybe_await(self.d.metrics_probe(goal))
        if not isinstance(after, dict):
            raise Blocked("ambiguous state: metrics probe failed after deploy")
        verdict = await maybe_await(self.d.gate_decide(ctx["metrics_before"], after, goal.target_metric,
                                                       goal.protected_metrics, self.c.thresholds))
        ok = verdict_flag(verdict)
        ctx["metrics_after"] = after
        self._log("metrics_verdict", ctx, {"ok": ok, "before": ctx["metrics_before"], "after": after})
        if ok is None:
            raise Blocked("ambiguous state: metrics gate verdict")
        if ok:
            self._finish(ctx, "COMPLETE", {"gate": "ACCEPT"})
            return
        req = HandRequest(goal_id=goal.goal_id, requested_by="jev", action="rollback", target="release_candidate",
                          arguments={"sha": ctx["sha"], "worktree": ctx["worktree"], "to_sha": ctx["base_sha"]},
                          expected_evidence=("exit_code",), risk_class="medium",
                          timeout_s=self._action_timeout(goal, 900), rollback="manual restore by the user")
        res = await maybe_await(self._hand_with_lease(ctx, req))
        ctx["rollback"] = hand_result_dict(res)
        if not res.ok:
            raise Blocked(f"rollback not guaranteed: {res.refused_reason or 'rollback failed'}"[:400],
                          request_hash=res.request_hash)
        self._finish(ctx, "ROLLED_BACK", {"gate": "ROLLBACK"})

    def _finish(self, ctx: dict, state: str, extra: dict | None = None) -> None:
        self._capture_trace(ctx, state)
        self._to(ctx, state, {"sha": ctx["sha"], "trace_hash": ctx["trace_hash"],
                              "metrics_after": ctx.get("metrics_after"), **(extra or {})})

    def _capture_trace(self, ctx: dict, state: str) -> str:
        goal = self._goal(ctx)
        gate = ctx.get("gate") or {}
        trace = capture_trace(
            goal_id=goal.goal_id, start_state={"base_sha": ctx["base_sha"], "metrics_before": ctx["metrics_before"]},
            instruction=goal.desired_result,
            actions=[{"action": "run_tests", "suite": e["suite"], "request_hash": e["request_hash"], "ok": e["ok"]}
                     for e in ctx["evidence"]]
                    + [{"action": "apply_candidate", "request_hash": d["request_hash"], "ok": d["ok"]}
                       for d in ctx["deploys"]],
            observations=[w.get("summary", "") for w in ctx["writer_runs"]], errors=[],
            recovery=[f"revision {i + 1}" for i in range(ctx["revisions"])],
            result={"state": state, "sha": ctx["sha"], "metrics_after": ctx.get("metrics_after")},
            reviews=[{"reviewer": r, **v} for r, v in sorted((gate.get("verdicts") or {}).items())],
            user_decision=(ctx.get("user_decision") or {}).get("decision"), origin="executed")
        (self._dir(goal.goal_id) / "trace.json").write_text(json.dumps(trace.as_dict(), ensure_ascii=False,
                                                                       indent=2, sort_keys=True), encoding="utf-8")
        ctx["trace_hash"] = trace.trace_hash()
        self._log("trace_captured", ctx, {"trace_hash": ctx["trace_hash"], "state": state})
        self._save(ctx)
        return ctx["trace_hash"]


# ------------------------------------------------------------------ real wiring (OWNER_REQUIRED)


def _real_deps(root: Path, repo: Path) -> CycleDeps:  # pragma: no cover - needs Line A + owner machine
    """Line A objects (same data dir as the dashboard/CLI) + the real Claude/Codex CLIs."""
    from . import constitution, metrics_gate
    from .goals import GoalStore
    from .hands import build_default_broker
    from .journal import Journal
    from .lease import EngineeringLease
    from .staging import build_default_runner
    from .identity_task import GOAL_ID, METRIC, _ollama_responder, run_redteam

    journal = Journal(root)

    async def probe(goal: Goal) -> dict | None:
        model = os.environ.get("BOSSMAN_AUTONOMY_JEFF_MODEL", "").strip()
        if goal.goal_id != GOAL_ID or not model:
            return None
        endpoint = os.environ.get("BOSSMAN_AUTONOMY_JEFF_ENDPOINT", "http://127.0.0.1:11434/v1")
        rep = await run_redteam(_ollama_responder(endpoint, model))
        return {METRIC: float(rep.leaks)}

    return CycleDeps(goals=GoalStore(root, journal), lease=EngineeringLease(root, journal=journal),
                     hands=build_default_broker(root, journal), journal=journal,
                     staging=build_default_runner(root, repo, journal=journal),
                     gate_decide=metrics_gate.decide, constitution_verify=constitution.verify,
                     metrics_probe=probe, source_repo=repo, work_root=root / "cycles",
                     savings=SavingsLedger(root), skills=SkillStore(root))


async def openrouter_nemotron() -> tuple[NemotronWriter | None, ModelFacts | None, str]:  # pragma: no cover
    """Live price/size verification of Nemotron through the existing OpenRouter client."""
    from ..v2.openrouter_ext import OpenRouterClient
    from ..v2.openrouter_identity import env_credential
    from .planner import NEMOTRON, facts_from_card

    cred = env_credential()
    key = getattr(cred, "value", "") or ""
    if not key:
        return None, None, "no OpenRouter key"
    client = OpenRouterClient(key)
    cards = {c.id: c for c in await client.list_models()}
    card = cards.get(NEMOTRON)
    if card is None:
        return None, None, f"{NEMOTRON} not in the live catalog"
    facts = facts_from_card(card)
    ok, why = planner_model_allowed(facts)
    if not ok:
        return None, facts, why

    async def chat(messages: list[dict]) -> dict:
        started = time.perf_counter()
        data = await client.chat_raw(NEMOTRON, messages, max_tokens=4000, temperature=0)
        usage = data.get("usage") or {}
        text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        return {"text": text, "tokens_in": usage.get("prompt_tokens", 0), "tokens_out": usage.get("completion_tokens", 0),
                "model": data.get("model") or NEMOTRON, "provider": "openrouter",
                "latency_ms": round((time.perf_counter() - started) * 1000)}

    return NemotronWriter(chat, model=NEMOTRON), facts, "ok"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m bcc.autonomy.cycle")
    p.add_argument("--goal", default="JEFF-0042")
    p.add_argument("--real", action="store_true", help="run the real Claude/Codex CLIs (OWNER_REQUIRED)")
    p.add_argument("--root", default=None)
    p.add_argument("--repo", default=".")
    p.add_argument("--level", type=int, default=2)
    p.add_argument("--seed", default="0")
    args = p.parse_args(argv)
    from .identity_task import GOAL_ID, jeff_0042_goal
    if args.goal != GOAL_ID:
        p.error("only JEFF-0042 is wired as a named goal; use the planner for others")
    goal = jeff_0042_goal()
    if not args.real:
        sys.stdout.write(json.dumps({"goal": goal_to_dict(goal), "route": route_writer(goal, seed=args.seed,
                                     nemotron_ok=False, nemotron_reason="dry run"),
                                     "note": "dry run; add --real on the owner machine"}, indent=2) + "\n")
        return 0
    root = Path(args.root).expanduser() if args.root else default_root()  # pragma: no cover - OWNER_REQUIRED
    deps = _real_deps(root, Path(args.repo).resolve())
    nemo, facts, _why = asyncio.run(openrouter_nemotron()) if os.environ.get("OPENROUTER_API_KEY") else (None, None, "")
    deps.nemotron, deps.nemotron_facts = nemo, facts
    level = min(args.level, 2)          # constitution: the system starts at L2 at most
    out = asyncio.run(AutonomyCycle(deps, CycleConfig(level=level, seed=args.seed)).run_goal(goal))
    sys.stdout.write(json.dumps(asdict(out), ensure_ascii=False, indent=2, default=str) + "\n")
    return 0 if out.state in ("USER_APPROVAL", "COMPLETE") else 1


if __name__ == "__main__":
    raise SystemExit(main())
