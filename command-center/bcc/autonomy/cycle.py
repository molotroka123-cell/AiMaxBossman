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

Stop conditions -> BLOCKED: owner STOP (global or autonomy, checked on every step and inside the writer /
reviewer / hand layers), constitution not pinned/mismatching, missing budget, scope or rollback condition, goal or
daily budget exhausted, lease busy, writer timeout / malformed output / scope, protected-path or tier violation,
failing tests or missing evidence, any reviewer rejection / timeout / malformed verdict / write, repeated
disagreement, revision limit, candidate SHA changed, staged evaluation REJECT (metrics measured on the staged
candidate BEFORE the owner is asked), ambiguous state or report, rollback not guaranteed.

Experience: every stop writes `trace.json` (atomic) and one lesson CANDIDATE (UNVERIFIED, with provenance and dedup)
into the retrieval memory. Nothing here trains a model: `weights: WEIGHTS_UNCHANGED`, `learning_kind:
retrieval_context`. Autonomous apply stays OFF (level cap L2, no apply executor).

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
from .budget import DailyBudget
from .experience import LEARNING_KIND, WEIGHTS, ExperienceWriter
from .planner import ModelFacts, facts_dict, planner_model_allowed
from .review import Candidate, ReviewGate, parse_review
from .savings import SavingsLedger
from .skills import SkillStore, capture_trace
from .goals import GoalError, TransitionError
from .probes import STAGING_PROBES
from .types import Budget, Goal, HandRequest
from .journal import atomic_write_bytes
from .workers import (HandsPort, JournalPort, LeasePort, NemotronWriter, ProcessRunner, ReviewerSession,
                      TreeRunner, WriterSession, assign_roles, full_diff, goal_rollback, goal_scope,
                      hand_result_dict, maybe_await, sha256_bytes, sha256_json)

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
    models: dict = field(default_factory=dict)         # agent -> CLI model alias (e.g. {"claude": "haiku"})
    max_cli_turns: int | None = None                   # `claude -p --max-turns N` (Codex has no such flag)


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
    stop_check: Callable[[], str] | None = None        # owner STOP: "" = run, else the reason
    candidate_probe: Callable[[Goal, Path], Any] | None = None   # metrics of the STAGED candidate (its own worktree)
    daily: DailyBudget | None = None                   # global daily cap (cycles / turns / usd)
    experience: ExperienceWriter | None = None         # lesson CANDIDATES (retrieval memory, never training)


@dataclass
class CycleOutcome:
    goal_id: str
    state: str
    reason: str = ""
    evidence: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        # experience saved by the loop is retrieval context: never claim (or imply) weight training
        self.evidence = {"weights": WEIGHTS, "learning_kind": LEARNING_KIND, **self.evidence}


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

    async def resume_after_stop(self, goal_id: str) -> CycleOutcome:
        """A goal the owner STOP blocked continues from the state it was blocked from, once the STOP is cleared.
        Any other BLOCKED goal stays blocked: it needs the owner's look, not an automatic retry."""
        ctx = self._load(goal_id)
        if ctx is None:
            raise KeyError(f"no persisted cycle for {goal_id}")
        rec = self.d.goals.get(goal_id)
        reason = str(rec.get("blocked_reason", "")) if isinstance(rec, dict) else ""
        if state_of(rec) != "BLOCKED" or not reason.startswith("owner STOP"):
            raise ValueError("only a goal blocked by the owner STOP resumes by itself")
        stopped = self._stop_reason()
        if stopped:
            return CycleOutcome(goal_id, "BLOCKED", stopped)
        self.d.goals.resume(goal_id, {"resumed_after": "owner STOP cleared"})
        self._log("cycle_resumed_after_stop", ctx, {"from_state": self._store_state(goal_id)})
        ctx.pop("blocked_reason", None)
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
            self._experience(ctx, "REJECTED", "")
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
    def _thresholds(self, goal: Goal) -> dict:
        """Metric thresholds: the config's, plus the honest JEFF-0042 tolerances for that goal."""
        from .identity_task import GOAL_ID, THRESHOLDS
        return {**(THRESHOLDS if goal.goal_id == GOAL_ID else {}), **self.c.thresholds}

    def _stop_reason(self) -> str:
        return (self.d.stop_check() if self.d.stop_check is not None else "") or ""

    def _sweep(self, ctx: dict) -> None:
        """Block every live goal whose own budget (turns / cost / time) ran out."""
        sweep = getattr(self.d.goals, "sweep_budgets", None)
        if sweep is None:
            return
        blocked = sweep()
        if blocked:
            self._log("budget.swept", ctx, {"blocked": list(blocked)})

    async def _drive(self, ctx: dict) -> CycleOutcome:
        gid = ctx["goal"]["goal_id"]
        while True:
            self._sweep(ctx)
            # the GoalStore is the source of truth; a crash between saving the context and the
            # transition simply repeats that step (every step is idempotent or re-runs isolated)
            store_state = self._store_state(gid)
            if store_state not in GOAL_STATES:
                return self._block(ctx, Blocked("ambiguous state", store=store_state, cycle=ctx["state"]))
            ctx["state"] = store_state
            if store_state in STOPS:
                if store_state in TERMINAL | {"BLOCKED"} and ctx.get("sha") and ctx.get("trace_state") != store_state:
                    self._capture_trace(ctx, store_state)
                reason = ""
                if store_state == "BLOCKED":
                    reason = ctx.get("blocked_reason", "") or self._stored_reason(gid)
                self._experience(ctx, store_state, reason)
                return CycleOutcome(gid, store_state, reason, {"cycle": str(self._dir(gid) / "cycle.json")})
            stopped = self._stop_reason()
            if stopped:                                         # the owner STOP halts the loop before any step
                return self._block(ctx, Blocked(stopped))
            try:
                await getattr(self, "_s_" + store_state.lower())(ctx)
            except Blocked as exc:
                return self._block(ctx, exc)

    def _stored_reason(self, goal_id: str) -> str:
        try:
            rec = self.d.goals.get(goal_id)
            return str(rec.get("blocked_reason", "")) if isinstance(rec, dict) else ""
        except (KeyError, ValueError, OSError):
            return ""

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
        self._experience(ctx, "BLOCKED", exc.reason)
        return CycleOutcome(gid, "BLOCKED", exc.reason, evidence)

    def _goal(self, ctx: dict) -> Goal:
        return goal_from_dict(ctx["goal"])

    def _check_budget(self, ctx: dict, *, need_turns: int = 1) -> None:
        b = self._goal(ctx).budget
        if ctx["turns_used"] + need_turns > b.max_agent_turns:
            raise Blocked("budget exhausted: agent turns", turns_used=ctx["turns_used"], max=b.max_agent_turns)
        if (self.d.wall() - ctx["started_at"]) / 60.0 > b.max_minutes:
            raise Blocked("budget exhausted: minutes", max_minutes=b.max_minutes)
        if self.d.daily is not None:
            why = self.d.daily.exhausted(need_turns=need_turns)
            if why:
                raise Blocked(why, daily=self.d.daily.snapshot())

    def _charge(self, ctx: dict, *, turns: int, cost_usd: float = 0.0) -> None:
        """Account the turns (and the USD of a paid route; the subscription CLIs and the free route cost 0) on the
        goal and on the daily cap. A goal that just ran out of budget is BLOCKED by the store; stop here."""
        gid = ctx["goal"]["goal_id"]
        if self.d.daily is not None and (turns or cost_usd):
            self.d.daily.charge(gid, turns=turns, usd=cost_usd)
        charge = getattr(self.d.goals, "charge", None)
        if charge is not None and (turns or cost_usd):
            rec = charge(gid, agent_turns=turns, cost_usd=cost_usd)
            if state_of(rec) == "BLOCKED":
                raise Blocked(str(rec.get("blocked_reason") or "budget exhausted"))

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
        if self.d.daily is not None:
            why = self.d.daily.start_cycle(goal.goal_id)              # journaled: budget.check
            if why:
                raise Blocked(why, daily=self.d.daily.snapshot())
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
            nemotron=self.d.nemotron, turns_left=lambda: budget_turns - ctx["turns_used"],
            model=self.c.models.get(writer), stop_check=self.d.stop_check, max_cli_turns=self.c.max_cli_turns)
        res = await session.run()
        ctx["turns_used"] += res.turns_used
        ctx["building_started"] = False
        ctx["writer_runs"].append({k: v for k, v in res.as_dict().items() if k != "hand_results"})
        self._save(ctx)
        self._charge(ctx, turns=res.turns_used)
        if res.status != "ok":
            reasons = {"lease_busy": "engineering lease busy", "timeout": "writer timed out",
                       "malformed": "writer output malformed", "violation": "boundary violation",
                       "budget": "budget exhausted: agent turns", "no_change": "writer produced no change",
                       "unavailable": "writer unavailable", "stopped": "owner STOP"}
            if res.status == "stopped":
                raise Blocked(res.summary if str(res.summary).startswith("owner STOP") else f"owner STOP: {res.summary}",
                              violations=res.violations)
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
        skipped = [t for t in goal.acceptance_tests if not str(t).strip().startswith("pytest:")]
        if skipped:                       # cmd: / measurable statements are not run by the hands: say so, never hide it
            evidence.append({"suite": "not_executed", "status": "NOT_EXECUTED", "tests": list(skipped),
                             "request_hash": "", "ok": None,
                             "note": "acceptance tests without a pytest: reference are recorded, not executed; the "
                                     "target metric is measured on the staged candidate instead"})
            self._log("acceptance_not_executed", ctx, {"tests": list(skipped)})
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
                               base_sha=ctx["base_sha"], diff_text=diff, model=self.c.models.get(agent),
                               stop_check=self.d.stop_check, max_cli_turns=self.c.max_cli_turns)
        out = await sess.run()
        ctx["turns_used"] += 1
        if out.status == "stopped":                     # an owner STOP is not a failed review: nothing was decided
            self._save(ctx)
            self._charge(ctx, turns=1)
            raise Blocked(out.reason if str(out.reason).startswith("owner STOP") else f"owner STOP: {out.reason}")
        if out.status != "ok":
            gate.record_failure(agent, f"{out.status}: {out.reason}")
            ctx["gate"] = gate.to_dict()
            self._charge(ctx, turns=1)
            raise Blocked(gate.blocked_reason)
        self._charge(ctx, turns=1)
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
        evaluation = await self._evaluate_candidate(ctx, goal)          # REJECT -> Blocked before the owner is asked
        if goal.risk_tier == "docs_tests" and self.c.level >= self.c.auto_deploy_min_level:
            await self._deploy(ctx)
            return
        self._capture_trace(ctx, "USER_APPROVAL")       # the evidence trace is there while the owner decides
        self._to(ctx, "USER_APPROVAL", {"sha": ctx["sha"], "rollback": goal_rollback(goal),
                                        "approvals": gate.approvals(), "staging_ok": True,
                                        "evaluation": evaluation,
                                        "not_executed": [t for e in ctx["evidence"] if e.get("status") == "NOT_EXECUTED"
                                                         for t in e.get("tests", [])],
                                        "weights": WEIGHTS, "learning_kind": LEARNING_KIND})

    async def _evaluate_candidate(self, ctx: dict, goal: Goal) -> dict:
        """Measure the STAGED candidate and apply the metrics gate BEFORE approval (deployed=False): the owner is
        never asked to approve something whose target metric did not improve or whose protected metrics regressed.
        With a `candidate_probe` the metrics are measured in the candidate's own worktree; without one the shared
        probe is used and the evaluation says so."""
        if self.d.candidate_probe is not None:
            after = await maybe_await(self.d.candidate_probe(goal, Path(ctx["worktree"])))
            measured_on = "candidate_worktree"
        else:
            after = await maybe_await(self.d.metrics_probe(goal))
            measured_on = "shared_probe (not candidate-specific)"
        if not isinstance(after, dict):
            raise Blocked("ambiguous state: the staged candidate's metrics could not be measured")
        verdict = await maybe_await(self.d.gate_decide(ctx["metrics_before"], after, goal.target_metric,
                                                       goal.protected_metrics, self._thresholds(goal), deployed=False))
        ok = verdict_flag(verdict)
        detail = verdict.as_dict() if hasattr(verdict, "as_dict") else (verdict if isinstance(verdict, dict) else {})
        evaluation = {"decision": getattr(verdict, "decision", None) or (detail.get("verdict") or ""),
                      "accepted": bool(ok), "measured_on": measured_on, "before": ctx["metrics_before"],
                      "after": after, "reasons": list(getattr(verdict, "reasons", None) or detail.get("reasons") or [])}
        ctx["candidate_eval"] = evaluation
        self._log("staging_evaluation", ctx, {"sha": ctx["sha"], **evaluation})
        if ok is None:
            raise Blocked("ambiguous state: staged evaluation verdict")
        if not ok:
            raise Blocked("staged evaluation rejected the candidate: " + "; ".join(evaluation["reasons"])[:300],
                          evaluation=evaluation)
        return evaluation

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
                                                       goal.protected_metrics, self._thresholds(goal), deployed=True))
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
                              "metrics_after": ctx.get("metrics_after"), "weights": WEIGHTS,
                              "learning_kind": LEARNING_KIND, **(extra or {})})
        if state == "COMPLETE":
            self._propose_skill(ctx)

    def _propose_skill(self, ctx: dict) -> None:
        """An accepted cycle may leave a skill PROPOSAL (a file, poison-filtered and deduplicated). It becomes a skill
        only through the owner's `SkillStore.confirm` (all five conditions again). Never raises."""
        if self.d.skills is None:
            return
        try:
            from .skills import SkillSpec
            goal = self._goal(ctx)
            steps = [{"action": "run_tests", "suite": e["suite"], "request_hash": e["request_hash"]}
                     for e in ctx["evidence"] if e.get("status") != "NOT_EXECUTED"]
            name = "auto-" + re.sub(r"[^a-z0-9]+", "-", goal.goal_id.lower()).strip("-")[:48]
            spec = SkillSpec(
                name=name, description=f"Bounded change procedure that completed goal {goal.goal_id} "
                                       f"({goal.risk_tier}) with passing acceptance tests and dual review.",
                app="bossman", env="autonomy-cycle", parameters={"goal_id": "goal identifier",
                                                                 "scope": "allowed path globs"},
                preconditions=["constitution pinned by the owner", "no owner STOP", "engineering lease free"],
                steps=steps or [{"action": "review", "suite": "none", "request_hash": ctx.get("trace_hash", "")}],
                failure_detection=["acceptance tests fail", "a protected metric regresses"],
                rollback=[goal_rollback(goal)], timeout_s=max(60, int(goal.budget.max_minutes) * 60),
                source_trace_hash=ctx.get("trace_hash", ""), evidence=[ctx.get("trace_hash", "")],
                keywords=[goal.risk_tier, "autonomy"])
            ahash = self.d.skills.propose(spec, by="autonomy-cycle", trace_hash=ctx.get("trace_hash", ""))
            self._log("skill_proposal", ctx, {"artifact_hash": ahash, "weights": WEIGHTS,
                                              "learning_kind": LEARNING_KIND})
        except Exception as exc:  # noqa: BLE001 - a proposal must never break the loop
            self._log("skill_proposal_skipped", ctx, {"reason": f"{type(exc).__name__}: {str(exc)[:200]}"})

    def _experience(self, ctx: dict, state: str, reason: str = "") -> None:
        """One lesson CANDIDATE per (state, sha) stop: retrieval memory with provenance, UNVERIFIED, deduplicated.
        Never raises: the loop does not depend on its memory."""
        if self.d.experience is None or state not in ("USER_APPROVAL", "COMPLETE", "ROLLED_BACK", "BLOCKED",
                                                       "REJECTED"):
            return
        key = f"{state}:{ctx.get('sha') or ''}:{reason[:40] if state == 'BLOCKED' else ''}"
        done = ctx.setdefault("lessons", {})
        if key in done:
            return
        try:
            head = self.d.journal.head() if hasattr(self.d.journal, "head") else ""
            res = self.d.experience.record(goal=self._goal(ctx), state=state, reason=reason,
                                           trace_hash=ctx.get("trace_hash") or "", sha=ctx.get("sha") or "",
                                           writer=(ctx.get("route") or {}).get("writer", ""), journal_head=head)
        except Exception as exc:  # noqa: BLE001 - experience must not break the loop
            self._log("lesson.skipped", ctx, {"state": state, "reason": f"{type(exc).__name__}: {str(exc)[:200]}"})
            return
        done[key] = (res or {}).get("lesson_id", "")
        self._save(ctx)

    def _capture_trace(self, ctx: dict, state: str) -> str:
        goal = self._goal(ctx)
        gate = ctx.get("gate") or {}
        trace = capture_trace(
            goal_id=goal.goal_id, start_state={"base_sha": ctx["base_sha"], "metrics_before": ctx["metrics_before"]},
            instruction=goal.desired_result,
            actions=[{"action": "run_tests", "suite": e["suite"], "request_hash": e["request_hash"], "ok": e["ok"]}
                     for e in ctx["evidence"] if e.get("status") != "NOT_EXECUTED"]
                    + [{"action": "apply_candidate", "request_hash": d["request_hash"], "ok": d["ok"]}
                       for d in ctx["deploys"]],
            observations=[w.get("summary", "") for w in ctx["writer_runs"]]
                         + [f"NOT_EXECUTED acceptance tests: {t}" for e in ctx["evidence"]
                            if e.get("status") == "NOT_EXECUTED" for t in e.get("tests", [])], errors=[],
            recovery=[f"revision {i + 1}" for i in range(ctx["revisions"])],
            result={"state": state, "sha": ctx["sha"], "metrics_after": ctx.get("metrics_after")},
            reviews=[{"reviewer": r, **v} for r, v in sorted((gate.get("verdicts") or {}).items())],
            user_decision=(ctx.get("user_decision") or {}).get("decision"), origin="executed")
        atomic_write_bytes(self._dir(goal.goal_id) / "trace.json",
                           json.dumps(trace.as_dict(), ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"))
        ctx["trace_hash"] = trace.trace_hash()
        ctx["trace_state"] = state
        self._log("trace_captured", ctx, {"trace_hash": ctx["trace_hash"], "state": state})
        self._save(ctx)
        return ctx["trace_hash"]


# ------------------------------------------------------------------ real wiring (OWNER_REQUIRED)


def default_data_dir() -> Path:
    from ..config import _data_dir
    return Path(_data_dir())


def real_deps(root: Path, repo: Path, *, data_dir: Path | None = None, constitution_path: Any = None,
              pin_path: Any = None, stop_check: Callable[[], str] | None = None) -> CycleDeps:  # pragma: no cover
    """Line A objects (same data dir as the dashboard/CLI) + the real Claude/Codex CLIs, with the owner STOP wired
    into the driver, the hand broker and the worker sessions, the daily cap, the experience writer and the metrics
    probes that measure the candidate's OWN code."""
    from . import constitution, metrics_gate, stop as stop_mod
    from .goals import GoalStore
    from .hands import build_default_broker
    from .identity_task import GOAL_ID, probe_in_checkout
    from .journal import Journal
    from .lease import EngineeringLease
    from .metrics_probes import probe_tests_failed
    from .staging import build_default_runner

    journal = Journal(root)
    data = Path(data_dir) if data_dir is not None else default_data_dir()
    stop = stop_check or stop_mod.checker(root, data)

    def verify() -> Any:
        return constitution.verify(constitution_path, pin_path)

    model = os.environ.get("BOSSMAN_AUTONOMY_JEFF_MODEL", "").strip()
    endpoint = os.environ.get("BOSSMAN_AUTONOMY_JEFF_ENDPOINT", "http://127.0.0.1:11434/v1")

    async def measure(goal: Goal, checkout: Path) -> dict | None:
        if goal.goal_id == GOAL_ID:
            return await probe_in_checkout(checkout, model=model, endpoint=endpoint) if model else None
        if goal.target_metric == "tests.failed":
            return await probe_tests_failed(checkout, goal.acceptance_tests)
        return None                        # no honest probe for this metric: the goal is not started

    async def probe(goal: Goal) -> dict | None:                      # the baseline: the source checkout's own code
        return await measure(goal, Path(repo))

    return CycleDeps(goals=GoalStore(root, journal), lease=EngineeringLease(root, journal=journal),
                     hands=build_default_broker(root, journal, constitution_status=verify, stop_check=stop),
                     journal=journal, staging=build_default_runner(root, repo, journal=journal),
                     gate_decide=metrics_gate.decide, constitution_verify=verify, metrics_probe=probe,
                     source_repo=repo, work_root=root / "cycles", runner=TreeRunner(stop_check=stop),
                     savings=SavingsLedger(root), skills=SkillStore(root, journal=journal), stop_check=stop,
                     candidate_probe=measure, daily=DailyBudget(root, journal=journal),
                     experience=ExperienceWriter(root, journal=journal))


_real_deps = real_deps      # the old private name


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
