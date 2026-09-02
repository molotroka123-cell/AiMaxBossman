"""Длинные задачи 6/10 → 10/10: journal, состояния, checkpoint, resume, DAG.

Главная проблема длинной задачи — восстановление состояния после десятков
действий (а не размер контекста). Поэтому:
- COMPLETED без verifier не существует (только VERIFIED);
- checkpoint после каждого важного шага;
- resume: journal → проверка SHA/среды → сверка RUNNING/RECONCILING с фактом →
  без слепых повторов → restore Working State → продолжение;
- DAG: новые зависимости, отмена ветки, retry узла, параллельность, блокировки,
  rollback/компенсации, версионирование плана.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Callable, Sequence

from .storage import CognitiveStore, json_dumps, sha256_text, stable_id, utcnow_iso


class StepState(str, Enum):
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    RECONCILING = "RECONCILING"
    VERIFIED = "VERIFIED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_FINAL = "FAILED_FINAL"
    CANCELLED = "CANCELLED"


_TERMINAL = {StepState.VERIFIED, StepState.FAILED_FINAL, StepState.CANCELLED}

# Допустимые переходы (fail-closed: всё остальное запрещено).
TRANSITIONS: dict[StepState, set[StepState]] = {
    StepState.PENDING: {StepState.READY, StepState.CANCELLED},
    StepState.READY: {StepState.RUNNING, StepState.CANCELLED},
    StepState.RUNNING: {StepState.WAITING, StepState.RECONCILING,
                        StepState.FAILED_RETRYABLE, StepState.FAILED_FINAL},
    StepState.WAITING: {StepState.RUNNING, StepState.RECONCILING, StepState.CANCELLED},
    StepState.RECONCILING: {StepState.RUNNING, StepState.VERIFIED,
                            StepState.FAILED_RETRYABLE, StepState.FAILED_FINAL},
    StepState.FAILED_RETRYABLE: {StepState.READY, StepState.CANCELLED},
    StepState.FAILED_FINAL: set(),
    StepState.VERIFIED: set(),
    StepState.CANCELLED: set(),
}


class IllegalTransition(ValueError):
    pass


class VerificationRequired(PermissionError):
    """Попытка завершить шаг без независимого verifier."""


@dataclass(slots=True)
class JournalStep:
    task_id: str
    step_id: str
    goal: str = ""
    constraints_text: str = ""
    plan_version: int = 1
    dependencies: list[str] = field(default_factory=list)
    state: StepState = StepState.PENDING
    attempt: int = 0
    input_hash: str = ""
    output_hash: str = ""
    effect_id: str = ""  # idempotency key внешнего эффекта
    receipt: str = ""
    verification: str = ""
    started_at: str = ""
    completed_at: str = ""
    checkpoint_ref: str = ""
    next_action: str = ""
    run_id: str = ""


class TaskJournal:
    """Durable Task Journal поверх CognitiveStore.journal."""

    def __init__(self, store: CognitiveStore) -> None:
        self.store = store

    # -- CRUD -----------------------------------------------------------
    def add_step(self, step: JournalStep) -> JournalStep:
        self.store.execute(
            """INSERT INTO journal VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(task_id, step_id) DO NOTHING""",
            (
                step.task_id, step.run_id, step.step_id, step.goal,
                step.constraints_text, step.plan_version,
                json_dumps(step.dependencies), step.state.value, step.attempt,
                step.input_hash, step.output_hash, step.effect_id, step.receipt,
                step.verification, step.started_at, step.completed_at,
                step.checkpoint_ref, step.next_action,
            ),
        )
        self.store.commit()
        return self.get_step(step.task_id, step.step_id)

    def get_step(self, task_id: str, step_id: str) -> JournalStep:
        row = self.store.execute(
            "SELECT * FROM journal WHERE task_id=? AND step_id=?", (task_id, step_id)
        ).fetchone()
        if not row:
            raise KeyError(f"{task_id}/{step_id}")
        return self._row(row)

    def steps(self, task_id: str) -> list[JournalStep]:
        return [self._row(r) for r in self.store.execute(
            "SELECT * FROM journal WHERE task_id=? ORDER BY step_id", (task_id,)).fetchall()]

    @staticmethod
    def _row(r: Any) -> JournalStep:
        import json as _json

        def _jl(s: str) -> list:
            try:
                v = _json.loads(s or "[]")
                return v if isinstance(v, list) else []
            except Exception:
                return []

        return JournalStep(
            task_id=r["task_id"], run_id=r["run_id"], step_id=r["step_id"],
            goal=r["goal"], constraints_text=r["constraints_text"],
            plan_version=r["plan_version"], dependencies=_jl(r["dependencies"]),
            state=StepState(r["state"]), attempt=r["attempt"],
            input_hash=r["input_hash"], output_hash=r["output_hash"],
            effect_id=r["effect_id"], receipt=r["receipt"],
            verification=r["verification"], started_at=r["started_at"],
            completed_at=r["completed_at"], checkpoint_ref=r["checkpoint_ref"],
            next_action=r["next_action"],
        )

    def _save(self, s: JournalStep) -> None:
        self.store.execute(
            """UPDATE journal SET goal=?, constraints_text=?, plan_version=?,
               dependencies=?, state=?, attempt=?, input_hash=?, output_hash=?,
               effect_id=?, receipt=?, verification=?, started_at=?, completed_at=?,
               checkpoint_ref=?, next_action=?, run_id=? WHERE task_id=? AND step_id=?""",
            (
                s.goal, s.constraints_text, s.plan_version,
                json_dumps(s.dependencies), s.state.value, s.attempt,
                s.input_hash, s.output_hash, s.effect_id, s.receipt,
                s.verification, s.started_at, s.completed_at,
                s.checkpoint_ref, s.next_action, s.run_id, s.task_id, s.step_id,
            ),
        )
        self.store.commit()

    # -- transitions ------------------------------------------------------
    def transition(
        self,
        task_id: str,
        step_id: str,
        to: StepState,
        *,
        verifier_id: str = "",
        verification: str = "",
        receipt: str = "",
        output_hash: str = "",
    ) -> JournalStep:
        s = self.get_step(task_id, step_id)
        if to not in TRANSITIONS[s.state]:
            # Единственное исключение: RUNNING → VERIFIED напрямую запрещён —
            # только через RECONCILING с verifier (защита от FalseCompletion).
            raise IllegalTransition(f"{s.state.value} → {to.value} forbidden")
        if to is StepState.VERIFIED:
            if not verifier_id or not verification:
                raise VerificationRequired("VERIFIED requires verifier_id + verification")
            s.completed_at = utcnow_iso()
            s.verification = verification
        if to is StepState.RUNNING:
            s.attempt += 1
            if not s.started_at:
                s.started_at = utcnow_iso()
            # Idempotency: effect_id фиксируется ДО первого внешнего вызова
            # и переиспользуется при retry — DuplicateExternalEffects = 0.
            if not s.effect_id:
                s.effect_id = stable_id("effect", task_id, step_id, s.input_hash or step_id)
        if receipt:
            s.receipt = receipt
        if output_hash:
            s.output_hash = output_hash
        s.state = to
        self._save(s)
        return s

    def reconcile_to_verified(
        self,
        task_id: str,
        step_id: str,
        *,
        verifier_id: str,
        verification: str,
        effect_probe: Callable[[JournalStep], bool] | None = None,
    ) -> JournalStep:
        """RECONCILING → VERIFIED только после сверки фактического effect."""
        s = self.get_step(task_id, step_id)
        if s.state not in (StepState.RUNNING, StepState.RECONCILING, StepState.WAITING):
            raise IllegalTransition(f"reconcile from {s.state.value} forbidden")
        if effect_probe is not None and not effect_probe(s):
            # Эффекта нет — не подтверждаем, уходим в retry вместо FalseCompletion.
            s.state = StepState.FAILED_RETRYABLE
            self._save(s)
            return s
        if s.state is not StepState.RECONCILING:
            s.state = StepState.RECONCILING
            self._save(s)
        return self.transition(task_id, step_id, StepState.VERIFIED,
                               verifier_id=verifier_id, verification=verification)

    # -- DAG ops ------------------------------------------------------------
    def ready_steps(self, task_id: str) -> list[JournalStep]:
        """Шаги, готовые к запуску: PENDING/FAILED_RETRYABLE с VERIFIED-зависимостями."""
        all_steps = {s.step_id: s for s in self.steps(task_id)}
        out = []
        for s in all_steps.values():
            if s.state not in (StepState.PENDING, StepState.FAILED_RETRYABLE):
                continue
            if all(all_steps.get(d) and all_steps[d].state is StepState.VERIFIED
                   for d in s.dependencies):
                out.append(s)
        return out

    def mark_ready(self, task_id: str) -> list[str]:
        moved = []
        for s in self.ready_steps(task_id):
            if s.state is StepState.PENDING:
                s.state = StepState.READY
                self._save(s)
                moved.append(s.step_id)
            elif s.state is StepState.FAILED_RETRYABLE:
                s.state = StepState.READY
                self._save(s)
                moved.append(s.step_id)
        return moved

    def add_dependency(self, task_id: str, step_id: str, dep: str, *, new_plan_version: int) -> JournalStep:
        s = self.get_step(task_id, step_id)
        if s.state in _TERMINAL:
            raise IllegalTransition("cannot rewire terminal step")
        if dep not in s.dependencies:
            s.dependencies.append(dep)
        s.plan_version = new_plan_version
        self._save(s)
        return s

    def cancel_branch(self, task_id: str, step_ids: Sequence[str]) -> int:
        n = 0
        for sid in step_ids:
            s = self.get_step(task_id, sid)
            if s.state in _TERMINAL:
                continue
            if StepState.CANCELLED in TRANSITIONS[s.state] or s.state in (
                    StepState.PENDING, StepState.READY, StepState.FAILED_RETRYABLE):
                s.state = StepState.CANCELLED
                self._save(s)
                n += 1
        return n

    def retry_step(self, task_id: str, step_id: str) -> JournalStep:
        """Retry только нужного узла: FAILED_RETRYABLE → READY (effect_id reuse)."""
        s = self.get_step(task_id, step_id)
        if s.state is not StepState.FAILED_RETRYABLE:
            raise IllegalTransition(f"retry from {s.state.value} forbidden")
        s.state = StepState.READY
        self._save(s)
        return s


# ---------------------------------------------------------------------------
# Checkpoint
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class Checkpoint:
    checkpoint_id: str
    task_id: str
    run_id: str
    plan_version: int
    created_at: str
    confirmed_results: list[str] = field(default_factory=list)
    remaining_steps: list[str] = field(default_factory=list)
    dag: dict[str, Any] = field(default_factory=dict)
    budget_spent: float = 0.0
    budget_total: float = 0.0
    external_effects: list[str] = field(default_factory=list)
    approvals: list[str] = field(default_factory=list)
    receipts: list[str] = field(default_factory=list)
    open_hypotheses: list[str] = field(default_factory=list)
    last_verified_env: dict[str, str] = field(default_factory=dict)


class Checkpointer:
    def __init__(self, store: CognitiveStore, journal: TaskJournal) -> None:
        self.store = store
        self.journal = journal

    def write(
        self,
        task_id: str,
        *,
        run_id: str = "",
        confirmed_results: Sequence[str] = (),
        budget_spent: float = 0.0,
        budget_total: float = 0.0,
        approvals: Sequence[str] = (),
        open_hypotheses: Sequence[str] = (),
        last_verified_env: dict[str, str] | None = None,
    ) -> Checkpoint:
        steps = self.journal.steps(task_id)
        remaining = [s.step_id for s in steps if s.state not in _TERMINAL]
        plan_version = max([s.plan_version for s in steps] or [1])
        dag = {s.step_id: {"deps": s.dependencies, "state": s.state.value} for s in steps}
        cp = Checkpoint(
            checkpoint_id=stable_id("ckpt", task_id, utcnow_iso()),
            task_id=task_id, run_id=run_id, plan_version=plan_version,
            created_at=utcnow_iso(),
            confirmed_results=list(confirmed_results),
            remaining_steps=remaining, dag=dag,
            budget_spent=budget_spent, budget_total=budget_total,
            external_effects=[s.effect_id for s in steps if s.effect_id],
            approvals=list(approvals),
            receipts=[s.receipt for s in steps if s.receipt],
            open_hypotheses=list(open_hypotheses),
            last_verified_env=dict(last_verified_env or {}),
        )
        import json as _json

        self.store.execute(
            "INSERT INTO checkpoints VALUES (?,?,?,?,?,?)",
            (cp.checkpoint_id, task_id, run_id, plan_version, cp.created_at,
             _json.dumps(asdict(cp), ensure_ascii=False)),
        )
        self.store.commit()
        return cp

    def latest(self, task_id: str) -> Checkpoint | None:
        row = self.store.execute(
            """SELECT payload FROM checkpoints WHERE task_id=?
               ORDER BY created_at DESC LIMIT 1""", (task_id,)
        ).fetchone()
        if not row:
            return None
        import json as _json

        d = _json.loads(row["payload"])
        return Checkpoint(**d)


# ---------------------------------------------------------------------------
# Resume после перезапуска + revalidation среды
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EnvSnapshot:
    git_sha: str = ""
    browser_tab: str = ""
    ui_digest: str = ""
    file_digest: str = ""
    api_state: str = ""
    credential_scope: str = ""
    model_price: str = ""

    def diff(self, other: "EnvSnapshot") -> list[str]:
        changed = []
        for f in ("git_sha", "browser_tab", "ui_digest", "file_digest",
                  "api_state", "credential_scope", "model_price"):
            if getattr(self, f) != getattr(other, f):
                changed.append(f)
        return changed


class ResumeRecovery:
    """1) journal → 2) SHA/среда → 3) RUNNING/RECONCILING → 4) сверка effect →
    5) без слепых повторов → 6) restore Working State → 7) продолжение."""

    def __init__(self, store: CognitiveStore, journal: TaskJournal,
                 checkpointer: Checkpointer) -> None:
        self.store = store
        self.journal = journal
        self.checkpointer = checkpointer

    def recover(
        self,
        task_id: str,
        *,
        current_env: EnvSnapshot,
        effect_probe: Callable[[JournalStep], bool | None],
    ) -> dict[str, Any]:
        steps = self.journal.steps(task_id)
        cp = self.checkpointer.latest(task_id)
        last_env = EnvSnapshot(**(cp.last_verified_env or {})) if cp else EnvSnapshot()
        env_changed = last_env.diff(current_env)
        need_revalidation = bool(env_changed)
        reconciled: list[str] = []
        retried: list[str] = []
        lost_verified = 0
        for s in steps:
            if s.state in (StepState.RUNNING, StepState.RECONCILING, StepState.WAITING):
                # Проверяем фактический effect вместо слепого повтора.
                probe = effect_probe(s)
                if probe is True:
                    # Эффект есть, но VERIFIED не зафиксирован — требуем verifier.
                    s.state = StepState.RECONCILING
                    self.journal._save(s)
                    reconciled.append(s.step_id)
                elif probe is False:
                    s.state = StepState.FAILED_RETRYABLE
                    self.journal._save(s)
                    retried.append(s.step_id)
                else:  # probe is None — неизвестно → тоже не повторять вслепую
                    s.state = StepState.RECONCILING
                    self.journal._save(s)
                    reconciled.append(s.step_id)
        # Потеря подтверждённых шагов: было VERIFIED — обязано остаться.
        verified_now = {s.step_id for s in self.journal.steps(task_id)
                        if s.state is StepState.VERIFIED}
        if cp:
            before = {sid for sid, meta in cp.dag.items() if meta.get("state") == "VERIFIED"}
            lost_verified = len(before - verified_now)
        first_unfinished = next(
            (s.step_id for s in self.journal.steps(task_id)
             if s.state not in _TERMINAL), ""
        )
        return {
            "task_id": task_id,
            "env_changed": env_changed,
            "need_revalidation": need_revalidation,
            "reconciled": reconciled,
            "retried": retried,
            "lost_verified_steps": lost_verified,
            "resume_from": first_unfinished,
            "checkpoint_id": cp.checkpoint_id if cp else "",
            "resume_accuracy_ok": lost_verified == 0,
        }
