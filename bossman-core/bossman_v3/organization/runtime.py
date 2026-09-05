"""OrganizationRuntime — Bossman как организация, а не набор агентов.

Цикл одного контракта (в этом порядке, без пропусков):

  зависимости → корректность контракта → команда (адаптивно к риску) →
  казначейство (резерв) → делегирование в V3 → валидация улик по контракту →
  независимое ревью → казначейство (факт) → обучение → durable-состояние →
  публикация ПОДТВЕРЖДЁННЫХ фактов в скоуп миссии/отдела.

Что организация НЕ делает: не исполняет, не верифицирует, не одобряет, не
расширяет права инструментов. Всё это — V3/V2 и владелец. Если нижний слой
остановился на «ждёт владельца», контракт переходит в WAITING_APPROVAL без
списания попытки; повторный `run_mission` после одобрения продолжает с того же
журнала (V3.4), не переигрывая сделанное.

Рестарт: конструктор поднимает отделы, агентов, конверты и обучение из store.
`resume()` возобновляет все незавершённые миссии. Завершённые контракты
(COMPLETED) больше никогда не делегируются — это и есть
DUPLICATE_SIDE_EFFECT_COUNT=0 на уровне организации; на уровне шагов то же
обеспечивает журнал V3.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Protocol

from .bridges import (ExecutionBridge, HumanReviewPort, MissionReporter, MissionStatus,
                      RecordingHumanReview)
from .contracts import DelegationContract
from .control_plane import OrganizationSnapshot, snapshot
from .events import EventIntake, EventOutcome, Reaction
from .learning import OrganizationalLearning
from .marketplace import CapabilityMarketplace
from .memory_scope import ScopedKnowledge
from .models import (EXECUTOR, LEAD, RISK, REVIEWER, AgentProfile, Department, MissionState, ReviewVerdict,
                     Resources, RiskTier, TaskState, WorkResult)
from .planner import NO_EXECUTABLE_STEPS, PlannerPort
from .store import OrganizationStore
from .teams import AdaptiveTeamFormer, MissionTeam
from .treasury import ResourceTreasury

EVENT_MISSION = "events"
TERMINAL = {TaskState.COMPLETED, TaskState.FAILED}
MAX_INFRA_RETRIES = 2          # переносов после потери узла до эскалации владельцу


class ReviewerPort(Protocol):
    def review(self, contract: DelegationContract, result: WorkResult, *, reviewer_id: str,
               producer_id: str) -> ReviewVerdict: ...


class ContractReviewer:
    """Ревьюер по умолчанию: детерминированная повторная валидация улик.
    Независимость проверяется типизированными principal'ами (bossman.deep_fix:
    alias одной модели под другим именем — не независимость), а не сравнением
    строк agent_id. Ревьюер может только наложить вето — подтвердить
    непроверенное он не способен по построению."""

    def __init__(self, marketplace: CapabilityMarketplace | None = None) -> None:
        self.marketplace = marketplace

    def _principal(self, agent_id: str, role: str) -> "Principal | str":
        from bossman.deep_fix import Principal
        a = self.marketplace.agent(agent_id) if self.marketplace else None
        if a is None:
            return f"{role}:{agent_id}"
        return Principal(principal_id=a.principal, model_id=a.model, role=role,
                         independence_class="cross_model" if a.model else "external_tool")

    def review(self, contract: DelegationContract, result: WorkResult, *, reviewer_id: str,
               producer_id: str) -> ReviewVerdict:
        from bossman.company.runtime import verifier_dependency_reason
        why = verifier_dependency_reason(self._principal(reviewer_id, "verifier"),
                                         self._principal(producer_id, "coder"))
        if reviewer_id == producer_id or why:
            return ReviewVerdict(reviewer_id, False, why or "reviewer is the producer — self-review is not review",
                                 independent=False)
        ok, errors = contract.validate(result)
        if not ok:
            return ReviewVerdict(reviewer_id, False, "; ".join(errors))
        return ReviewVerdict(reviewer_id, True, "evidence re-validated independently")


class OrganizationRuntime:
    def __init__(self, *, store: OrganizationStore, execution: ExecutionBridge,
                 human_review: HumanReviewPort | None = None, reporter: MissionReporter | None = None,
                 reviewer: ReviewerPort | None = None, reactions: list[Reaction] | None = None,
                 failure_root: str | None = None, planner: "PlannerPort | None" = None) -> None:
        self.store = store
        self.execution = execution
        self.planner = planner                    # ORG-02: контракт без steps → план или BLOCKED
        self.human_review = human_review or RecordingHumanReview()
        self.reporter = reporter
        self.learning = OrganizationalLearning(store)
        self.marketplace = CapabilityMarketplace(store.agents(), self.learning)
        self.reviewer = reviewer or ContractReviewer(self.marketplace)
        self.teams = AdaptiveTeamFormer(self.marketplace)
        self.treasury = ResourceTreasury()
        self.knowledge = ScopedKnowledge(store, failure_root=failure_root)
        self.events = EventIntake(store, reactions or [])
        self._departments: dict[str, Department] = {d.department_id: d for d in store.departments()}
        parents = store.envelope_parents()
        for scope, (limit, spent) in store.envelopes().items():
            self.treasury.restore(scope, limit=limit, spent=spent, parent=parents.get(scope, ""))

    # ------------------------------------------------------------ registry

    def register_department(self, d: Department) -> None:
        self.treasury.set_limit(f"department:{d.department_id}", d.budget, parent="organization")   # INV-3
        self._departments[d.department_id] = d
        self.store.save_department(d)
        self._persist_envelope(f"department:{d.department_id}")

    def register_agent(self, a: AgentProfile) -> None:
        if a.department_id not in self._departments:
            raise KeyError(f"unknown department {a.department_id!r}; register the department first")
        self.marketplace.upsert(a)
        self.store.save_agent(a)

    def set_organization_budget(self, limit: Resources) -> None:
        self.treasury.set_limit("organization", limit)
        self._persist_envelope("organization")

    def department(self, department_id: str) -> Department:
        try:
            return self._departments[department_id]
        except KeyError:
            raise KeyError(f"unknown department {department_id!r}") from None

    def departments(self) -> list[Department]:
        return list(self._departments.values())

    # ------------------------------------------------------------ missions

    def receive_mission(self, mission_id: str, *, title: str, department_id: str,
                        contracts: list[DelegationContract], source: str = "",
                        budget: Resources | None = None) -> MissionStatus:
        self.department(department_id)
        if self.store.mission(mission_id) is not None:
            raise ValueError(f"mission {mission_id!r} already received; use run_mission/resume")
        ids = [c.work_id for c in contracts]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate work_id in mission contracts")
        known = set(ids)
        for c in contracts:
            if c.mission_id != mission_id:
                raise ValueError(f"contract {c.work_id} belongs to mission {c.mission_id!r}")
            for dep in c.dependencies:
                if dep not in known:
                    raise ValueError(f"contract {c.work_id} depends on unknown {dep!r}")
            if c.department_id not in self._departments:
                raise KeyError(f"contract {c.work_id}: unknown department {c.department_id!r}")
        _topological(contracts)                          # цикл → ValueError до записи
        self.store.save_mission(mission_id, title=title, department_id=department_id,
                                state=MissionState.RECEIVED, source=source,
                                payload={"contracts": ids, "budget": (budget or Resources()).to_dict()})
        if budget is not None:
            self.treasury.set_limit(f"mission:{mission_id}", budget, parent=f"department:{department_id}")   # INV-3
            self._persist_envelope(f"mission:{mission_id}")
        for c in contracts:
            self.store.save_work(c, state=TaskState.PLANNED, assigned=[], attempts=0)
        self.store.log("mission.received", mission_id=mission_id, detail=title)
        return self.status(mission_id)

    def run_mission(self, mission_id: str) -> MissionStatus:
        mission = self.store.mission(mission_id)
        if mission is None:
            raise KeyError(mission_id)
        if mission["state"] == MissionState.COMPLETED.value:
            return self.status(mission_id)
        self.store.save_mission(mission_id, title=mission["title"], department_id=mission["department_id"],
                                state=MissionState.ACTIVE, source=mission["source"], payload=mission["payload"])
        works = {w["work_id"]: w for w in self.store.works(mission_id)}
        for contract in _topological([w["contract"] for w in works.values()]):
            self._drive(contract, mission_id)
        self._finish_mission(mission_id, self.status(mission_id))
        status = self.status(mission_id)              # состояние миссии уже обновлено
        if self.reporter is not None:
            self.reporter.report(status)
        return status

    def resume(self) -> list[MissionStatus]:
        """После рестарта: все незавершённые миссии продолжаются с первого
        незавершённого контракта; COMPLETED контракты не трогаются."""
        out = []
        for m in self.store.missions():
            if m["state"] in (MissionState.RECEIVED.value, MissionState.ACTIVE.value, MissionState.BLOCKED.value):
                out.append(self.run_mission(m["mission_id"]))
        return out

    # -------------------------------------------------------------- events

    def accept_event(self, kind: str, payload: Mapping[str, Any]) -> EventOutcome:
        if self.store.mission(EVENT_MISSION) is None:
            self.store.save_mission(EVENT_MISSION, title="event reactions", department_id="organization",
                                    state=MissionState.ACTIVE, source="events", payload={})
        outcome, contract = self.events.accept(kind, payload, mission_id=EVENT_MISSION)
        if contract is not None:
            self.store.save_work(contract, state=TaskState.PLANNED, assigned=[], attempts=0)
            self.store.log("event.accepted", mission_id=EVENT_MISSION, work_id=contract.work_id, detail=kind)
        return outcome

    def run_reactions(self) -> list[WorkResult | None]:
        out = []
        for w in self.store.works(EVENT_MISSION):
            if TaskState(w["state"]) not in TERMINAL:
                out.append(self._drive(w["contract"], EVENT_MISSION))
        return out

    # ------------------------------------------------------------- reading

    def status(self, mission_id: str) -> MissionStatus:
        mission = self.store.mission(mission_id)
        if mission is None:
            raise KeyError(mission_id)
        works = self.store.works(mission_id)
        results = {r.work_id: r for r in self.store.results(mission_id)}
        completed = tuple(w["work_id"] for w in works if w["state"] == TaskState.COMPLETED.value)
        verified = tuple(w for w in completed if w in results and results[w].verified)
        blockers = tuple({"work_id": w["work_id"], "state": w["state"],
                          "reason": str((w["contract"].metadata.get("runtime") or {}).get("last_reason", ""))}
                         for w in works if w["state"] in (TaskState.BLOCKED.value, TaskState.FAILED.value))
        waiting = tuple(w["work_id"] for w in works if w["state"] == TaskState.WAITING_APPROVAL.value)
        failed = tuple(w["work_id"] for w in works if w["state"] == TaskState.FAILED.value)
        env = self.treasury.envelope(f"mission:{mission_id}")
        false_success = sum(1 for r in results.values()
                            if (r.claims.get("runner_completed") or r.claims.get("claimed_effect")) and not r.verified)
        vetoes = sum(1 for r in results.values() if r.metadata.get("review_veto"))
        state = MissionState(mission["state"])
        done = bool(works) and len(completed) == len(works)
        return MissionStatus(mission_id=mission_id, state=state.value,
                             progress=(len(completed) / len(works)) if works else 0.0,
                             completed=completed, verified_results=verified, blockers=blockers,
                             waiting_approval=waiting, failed=failed, cost=env.to_dict(),
                             quality={"false_success_attempts": false_success, "review_vetoes": vetoes,
                                      "unverified_completed": len(completed) - len(verified)},
                             done=done)

    def snapshot(self) -> OrganizationSnapshot:
        return snapshot(self.store, self.treasury, self.learning)

    # ---------------------------------------------------------------- core

    def _drive(self, contract: DelegationContract, mission_id: str) -> WorkResult | None:
        """Довести один контракт до терминального состояния или до точки, где
        нужен владелец. Возвращает последний результат (или None, если до
        исполнения не дошло)."""
        row = self.store.work(contract.work_id)
        state = TaskState(row["state"]) if row else TaskState.PLANNED
        if state == TaskState.COMPLETED:
            return self.store.result(contract.work_id)          # сделано — не переигрываем
        if state == TaskState.FAILED:
            return self.store.result(contract.work_id)
        last: WorkResult | None = None
        while True:
            outcome = self._attempt(contract, mission_id)
            last = outcome if isinstance(outcome, WorkResult) else last
            new_state = TaskState(self.store.work(contract.work_id)["state"])
            if new_state in TERMINAL or new_state in (TaskState.BLOCKED, TaskState.WAITING_APPROVAL):
                return last
            # PLANNED после провала = эскалация уровня: следующий круг с новым
            # исполнителем; предел — escalation.max_attempts в _attempt

    def _attempt(self, contract: DelegationContract, mission_id: str) -> WorkResult | None:
        row = self.store.work(contract.work_id)
        attempts = int(row["attempts"]) if row else 0
        runtime_meta = dict(contract.metadata.get("runtime") or {})
        failed_agents: list[str] = list(runtime_meta.get("failed_agents") or [])
        dept = self.department(contract.department_id)

        # 1. зависимости
        for dep in contract.dependencies:
            drow = self.store.work(dep)
            if drow is None or drow["state"] != TaskState.COMPLETED.value:
                return self._block(contract, f"dependency {dep!r} is not completed", ask_owner=False)

        # 1b. SLA (ORG-03): истёкший deadline — не делегируем, владелец решает
        if contract.deadline:
            try:
                due = datetime.fromisoformat(contract.deadline.replace("Z", "+00:00"))
                if due.tzinfo is None:
                    due = due.replace(tzinfo=timezone.utc)
            except ValueError:
                return self._block(contract, f"deadline is not ISO-8601: {contract.deadline!r}", ask_owner=True)
            if datetime.now(timezone.utc) > due:
                return self._block(contract, f"deadline_missed: {contract.deadline}", ask_owner=True)

        # 2. контракт корректен?
        problems = contract.problems()
        if problems:
            return self._block(contract, "contract rejected: " + "; ".join(problems), ask_owner=True)

        # 2b. ORG-02: шаги. Без исполняемых шагов делегировать нечего — это не
        #     провал исполнителя и не попытка: BLOCKED/no_executable_steps, владелец решает.
        if not contract.steps:
            planned = None
            if self.planner is not None:
                try:
                    planned = self.planner.plan(contract)
                except Exception as exc:  # noqa: BLE001 — планировщик упал = плана нет
                    self.store.log("work.planner_failed", mission_id=mission_id, work_id=contract.work_id,
                                   detail=f"{type(exc).__name__}: {exc}"[:300])
            if planned:
                contract.steps = [dict(s) for s in planned]
                contract.metadata["planned_by"] = type(self.planner).__name__
                self.store.save_work(contract, state=TaskState(row["state"]) if row else TaskState.PLANNED)
                self.store.log("work.planned", mission_id=mission_id, work_id=contract.work_id,
                               detail=f"{len(planned)} step(s) by {type(self.planner).__name__}")
            else:
                return self._block(contract, f"{NO_EXECUTABLE_STEPS}: contract carries no executable steps "
                                             f"and the planner produced none", ask_owner=True)

        # 3. попытки исчерпаны?
        if attempts >= contract.escalation.max_attempts:
            return self._fail(contract, mission_id, f"max attempts ({contract.escalation.max_attempts}) exhausted",
                              result=None)

        # 4. команда пропорционально риску, с эскалацией уровня после провалов
        min_tier = self.marketplace.escalated_min_tier(contract, failed_agents=failed_agents) \
            if contract.escalation.on_failure == "escalate_tier" else "deterministic"
        team = self.teams.form(team_id=f"team-{contract.work_id}-{attempts + 1}", mission_id=mission_id,
                               department=dept, contract=contract, min_tier=min_tier, exclude=set(failed_agents))
        self.store.save_team(team.team_id, mission_id, team.to_dict())
        if EXECUTOR not in team.slots:
            return self._block(contract, f"no eligible executor (min tier {min_tier}); unfilled={team.unfilled}",
                               ask_owner=True)
        if team.unfilled:
            return self._block(contract, f"required roles unfilled for risk {contract.risk.value}: {team.unfilled}",
                               ask_owner=True)
        executor_id = team.slots[EXECUTOR]

        # 5. казначейство — резерв ДО делегирования
        scopes = self.treasury.scopes_for(contract.department_id, mission_id)
        reserve = self.treasury.reserve(scopes, contract.budget)
        if not reserve.allowed:
            return self._block(contract, reserve.reason, ask_owner=reserve.ask_owner)

        # 6. делегирование в V3
        self.store.save_work(contract, state=TaskState.EXECUTING, assigned=team.members, attempts=attempts + 1)
        self.store.log("work.delegated", mission_id=mission_id, work_id=contract.work_id,
                       detail=f"executor={executor_id} team={team.slots} tier>={min_tier}")
        self._bump_load(team, +1)
        try:
            result = self.execution.execute(contract, agent_id=executor_id)
        except Exception as exc:  # noqa: BLE001 — падение исполнителя = шаг не исполнен
            result = WorkResult(contract.work_id, executed=False, produced_by=executor_id,
                                reason=f"execution bridge raised: {type(exc).__name__}: {exc}")
        finally:
            self._bump_load(team, -1)
        if not isinstance(result, WorkResult) or result.work_id != contract.work_id:
            result = WorkResult(contract.work_id, executed=False, produced_by=executor_id,
                                reason="execution bridge returned a result for another work item")

        # 7. ждёт владельца? — не провал и не попытка
        if result.metadata.get("waiting_approval"):
            self.treasury.release(scopes, contract.budget)
            self.store.save_work(contract, state=TaskState.WAITING_APPROVAL, attempts=attempts)
            self._remember(contract, last_reason=result.reason, failed_agents=failed_agents)
            self.store.save_result(result, mission_id)
            self.store.log("work.waiting_approval", mission_id=mission_id, work_id=contract.work_id,
                           detail=result.reason)
            self.human_review.request(contract, f"lower layer waits for the owner: {result.reason}")
            self._dissolve(team, mission_id)
            return result

        # 7b. флот не смог разместить работу (нет узла / приватность / ресурсы):
        #     это не провал исполнителя и не попытка — BLOCKED до изменения флота/решения владельца
        if result.metadata.get("fleet_blocked"):
            self.treasury.release(scopes, contract.budget)
            self.store.save_work(contract, state=TaskState.PLANNED, attempts=attempts)
            self.store.save_result(result, mission_id)
            self._dissolve(team, mission_id)
            return self._block(contract, result.reason, ask_owner=bool(result.metadata.get("ask_owner")), result=result)

        # 7c. инфраструктурный провал (узел потерян): исполнитель не виноват,
        #     попытка не списывается; ограниченное число переносов, потом BLOCKED
        if result.metadata.get("infrastructure_failure"):
            self.treasury.release(scopes, contract.budget)
            infra = int(runtime_meta.get("infra_retries", 0)) + 1
            contract.metadata["runtime"] = {**runtime_meta, "infra_retries": infra, "last_reason": result.reason[:500],
                                            "failed_agents": failed_agents}
            self.store.save_result(result, mission_id)
            self._dissolve(team, mission_id)
            self.store.log("work.infrastructure_failure", mission_id=mission_id, work_id=contract.work_id,
                           detail=f"{result.reason[:200]} (infra retry {infra}/{MAX_INFRA_RETRIES})")
            if infra > MAX_INFRA_RETRIES:
                self.store.save_work(contract, state=TaskState.PLANNED, attempts=attempts)
                return self._block(contract, f"infrastructure retries exhausted: {result.reason}", ask_owner=True, result=result)
            self.store.save_work(contract, state=TaskState.PLANNED, attempts=attempts)
            return result

        # 8. валидация по контракту — единственный источник success
        ok, errors = contract.validate(result)
        result.success = ok
        result.contract_errors = errors

        # 9. независимое ревью (может только запретить)
        reviewer_id = team.slots.get(REVIEWER) or team.slots.get(RISK)
        if ok and reviewer_id:
            self.store.save_work(contract, state=TaskState.VERIFYING)
            verdict = self.reviewer.review(contract, result, reviewer_id=reviewer_id, producer_id=executor_id)
            result.reviewed_by = reviewer_id
            result.metadata["review"] = verdict.to_dict()
            if not verdict.approved:
                result.success = False
                result.metadata["review_veto"] = True
                result.contract_errors.append(f"review veto by {reviewer_id}: {verdict.reason}")
                ok = False

        # 10. казначейство — факт
        actual = result.cost if any(result.cost.to_dict().values()) else contract.budget
        commit = self.treasury.commit(scopes, contract.budget, actual)
        for scope in scopes:
            self._persist_envelope(scope)

        # 11. обучение — по наблюдаемому исходу
        claimed = any(bool(result.claims.get(k)) for k in ("runner_completed", "claimed_effect", "done"))
        self.learning.observe(executor_id, contract.required_capability, verified=result.verified,
                              claimed_success=claimed, cost_usd=actual.usd,
                              retry=attempts > 0, escalated=bool(failed_agents))

        # 12. состояние + публикация подтверждённого
        self.store.save_result(result, mission_id)
        self._dissolve(team, mission_id)
        if ok and result.verified:
            self.store.save_work(contract, state=TaskState.COMPLETED)
            self._remember(contract, last_reason="", failed_agents=failed_agents)
            self._publish(contract, result, mission_id)
            self.store.log("work.completed", mission_id=mission_id, work_id=contract.work_id,
                           detail=f"evidence={len(result.evidence)} reviewer={reviewer_id or '-'}")
            if not commit.allowed:
                self.human_review.request(contract, commit.reason)
            return result
        if not commit.allowed and contract.escalation.on_budget_exceeded == "ask_owner":
            return self._block(contract, commit.reason, ask_owner=True, result=result)
        reason = result.reason or "; ".join(result.contract_errors) or "verification failed"
        failed_agents = failed_agents + [executor_id]
        self._remember(contract, last_reason=reason, failed_agents=failed_agents)
        fm = self.knowledge.failure_memory(contract.department_id)
        if fm is not None:
            fm.record({"signature": f"{contract.required_capability}:{contract.work_id}", "approach": executor_id,
                       "error": reason[:500], "by": executor_id})
        if attempts + 1 >= contract.escalation.max_attempts or contract.escalation.on_failure == "fail":
            return self._fail(contract, mission_id, reason, result=result)
        if contract.escalation.on_failure == "ask_owner":
            return self._block(contract, reason, ask_owner=True, result=result)
        # escalate_tier: остаёмся PLANNED и идём на следующий круг _drive
        self.store.save_work(contract, state=TaskState.PLANNED)
        self.store.log("work.escalate", mission_id=mission_id, work_id=contract.work_id, detail=reason)
        return result

    # ------------------------------------------------------------ helpers

    def _remember(self, contract: DelegationContract, *, last_reason: str, failed_agents: list[str]) -> None:
        contract.metadata["runtime"] = {"last_reason": last_reason[:500], "failed_agents": list(failed_agents)}
        row = self.store.work(contract.work_id)
        self.store.save_work(contract, state=TaskState(row["state"]) if row else TaskState.PLANNED)

    def _block(self, contract: DelegationContract, reason: str, *, ask_owner: bool,
               result: WorkResult | None = None) -> WorkResult | None:
        failed = list((contract.metadata.get("runtime") or {}).get("failed_agents") or [])
        contract.metadata["runtime"] = {"last_reason": reason[:500], "failed_agents": failed}
        self.store.save_work(contract, state=TaskState.BLOCKED)
        self.store.log("work.blocked", mission_id=contract.mission_id, work_id=contract.work_id, detail=reason)
        if ask_owner:
            self.human_review.request(contract, reason)
        return result

    def _fail(self, contract: DelegationContract, mission_id: str, reason: str, *,
              result: WorkResult | None) -> WorkResult | None:
        failed = list((contract.metadata.get("runtime") or {}).get("failed_agents") or [])
        contract.metadata["runtime"] = {"last_reason": reason[:500], "failed_agents": failed}
        self.store.save_work(contract, state=TaskState.FAILED)
        if result is not None:
            result.success = False
            self.store.save_result(result, mission_id)
        self.store.log("work.failed", mission_id=mission_id, work_id=contract.work_id, detail=reason)
        return result

    def _publish(self, contract: DelegationContract, result: WorkResult, mission_id: str) -> None:
        dept = self.department(contract.department_id)
        for e in result.evidence:
            if not e.verified:
                continue
            payload = {"work_id": contract.work_id, "kind": e.kind, "ref": e.ref, "source": e.source}
            self.knowledge.publish(f"mission:{mission_id}", "verified_fact", payload,
                                   provenance=e.source, confidence=1.0)
            self.knowledge.publish(dept.memory_scope, "verified_fact", payload, provenance=e.source,
                                   confidence=1.0, source_scope=f"mission:{mission_id}")

    def _bump_load(self, team: MissionTeam, delta: int) -> None:
        for agent_id in team.members:
            a = self.marketplace.agent(agent_id)
            if a is not None:
                a.current_load = max(0, a.current_load + delta)

    def _dissolve(self, team: MissionTeam, mission_id: str) -> None:
        team.dissolved = True
        self.store.save_team(team.team_id, mission_id, team.to_dict(), dissolved=True)

    def _persist_envelope(self, scope: str) -> None:
        env = self.treasury.envelope(scope)
        self.store.save_envelope(scope, limit=env.limit, spent=env.spent, reserved=env.reserved, parent=env.parent)

    def _finish_mission(self, mission_id: str, status: MissionStatus) -> None:
        mission = self.store.mission(mission_id)
        if status.done and len(status.verified_results) == len(status.completed):
            state = MissionState.COMPLETED
        elif status.failed:
            state = MissionState.FAILED
        elif status.blockers or status.waiting_approval:
            state = MissionState.BLOCKED
        else:
            state = MissionState.ACTIVE
        self.store.save_mission(mission_id, title=mission["title"], department_id=mission["department_id"],
                                state=state, source=mission["source"], payload=mission["payload"])
        self.store.log(f"mission.{state.value}", mission_id=mission_id,
                       detail=f"progress={status.progress:.2f} verified={len(status.verified_results)}")


def _topological(contracts: list[DelegationContract]) -> list[DelegationContract]:
    by_id = {c.work_id: c for c in contracts}
    indeg = {c.work_id: 0 for c in contracts}
    for c in contracts:
        for d in c.dependencies:
            if d not in by_id:
                raise ValueError(f"{c.work_id} depends on unknown {d!r}")
            if d == c.work_id:
                raise ValueError(f"{c.work_id} depends on itself")
            indeg[c.work_id] += 1
    ready = sorted([w for w, n in indeg.items() if n == 0], key=lambda w: (by_id[w].priority, w))
    out: list[DelegationContract] = []
    while ready:
        w = ready.pop(0)
        out.append(by_id[w])
        for c in contracts:
            if w in c.dependencies:
                indeg[c.work_id] -= 1
                if indeg[c.work_id] == 0:
                    ready.append(c.work_id)
                    ready.sort(key=lambda x: (by_id[x].priority, x))
    if len(out) != len(contracts):
        raise ValueError("cycle in mission contracts")
    return out
