from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import re
import threading
import time
from dataclasses import replace

from .models import ActionKind, ComputerAction, ComputerTask, StepRecord, TaskMode, TaskState
from ..obs import redact, redact_obj
from .policy import ComputerPolicy
from .store import StaleTaskWrite
from .verifier import Verifier
from .loop_guard import LoopGuard

OBSERVATION_REUSE_MAX_AGE_S = 0.75
_CAS_RETRIES = 5
_OWNER_SET_STATES = frozenset({TaskState.PAUSED, TaskState.USER_CONTROL, TaskState.CANCELLED})
_EFFECT_GOAL = re.compile(
    r"\b(create|write|save|send|upload|download|delete|remove|rename|move|copy|"
    r"install|uninstall|publish|post|submit|click|type|edit|change|update|open|"
    r"close|launch|book|buy|pay|transfer|export|render|generate)\b|"
    r"([A-Za-z]:\\|/[^ ]+|\.(txt|json|csv|pdf|docx?|xlsx?|pptx?|png|jpe?g|webp|mp4|mov)\b)",
    re.IGNORECASE,
)


class OwnerStateChanged(RuntimeError):
    pass


class OwnerInterrupted(RuntimeError):
    pass


class ControlLease:
    def __init__(self, ttl_s: float = 30.0):
        self.ttl_s = float(ttl_s)
        self._lock = threading.RLock()
        self._holder = None

    def acquire(self, task_id: str, ttl_s: float | None = None) -> bool:
        with self._lock:
            now = time.monotonic()
            if self._holder and self._holder[0] != task_id and self._holder[1] > now:
                return False
            self._holder = (task_id, now + float(ttl_s if ttl_s is not None else self.ttl_s))
            return True

    def heartbeat(self, task_id: str) -> bool:
        with self._lock:
            if self._holder and self._holder[0] == task_id:
                self._holder = (task_id, time.monotonic() + self.ttl_s)
                return True
            return False

    def release(self, task_id: str) -> bool:
        with self._lock:
            if self._holder and self._holder[0] == task_id:
                self._holder = None
                return True
            return False

    def revoke(self) -> None:
        with self._lock:
            self._holder = None

    def holder(self) -> str | None:
        with self._lock:
            if self._holder and self._holder[1] > time.monotonic():
                return self._holder[0]
            return None


class ComputerOperatorManager:
    def __init__(
        self, *, store, planner, observer, action_router, approval_create, approval_wait, event_emit,
        policy=None, verifier=None, control_lease=None, access_check=None,
        observation_reuse_max_age_s=OBSERVATION_REUSE_MAX_AGE_S,
    ):
        self.store = store
        self.planner = planner
        self.observer = observer
        self.action_router = action_router
        self.approval_create = approval_create
        self.approval_wait = approval_wait
        self.event_emit = event_emit
        self.policy = policy or ComputerPolicy()
        self.verifier = verifier or Verifier()
        self.control_lease = control_lease or ControlLease()
        self.access_check = access_check
        self.locks = {}
        self.global_locked = False
        self.loop_guards = {}
        self.observation_reuse_max_age_s = max(0.0, float(observation_reuse_max_age_s or 0.0))
        self.observations_taken = 0
        self.observations_reused = 0
        self._interrupts = {}
        self.interrupts_observed = 0
        self.dispatches_prevented = 0
        self.phase_seconds = {
            k: 0.0 for k in ("observe", "plan", "admit", "dispatch", "verify", "persist")
        }
        self.phase_calls = {k: 0 for k in self.phase_seconds}

    def create_task(self, goal, *, mode=TaskMode.CONTROL, source="local", owner_device_id=None):
        if self.access_check is not None:
            try:
                self.access_check(owner_device_id, source)
            except TypeError:
                self.access_check(owner_device_id)
        task = ComputerTask.create(goal, mode=mode, source=source, owner_device_id=owner_device_id)
        self._clear_interrupt(task.id)
        self._save(task)
        self._emit(task, "created")
        return task

    async def run(self, task_id):
        lock = self.locks.setdefault(task_id, asyncio.Lock())
        async with lock:
            try:
                task = self._req(task_id)
                if task.terminal:
                    return task.state
                if task.state in {TaskState.PAUSED, TaskState.USER_CONTROL, TaskState.WAITING_APPROVAL}:
                    return task.state
                if task.state is TaskState.RECOVERING and task.pending_action is not None:
                    return TaskState.RECOVERING
                if not self.control_lease.acquire(task_id):
                    return self._fail(
                        task, f"desktop busy: control lease held by {self.control_lease.holder()}"
                    )
                return await self._run_loop(task)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                try:
                    return self._fail(self._req(task_id), f"operator crash:{type(exc).__name__}:{exc}")
                except Exception:
                    return TaskState.FAILED
            finally:
                self.control_lease.release(task_id)

    async def _run_loop(self, task):
        last = ""
        reusable = None
        while True:
            try:
                task = self._req(task.id)
                if task.terminal:
                    return task.state
                if self.global_locked:
                    return self._fail(task, "operator globally locked", TaskState.LOCKED)
                if task.state in {TaskState.PAUSED, TaskState.USER_CONTROL, TaskState.WAITING_APPROVAL}:
                    return task.state
                if task.state is TaskState.RECOVERING and task.pending_action is not None:
                    return TaskState.RECOVERING
                self.control_lease.heartbeat(task.id)
                if self.control_lease.holder() != task.id:
                    return self._fail(task, "desktop control lease lost")
                if task.steps_used >= task.max_steps:
                    return self._fail(task, "max steps exceeded")

                before = self._reuse(reusable, task)
                reusable = None
                if before is None:
                    task.state = TaskState.OBSERVING
                    self._save(task)
                    with self._phase("observe"):
                        before = await self._read_or_interrupt(
                            task.id, self.observer.observe(generation=task.generation)
                        )
                    self.observations_taken += 1
                task.last_observation = before
                plan_generation = task.generation
                task.state = TaskState.PLANNING
                self._save(task)

                try:
                    with self._phase("plan"):
                        action = await self._read_or_interrupt(
                            task.id,
                            self.planner.next_action(
                                goal=task.goal,
                                observation_summary=before.summary,
                                foreground=before.foreground,
                                ui_tree=before.ui_tree,
                                last_result=last,
                                remaining_steps=task.max_steps - task.steps_used,
                            ),
                        )
                except OwnerInterrupted:
                    raise
                except Exception as exc:
                    task.replans_used += 1
                    last = f"planner:{type(exc).__name__}:{exc}"
                    self._save(task)
                    if task.replans_used > task.max_replans:
                        return self._fail(task, "planner replan budget")
                    continue

                if action.kind is ActionKind.COMPLETE:
                    if self._completion_requires_verified_effect(task):
                        task.replans_used += 1
                        last = "completion refused: required external effect has no verified result"
                        task.last_error = last
                        self._save(task)
                        self._emit(task, "completion_refused", reason=last)
                        if task.replans_used > task.max_replans:
                            return self._fail(task, "completion verification budget")
                        continue
                    task.state = TaskState.COMPLETED
                    task.pending_action = None
                    self._save(task)
                    self.loop_guards.pop(task.id, None)
                    self._emit(task, "completed")
                    return task.state

                if action.kind is ActionKind.FAIL:
                    return self._fail(task, action.text or "planner failed")

                with self._phase("admit"):
                    try:
                        decision = self.policy.classify(
                            action, mode=task.mode, locked=self.global_locked, observation=before
                        )
                    except TypeError:
                        decision = self.policy.classify(
                            action, mode=task.mode, locked=self.global_locked
                        )
                if not decision.allow:
                    task.replans_used += 1
                    last = f"policy denied:{decision.reason}"
                    self._save(task)
                    if task.replans_used > task.max_replans:
                        return self._fail(task, "policy/replan budget")
                    continue

                guard = self.loop_guards.setdefault(task.id, LoopGuard())
                gv = guard.check(action, before)
                if gv.tripped:
                    task.replans_used += 1
                    last = f"loop guard [{gv.kind}]: {gv.reason}"
                    self._save(task)
                    self._emit(task, "loop_guard", kind=gv.kind, reason=gv.reason)
                    if task.replans_used > task.max_replans:
                        return self._fail(task, f"loop guard: {gv.reason}")
                    continue

                fresh = await self._fresh_action_boundary(task, before)
                if fresh is None:
                    task.replans_used += 1
                    last = "stale observation: UI changed during planning"
                    self._save(task)
                    self._emit(task, "stale_observation", phase="pre_intent")
                    if task.replans_used > task.max_replans:
                        return self._fail(task, "freshness replan budget")
                    continue
                before = fresh

                cur = self._req(task.id)
                if cur.generation != plan_generation or cur.state in {
                    TaskState.PAUSED, TaskState.USER_CONTROL, TaskState.CANCELLED, TaskState.LOCKED
                }:
                    return self._fail(
                        cur,
                        "stale observation: generation changed"
                        if cur.generation != plan_generation
                        else "input state changed before action",
                    )

                task.pending_action = self._sanitize_action(action)
                step = StepRecord(action=task.pending_action, before_observation_id=before.id)
                task.history.append(step)
                task.state = (
                    TaskState.WAITING_APPROVAL if decision.requires_approval else TaskState.RUNNING
                )
                self._save(task)

                if decision.requires_approval:
                    aid = await self.approval_create(
                        decision.approval_kind or "computer_action",
                        self._preview(task, action, decision.reason),
                        tool="computer_operator",
                        payload={
                            "computer_task_id": task.id,
                            "action_id": action.id,
                            "idempotency_key": action.idempotency_key,
                            "kind": action.kind.value,
                        },
                    )
                    task.waiting_approval_id = aid
                    step.approval_id = aid
                    self._save(task)
                    self._emit(task, "waiting_approval", approval_id=aid)
                    self.control_lease.release(task.id)
                    result = await self.approval_wait(aid)
                    if result.get("status") != "approved":
                        return self._fail(task, f"approval {result.get('status', 'unknown')}")
                    if not self.control_lease.acquire(task.id):
                        return self._fail(
                            self._req(task.id),
                            f"desktop busy: control lease held by {self.control_lease.holder()}",
                        )
                    cur = self._req(task.id)
                    if (
                        cur.generation != plan_generation
                        or not cur.pending_action
                        or cur.pending_action.id != action.id
                    ):
                        return self._fail(cur, "approved action stale")
                    task = cur
                    step = task.history[-1] if task.history else step
                    if task.state in {
                        TaskState.PAUSED, TaskState.USER_CONTROL, TaskState.CANCELLED, TaskState.LOCKED
                    }:
                        return self._fail(task, "input state changed before action")
                    fresh = await self._fresh_action_boundary(task, before)
                    if fresh is None:
                        task.replans_used += 1
                        task.pending_action = None
                        task.waiting_approval_id = None
                        task.state = TaskState.RECOVERING
                        self._save(task)
                        last = "approved action stale: UI changed while awaiting approval"
                        self._emit(task, "stale_observation", phase="post_approval")
                        continue
                    before = fresh
                    step.before_observation_id = before.id
                    task.state = TaskState.RUNNING
                    self._save(task)

                index = len(task.history) - 1
                if self._interrupt_pending(task.id):
                    self.dispatches_prevented += 1
                    return self._fail(self._req(task.id), "owner interrupt: dispatch prevented")

                try:
                    with self._phase("dispatch"):
                        backend = await self.action_router.execute(action, before)
                except Exception as exc:
                    error = f"{type(exc).__name__}:{exc}"
                    task = self._persist_effect_outcome(
                        task.id,
                        index,
                        {"error": error, "finished_at": time.time()},
                        replans_used=task.replans_used + 1,
                    )
                    last = f"action failed:{error}"
                    if task.replans_used > task.max_replans:
                        return self._fail(task, "action replan budget")
                    continue

                task = self._persist_effect_outcome(
                    task.id,
                    index,
                    {},
                    steps_used=task.steps_used + 1,
                    state=TaskState.OBSERVING,
                )
                with self._phase("observe"):
                    after = await self.observer.observe(generation=task.generation)
                self.observations_taken += 1
                with self._phase("verify"):
                    verdict = self.verifier.verify(action, after)
                guard.record(action, before, after, verdict.ok)
                task = self._persist_effect_outcome(
                    task.id,
                    index,
                    {
                        "after_observation_id": after.id,
                        "verified": verdict.ok,
                        "finished_at": time.time(),
                    },
                    observation=after,
                    replans_used=None if verdict.ok else task.replans_used + 1,
                )
                if verdict.ok:
                    last = f"verified via {backend}:{verdict.reason}"
                    self._emit(task, "step_verified", action=action.kind.value)
                    reusable = (after, task.generation, time.monotonic())
                else:
                    last = f"verify failed:{verdict.reason}"
                    if task.replans_used > task.max_replans:
                        return self._fail(task, "verification budget")

            except OwnerInterrupted:
                reusable = None
                fresh = self._req(task.id)
                if not (
                    fresh.terminal
                    or self.global_locked
                    or fresh.state in {TaskState.PAUSED, TaskState.USER_CONTROL}
                ):
                    self._clear_interrupt(task.id)
                continue
            except OwnerStateChanged:
                reusable = None
                continue

    @contextlib.contextmanager
    def _phase(self, name):
        started = time.perf_counter()
        try:
            yield
        finally:
            self.phase_seconds[name] += time.perf_counter() - started
            self.phase_calls[name] += 1

    def phase_report(self) -> dict:
        return {
            "total_ms": {k: round(v * 1000, 3) for k, v in self.phase_seconds.items()},
            "calls": dict(self.phase_calls),
            "mean_ms": {
                k: (
                    round(self.phase_seconds[k] * 1000 / self.phase_calls[k], 3)
                    if self.phase_calls[k]
                    else None
                )
                for k in self.phase_seconds
            },
        }

    def _interrupt_event(self, task_id):
        event = self._interrupts.get(task_id)
        if event is None:
            event = self._interrupts[task_id] = asyncio.Event()
        return event

    def _interrupt_pending(self, task_id) -> bool:
        event = self._interrupts.get(task_id)
        return bool(event is not None and event.is_set())

    def _signal_interrupt(self, task_id) -> None:
        with contextlib.suppress(RuntimeError):
            self._interrupt_event(task_id).set()

    def _clear_interrupt(self, task_id) -> None:
        self._interrupts.pop(task_id, None)

    async def _read_or_interrupt(self, task_id, coro):
        event = self._interrupts.get(task_id)
        if event is not None and event.is_set():
            coro.close()
            self.interrupts_observed += 1
            raise OwnerInterrupted(task_id)
        event = self._interrupt_event(task_id)
        work = asyncio.ensure_future(coro)
        waiter = asyncio.ensure_future(event.wait())
        try:
            done, _ = await asyncio.wait({work, waiter}, return_when=asyncio.FIRST_COMPLETED)
            if work in done:
                return work.result()
            work.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await work
            self.interrupts_observed += 1
            raise OwnerInterrupted(task_id)
        finally:
            waiter.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await waiter

    def _reuse(self, reusable, task):
        if not reusable or self.observation_reuse_max_age_s <= 0:
            return None
        obs, generation, taken_at = reusable
        if generation != task.generation or getattr(obs, "generation", generation) != task.generation:
            return None
        if (time.monotonic() - taken_at) > self.observation_reuse_max_age_s:
            return None
        self.observations_reused += 1
        return obs

    @staticmethod
    def _observation_signature(obs) -> str:
        payload = {
            "generation": getattr(obs, "generation", None),
            "foreground": getattr(obs, "foreground", None),
            "summary": getattr(obs, "summary", None),
            "ui_tree": getattr(obs, "ui_tree", None),
        }
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=True, default=repr)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    async def _fresh_action_boundary(self, task, planned_observation):
        cur = self._req(task.id)
        if cur.generation != task.generation or cur.state in {
            TaskState.PAUSED, TaskState.USER_CONTROL, TaskState.CANCELLED, TaskState.LOCKED
        }:
            return None
        with self._phase("observe"):
            fresh = await self._read_or_interrupt(
                task.id, self.observer.observe(generation=cur.generation)
            )
        self.observations_taken += 1
        if self._observation_signature(fresh) != self._observation_signature(planned_observation):
            return None
        return fresh

    @staticmethod
    def _goal_requires_external_effect(goal: str) -> bool:
        return bool(_EFFECT_GOAL.search(goal or ""))

    def _completion_requires_verified_effect(self, task) -> bool:
        if not self._goal_requires_external_effect(task.goal):
            return False
        return not any(step.verified is True and step.finished_at is not None for step in task.history)

    def pause(self, task_id):
        self._signal_interrupt(task_id)
        return self._state(task_id, TaskState.PAUSED, "paused", invalidate=True)

    def take_control(self, task_id):
        self._signal_interrupt(task_id)
        self.loop_guards.pop(task_id, None)
        task = self._state(task_id, TaskState.USER_CONTROL, "user_control", invalidate=True)
        self.control_lease.revoke()
        return task

    def stop(self, task_id):
        self._signal_interrupt(task_id)
        return self._state(task_id, TaskState.CANCELLED, "cancelled", invalidate=True)

    def resume(self, task_id):
        task = self._req(task_id)
        if task.state not in {TaskState.PAUSED, TaskState.USER_CONTROL, TaskState.RECOVERING}:
            raise RuntimeError("invalid resume")
        self.loop_guards.pop(task_id, None)
        self._clear_interrupt(task_id)
        task.state = TaskState.RECOVERING
        task.generation += 1
        task.pending_action = None
        task.waiting_approval_id = None
        self._save(task)
        self._emit(task, "recovering")
        return task

    def recover_all(self):
        out = []
        for task in self.store.list():
            if task.terminal:
                continue
            if task.state in {TaskState.PAUSED, TaskState.USER_CONTROL, TaskState.WAITING_APPROVAL}:
                out.append(task)
                continue
            self._clear_interrupt(task.id)
            task.state = TaskState.RECOVERING
            task.generation += 1
            unresolved = (
                task.pending_action is not None
                and bool(task.history)
                and task.history[-1].finished_at is None
            )
            if unresolved:
                task.last_error = "reconciliation required: prior effect outcome is unknown"
            else:
                task.pending_action = None
                task.waiting_approval_id = None
            self._save(task)
            out.append(task)
        self.control_lease.revoke()
        return out

    def emergency_lock(self):
        self.global_locked = True
        self.control_lease.revoke()
        for task_id in list(self._interrupts):
            self._signal_interrupt(task_id)
        for task in self.store.list():
            if not task.terminal:
                self._signal_interrupt(task.id)
                self._fail(task, "emergency lock", TaskState.LOCKED)

    def _state(self, task_id, state, event, invalidate=False):
        for _ in range(_CAS_RETRIES):
            task = self._req(task_id)
            if task.terminal:
                return task
            task.state = state
            if invalidate:
                task.generation += 1
                task.pending_action = None
            try:
                self._save(task)
            except OwnerStateChanged:
                continue
            self._emit(task, event)
            return task
        raise OwnerStateChanged(f"{task_id}: task row kept changing under the owner command")

    def _fail(self, task, reason, state=TaskState.FAILED):
        error = str(reason)[:3000]
        if state is TaskState.FAILED and task.state in _OWNER_SET_STATES:
            return self._record_owner_stop(task, error)
        for _ in range(_CAS_RETRIES):
            task.state = state
            task.last_error = error
            task.pending_action = None
            try:
                self._save(task)
            except OwnerStateChanged:
                task = self._req(task.id)
                if task.terminal:
                    self.loop_guards.pop(task.id, None)
                    return task.state
                continue
            self.loop_guards.pop(task.id, None)
            self._emit(task, "failed", error=task.last_error)
            return task.state
        return self._req(task.id).state

    def _persist_effect_outcome(
        self, task_id, index, fields, *, steps_used=None, replans_used=None,
        state=None, observation=None,
    ):
        for _ in range(_CAS_RETRIES):
            task = self._req(task_id)
            if 0 <= index < len(task.history):
                for key, value in fields.items():
                    setattr(task.history[index], key, value)
            if steps_used is not None:
                task.steps_used = max(task.steps_used, steps_used)
            if replans_used is not None:
                task.replans_used = max(task.replans_used, replans_used)
            if observation is not None:
                task.last_observation = observation
            if state is not None and not task.terminal and task.state not in _OWNER_SET_STATES:
                task.state = state
            task.pending_action = None
            task.waiting_approval_id = None
            try:
                self._save(task)
                return task
            except OwnerStateChanged:
                continue
        return self._req(task_id)

    def _record_owner_stop(self, task, error):
        for _ in range(_CAS_RETRIES):
            task.last_error = error
            task.pending_action = None
            try:
                self._save(task)
            except OwnerStateChanged:
                task = self._req(task.id)
                if task.state not in _OWNER_SET_STATES:
                    break
                continue
            self.loop_guards.pop(task.id, None)
            self._emit(task, "owner_stopped", error=error)
            return task.state
        return self._req(task.id).state

    def _req(self, task_id):
        task = self.store.get(task_id)
        if not task:
            raise KeyError(task_id)
        return task

    def _save(self, task):
        task.touch()
        with self._phase("persist"):
            try:
                self.store.save(task)
            except StaleTaskWrite as exc:
                raise OwnerStateChanged(str(exc)) from exc

    def _emit(self, task, event, **kwargs):
        self.event_emit(
            "computer_operator.task",
            computer_task_id=task.id,
            state=task.state.value,
            event=event,
            **kwargs,
        )

    @staticmethod
    def _sanitize_action(action: ComputerAction) -> ComputerAction:
        if action.kind is ActionKind.TYPE:
            return replace(
                action,
                text=redact(action.text) if action.text else action.text,
                args=redact_obj(action.args),
            )
        return action

    @staticmethod
    def _preview(task, action, reason):
        return redact(
            f"Computer task: {task.goal[:500]}\n"
            f"Action: {action.kind.value} {action.target or ''}\n"
            f"Reason: {reason}"
        )
