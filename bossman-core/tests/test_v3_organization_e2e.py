"""Organization Layer — детерминированный E2E (§22) и рестарт (§19).

Реальные V3-компоненты: UniversalComputerAgent (policy → approval → execute →
observe → verify), CompoundRunner, TaskJournal на диске. Фейковы только
инструмент («файловая система» в tmp_path) и его наблюдатель — они настоящие
побочные эффекты в песочнице, и именно их дубликаты считаются.

Сценарии:
  * миссия → организация → команда → контракт → исполнение → улики из журнала →
    независимое ревью → состояние → миссия завершена;
  * исполнитель заявляет «готово», а эффекта нет → контракт FAILED, миссия не
    COMPLETED, false_success учтён, агент понижен рынком;
  * рестарт после первого подтверждённого шага → организация восстановлена →
    сделанное не повторяется (DUPLICATE_SIDE_EFFECT_COUNT=0) → продолжение с
    первого незакрытого шага;
  * ASK нижнего слоя → WAITING_APPROVAL без списания попытки → после решения
    владельца цепочка продолжается с того же журнала;
  * бюджет отдела исчерпан → BLOCKED + запрос владельцу, исполнитель не вызван;
  * эскалация уровня после провала local_small → local_strong, не frontier;
  * события: CI failure → контракт триажа → прогон через тот же цикл.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from bossman_v3.computer_agent.agent import UniversalComputerAgent
from bossman_v3.contracts import (ApprovalDecision, ExecutionReceipt, Observation, PolicyDecision,
                                  SideEffectClass, TypedAction, VerificationResult)
from bossman_v3.execution import PlanStep
from bossman_v3.organization import (
    EXECUTOR, REVIEWER, AgentProfile, DelegationContract, Department, EscalationPolicy, EvidenceRequirement,
    MissionState, OrganizationRuntime, OrganizationStore, Reaction, RecordingHumanReview, RecordingReporter,
    Resources, RiskTier, TaskState, V3ExecutionBridge, step_to_dict)


# ------------------------------------------------------------------ world

class World:
    """Песочница с реальными побочными эффектами: файлы в tmp_path + счётчик
    записей. Каждая запись = один side effect; повтор = дубликат."""

    def __init__(self, root: Path):
        self.root = root
        self.writes: list[str] = []
        self.ask_for: set[str] = set()         # имена файлов, запись которых требует ASK
        self.approved: set[str] = set()
        self.liars: set[str] = set()           # агенты, которые «пишут», но не пишут

    def side_effects(self) -> int:
        return len(self.writes)


class _Policy:
    def __init__(self, world: World):
        self.world = world

    def authorize(self, action, context):
        name = str(action.args["name"])
        return PolicyDecision(True, requires_approval=name in self.world.ask_for)


class _Approval:
    def __init__(self, world: World):
        self.world = world
        self.requests: list[str] = []

    def request(self, action, policy, context):
        name = str(action.args["name"])
        if name in self.world.approved:
            return ApprovalDecision(True, approval_id=f"ap-{name}")
        self.requests.append(name)
        return ApprovalDecision(False, reason=f"создан запрос на подтверждение: {name}")


class _Executor:
    def __init__(self, world: World, agent_id: str):
        self.world, self.agent_id = world, agent_id

    def supports(self, action_type):
        return action_type == "fs.write"

    def execute(self, action):
        name, content = str(action.args["name"]), str(action.args.get("content", "1"))
        now = datetime.now(timezone.utc)
        if self.agent_id not in self.world.liars:
            (self.world.root / name).write_text(content, encoding="utf-8")
            self.world.writes.append(name)
        return ExecutionReceipt("fs.write", now, now, effect_id=f"eff-{name}-{len(self.world.writes)}")


class _Observer:
    def __init__(self, world: World):
        self.world = world

    def observe_fresh(self, action, receipt):
        p = self.world.root / str(action.args["name"])
        return Observation(observed_at=datetime.now(timezone.utc), source="fs",
                           state={"exists": p.exists()})


class _Verifier:
    def verify(self, action, receipt, observation):
        ok = bool(observation.state.get("exists"))
        return VerificationResult(ok, "" if ok else "файл не появился после исполнения")


def _agent_factory(world: World, approval: _Approval):
    def factory(agent_id: str, contract: DelegationContract) -> UniversalComputerAgent:
        return UniversalComputerAgent(_Policy(world), approval, _Executor(world, agent_id), _Observer(world), _Verifier())
    return factory


def _write_step(world: World, sid: str, name: str) -> dict:
    action = TypedAction("fs.write", {"name": name, "content": "1",
                                     "expect": {"kind": "file", "target": str(world.root / name),
                                                "expect": {"exists": True}}},
                         side_effect=SideEffectClass.IDEMPOTENT_WRITE)
    return step_to_dict(PlanStep(sid, f"записать {name}", action))


def _contract(world: World, work_id: str, names: list[str], *, risk=RiskTier.MEDIUM, deps=(), mission="m1",
              max_attempts=2, budget=Resources(usd=0.5, tokens=1000, compute_seconds=60)) -> DelegationContract:
    return DelegationContract(
        work_id=work_id, mission_id=mission, department_id="engineering", goal=f"создать {names}",
        required_capability="fs.write", success_criteria=["файлы существуют"],
        evidence_required=[EvidenceRequirement("file", str(world.root / n)) for n in names],
        budget=budget, risk=risk, dependencies=list(deps),
        escalation=EscalationPolicy(max_attempts=max_attempts, on_failure="escalate_tier"),
        steps=[_write_step(world, f"{work_id}-s{i}", n) for i, n in enumerate(names, 1)])


# ---------------------------------------------------------------- fixture

class Org:
    def __init__(self, tmp_path: Path):
        self.tmp = tmp_path
        self.world = World(tmp_path / "world")
        self.world.root.mkdir()
        self.approval = _Approval(self.world)
        self.human = RecordingHumanReview()
        self.reporter = RecordingReporter()
        self.runtime = self.boot()

    def boot(self, *, reactions=None) -> OrganizationRuntime:
        store = OrganizationStore(self.tmp / "org.sqlite")
        bridge = V3ExecutionBridge(agent_factory=_agent_factory(self.world, self.approval),
                                   journal_root=self.tmp / "journals",
                                   failure_memory_for=lambda d: None)
        return OrganizationRuntime(store=store, execution=bridge, human_review=self.human,
                                   reporter=self.reporter, reactions=reactions or [],
                                   failure_root=str(self.tmp / "failures"))

    def restart(self, **kw) -> OrganizationRuntime:
        """Убить процесс = потерять всё в памяти; store/journals на диске."""
        self.runtime = self.boot(**kw)
        return self.runtime


@pytest.fixture
def org(tmp_path) -> Org:
    o = Org(tmp_path)
    rt = o.runtime
    rt.set_organization_budget(Resources(usd=100))
    rt.register_department(Department("engineering", purpose="код", capabilities={"fs.write"},
                                      budget=Resources(usd=10, tokens=100_000, compute_seconds=3600)))
    rt.register_agent(AgentProfile("coder-local", "engineering", {EXECUTOR}, {"fs.write"}, tier="local_small", model="glm"))
    rt.register_agent(AgentProfile("coder-strong", "engineering", {EXECUTOR}, {"fs.write"}, tier="local_strong", model="qwen"))
    rt.register_agent(AgentProfile("coder-frontier", "engineering", {EXECUTOR}, {"fs.write"}, tier="frontier", model="claude",
                                   cost_per_call_usd=0.2))
    rt.register_agent(AgentProfile("reviewer", "engineering", {REVIEWER}, {"fs.write"}, tier="local_small", model="llama"))
    return o


# ------------------------------------------------------------------ tests

def test_e2e_mission_completes_with_journal_evidence_and_independent_review(org):
    rt, w = org.runtime, org.world
    rt.receive_mission("m1", title="release", department_id="engineering", source="executive-os",
                       contracts=[_contract(w, "w1", ["a.txt"]), _contract(w, "w2", ["b.txt"], deps=["w1"])],
                       budget=Resources(usd=5))
    status = rt.run_mission("m1")

    assert status.done and status.state == MissionState.COMPLETED.value
    assert status.verified_results == ("w1", "w2") and status.progress == 1.0
    assert (w.root / "a.txt").exists() and (w.root / "b.txt").exists() and w.side_effects() == 2
    r1 = rt.store.result("w1")
    assert r1.verified and r1.evidence[0].source.startswith("journal:m1__w1/") and r1.evidence[0].kind == "file"
    assert r1.produced_by == "coder-local" and r1.reviewed_by == "reviewer"
    assert r1.metadata["review"]["independent"] is True
    teams = rt.store.teams("m1")
    assert teams and all(t["dissolved"] for t in teams)                     # временные команды распущены
    assert [f.kind for f in rt.knowledge.read("mission:m1")] == ["verified_fact", "verified_fact"]
    assert rt.knowledge.read("department:trading") == []
    assert org.reporter.statuses[-1].verified_results == ("w1", "w2")     # Executive OS получил отчёт
    assert rt.treasury.envelope("mission:m1").spent.compute_seconds >= 0
    assert org.human.requests == []


def test_false_success_is_forced_failed_and_parent_not_completed(org):
    rt, w = org.runtime, org.world
    w.liars = {"coder-local", "coder-strong", "coder-frontier"}
    rt.receive_mission("m1", title="x", department_id="engineering",
                       contracts=[_contract(w, "w1", ["a.txt"], max_attempts=1), _contract(w, "w2", ["b.txt"], deps=["w1"])])
    status = rt.run_mission("m1")

    assert not status.done and status.state == MissionState.FAILED.value
    assert status.failed == ("w1",) and status.verified_results == ()
    assert rt.store.work("w2")["state"] == TaskState.BLOCKED.value          # ребёнок без родителя не идёт
    assert w.side_effects() == 0
    r = rt.store.result("w1")
    assert r.executed is False and r.success is False                        # «исполнитель отработал» ≠ исполнено
    assert rt.learning.stats("coder-local", "fs.write").failures == 1
    snap = rt.snapshot()
    assert snap.counts["verified_completed"] == 0 and snap.counts["failed"] == 1


def test_restart_resumes_without_duplicate_side_effects(org):
    rt, w = org.runtime, org.world
    w.ask_for = {"b.txt"}                                                    # второй шаг требует владельца
    rt.receive_mission("m1", title="x", department_id="engineering",
                       contracts=[_contract(w, "w1", ["a.txt", "b.txt", "c.txt"])])
    first = rt.run_mission("m1")
    assert first.waiting_approval == ("w1",) and w.side_effects() == 1       # a.txt записан, b.txt ждёт
    assert rt.store.work("w1")["attempts"] == 0                              # ожидание — не попытка
    assert org.human.requests and "waits for the owner" in org.human.requests[-1][1]

    # --- процесс умирает; владелец одобряет; новый процесс ---
    w.approved = {"b.txt"}
    revived = org.restart()
    assert revived.department("engineering").purpose == "код"               # реестр восстановлен из store
    assert revived.marketplace.agent("coder-local") is not None
    assert revived.store.mission("m1")["state"] == MissionState.BLOCKED.value
    statuses = revived.resume()

    assert statuses[-1].done and statuses[-1].verified_results == ("w1",)
    assert w.writes == ["a.txt", "b.txt", "c.txt"]                           # DUPLICATE_SIDE_EFFECT_COUNT = 0
    j = json.loads((org.tmp / "journals" / "m1__w1.json").read_text())
    assert [s["status"] for s in j["steps"]] == ["DONE", "DONE", "DONE"]
    # завершённую миссию можно «возобновлять» сколько угодно — ничего не исполняется
    revived.resume()
    org.restart().resume()
    assert w.side_effects() == 3


def test_completed_work_is_never_delegated_again_even_if_journal_is_lost(org):
    rt, w = org.runtime, org.world
    rt.receive_mission("m1", title="x", department_id="engineering", contracts=[_contract(w, "w1", ["a.txt"])])
    rt.run_mission("m1")
    (org.tmp / "journals" / "m1__w1.json").unlink()                          # журнал потерян, store — нет
    org.restart().resume()
    assert w.side_effects() == 1


def test_department_budget_exhaustion_blocks_before_execution_and_asks_owner(org):
    rt, w = org.runtime, org.world
    rt.register_department(Department("engineering", budget=Resources(usd=0.3)))
    rt.receive_mission("m1", title="x", department_id="engineering", contracts=[_contract(w, "w1", ["a.txt"])])
    status = rt.run_mission("m1")
    assert status.state == MissionState.BLOCKED.value and w.side_effects() == 0
    assert "budget exceeded in department:engineering" in status.blockers[0]["reason"]
    assert org.human.requests[-1][0] == "w1"
    # владелец поднял бюджет → тот же контракт проходит
    rt.register_department(Department("engineering", budget=Resources(usd=10)))
    assert rt.run_mission("m1").done and w.side_effects() == 1


def test_tier_escalation_after_local_failure_skips_frontier(org):
    rt, w = org.runtime, org.world
    w.liars = {"coder-local"}
    rt.receive_mission("m1", title="x", department_id="engineering", contracts=[_contract(w, "w1", ["a.txt"], max_attempts=3)])
    status = rt.run_mission("m1")
    assert status.done
    r = rt.store.result("w1")
    assert r.produced_by == "coder-strong"                                   # не frontier
    assert rt.store.work("w1")["attempts"] == 2
    assert rt.learning.stats("coder-local", "fs.write").false_success_attempts == 1
    assert rt.learning.stats("coder-strong", "fs.write").verified_success == 1
    assert w.side_effects() == 1


def test_high_risk_without_lead_is_blocked_not_downgraded(org):
    rt, w = org.runtime, org.world
    rt.receive_mission("m1", title="x", department_id="engineering", contracts=[_contract(w, "w1", ["a.txt"], risk=RiskTier.HIGH)])
    status = rt.run_mission("m1")
    assert status.state == MissionState.BLOCKED.value and w.side_effects() == 0
    assert "unfilled" in status.blockers[0]["reason"]


def test_low_risk_uses_single_executor_no_review_theater(org):
    rt, w = org.runtime, org.world
    rt.receive_mission("m1", title="x", department_id="engineering", contracts=[_contract(w, "w1", ["a.txt"], risk=RiskTier.LOW)])
    assert rt.run_mission("m1").done
    team = rt.store.teams("m1")[0]
    assert set(team["slots"]) == {"executor"}
    assert rt.store.result("w1").reviewed_by == ""


def test_informational_work_completes_without_side_effect_evidence(org):
    rt, w = org.runtime, org.world
    c = DelegationContract(work_id="info", mission_id="m1", department_id="engineering", goal="оценить объём",
                           required_capability="fs.write", success_criteria=["оценка дана"], evidence_required=[],
                           side_effect=False, risk=RiskTier.LOW, steps=[_write_step(w, "s1", "note.txt")])
    rt.receive_mission("m1", title="x", department_id="engineering", contracts=[c])
    status = rt.run_mission("m1")
    assert status.done and status.verified_results == ("info",)


def test_event_reaction_runs_through_the_same_cycle(org):
    rt, w = org.runtime, org.world
    reaction = Reaction("ci.failed", "engineering", "fs.write", "триаж {job}",
                        evidence=(EvidenceRequirement("file", str(w.root / "triage.txt")),), risk=RiskTier.LOW)
    rt = org.restart(reactions=[reaction])
    out = rt.accept_event("ci.failed", {"job": "unit", "idempotency_key": "run-42"})
    assert out.accepted
    # реакция — контракт без шагов: организация не выдумывает действий за инструмент;
    # ORG-02: это BLOCKED/no_executable_steps (не провал исполнителя), владелец решает
    results = rt.run_reactions()
    work = rt.store.work(out.work_id)
    assert work["state"] == TaskState.BLOCKED.value and w.side_effects() == 0
    assert results == [None]
    assert "no_executable_steps" in work["contract"].metadata["runtime"]["last_reason"]
    dup = rt.accept_event("ci.failed", {"job": "unit", "idempotency_key": "run-42"})
    assert dup.duplicate


def test_control_plane_answers_ceo_questions(org):
    rt, w = org.runtime, org.world
    w.ask_for = {"b.txt"}
    rt.receive_mission("m1", title="release", department_id="engineering",
                       contracts=[_contract(w, "w1", ["a.txt"]), _contract(w, "w2", ["b.txt"])], budget=Resources(usd=2))
    rt.run_mission("m1")
    snap = rt.snapshot().to_dict()
    assert [m["mission_id"] for m in snap["active_missions"]] == ["m1"]
    assert snap["active_missions"][0]["department_id"] == "engineering"
    assert [x["work_id"] for x in snap["waiting_approval"]] == ["w2"]
    assert [x["work_id"] for x in snap["verified_completed"]] == ["w1"]
    assert snap["treasury"]["mission:m1"]["limit"]["usd"] == 2
    assert snap["working_agents"] == []                                      # ничего не исполняется прямо сейчас
    assert snap["counts"]["works"] == 2 and snap["counts"]["verified_completed"] == 1
    assert snap["teams"] == []                                               # все временные команды распущены
