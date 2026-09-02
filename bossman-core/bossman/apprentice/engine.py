"""UniversalComputerApprentice — deterministic state machine.

RECEIVE_TASK -> PLAN -> OBSERVE -> ACT -> VERIFY -> CONTINUE | RECOVER | FALLBACK |
WAIT_APPROVAL | SUCCEED | FAIL.  Planner / Observer / Actuator / Verifier are injected
(simulators in tests).  Every ACT is preceded, in fixed order, by: freshness, window
identity, semantic target resolution, injection channel check, negative-lesson
pre-check, loop guard, desktop policy, risk/approval, side-effect idempotency, budget.
"""
from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import dataclass, field, replace
from typing import Any, Callable
from urllib.parse import urlparse

from bossman.computer_operator.loop_guard import LoopGuard
from bossman.computer_operator.models import ActionKind, ComputerAction, ExpectedState, Observation, TaskMode
from bossman.computer_operator.policy import ComputerPolicy
from bossman.computer_operator.verifier import Verifier as _CoVerifier
from bossman.cybersec.injection import inspect as firewall_inspect
from bossman.cybersec.trust import TrustLevel

from . import flags
from ._bootstrap import trace
from .errors import (ApprenticeDisabled, ApprovalInvalid, ApprovalRequired, BudgetExhausted, DuplicateAction,
                     FalseCompletion, FlagDisabled, IdempotencyKeyRequired, InjectionBlocked, InvalidObservation,
                     LessonBlocked, LoopDetected, PolicyRefused, ReceiptInvalid, SecretInRecord, SelectorDrift,
                     StaleObservation, VerificationFailed, WrongWindow)
from .guards import (ApprovalRegistry, SideEffectLedger, freshness_error, observation_hash, observation_ref,
                     resolve_target, side_effect_id, step_digest)
from .models import (APPROVAL_RISK, TERMINAL, TRANSITIONS, WRITE_RISK, ActionRecord, AppIdentity, ApprenticeState,
                     ApprenticeTask, EffectReceipt, ObservationRef, Plan, PlanStep, RiskClass, TaskResult, Verification,
                     expected_as_dict, new_id)

REDACTED = "***REDACTED***"
_NO_TARGET_KINDS = frozenset({ActionKind.NOOP, ActionKind.WAIT, ActionKind.FOCUS, ActionKind.TAKE_SCREENSHOT,
                              ActionKind.APP_LAUNCH, ActionKind.APP_CLOSE, ActionKind.BROWSER, ActionKind.HOTKEY,
                              ActionKind.SCROLL, ActionKind.COMPLETE, ActionKind.FAIL})
_PASSIVE_KINDS = frozenset({ActionKind.NOOP, ActionKind.WAIT, ActionKind.TAKE_SCREENSHOT, ActionKind.FOCUS})


@dataclass(slots=True)
class ObservationView:
    """What the planner is allowed to see: sanitized text, trust marked, no raw secrets."""
    ref: ObservationRef
    foreground: dict
    elements: list[dict]
    text: str
    untrusted: bool
    injection_severity: str
    findings: tuple[str, ...] = ()
    sensitive: bool = False


class DefaultVerifier:
    """Fresh post-observation + computer_operator.Verifier expectations + checkpoint predicates."""

    def __init__(self, checkpoints: dict[str, Callable[[Observation], tuple[bool, str]]] | None = None,
                 principal_id: str = "verifier:computer_operator") -> None:
        self.checkpoints = dict(checkpoints or {})
        self.principal_id = principal_id
        self._co = _CoVerifier()

    def verify(self, step: PlanStep, action: ComputerAction, before: Observation, after: Observation) -> Verification:
        if int(after.generation) <= int(before.generation) or after.id == before.id:
            return Verification("freshness", False, "post-action observation is not fresh", self.principal_id)
        methods: list[str] = ["freshness"]
        if not step.expected.is_empty():
            v = self._co.verify(action, after)
            methods.append("expected_state")
            if not v.ok:
                return Verification("+".join(methods), False, v.reason, self.principal_id)
        if step.checkpoint:
            pred = self.checkpoints.get(step.checkpoint)
            methods.append(f"checkpoint:{step.checkpoint}")
            if pred is None:
                return Verification("+".join(methods), False, f"unknown checkpoint {step.checkpoint!r}", self.principal_id)
            ok, why = pred(after)
            if not ok:
                return Verification("+".join(methods), False, f"checkpoint {step.checkpoint} failed: {why}", self.principal_id)
        significant = step.side_effecting or step.is_goal or step.risk is not RiskClass.LOW
        if significant and len(methods) == 1:
            return Verification("freshness", False, "significant action without verification method", self.principal_id)
        if len(methods) == 1 and step.kind not in _PASSIVE_KINDS:
            methods.append("state_change")
            if observation_hash(before) == observation_hash(after):
                return Verification("+".join(methods), False, "state did not change", self.principal_id)
        return Verification("+".join(methods), True, "verified on fresh observation", self.principal_id)


@dataclass(slots=True)
class _Ctx:
    task: ApprenticeTask
    queue: deque
    state: ApprenticeState = ApprenticeState.RECEIVE_TASK
    transitions: list = field(default_factory=list)
    records: list = field(default_factory=list)
    checkpoints: list = field(default_factory=list)
    latest: Observation | None = None
    generation: int = 0
    steps_used: int = 0
    recoveries: int = 0
    fallbacks: int = 0
    last_failure: str = ""
    failure_signatures: list = field(default_factory=list)
    goal_verified: bool = False
    focus_inserted_for: set = field(default_factory=set)
    pending_step: PlanStep | None = None
    pending_digest: str = ""
    dropped_records: int = 0
    resume_from: dict | None = None
    latest_binding: dict = field(default_factory=dict)
    action_id: str = ""


class UniversalComputerApprentice:
    def __init__(self, *, planner: Any, observer: Any, actuator: Any, verifier: DefaultVerifier | None = None,
                 approval_gate: Callable[[PlanStep, str, str], Any] | None = None,
                 ledger: SideEffectLedger | None = None, approvals: ApprovalRegistry | None = None,
                 on_record: Callable[[ActionRecord], None] | None = None,
                 lessons: list[dict] | None = None, fallback: Callable[..., Plan | None] | None = None,
                 clock: Callable[[], float] = time.time, policy: ComputerPolicy | None = None,
                 loop_guard: LoopGuard | None = None, allowed_skew_s: float = 300.0) -> None:
        self.planner, self.observer, self.actuator = planner, observer, actuator
        self.verifier = verifier or DefaultVerifier()
        self.approval_gate = approval_gate
        self.ledger = ledger if ledger is not None else SideEffectLedger()   # __len__ makes an empty ledger falsy
        self.approvals = approvals or ApprovalRegistry(clock)
        self.on_record = on_record
        self.lessons = list(lessons or [])
        self.fallback = fallback
        self.clock = clock
        self.policy = policy or ComputerPolicy()
        self.loop_guard = loop_guard or LoopGuard()
        self.allowed_skew_s = float(allowed_skew_s)
        self._ctx: _Ctx | None = None

    # ------------------------------------------------------------ public API
    def preview(self, task: ApprenticeTask) -> list[dict]:
        """Proposal P1: plan + risk table without observing or acting."""
        if not flags.enabled(flags.DRY_RUN_PREVIEW):
            raise FlagDisabled(f"{flags.DRY_RUN_PREVIEW} is off")
        plan = self.planner.plan(task, None)
        out = []
        for s in plan.steps:
            label = s.target.label() if s.target else ""
            out.append({"step_id": s.step_id, "kind": s.kind.value, "target": label, "risk": s.risk.value,
                        "needs_approval": s.risk in APPROVAL_RISK, "side_effecting": s.side_effecting,
                        "side_effect_id": side_effect_id(task.task_id, s.step_id, s.kind.value, label, s.text, s.args)
                        if s.side_effecting else "", "checkpoint": s.checkpoint, "is_goal": s.is_goal})
        return out

    def run(self, task: ApprenticeTask, *, resume_from: dict | None = None) -> TaskResult:
        if not flags.master_enabled():
            raise ApprenticeDisabled(f"{flags.MASTER} is off: the apprentice refuses to execute")
        ctx = _Ctx(task=task, queue=deque(), resume_from=resume_from if flags.enabled(flags.CHECKPOINT_RESUME) else None)
        self._ctx = ctx
        self.loop_guard.reset()
        self._go(ctx, ApprenticeState.PLAN, "task received")
        try:
            plan = self.planner.plan(task, None)
        except Exception as exc:  # noqa: BLE001 — planner failure = honest FAIL
            return self._fail(ctx, f"planner error: {exc!r}")
        if not plan or not plan.steps:
            return self._fail(ctx, "empty plan")
        ctx.queue = deque(plan.steps)
        return self._loop(ctx)

    def resume(self, approval: Any) -> TaskResult:
        """Continue a WAIT_APPROVAL task with an owner decision (one-time, digest-bound)."""
        ctx = self._ctx
        if ctx is None or ctx.state is not ApprenticeState.WAIT_APPROVAL or ctx.pending_step is None:
            raise ApprovalInvalid("nothing is waiting for approval")
        why = self.approvals.validate(approval, digest=ctx.pending_digest, scope=ctx.task.task_id)
        if why:
            self._record_refusal(ctx, ctx.pending_step, None, ApprovalInvalid(why))
            return self._fail(ctx, f"approval invalid: {why}")
        self.approvals.consume(approval)
        step = ctx.pending_step
        digest = ctx.pending_digest
        ctx.pending_step, ctx.pending_digest = None, ""
        ctx.queue.appendleft(replace(step, args={**step.args, "_approved_digest": digest}))
        self._go(ctx, ApprenticeState.ACT, "approval consumed")
        return self._loop(ctx, skip_first_transition=True)

    @property
    def transitions(self) -> list[tuple[str, str, str]]:
        return list(self._ctx.transitions) if self._ctx else []

    def last_view(self) -> ObservationView | None:
        """Sanitized view of the latest observation (what the planner would see now)."""
        return self._view(self._ctx.latest) if (self._ctx and self._ctx.latest is not None) else None

    # ------------------------------------------------------------ state machine
    def _go(self, ctx: _Ctx, new: ApprenticeState, reason: str) -> None:
        if new not in TRANSITIONS[ctx.state]:
            raise RuntimeError(f"illegal transition {ctx.state.value} -> {new.value}")
        ctx.transitions.append((ctx.state.value, new.value, reason))
        ctx.state = new

    def _result(self, ctx: _Ctx, reason: str) -> TaskResult:
        t = ctx.task
        return TaskResult(task_id=t.task_id, run_id=t.run_id, session_id=t.session_id, state=ctx.state,
                          reason=reason, records=list(ctx.records), checkpoints_reached=list(ctx.checkpoints),
                          steps_used=ctx.steps_used, recoveries=ctx.recoveries, fallbacks=ctx.fallbacks,
                          pending_step=ctx.pending_step, pending_digest=ctx.pending_digest, head_sha=t.head_sha,
                          environment=t.environment, goal=t.goal)

    def _fail(self, ctx: _Ctx, reason: str) -> TaskResult:
        if ctx.state not in TERMINAL:
            self._go(ctx, ApprenticeState.FAIL, reason)
        return self._result(ctx, reason)

    def _loop(self, ctx: _Ctx, *, skip_first_transition: bool = False) -> TaskResult:
        first = skip_first_transition
        while ctx.state not in TERMINAL:
            if not ctx.queue:
                if ctx.goal_verified:
                    self._go(ctx, ApprenticeState.SUCCEED, "goal checkpoint verified")
                    return self._result(ctx, "goal checkpoint verified on fresh observation")
                self._record_refusal(ctx, None, ctx.latest, FalseCompletion("plan ended without a verified goal"))
                return self._fail(ctx, "false completion: plan ended without a verified goal checkpoint")
            step = ctx.queue[0]
            if step.kind in (ActionKind.COMPLETE, ActionKind.FAIL):
                ctx.queue.popleft()
                if step.kind is ActionKind.FAIL:
                    return self._fail(ctx, f"planner gave up: {step.precondition or 'no reason'}")
                if not ctx.goal_verified:
                    self._record_refusal(ctx, step, ctx.latest, FalseCompletion("COMPLETE without a verified goal"))
                    return self._fail(ctx, "false completion: COMPLETE without a verified goal checkpoint")
                continue
            if not first:
                if ctx.steps_used >= ctx.task.max_steps:
                    return self._fail(ctx, f"budget exhausted: {ctx.task.max_steps} steps")
                self._go(ctx, ApprenticeState.OBSERVE, f"before {step.step_id}")
                ctx.action_id = new_id("act")
                try:
                    obs = self._observe(ctx, action_id=ctx.action_id)
                except InvalidObservation as exc:
                    self._record_refusal(ctx, step, None, exc)
                    res = self._recover(ctx, step, exc)
                    if res is not None:
                        return res
                    continue
                if ctx.resume_from and self._try_resume(ctx, obs):
                    self._go(ctx, ApprenticeState.PLAN, "resumed from checkpoint")
                    continue
                self._go(ctx, ApprenticeState.ACT, step.step_id)
            first = False
            obs = ctx.latest
            view = self._view(obs)
            try:
                self._act(ctx, step, obs, view)
            except ApprovalRequired as exc:
                ctx.pending_step, ctx.pending_digest = step, str(exc.args[1]) if len(exc.args) > 1 else ""
                self._go(ctx, ApprenticeState.WAIT_APPROVAL, str(exc.args[0]))
                if self.approval_gate is not None:
                    decision = self.approval_gate(step, ctx.pending_digest, ctx.task.task_id)
                    if decision is not None:
                        return self.resume(decision)
                return self._result(ctx, "waiting for owner approval")
            except (StaleObservation, WrongWindow, SelectorDrift, InjectionBlocked, LessonBlocked, LoopDetected,
                    PolicyRefused, BudgetExhausted, VerificationFailed, IdempotencyKeyRequired) as exc:
                if not isinstance(exc, VerificationFailed):        # verification / receipt failures are already recorded
                    self._record_refusal(ctx, step, obs, exc)
                if isinstance(exc, BudgetExhausted):
                    return self._fail(ctx, str(exc))
                res = self._recover(ctx, step, exc)
                if res is not None:
                    return res
                continue
            ctx.steps_used += 1
            ctx.queue.popleft()
            if step.checkpoint:
                ctx.checkpoints.append(step.checkpoint)
            self._go(ctx, ApprenticeState.CONTINUE, f"{step.step_id} verified")
            if step.is_goal:
                ctx.goal_verified = True
                self._go(ctx, ApprenticeState.SUCCEED, "goal checkpoint verified")
                return self._result(ctx, "goal checkpoint verified on fresh observation")
        return self._result(ctx, ctx.last_failure)

    # ------------------------------------------------------------ observe
    def _observe(self, ctx: _Ctx, *, action_id: str = "", side_effect_id: str = "") -> Observation:
        """Fresh observation bound to task/run/session/action. Rejects observations from the
        future (clock skew) and observations the observer binds to another task/run/session."""
        t = ctx.task
        binding = {"task_id": t.task_id, "run_id": t.run_id, "session_id": t.session_id, "action_id": action_id,
                   "side_effect_id": side_effect_id}
        obs = self.observer.observe(binding=binding) if getattr(self.observer, "accepts_binding", False) else self.observer.observe()
        now = self.clock()
        if float(obs.created_at) > now + self.allowed_skew_s:
            raise InvalidObservation(f"observation {obs.id} is from the future ({obs.created_at:.0f} > now {now:.0f} + skew)")
        if float(obs.created_at) <= 0:
            raise InvalidObservation(f"observation {obs.id} has no timestamp")
        binding_of = getattr(self.observer, "binding_of", None)
        if callable(binding_of):
            got = binding_of(obs) or {}
            if not got:
                raise InvalidObservation(f"observation {obs.id} is not bound to a task/run/session")
            for k in ("task_id", "run_id", "session_id"):
                if str(got.get(k, "")) != binding[k]:
                    raise InvalidObservation(f"observation {obs.id} belongs to another task/run/session ({k}={got.get(k)!r})")
        ctx.generation = int(obs.generation)
        ctx.latest = obs
        ctx.latest_binding = binding
        return obs

    def _ref(self, ctx: _Ctx, obs: Observation, **override: str) -> ObservationRef:
        b = {**ctx.latest_binding, **override} if obs is ctx.latest else {**{k: "" for k in ctx.latest_binding}, **override}
        return ObservationRef.of(obs, observation_hash(obs), **{k: b.get(k, "") for k in ("task_id", "run_id", "session_id", "action_id", "side_effect_id")})

    def _view(self, obs: Observation) -> ObservationView:
        els = obs.ui_tree.get("elements", []) if isinstance(obs.ui_tree, dict) else []
        raw = "\n".join([obs.summary or ""] + [str(e.get("text") or "") for e in els if isinstance(e, dict)])
        verdict = firewall_inspect(raw, source_trust=TrustLevel.UNTRUSTED)
        tr = trace()
        ref = self._ref(self._ctx, obs) if self._ctx is not None else observation_ref(obs)
        return ObservationView(ref=ref, foreground=dict(obs.foreground or {}),
                               elements=[dict(e) for e in els if isinstance(e, dict)],
                               text=tr.redact_text(verdict.sanitized), untrusted=not verdict.safe,
                               injection_severity=verdict.severity,
                               findings=tuple(f.pattern_id for f in verdict.findings), sensitive=bool(obs.sensitive))

    def _try_resume(self, ctx: _Ctx, obs: Observation) -> bool:
        """Proposal P2: skip up to a previously verified checkpoint only if the fresh
        observation still satisfies it."""
        want = ctx.resume_from or {}
        ctx.resume_from = None
        name = str(want.get("checkpoint") or "")
        pred = self.verifier.checkpoints.get(name) if isinstance(self.verifier, DefaultVerifier) else None
        if not name or pred is None or not pred(obs)[0]:
            return False
        idx = next((i for i, s in enumerate(ctx.queue) if s.checkpoint == name), None)
        if idx is None:
            return False
        for _ in range(idx + 1):
            ctx.queue.popleft()
        ctx.checkpoints.append(name)
        return True

    # ------------------------------------------------------------ act
    def _act(self, ctx: _Ctx, step: PlanStep, obs: Observation, view: ObservationView) -> bool:
        task = ctx.task
        # 1. freshness (id + generation + hash) — also asks the observer whether the world moved on
        why = freshness_error(view.ref, ctx.latest, current_generation=ctx.generation)
        if why:
            raise StaleObservation(why)
        is_current = getattr(self.observer, "is_current", None)
        if callable(is_current) and not is_current(obs):
            raise StaleObservation("the environment changed after the observation was taken")
        # 2. window identity
        ok, why = step.app.matches(obs.foreground)
        if not ok:
            raise WrongWindow(why)
        # 3. semantic target
        label = step.target.label() if step.target else ""
        if step.target is not None:
            res = resolve_target(step.target, obs)
            if res.element is None:
                raise SelectorDrift(f"target {label} not resolvable in fresh UI tree (state={res.state}, score={res.score})")
        # 4. injection channel
        if step.derived_from_observation and view.untrusted:
            raise InjectionBlocked(f"action derived from untrusted observed text (findings={list(view.findings)})")
        if step.kind is ActionKind.BROWSER and step.args.get("op") == "navigate":
            host = urlparse(str(step.args.get("url", ""))).hostname or ""
            if step.allowed_domains and not any(host == d or host.endswith("." + d) for d in step.allowed_domains):
                raise InjectionBlocked(f"navigation to {host!r} is outside allowed domains {list(step.allowed_domains)}")
        # 4b. writes need an explicit idempotency key (refused BEFORE any execution)
        if step.risk in WRITE_RISK and not step.idempotency_key:
            raise IdempotencyKeyRequired(f"{step.risk.value} action {step.step_id} has no idempotency_key")
        # 5. negative-lesson pre-check (P4)
        if flags.enabled(flags.LESSON_PRECHECK):
            for lesson in self.lessons:
                if (lesson.get("app", "") in (step.app.app, "") and lesson.get("target_label") == label
                        and lesson.get("action_kind") == step.kind.value):
                    raise LessonBlocked(f"verified negative lesson {lesson.get('lesson_id', '?')}: {lesson.get('summary', '')}")
        action = self._computer_action(step, label, ctx.action_id)
        # 6. loop guard (existing)
        verdict = self.loop_guard.check(action, obs)
        if verdict.tripped:
            raise LoopDetected(f"{verdict.kind}: {verdict.reason}")
        # 7. desktop policy (existing)
        decision = self.policy.classify(action, mode=TaskMode.CONTROL)
        if not decision.allow:
            raise PolicyRefused(decision.reason)
        # 8. risk -> approval (digest-bound, one-time)
        clean_args = {k: v for k, v in step.args.items() if k != "_approved_digest"}
        digest = step_digest(task.task_id, step.step_id, step.kind.value, label, step.text, clean_args)
        needs_approval = step.risk in APPROVAL_RISK or decision.requires_approval
        if needs_approval and not step.args.get("_approved_digest") == digest:
            raise ApprovalRequired(f"{step.risk.value} action needs owner approval ({decision.reason or 'risk'})", digest)
        # 9. side-effect idempotency
        seid = (side_effect_id(task.task_id, step.step_id, step.kind.value, label, step.text, clean_args, step.idempotency_key)
                if (step.side_effecting or step.risk in WRITE_RISK) else "")
        duplicate = False
        result: dict | None = None
        if seid:
            claimed, prior = self.ledger.claim(seid)
            if not claimed:
                duplicate, result = True, (prior or {"detail": "in progress elsewhere"})
        # 10. budget already checked in loop; actuate
        pre_ref = view.ref
        receipt: EffectReceipt | None = None
        if not duplicate:
            try:
                raw = self.actuator.act(step, obs, action_id=action.id, side_effect_id=seid)
            except Exception as exc:  # noqa: BLE001 — actuator failure is a recoverable step failure
                if seid:
                    self.ledger.abandon(seid)
                self._append_record(ctx, step, label, action, pre_ref, None, None, f"actuator_error:{exc!r}", seid, view,
                                    error_code="actuator_error")
                raise VerificationFailed(f"actuator error: {exc!r}")
            if seid:
                why = self._receipt_error(raw, seid, action, step)
                if why:
                    self.ledger.abandon(seid)               # NOT completed: the effect is neither duplicated nor silently lost
                    self._append_record(ctx, step, label, action, pre_ref, None, None, f"receipt_invalid: {why}", seid, view,
                                        error_code="receipt_invalid")
                    raise ReceiptInvalid(why)
                receipt = raw
                result = {"receipt": receipt.as_dict()}
                self.ledger.complete(seid, result)
            else:
                result = raw.as_dict() if isinstance(raw, EffectReceipt) else dict(raw or {})
        elif result and isinstance(result.get("receipt"), dict):
            receipt = EffectReceipt(**result["receipt"])
        # VERIFY on a fresh post-observation (bound to this action + side effect)
        self._go(ctx, ApprenticeState.VERIFY, step.step_id)
        after = self._observe(ctx, action_id=action.id, side_effect_id=seid)
        ver = self.verifier.verify(step, action, obs, after)
        self.loop_guard.record(action, obs, after, ver.ok)
        self._append_record(ctx, step, label, action, pre_ref, self._ref(ctx, after), ver,
                            "ok" if ver.ok else "verification_failed", seid, view, duplicate=duplicate, receipt=receipt)
        if not ver.ok:
            raise VerificationFailed(ver.reason)
        return True

    def _receipt_error(self, raw: Any, seid: str, action: ComputerAction, step: PlanStep) -> str:
        """Empty string = receipt verified against the request."""
        if not isinstance(raw, EffectReceipt):
            return f"actuator returned {type(raw).__name__}, not an EffectReceipt"
        if raw.side_effect_id != seid:
            return f"receipt side_effect_id {raw.side_effect_id[:12]!r} != requested {seid[:12]!r}"
        if raw.action_id != action.id:
            return f"receipt action_id {raw.action_id!r} != {action.id!r}"
        if raw.action_type != step.kind.value:
            return f"receipt action_type {raw.action_type!r} != action {step.kind.value!r}"
        now = self.clock()
        if not raw.observed_at or raw.observed_at <= 0 or raw.observed_at > now + self.allowed_skew_s:
            return f"receipt observed_at {raw.observed_at!r} is missing or from the future"
        if not str(raw.evidence_source).strip():
            return "receipt without evidence_source"
        return ""

    def _computer_action(self, step: PlanStep, label: str, action_id: str = "") -> ComputerAction:
        return ComputerAction(id=action_id or new_id("act"), kind=step.kind, expected=step.expected, target=label or None,
                              text=step.text or None, args={k: v for k, v in step.args.items() if k != "_approved_digest"},
                              source=step.source, idempotency_key=step.idempotency_key or step.step_id)

    # ------------------------------------------------------------ recovery
    def _recover(self, ctx: _Ctx, step: PlanStep, exc: Exception) -> TaskResult | None:
        """Leaves the machine in RECOVER / PLAN / CONTINUE (OBSERVE is legal from each)."""
        ctx.last_failure = f"{getattr(exc, 'code', type(exc).__name__)}: {exc}"
        ctx.failure_signatures.append(getattr(exc, "code", type(exc).__name__))
        self._go(ctx, ApprenticeState.RECOVER, ctx.last_failure)
        if ctx.recoveries >= ctx.task.max_recoveries:
            return self._fallback_or_fail(ctx, f"recovery budget exhausted after: {ctx.last_failure}")
        ctx.recoveries += 1
        self.loop_guard.reset()
        if isinstance(exc, StaleObservation):
            return None                                   # loop re-observes the same step
        if isinstance(exc, WrongWindow) and step.step_id not in ctx.focus_inserted_for and step.kind is not ActionKind.FOCUS:
            ctx.focus_inserted_for.add(step.step_id)
            ctx.queue.appendleft(PlanStep(step_id=f"{step.step_id}.focus", kind=ActionKind.FOCUS, app=AppIdentity(),
                                          args={"focus": step.app.as_dict()}, expected=_focus_expected(step.app),
                                          source="recovery"))
            return None
        self._go(ctx, ApprenticeState.OBSERVE, "fresh observation before replan")
        obs = self._observe(ctx)
        self._go(ctx, ApprenticeState.PLAN, "replan after failure")
        try:
            plan = self.planner.replan(ctx.task, self._view(obs), ctx.last_failure, list(ctx.queue))
        except Exception as e:  # noqa: BLE001
            return self._fallback_or_fail(ctx, f"replan error: {e!r}")
        if not plan or not plan.steps:
            return self._fallback_or_fail(ctx, "planner returned no recovery plan")
        ctx.queue = deque(plan.steps)
        return None

    def _fallback_or_fail(self, ctx: _Ctx, reason: str) -> TaskResult | None:
        if self.fallback is not None and flags.enabled(flags.CLAUDE_CODE_FALLBACK) and ctx.fallbacks < ctx.task.max_fallbacks:
            ctx.fallbacks += 1
            if ctx.state is not ApprenticeState.RECOVER:
                self._go(ctx, ApprenticeState.RECOVER, reason)
            self._go(ctx, ApprenticeState.FALLBACK, reason)
            try:
                plan = self.fallback(ctx.task, reason, self._view(ctx.latest) if ctx.latest else None)
            except Exception as e:  # noqa: BLE001
                return self._fail(ctx, f"fallback error: {e!r}")
            if plan and plan.steps:
                self._go(ctx, ApprenticeState.PLAN, "fallback plan")
                ctx.queue = deque(plan.steps)
                ctx.recoveries = 0
                return None
            return self._fail(ctx, f"fallback produced no plan: {reason}")
        return self._fail(ctx, reason)

    # ------------------------------------------------------------ records
    def _record_refusal(self, ctx: _Ctx, step: PlanStep | None, obs: Observation | None, exc: Exception) -> None:
        label = step.target.label() if (step and step.target) else ""
        action = self._computer_action(step, label, ctx.action_id) if step else ComputerAction.make(ActionKind.NOOP)
        pre = self._ref(ctx, obs) if obs is not None else ObservationRef("none", -1, "", 0.0, ctx.task.task_id, ctx.task.run_id,
                                                                          ctx.task.session_id, ctx.action_id)
        s = step or PlanStep(step_id="(none)", kind=ActionKind.NOOP, app=AppIdentity())
        view = self._view(obs) if obs is not None else None
        self._append_record(ctx, s, label, action, pre, None, None, f"refused:{getattr(exc, 'code', 'error')}", "",
                            view, error=f"{exc}", error_code=getattr(exc, "code", "error"))

    def _append_record(self, ctx: _Ctx, step: PlanStep, label: str, action: ComputerAction, pre: ObservationRef,
                       post: ObservationRef | None, ver: Verification | None, result: str, seid: str,
                       view: ObservationView | None, *, duplicate: bool = False, error: str = "",
                       receipt: EffectReceipt | None = None, error_code: str = "", **_: Any) -> None:
        tr = trace()
        sensitive = bool(step.args.get("sensitive")) or (view.sensitive if view else False) or (
            step.kind is ActionKind.TYPE and ComputerPolicy.refs_secret_args(step.args))
        text_redacted = REDACTED if (sensitive and step.text) else tr.redact_text(step.text)
        args_redacted = tr.redact_obj({k: v for k, v in step.args.items() if k != "_approved_digest"})
        rec = ActionRecord(
            record_id=new_id("rec"), task_id=ctx.task.task_id, run_id=ctx.task.run_id, session_id=ctx.task.session_id,
            application={"app": str((view.foreground if view else {}).get("app", "")),
                         "window_title": str((view.foreground if view else {}).get("title", "")),
                         "url": str((view.foreground if view else {}).get("url", "")),
                         "tab_id": str((view.foreground if view else {}).get("tab_id", "")),
                         "expected": step.app.as_dict()},
            semantic_target=step.target.as_dict() if step.target else {"role": "", "name": "", "text": "",
                                                                     "description": "", "anchors": []},
            action={"kind": step.kind.value, "text_redacted": text_redacted, "args_redacted": args_redacted,
                    "idempotency_key": step.step_id, "source": step.source},
            precondition=step.precondition, pre_observation=pre.as_dict(),
            expected_transition={**expected_as_dict(step.expected), "checkpoint": step.checkpoint},
            post_observation=post.as_dict() if post else None, verification=ver.as_dict() if ver else None,
            result=result if not error else f"{result}: {tr.redact_text(error)}", risk_class=step.risk.value,
            side_effect_id=seid, timestamp=self.clock(),
            evidence_source=f"observer:{getattr(self.observer, 'name', type(self.observer).__name__)}",
            step_id=step.step_id, injection_flagged=bool(view.untrusted) if view else False,
            duplicate_suppressed=duplicate, error_code=error_code or ("verification_failed" if (ver and not ver.ok) else ""),
            checkpoint=step.checkpoint, receipt=receipt.as_dict() if receipt else None)
        blob = json.dumps(rec.to_dict(), default=str, ensure_ascii=False)
        if tr.has_secret(blob):
            ctx.dropped_records += 1
            raise SecretInRecord("record still contains a secret after redaction; not stored")
        ctx.records.append(rec)
        if self.on_record is not None:
            self.on_record(rec)


def _focus_expected(app: AppIdentity) -> ExpectedState:
    return ExpectedState(window_title_contains=app.title_contains or None, foreground_app_contains=app.app or None,
                         url_contains=app.url_contains or None)
