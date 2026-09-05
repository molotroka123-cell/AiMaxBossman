"""Fleet OS — обязательные E2E (§30–§33) на реальных V3-компонентах.

Два логических узла в одном процессе, каждый со СВОИМ V3-мостом
(UniversalComputerAgent → CompoundRunner → TaskJournal). Общее durable-хранилище
(journal_root, org.sqlite, fleet.sqlite) — как на одной машине/NAS. Побочные
эффекты — реальные файлы в песочнице; их количество и есть
DUPLICATE_SIDE_EFFECT_COUNT.

  E2E #1  Organization → Fleet → узел → реальный эффект → верификация → VERIFIED;
          одно размещение без исполнения НЕ проходит.
  E2E #2  узел 1 умирает после подтверждённого шага A; рестарт control plane;
          узел 2 продолжает с B; A исполнен ровно один раз.
  E2E #3  PRIVATE-работа никогда не уходит на CLOUD; без локальной способности —
          BLOCKED, не облачный fallback.
  E2E #4  двойной claim — один победитель.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from bossman_v3.computer_agent.agent import UniversalComputerAgent
from bossman_v3.contracts import (ApprovalDecision, ExecutionReceipt, Observation, PolicyDecision, SideEffectClass,
                                  TypedAction, VerificationResult)
from bossman_v3.execution import PlanStep
from bossman_v3.fleet import (CLOUD, FleetControlPlane, FleetExecutionBridge, FlightState, LocalNodeTransport,
                              NodeState, NodeStatus)
from bossman_v3.organization import (EXECUTOR, REVIEWER, AgentProfile, DelegationContract, Department,
                                     EscalationPolicy, EvidenceRequirement, MissionState, OrganizationRuntime,
                                     OrganizationStore, RecordingHumanReview, Resources, RiskTier, TaskState,
                                     V3ExecutionBridge, step_to_dict)


# ------------------------------------------------------------------ world

class World:
    def __init__(self, root: Path):
        self.root = root
        self.writes: list[tuple[str, str]] = []          # (node, file)
        self.crash_on: dict[str, set[str]] = {}          # node → файлы, на которых узел «умирает»
        self.ask_for: set[str] = set()
        self.approved: set[str] = set()

    def side_effects(self) -> int:
        return len(self.writes)


class _Policy:
    def __init__(self, w): self.w = w
    def authorize(self, action, context):
        return PolicyDecision(True, requires_approval=str(action.args["name"]) in self.w.ask_for)


class _Approval:
    def __init__(self, w): self.w = w
    def request(self, action, policy, context):
        name = str(action.args["name"])
        if name in self.w.approved:
            return ApprovalDecision(True, approval_id=f"ap-{name}")
        return ApprovalDecision(False, reason=f"создан запрос на подтверждение: {name}")


class _Executor:
    def __init__(self, w, node_id): self.w, self.node_id = w, node_id
    def supports(self, action_type): return action_type == "fs.write"
    def execute(self, action):
        name = str(action.args["name"])
        if name in self.w.crash_on.get(self.node_id, set()):
            raise ConnectionError(f"{self.node_id} lost power")
        (self.w.root / name).write_text(str(action.args.get("content", "1")), encoding="utf-8")
        self.w.writes.append((self.node_id, name))
        now = datetime.now(timezone.utc)
        return ExecutionReceipt("fs.write", now, now, effect_id=f"{self.node_id}:{name}")


class _Observer:
    def __init__(self, w): self.w = w
    def observe_fresh(self, action, receipt):
        return Observation(receipt.completed_at + timedelta(milliseconds=1), "fs",
                           {"exists": (self.w.root / str(action.args["name"])).exists()})


class _Verifier:
    def verify(self, action, receipt, observation):
        ok = bool(observation.state.get("exists"))
        return VerificationResult(ok, "" if ok else "файл не появился")


class _NodeRuntime:
    """V3-мост узла. Если исполнитель узла «потерял питание», узел не отвечает
    транспорту вовсе (ConnectionError) — журнал при этом уже содержит всё, что
    успело подтвердиться до смерти."""

    def __init__(self, w: World, node_id: str, journal_root: Path):
        self.w, self.node_id = w, node_id
        self.inner = V3ExecutionBridge(agent_factory=lambda agent_id, c: UniversalComputerAgent(
            _Policy(w), _Approval(w), _Executor(w, node_id), _Observer(w), _Verifier()), journal_root=journal_root)

    def execute(self, contract, *, agent_id):
        result = self.inner.execute(contract, agent_id=agent_id)
        if "lost power" in result.reason:
            raise ConnectionError(f"{self.node_id} stopped responding")
        return result


def _node_bridge(w: World, node_id: str, journal_root: Path):
    return _NodeRuntime(w, node_id, journal_root)


def _step(w: World, sid: str, name: str, cls=SideEffectClass.IDEMPOTENT_WRITE) -> dict:
    return step_to_dict(PlanStep(sid, f"write {name}", TypedAction("fs.write", {"name": name, "expect": {
        "kind": "file", "target": str(w.root / name), "expect": {"exists": True}}}, side_effect=cls)))


def _contract(w: World, work_id: str, names: list[str], *, privacy="private", placement=None, deps=(), risk=RiskTier.LOW,
              mission="m1", max_attempts=2) -> DelegationContract:
    return DelegationContract(work_id=work_id, mission_id=mission, department_id="engineering", goal=f"write {names}",
                              required_capability="fs.write", success_criteria=["files exist"],
                              evidence_required=[EvidenceRequirement("file", str(w.root / n)) for n in names],
                              budget=Resources(usd=0.5, gpu_memory_gb=0), risk=risk, dependencies=list(deps),
                              escalation=EscalationPolicy(max_attempts=max_attempts, on_failure="escalate_tier"),
                              steps=[_step(w, f"{work_id}-s{i}", n) for i, n in enumerate(names, 1)],
                              privacy=privacy, placement=dict(placement or {}))


def _node(nid, **kw):
    base = dict(hostname=nid, os_name="Linux", ram_gb=128, gpu_memory_gb=96, capabilities={"fs.write"},
                privacy_level="private", trust_class="trusted_local", last_heartbeat_ts=1000.0)
    base.update(kw)
    return NodeState(nid, **base)


class Stack:
    """Organization + Fleet + два узла над общим durable-хранилищем."""

    def __init__(self, tmp: Path):
        self.tmp, self.world = tmp, World(tmp / "world")
        self.world.root.mkdir()
        self.human = RecordingHumanReview()
        self.transport = LocalNodeTransport()
        self.boot(register=("node-1", "node-2"))

    def boot(self, *, register=()):
        self.plane = FleetControlPlane(self.tmp / "fleet.sqlite", transport=self.transport, heartbeat_timeout_s=60)
        for nid in register:
            self.plane.registry.register(_node(nid), now=1000.0)
            self.transport.attach(nid, _node_bridge(self.world, nid, self.tmp / "journals"))
        bridge = FleetExecutionBridge(self.plane, journal_root=self.tmp / "journals")
        self.org = OrganizationRuntime(store=OrganizationStore(self.tmp / "org.sqlite"), execution=bridge,
                                       human_review=self.human)
        if not self.org.departments():
            self.org.register_department(Department("engineering", capabilities={"fs.write"}, budget=Resources(usd=10)))
            self.org.register_agent(AgentProfile("coder", "engineering", {EXECUTOR}, {"fs.write"}, tier="local_small", model="glm"))
            self.org.register_agent(AgentProfile("rev", "engineering", {REVIEWER}, {"fs.write"}, tier="local_small", model="qwen"))
        return self

    def kill_node(self, nid: str):
        self.transport.detach(nid)


@pytest.fixture
def stack(tmp_path):
    return Stack(tmp_path)


# ------------------------------------------------------------------ E2E #1

def test_e2e_org_to_fleet_to_real_side_effect_to_verified(stack):
    s, w = stack, stack.world
    s.org.receive_mission("m1", title="fleet e2e", department_id="engineering",
                          contracts=[_contract(w, "w1", ["a.txt"]), _contract(w, "w2", ["b.txt"], deps=["w1"], risk=RiskTier.MEDIUM)])
    status = s.org.run_mission("m1")

    assert status.done and status.verified_results == ("w1", "w2")
    assert w.side_effects() == 2 and {n for n, _ in w.writes} <= {"node-1", "node-2"}
    f1 = s.plane.flights.get("w1")
    assert f1.state == FlightState.VERIFIED and [h["to"] for h in f1.history] == [
        "QUEUED", "PLACED", "LEASED", "DISPATCHED", "EXECUTING", "OBSERVED", "VERIFYING", "VERIFIED"]
    assert f1.evidence_refs and all(r.startswith("journal:") for r in f1.evidence_refs)
    assert len(s.plane.store.verified_mutations()) == 2 and s.plane.flights.duplicate_preventions == 0
    r2 = s.org.store.result("w2")
    assert r2.reviewed_by == "rev" and r2.metadata["fleet"]["state"] == "VERIFIED"
    assert "selected node-" in r2.metadata["fleet"]["placement_reason"]
    assert s.plane.store.leases() == []                                           # аренды освобождены
    kinds = {e["type"] for e in s.plane.journal.events()}
    assert {"TASK_PLACED", "LEASE_ACQUIRED", "TASK_DISPATCHED", "TASK_VERIFIED", "LEASE_RELEASED"} <= kinds


def test_placement_alone_never_completes_work(stack):
    """Размещение состоялось, исполнения не было: узел «принял» и ничего не сделал."""
    s, w = stack, stack.world

    class Idle:
        def execute(self, contract, *, agent_id):
            from bossman_v3.organization import WorkResult
            return WorkResult(contract.work_id, executed=False, produced_by=agent_id, claims={"done": True},
                              reason="node accepted the task")
    s.transport.attach("node-1", Idle()); s.transport.attach("node-2", Idle())
    s.org.receive_mission("m1", title="x", department_id="engineering", contracts=[_contract(w, "w1", ["a.txt"], max_attempts=1)])
    status = s.org.run_mission("m1")
    assert not status.done and status.verified_results == () and w.side_effects() == 0
    f = s.plane.flights.get("w1")
    assert f.state == FlightState.FAILED and "PLACED" in [h["to"] for h in f.history]
    assert status.quality["false_success_attempts"] >= 0                          # текст узла ≠ исполнение
    assert s.org.store.result("w1").success is False


# ------------------------------------------------------------------ E2E #2

def test_e2e_node_failure_restart_resumes_on_node_2_without_duplicate(stack):
    s, w = stack, stack.world
    # узел 2 пока не существует; узел 1 умирает на шаге B
    s.kill_node("node-2"); s.plane.registry.set_status("node-2", NodeStatus.OFFLINE, reason="not yet provisioned")
    w.crash_on["node-1"] = {"b.txt"}
    s.org.receive_mission("m1", title="long", department_id="engineering", contracts=[_contract(w, "w1", ["a.txt", "b.txt", "c.txt"])])
    first = s.org.run_mission("m1")

    assert not first.done
    assert w.writes == [("node-1", "a.txt")]                                       # A подтверждён, B не состоялся
    f = s.plane.flights.get("w1")
    assert "NODE_LOST" in [h["to"] for h in f.history]
    assert s.plane.registry.node("node-1").status == NodeStatus.OFFLINE
    assert s.plane.store.leases(node_id="node-1") == []
    work = s.org.store.work("w1")
    assert work["attempts"] == 0                                                   # инфра-провал — не попытка исполнителя
    assert first.state in (MissionState.BLOCKED.value, MissionState.ACTIVE.value)

    # --- рестарт control plane и организации; узел 2 появляется ---
    s.boot(register=("node-2",))
    assert s.plane.store.flight("w1").state in (FlightState.NODE_LOST, FlightState.BLOCKED, FlightState.QUEUED)
    statuses = s.org.resume()

    assert statuses[-1].done and statuses[-1].verified_results == ("w1",)
    assert w.writes == [("node-1", "a.txt"), ("node-2", "b.txt"), ("node-2", "c.txt")]   # DUPLICATE_SIDE_EFFECT_COUNT=0
    assert sum(1 for _, n in w.writes if n == "a.txt") == 1
    j = json.loads((s.tmp / "journals" / "m1__w1.json").read_text())
    assert [x["status"] for x in j["steps"]] == ["DONE", "DONE", "DONE"]
    assert s.plane.flights.duplicate_preventions == 0
    assert len(s.plane.store.verified_mutations()) == 3
    f = s.plane.flights.get("w1")
    assert f.state == FlightState.VERIFIED and f.node_id == "node-2"
    # повторный resume/рестарт — ноль новых эффектов
    s.boot(register=("node-2",)).org.resume()
    assert w.side_effects() == 3


def test_node_loss_mid_irreversible_step_blocks_instead_of_replaying(stack):
    s, w = stack, stack.world
    s.kill_node("node-2"); s.plane.registry.set_status("node-2", NodeStatus.OFFLINE)
    c = _contract(w, "w1", ["a.txt", "b.txt"])
    c.steps[1] = _step(w, "w1-s2", "b.txt", SideEffectClass.IRREVERSIBLE)
    w.crash_on["node-1"] = {"b.txt"}
    s.org.receive_mission("m1", title="x", department_id="engineering", contracts=[c])
    s.org.run_mission("m1")
    assert w.writes == [("node-1", "a.txt")]
    s.boot(register=("node-2",))
    status = s.org.resume()[-1]
    assert not status.done and status.waiting_approval == ("w1",)               # владелец решает, не флот
    assert w.side_effects() == 1
    assert s.plane.flights.get("w1").state == FlightState.BLOCKED
    assert s.human.requests and "owner decision" in s.human.requests[-1][1]


# ------------------------------------------------------------------ E2E #3

def test_e2e_private_work_never_reaches_cloud(stack):
    s, w = stack, stack.world
    calls: list[str] = []

    class CloudRuntime:
        def execute(self, contract, *, agent_id):
            calls.append(contract.work_id)
            raise AssertionError("cloud must never receive private work")
    # локальные узлы без нужной способности; облако — с ней
    for nid in ("node-1", "node-2"):
        n = s.plane.registry.node(nid); n.capabilities = {"other"}; s.plane.store.save_node(n)
    s.plane.registry.register(_node("cloud-01", trust_class="cloud", privacy_level="public", capabilities={"fs.write"}), now=1000.0)
    s.transport.attach("cloud-01", CloudRuntime())
    s.org.receive_mission("m1", title="private", department_id="engineering", contracts=[_contract(w, "w1", ["secret.txt"])])
    status = s.org.run_mission("m1")

    assert not status.done and calls == [] and w.side_effects() == 0
    assert status.state == MissionState.BLOCKED.value
    reason = status.blockers[0]["reason"]
    assert "cloud-01=private_task_requires_trusted_local_node" in reason
    assert s.plane.flights.get("w1").state == FlightState.BLOCKED
    # ни одно событие ЗАДАЧИ в журнале флота не привязано к облачному узлу (регистрация узла — не контекст задачи)
    assert all(e["node_id"] != "cloud-01" for e in s.plane.journal.events() if e["work_id"])
    # публичная работа на облако может уйти — с минимизированным контекстом
    seen = {}

    class CloudOk:
        def execute(self, contract, *, agent_id):
            seen["inputs"] = dict(contract.inputs); seen["meta"] = dict(contract.metadata)
            from bossman_v3.organization import WorkResult
            return WorkResult(contract.work_id, executed=False, produced_by=agent_id, reason="no steps ran")
    s.transport.attach("cloud-01", CloudOk())
    pub = _contract(w, "w2", ["pub.txt"], privacy="public", mission="m2")
    pub.inputs["customer_list"] = ["a", "b"]
    s.org.receive_mission("m2", title="public", department_id="engineering", contracts=[pub])
    s.org.run_mission("m2")
    assert seen["inputs"] == {} and set(seen["meta"]) <= {"fleet_dispatch"}          # MINIMIZED context (fence/lease — конверт диспетчеризации, не контекст владельца)


# ------------------------------------------------------------------ E2E #4

def test_e2e_double_claim_two_nodes_one_winner(tmp_path):
    import threading
    from bossman_v3.fleet import FleetScheduler, FleetStore, PlacementRequirement, WorkQueue
    store = FleetStore(tmp_path / "fleet.sqlite")
    q = WorkQueue(store, FleetScheduler())
    for i in range(5):
        q.enqueue(f"w{i}", "m1", priority=i, requirement=PlacementRequirement(capabilities=("fs.write",)))
    nodes = [_node("node-1"), _node("node-2"), _node("node-3")]
    claimed: list[tuple[str, str]] = []
    lock, barrier = threading.Lock(), threading.Barrier(3)

    def worker(node):
        barrier.wait()
        while True:
            c = q.claim(node, now=1.0)
            if c is None:
                return
            with lock:
                claimed.append((c.work_id, node.node_id))

    ts = [threading.Thread(target=worker, args=(n,)) for n in nodes]
    [t.start() for t in ts]; [t.join() for t in ts]
    work_ids = [w for w, _ in claimed]
    assert sorted(work_ids) == [f"w{i}" for i in range(5)] and len(set(work_ids)) == 5   # каждая работа — ровно один владелец
    assert all(r["claimed_by"] for r in store.queue())


# ------------------------------------------------------------ EH-01 boundary

def test_node_returned_forged_journal_evidence_is_rejected_by_fleet(stack):
    """Узел присылает улику с source='journal:…', которой нет в журнале.
    Флот перечитывает журнал сам: подделка отброшена, работа не VERIFIED."""
    s, w = stack, stack.world
    from bossman_v3.organization import Evidence, WorkResult

    class Liar:
        def execute(self, contract, *, agent_id):
            return WorkResult(contract.work_id, executed=True, produced_by=agent_id, claims={"done": True},
                              evidence=[Evidence("file", str(w.root / "a.txt"), True,
                                                 source=f"journal:{contract.mission_id}__{contract.work_id}/w1-s1")])
    s.transport.attach("node-1", Liar()); s.transport.attach("node-2", Liar())
    s.org.receive_mission("m1", title="x", department_id="engineering", contracts=[_contract(w, "w1", ["a.txt"], max_attempts=1)])
    status = s.org.run_mission("m1")
    assert not status.done and status.verified_results == () and w.side_effects() == 0
    r = s.org.store.result("w1")
    assert r.evidence == [] and r.metadata.get("forged_evidence_rejected")
    assert s.plane.flights.get("w1").state == FlightState.FAILED
    assert any(e["type"] == "TASK_REJECTED" and e["payload"].get("reason") == "evidence not backed by journal"
               for e in s.plane.journal.events())


def test_deadline_missed_blocks_before_placement(stack):
    s, w = stack, stack.world
    c = _contract(w, "w1", ["a.txt"])
    c.deadline = "2000-01-01T00:00:00+00:00"
    s.org.receive_mission("m1", title="late", department_id="engineering", contracts=[c])
    status = s.org.run_mission("m1")
    assert status.state == MissionState.BLOCKED.value and "deadline_missed" in status.blockers[0]["reason"]
    assert w.side_effects() == 0 and s.plane.flights.get("w1") is None            # до флота не дошло
