"""Рантайм AI Company Mode: исполнение DAG с гейтами, свежей верификацией,
агрегацией KPI, перепланированием, отчётом и learning records.

Всё внешнее внедряется:
  executor(task) -> WorkResult                       — кто делает работу (самоотчёт)
  approval_gate(task, requirement) -> ApprovalDecision — кто РЕШАЕТ (по умолчанию отказ)
  verifier(task, result) -> VerificationOutcome      — кто наблюдает свежее состояние
  kpi_reader() -> Mapping[str, float]                — свежее чтение KPI до/после

Гарантии (код, не промпт):
  * флаг OFF и не synthetic → CompanyModeDisabled, исполнитель не вызывается;
  * задача с requires_approval не доходит до executor без ApprovalDecision(approved=True)
    от гейта; любой иной ответ гейта (bool, None, исключение) — отказ;
  * бюджет проверяется до исполнения; превышение → BUDGET_EXCEEDED, executor не вызывается;
  * DONE ставится только если самоотчёт ok И верификатор не сказал FAILED;
    вердикт FAILED верификатора перекрывает ok самоотчёта;
  * learning record получает VERIFIED только при вердикте VERIFIED верификатора.
Рантайм не реализует ни политику, ни одобрения, ни верификацию, ни память — он
только вызывает внедрённые движки и записывает трассу.
"""
from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping
from typing import Any

from .model import (task_digest, ApprovalDecision, ApprovalRequirement, CompanyModeDisabled, CompanyPlan,
                    CompanyReport, CompanyRunState, CompanyTask, KPI, TaskOutcome,
                    VerificationOutcome, WorkResult, enabled)
from .planner import replan

Executor = Callable[[CompanyTask], WorkResult]
ApprovalGate = Callable[[CompanyTask, ApprovalRequirement], ApprovalDecision]
Verifier = Callable[[CompanyTask, WorkResult], VerificationOutcome]
KpiReader = Callable[[], Mapping[str, float]]

AGENT_NAME = "company-runtime"
MODEL_NAME = "deterministic-planner"   # планировщик без LLM


def deny_all_gate(task: CompanyTask, requirement: ApprovalRequirement) -> ApprovalDecision:
    """Гейт по умолчанию: отказ. Роль задачи не рассматривается — она не полномочие."""
    return ApprovalDecision(False, "policy:default-deny",
                            f"no approval authority injected for {requirement.kind!r}; "
                            f"role {task.role!r} confers none")


def _fingerprint(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:40]


def _callable_name(fn: Any) -> str:
    return getattr(fn, "__name__", None) or type(fn).__name__


def aggregate_kpis(kpis: tuple[KPI, ...], before: Mapping[str, float],
                   after: Mapping[str, float]) -> tuple[dict[str, Any], ...]:
    out = []
    for k in kpis:
        b, a = before.get(k.name), after.get(k.name)
        out.append({"name": k.name, "before": b, "after": a,
                    "delta": (a - b) if (a is not None and b is not None) else None,
                    "improved": k.improved(b, a), "target": k.target, "met": k.met(a)})
    return tuple(out)


class CompanyRuntime:
    def __init__(self, plan: CompanyPlan, *, executor: Executor,
                 approval_gate: ApprovalGate | None = None,
                 verifier: Verifier | None = None,
                 kpi_reader: KpiReader | None = None,
                 synthetic: bool = False,
                 max_rounds: int = 3,
                 clock: Callable[[], float] | None = None,
                 cost_meter: Callable[[CompanyTask, WorkResult], float] | None = None,
                 executor_principal: str = "executor",
                 verifier_principal: str = "") -> None:
        self.plan = plan
        self.executor = executor
        self.approval_gate = approval_gate or deny_all_gate
        self.verifier = verifier
        # P0-05: измеренная стоимость (Cost Governor/метр) — не только самоотчёт исполнителя
        self.cost_meter = cost_meter
        # P0-05: identity по principal, не по display-строке; verifier == executor → UNVERIFIED
        self.executor_principal = executor_principal
        self.verifier_principal = verifier_principal or (f"verifier:{_callable_name(verifier)}" if verifier else "")
        self.kpi_reader = kpi_reader
        self.synthetic = synthetic
        self.max_rounds = max(1, max_rounds)
        self.clock = clock or time.time
        self.state = CompanyRunState()
        self._executor_calls: list[str] = []

    # ---- трасса -------------------------------------------------------------
    def _trace(self, event: str, task_id: str = "", detail: str = "", **extra: Any) -> None:
        self.state.trace.append({"seq": len(self.state.trace), "round": self.state.rounds,
                                 "task_id": task_id, "event": event, "detail": detail,
                                 "at": self.clock(), **extra})

    # ---- запуск ---------------------------------------------------------------
    def run(self) -> CompanyReport:
        if not (enabled() or self.synthetic):
            raise CompanyModeDisabled(
                "AI Company Mode is disabled (set AI_COMPANY_MODE_ENABLED=1) and the runtime "
                "was not constructed with synthetic=True")
        self.plan.validate()
        for t in self.plan.tasks:
            self.state.outcome(t.id)
        self._trace("plan.accepted", detail=self.plan.objective.title,
                    tasks=[t.id for t in self.plan.ordered()])
        self.state.kpi_before = self._read_kpis("kpi.before")

        while self.state.rounds < self.max_rounds:
            todo = replan(self.plan, self.state)
            if not todo:
                break
            self.state.rounds += 1
            self._trace("round.start", detail=",".join(todo))
            by_id = self.plan.by_id()
            for tid in todo:
                self._run_task(by_id[tid])
            self._trace("round.end", kpi=dict(self._read_kpis("kpi.round")))

        self.state.kpi_after = self._read_kpis("kpi.after")
        return self._report()

    def _read_kpis(self, event: str) -> dict[str, float]:
        if self.kpi_reader is None:
            return {}
        try:
            vals = {str(k): float(v) for k, v in dict(self.kpi_reader()).items()}
        except Exception as exc:  # noqa: BLE001 — чтение KPI не должно ронять прогон
            self._trace(event + ".error", detail=repr(exc))
            return {}
        self._trace(event, kpi=vals)
        return vals

    # ---- одна задача --------------------------------------------------------
    def _run_task(self, task: CompanyTask) -> None:
        o = self.state.outcome(task.id)
        # 1. зависимости
        for dep in task.dependencies:
            up = self.state.outcome(dep.upstream)
            if up.state != "DONE":
                o.set_state("SKIPPED", f"blocked by {dep.upstream}:{up.state}")
                self._trace("task.skipped", task.id, o.reason)
                return
        # 2. одобрение — ДО бюджета и исполнителя; роль не участвует
        if task.gated:
            decision = self._ask_gate(task)
            o.approval = decision
            if not decision.approved:
                o.set_state("DENIED", decision.reason)
                self._trace("task.denied", task.id, decision.reason, approver=decision.approver,
                            kinds=[r.kind for r in task.requires_approval])
                return
            self._trace("task.approved", task.id, decision.reason, approver=decision.approver)
        # 3. бюджет: reserve ДО запуска (оценка), commit/release ПОСЛЕ (факт)
        if self.state.budget_exhausted:
            o.set_state("BUDGET_EXCEEDED", "budget exhausted by an earlier overrun")
            self._trace("task.budget_exceeded", task.id, o.reason)
            return
        ok, why = self.plan.budget.allows(task.estimated_cost, self.state.spent,
                                          self.state.executed, self.state.reserved)
        if not ok:
            o.set_state("BUDGET_EXCEEDED", why)
            self._trace("task.budget_exceeded", task.id, why)
            return
        reserved = max(0.0, float(task.estimated_cost))
        self.state.reserved += reserved
        self._trace("task.reserved", task.id, cost=reserved, reserved_total=self.state.reserved)
        # 4. исполнение
        o.set_state("RUNNING")
        o.attempts += 1
        self._trace("task.start", task.id, task.action, role=task.role, attempt=o.attempts)
        self._executor_calls.append(task.id)
        try:
            result = self.executor(task)
        except Exception as exc:  # noqa: BLE001 — сбой исполнителя = FAILED, не падение прогона
            self._release(task, reserved)
            o.result = None
            o.set_state("FAILED", f"executor raised: {exc!r}")
            self._trace("task.failed", task.id, o.reason)
            return
        if not isinstance(result, WorkResult) or result.task_id != task.id:
            self._release(task, reserved)
            o.result = result if isinstance(result, WorkResult) else None
            o.set_state("FAILED", "executor returned a result for a different task or wrong type")
            self._trace("task.failed", task.id, o.reason)
            return
        o.result = result
        actual = self._commit(task, result, reserved)
        self._trace("task.result", task.id, result.summary, ok=result.ok, cost=actual,
                    self_reported_cost=result.cost)
        if actual is None:                       # overrun → терминально BUDGET_EXCEEDED, без retry
            o.set_state("BUDGET_EXCEEDED", f"cost overrun: actual exceeds the envelope "
                                           f"(spent={self.state.spent:.2f}, max={self.plan.budget.max_total_cost})")
            self._trace("task.budget_exceeded", task.id, o.reason)
            return
        # 5. верификация свежим наблюдением
        if task.evidence_requirements:
            o.verification = self._verify(task, result)
            self._trace("task.verified", task.id, o.verification.reason,
                        status=o.verification.status, evidence=list(o.verification.evidence))
            if o.verification.status == "FAILED":
                o.set_state("FAILED", f"verifier contradicted self-report: {o.verification.reason}")
                self._trace("task.failed", task.id, o.reason)
                return
        if not result.ok:
            o.set_state("FAILED", f"executor reported failure: {result.summary}")
            self._trace("task.failed", task.id, o.reason)
            return
        o.set_state("DONE", "")
        self._trace("task.done", task.id,
                    o.verification.status if o.verification else "no evidence requirement")

    # ---- бюджет: reserve / commit / release ----------------------------------
    def _release(self, task: CompanyTask, reserved: float) -> None:
        self.state.reserved = max(0.0, self.state.reserved - reserved)
        self._trace("task.released", task.id, cost=reserved, reserved_total=self.state.reserved)

    def _commit(self, task: CompanyTask, result: WorkResult, reserved: float) -> float | None:
        """Факт = max(самоотчёт, измеритель). Если факт не помещается в конверт —
        коммитим то, что реально потрачено, помечаем overrun и закрываем бюджет
        (молчаливого превышения нет). Возвращает факт или None при overrun."""
        self_reported = max(0.0, float(result.cost))
        measured = None
        if self.cost_meter is not None:
            try:
                measured = max(0.0, float(self.cost_meter(task, result)))
            except Exception as exc:  # noqa: BLE001 — сбой метра: остаёмся консервативными
                self._trace("task.cost_meter_error", task.id, repr(exc))
        actual = max(self_reported, measured) if measured is not None else self_reported
        self.state.reserved = max(0.0, self.state.reserved - reserved)
        self.state.spent += actual
        self.state.executed += 1
        limit = self.plan.budget.max_total_cost
        per_task = self.plan.budget.max_task_cost
        if self.state.spent > limit or (per_task is not None and actual > per_task):
            self.state.budget_exhausted = True
            self.state.overruns.append(task.id)
            self._trace("task.cost_overrun", task.id, actual=actual, spent=self.state.spent,
                        max_total_cost=limit, max_task_cost=per_task)
            return None
        self._trace("task.committed", task.id, cost=actual, measured=measured is not None)
        return actual

    def _valid_approval(self, task: CompanyTask, d: ApprovalDecision) -> str:
        """Пустая строка = одобрение действительно; иначе причина отказа."""
        if not d.approved:
            return d.reason or "denied"
        want = task_digest(self.plan.objective.id, task)
        if d.digest != want:
            return "approval digest does not match this task/action"
        if d.scope != self.plan.objective.id:
            return "approval scope is another objective"
        if d.expires_at is not None and self.clock() >= d.expires_at:
            return "approval expired"
        if not d.nonce:
            return "approval without nonce (one-time consumption impossible)"
        if d.nonce in self.state.consumed_nonces:
            return "approval already consumed (replay)"
        return ""

    def _ask_gate(self, task: CompanyTask) -> ApprovalDecision:
        """Роль не участвует. Каждое требование — отдельное решение гейта; любое
        не-ApprovalDecision, исключение, чужой digest/scope, истёкший срок или
        повтор nonce — отказ (fail closed). Одобрение потребляется один раз."""
        for req in task.requires_approval:
            try:
                d = self.approval_gate(task, req)
            except Exception as exc:  # noqa: BLE001 — сбой гейта = отказ
                return ApprovalDecision(False, "gate:error", f"gate raised for {req.kind}: {exc!r}")
            if not isinstance(d, ApprovalDecision):
                return ApprovalDecision(False, "gate:invalid", f"gate returned {type(d).__name__}, not ApprovalDecision")
            why = self._valid_approval(task, d)
            if why:
                return ApprovalDecision(False, d.approver or "gate", f"{req.kind}: {why}")
            self.state.consumed_nonces.add(d.nonce)
        last = d if task.requires_approval else ApprovalDecision(True, "policy:ungated", "no approval required")
        return ApprovalDecision(True, last.approver, last.reason, digest=last.digest, scope=last.scope,
                                expires_at=last.expires_at, nonce=last.nonce)

    def _verify(self, task: CompanyTask, result: WorkResult) -> VerificationOutcome:
        if self.verifier is None:
            return VerificationOutcome("UNVERIFIED", "no verifier injected — self-report is not evidence")
        if not self.verifier_principal or self.verifier_principal == self.executor_principal:
            return VerificationOutcome("UNVERIFIED", "verifier principal is the executor — "
                                                     "self-verification is not evidence")
        try:
            v = self.verifier(task, result)
        except Exception as exc:  # noqa: BLE001 — наблюдение недоступно → UNVERIFIED, не PASS
            return VerificationOutcome("UNVERIFIED", f"verifier raised: {exc!r}")
        if not isinstance(v, VerificationOutcome):
            return VerificationOutcome("UNVERIFIED", f"verifier returned {type(v).__name__}")
        return v

    # ---- отчёт ----------------------------------------------------------------
    def _overall_status(self) -> str:
        """VERIFIED только если КАЖДАЯ задача DONE и КАЖДАЯ проверена свежим
        наблюдением; любая DENIED/SKIPPED/BUDGET_EXCEEDED/непроверенная → не VERIFIED."""
        outs = list(self.state.outcomes.values())
        if any(o.state == "FAILED" for o in outs):
            return "FAILED"
        done = [o for o in outs if o.state == "DONE"]
        if any(o.verification is None or not o.verification.verified for o in done):
            return "UNVERIFIED"                  # сделанная, но не проверенная работа — важнее PARTIAL
        if not outs or len(done) != len(outs):
            return "PARTIAL"                     # всё сделанное проверено, но часть задач не выполнена
        return "VERIFIED"

    def _report(self) -> CompanyReport:
        st = self.state
        obj = self.plan.objective
        status = self._overall_status()
        completion = "COMPLETE" if all(o.state == "DONE" for o in st.outcomes.values()) else "PARTIAL"
        kpi_summary = aggregate_kpis(obj.kpis, st.kpi_before, st.kpi_after)
        outcomes = tuple(st.outcomes[t.id] for t in self.plan.ordered())
        records = tuple(self._task_record(t, st.outcomes[t.id]) for t in self.plan.ordered())
        records += (self._run_record(status, completion, kpi_summary, outcomes),)
        return CompanyReport(
            objective_id=obj.id, objective_title=obj.title, status=status, completion=completion,
            dag=self.plan.dag(), assignments={t.id: t.role for t in self.plan.tasks},
            task_states=st.states(), kpi_before=dict(st.kpi_before), kpi_after=dict(st.kpi_after),
            kpi_summary=kpi_summary, outcomes=outcomes,
            denied=tuple(o.task_id for o in outcomes if o.state == "DENIED"),
            budget={"max_total_cost": self.plan.budget.max_total_cost,
                    "max_task_cost": self.plan.budget.max_task_cost, "spent": st.spent,
                    "reserved": st.reserved, "overruns": list(st.overruns),
                    "budget_exhausted": st.budget_exhausted,
                    "executed": st.executed, "currency": self.plan.budget.currency},
            rounds=st.rounds, trace=tuple(st.trace), learning_records=records,
        )

    @property
    def executor_calls(self) -> tuple[str, ...]:
        return tuple(self._executor_calls)

    # ---- learning records (форма schemas/learning_fix_case.schema.json) --------
    def _base_record(self, task_id: str, task_text: str) -> dict[str, Any]:
        obj = self.plan.objective
        return {
            "task_id": task_id, "model": MODEL_NAME, "agent": AGENT_NAME,
            "start_sha": _fingerprint(self.state.kpi_before), "end_sha": _fingerprint(self.state.kpi_after),
            "task": task_text, "symptom": "", "reproduction": [], "evidence": [],
            "root_cause_hypotheses": [], "rejected_hypotheses": [], "root_cause": "",
            "relevant_code_paths": ["bossman-core/bossman/company/runtime.py"],
            "fix_strategy": "", "alternatives_considered": [], "why_this_fix": "",
            "files_changed": [], "tests_added": [], "original_repro_result": "",
            "adversarial_variants": [], "regression_result": "", "external_verification": "",
            "generalizable_lessons": [], "teach_local_model": [], "confidence": 0.0,
            "limitations": [], "verified_by": [], "learning_status": "UNVERIFIED",
            "tags": {"domain": obj.domain, "component": "bossman.company", "severity": "INFO"},
        }

    def _task_record(self, task: CompanyTask, o: TaskOutcome) -> dict[str, Any]:
        rec = self._base_record(f"{self.plan.objective.id}/{task.id}",
                                f"{task.title} [{task.action}] role={task.role} kind={task.kind}")
        rec["symptom"] = o.reason or f"state={o.state}"
        rec["reproduction"] = [f"{e['event']}: {e['detail']}" for e in self.state.trace
                               if e["task_id"] == task.id]
        rec["fix_strategy"] = task.action
        rec["original_repro_result"] = o.result.summary if o.result else ""
        rec["confidence"] = 0.9 if (o.verification and o.verification.verified) else 0.3
        if o.verification is not None:
            rec["evidence"] = list(o.verification.evidence)
            rec["external_verification"] = o.verification.reason
        if o.state == "DONE" and o.verification is not None and o.verification.verified:
            rec["learning_status"], rec["outcome"] = "VERIFIED", "FIXED"
            rec["verified_by"] = [self.verifier_principal]
            rec["verifiers"] = [{"principal_id": self.verifier_principal, "role": "verifier",
                                 "independence_class": "external_tool"}]
            rec["evidence_records"] = [{"observed_at": float(self.clock()), "task_id": rec["task_id"],
                                        "source": self.verifier_principal, "principal_id": self.verifier_principal,
                                        "expected": "; ".join(f"{e.kind}:{e.target}" for e in task.evidence_requirements) or "fresh observation",
                                        "actual": o.verification.reason or "verified"}]
        elif o.state == "DENIED":
            rec["learning_status"], rec["outcome"] = "PARTIAL", "ACCEPTED_RISK_REQUIRES_OWNER"
            rec["symptom"] = "denied by approval gate: " + (o.approval.reason if o.approval else "")
            rec["generalizable_lessons"] = ["role name is not authority; gated kinds need an external approver"]
        elif o.state == "BUDGET_EXCEEDED":
            rec["learning_status"], rec["outcome"] = "PARTIAL", "BLOCKED_ENV"
        elif o.state == "SKIPPED":
            rec["learning_status"], rec["outcome"] = "PARTIAL", "PARTIAL"
        elif o.state == "FAILED":
            rec["learning_status"], rec["outcome"] = "UNVERIFIED", "REJECTED"
            if o.verification is not None and o.verification.status == "FAILED":
                rec["generalizable_lessons"] = ["executor self-report contradicted by fresh observation"]
        else:  # DONE без верификации / без требований к доказательствам
            rec["learning_status"], rec["outcome"] = "UNVERIFIED", "PARTIAL"
            rec["limitations"] = ["no evidence requirement or no verifier: self-report only"]
        return rec

    def _run_record(self, status: str, completion: str, kpi_summary: tuple[dict[str, Any], ...],
                    outcomes: tuple[TaskOutcome, ...]) -> dict[str, Any]:
        obj = self.plan.objective
        rec = self._base_record(f"{obj.id}/run", obj.title)
        rec["symptom"] = f"status={status} completion={completion}"
        rec["reproduction"] = [f"{o.task_id}: {o.state}" for o in outcomes]
        rec["evidence"] = [f"kpi {s['name']}: {s['before']} -> {s['after']}" for s in kpi_summary]
        rec["evidence"] += [e for o in outcomes for e in o.evidence]
        rec["limitations"] = [f"{o.task_id} {o.state}: {o.reason}" for o in outcomes if o.state != "DONE"]
        if status == "VERIFIED" and completion == "COMPLETE":
            rec["learning_status"] = "VERIFIED"
            rec["outcome"] = "FIXED"
            rec["verified_by"] = [self.verifier_principal]
            rec["verifiers"] = [{"principal_id": self.verifier_principal, "role": "verifier",
                                 "independence_class": "external_tool"}]
            rec["evidence_records"] = [{"observed_at": float(self.clock()), "task_id": rec["task_id"],
                                        "source": self.verifier_principal, "principal_id": self.verifier_principal,
                                        "expected": "all tasks verified by fresh observation",
                                        "actual": "; ".join(o.verification.status for o in outcomes if o.verification)}]
            rec["external_verification"] = "; ".join(
                f"{o.task_id}: {o.verification.reason}" for o in outcomes if o.verification)
            rec["confidence"] = 0.9
        elif status == "FAILED":
            rec["learning_status"], rec["outcome"] = "UNVERIFIED", "REJECTED"
        else:
            rec["learning_status"], rec["outcome"] = "PARTIAL", "PARTIAL"
        return rec
