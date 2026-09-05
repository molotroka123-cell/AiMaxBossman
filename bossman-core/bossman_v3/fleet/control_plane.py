"""Fleet Control Plane + FleetExecutionBridge — точка склейки с Organization.

Organization (КТО) вызывает `ExecutionBridge.execute(contract, agent_id)`.
`FleetExecutionBridge` реализует этот порт и делает четыре вещи, не касаясь
ни организации, ни V3/V2:

  PLACED    — планировщик выбирает узел (объяснимо; приватность — жёсткий фильтр);
  LEASED    — аренда с TTL и fencing-токеном на узле;
  DISPATCHED/EXECUTING — работа уходит Node Agent'у (локальный V3-мост узла);
  OBSERVED → VERIFYING → VERIFIED/FAILED/BLOCKED — по уликам ЖУРНАЛА V3 и
  контракту делегирования; сам флот ничего не подтверждает.

Потеря узла (исключение транспорта) → NODE_LOST, аренды сняты, узел OFFLINE,
FleetResumeKernel решает, безопасно ли переносить незакрытый шаг. Результат
возвращается организации как «инфраструктурный провал»: попытка исполнителю не
засчитывается, работа остаётся PLANNED и продолжится с того же журнала на другом
узле после `run_mission`/`resume()`.
"""
from __future__ import annotations

from datetime import datetime

import time
from pathlib import Path
from typing import Any

from ..organization.bridges import step_from_dict
from ..organization.contracts import DelegationContract
from ..organization.models import Evidence, WorkResult
from ..memory.journal import TaskJournal
from .artifacts import ArtifactRegistry
from .credentials import CredentialBroker
from .flight import DistributedFlightRecorder
from .journal import FleetEventJournal
from .leases import LeaseConflict, LeaseManager
from .models import (FleetEventType, FlightState, NodeStatus, Placement, PlacementRequirement, RetryPolicy)
from .node_agent import LocalNodeTransport, NodeExecutionRequest, NodeTransport, NodeUnavailable
from .privacy import PrivacyRouter
from .queue import WorkQueue
from .registry import NodeRegistry
from .resume import FleetResumeKernel
from .scheduler import FleetScheduler
from .store import FleetStore
from .twin import FleetDigitalTwin


class FleetLearning:
    """Надёжность узла по способности — только наблюдаемые исходы."""

    def __init__(self, store: FleetStore) -> None:
        self.store = store
        self._stats: dict[tuple[str, str], dict] = {(n, c): p for n, c, p in store.node_stats()}

    def reliability(self, node_id: str, capability: str) -> float:
        s = self._stats.get((node_id, capability), {"attempts": 0, "verified": 0})
        return (1.0 + s["verified"]) / (2.0 + s["attempts"])

    def observe(self, node_id: str, capability: str, *, verified: bool, node_lost: bool = False) -> None:
        s = self._stats.setdefault((node_id, capability), {"attempts": 0, "verified": 0, "node_lost": 0})
        s["attempts"] += 1
        s["verified"] += 1 if verified else 0
        s["node_lost"] = s.get("node_lost", 0) + (1 if node_lost else 0)
        self.store.save_node_stats(node_id, capability, s)


class FleetControlPlane:
    def __init__(self, db_path: str | Path, *, transport: NodeTransport | None = None,
                 heartbeat_timeout_s: float = 90.0, lease_ttl_s: float = 600.0,
                 secret_provider=None) -> None:
        self.store = FleetStore(db_path)
        self.journal = FleetEventJournal(self.store)
        self.leases = LeaseManager(self.store)
        self.registry = NodeRegistry(self.store, self.leases, self.journal, heartbeat_timeout_s=heartbeat_timeout_s)
        self.privacy = PrivacyRouter()
        self.learning = FleetLearning(self.store)
        self.scheduler = FleetScheduler(self.privacy, reliability=self.learning.reliability)
        self.queue = WorkQueue(self.store, self.scheduler, self.journal, RetryPolicy())
        self.flights = DistributedFlightRecorder(self.store, self.journal)
        self.credentials = CredentialBroker(self.store, secret_provider)
        self.artifacts = ArtifactRegistry(self.store)
        self.resume = FleetResumeKernel()
        self.transport: NodeTransport = transport or LocalNodeTransport()
        if isinstance(self.transport, LocalNodeTransport):
            self.transport.leases = self.leases
        self.twin = FleetDigitalTwin(self)
        self.lease_ttl_s = lease_ttl_s
        self.metrics = {"placements": 0, "placement_failures": 0, "lease_conflicts": 0, "node_lost": 0,
                        "dispatches": 0, "verified": 0, "failed": 0, "blocked": 0}

    # ---------------------------------------------------------- placement

    def place(self, contract: DelegationContract, *, now: float | None = None) -> Placement:
        now = time.time() if now is None else now
        req = PlacementRequirement.from_contract(contract)
        if req.artifacts:
            # локальность артефактов: объём переноса считается для КАЖДОГО кандидата
            req = PlacementRequirement(**{**req.__dict__, "artifact_bytes": req.artifact_bytes or
                                          max((self.artifacts.transfer_bytes(list(req.artifacts), n.node_id)
                                               for n in self.registry.nodes()), default=0)})
        best, explanations = self.scheduler.choose(self.registry.nodes(), req)
        if best is None:
            self.metrics["placement_failures"] += 1
            admission = self.scheduler.admission_reason(explanations)
            status = "ADMISSION_REJECTED" if admission else (
                "BLOCKED" if any("private_task" in r or "secrets_must" in r or "not_cleared" in r
                                 for e in explanations for r in e.reasons) else "CAPABILITY_UNAVAILABLE")
            reason = admission or "no_eligible_node:" + ";".join(f"{e.node_id}={','.join(e.reasons)}" for e in explanations)
            self.journal.emit(FleetEventType.TASK_REJECTED, mission_id=contract.mission_id, work_id=contract.work_id,
                              payload={"status": status, "reason": reason[:300]}, ts=now)
            return Placement(contract.work_id, status, None, reason, None, tuple(explanations))
        try:
            lease = self.leases.acquire(node_id=best.node_id, work_id=contract.work_id, now=now,
                                        ttl_seconds=self.lease_ttl_s,
                                        resource_class=str(contract.placement.get("resource_class", "default")),
                                        exclusive=bool(contract.placement.get("exclusive", False)), requirement=req)
        except LeaseConflict as exc:
            self.metrics["lease_conflicts"] += 1
            return Placement(contract.work_id, "BLOCKED", None, f"resource_leased:{exc}", None, tuple(explanations))
        self.metrics["placements"] += 1
        self.journal.emit(FleetEventType.LEASE_ACQUIRED, mission_id=contract.mission_id, work_id=contract.work_id,
                          node_id=best.node_id, payload={"lease_id": lease.lease_id, "fence": lease.fence}, ts=now)
        return Placement(contract.work_id, "PLACED", best.node_id,
                         "selected " + best.node_id + " because: " + ", ".join(best.reasons), lease, tuple(explanations))

    # ---------------------------------------------------------- lifecycle

    def health(self, now: float | None = None):
        report = self.registry.evaluate(time.time() if now is None else now)
        for node_id in report.newly_offline:
            self._node_lost(node_id, reason="heartbeat_timeout")
        return report

    def _node_lost(self, node_id: str, *, reason: str) -> None:
        self.metrics["node_lost"] += 1
        for f in self.store.flights(node_id=node_id, states=("LEASED", "DISPATCHED", "EXECUTING")):
            self.flights.transition(f, FlightState.NODE_LOST, reason=reason)
            for row in self.store.queue():
                if row["work_id"] == f.work_id and row["claimed_by"] == node_id:
                    self.store.release_claim(f.work_id, node_id, row["claim_fence"])

    def snapshot(self, now: float | None = None) -> dict[str, Any]:
        return self.twin.snapshot(time.time() if now is None else now)


class FleetExecutionBridge:
    """Organization.ExecutionBridge поверх флота. Узлы исполняют через СВОЙ
    V3-мост (журнал общий на durable-хранилище), флот лишь размещает,
    арендует, диспетчеризует и записывает полёт."""

    def __init__(self, plane: FleetControlPlane, *, journal_root: str | Path | None = None) -> None:
        self.plane = plane
        self.journal_root = Path(journal_root) if journal_root else None

    def execute(self, contract: DelegationContract, *, agent_id: str) -> WorkResult:
        plane = self.plane
        now = time.time()
        flight = plane.flights.open(contract.work_id, contract.mission_id)
        if flight.state in (FlightState.NODE_LOST, FlightState.FAILED, FlightState.BLOCKED):
            plane.flights.transition(flight, FlightState.QUEUED, reason="re-placement after " + flight.state.value)
        elif flight.state == FlightState.PLANNED:
            plane.flights.transition(flight, FlightState.QUEUED, reason="organization delegated")

        # безопасно ли вообще продолжать после потери узла?
        if self.journal_root is not None and contract.steps:
            plan = [step_from_dict(s) for s in contract.steps]
            jid = f"{contract.mission_id}__{contract.work_id}"
            if (self.journal_root / f"{jid}.json").exists():
                j = TaskJournal.load(task_id=jid, root=self.journal_root)
                lost = any(h["to"] == "NODE_LOST" for h in flight.history)
                decision = plane.resume.decide(j, plan, lost_in_flight=lost)
                if not decision.resumable:
                    plane.flights.transition(flight, FlightState.BLOCKED, reason=decision.reason)
                    plane.metrics["blocked"] += 1
                    return WorkResult(contract.work_id, executed=bool(decision.finished_steps),
                                      evidence=_journal_evidence(j, plan), produced_by=agent_id,
                                      reason=decision.reason, metadata={"waiting_approval": True, "fleet": {
                                          "state": "BLOCKED", "resume": decision.__dict__}})

        placement = plane.place(contract, now=now)
        if not placement.ok:
            plane.flights.transition(flight, FlightState.BLOCKED, reason=placement.reason)
            plane.metrics["blocked"] += 1
            return WorkResult(contract.work_id, executed=False, produced_by=agent_id,
                              reason=f"fleet: {placement.status}: {placement.reason}",
                              metadata={"fleet_blocked": True,
                                        "ask_owner": placement.status in ("BLOCKED", "ADMISSION_REJECTED"),
                                        "fleet": placement.to_dict()})
        node_id, lease = placement.node_id, placement.lease
        plane.flights.transition(flight, FlightState.PLACED, reason=placement.reason, node_id=node_id)
        plane.flights.transition(flight, FlightState.LEASED, lease_id=lease.lease_id, fence=lease.fence)
        node = plane.registry.node(node_id)
        pd = plane.privacy.decide(requested_privacy=contract.privacy, node=node,
                                  contains_secrets=bool(contract.placement.get("contains_secrets", False)))
        req = NodeExecutionRequest(contract.work_id, contract.mission_id, agent_id, lease.lease_id, lease.fence,
                                   contract, context_policy=pd.context_policy)
        plane.registry.adjust_active(node_id, +1)
        plane.flights.transition(flight, FlightState.DISPATCHED, node_id=node_id)
        plane.metrics["dispatches"] += 1
        try:
            plane.flights.transition(flight, FlightState.EXECUTING)
            result = plane.transport.dispatch(node_id, req)
        except (NodeUnavailable, ConnectionError, TimeoutError, OSError) as exc:
            return self._lost(flight, contract, agent_id, node_id, lease, f"node lost: {type(exc).__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001 — падение исполнителя узла = потеря узла для этой работы
            return self._lost(flight, contract, agent_id, node_id, lease, f"node runtime crashed: {type(exc).__name__}: {exc}")
        finally:
            plane.registry.adjust_active(node_id, -1)

        # EH-01 на границе флота: уликам, ПРИСЛАННЫМ узлом, не верим — они
        # перечитываются из durable-журнала V3 (общее хранилище). Узел, вернувший
        # «journal:…» без реальной записи в журнале, улик не получает.
        if self.journal_root is not None and contract.steps:
            plan = [step_from_dict(s) for s in contract.steps]
            jid = f"{contract.mission_id}__{contract.work_id}"
            jpath = self.journal_root / f"{jid}.json"
            derived = _journal_evidence(TaskJournal.load(task_id=jid, root=self.journal_root), plan,
                                        flight=flight, journal=plane.journal) if jpath.exists() else []
            forged = [e for e in result.evidence if e.verified and e.source not in {d.source for d in derived}]
            if forged:
                result.metadata["forged_evidence_rejected"] = [e.source for e in forged]
                plane.journal.emit(FleetEventType.TASK_REJECTED, mission_id=contract.mission_id, work_id=contract.work_id,
                                   node_id=node_id, payload={"reason": "evidence not backed by journal",
                                                             "count": len(forged)})
            result.evidence = derived
            result.executed = result.executed and bool(derived) if contract.side_effect else result.executed

        valid, why = plane.leases.valid(lease, now=time.time())
        plane.leases.release(lease)
        plane.journal.emit(FleetEventType.LEASE_RELEASED, mission_id=contract.mission_id, work_id=contract.work_id,
                           node_id=node_id, payload={"lease_id": lease.lease_id, "valid_at_return": valid, "why": why})
        plane.flights.transition(flight, FlightState.OBSERVED, reason=f"evidence={len(result.evidence)} executed={result.executed}",
                                 evidence_refs=[e.source for e in result.evidence if e.verified])
        if not valid:
            # исполнитель вернулся со stale-арендой: его результат не принимается как подтверждённый
            plane.flights.transition(flight, FlightState.BLOCKED, reason=f"stale lease at return: {why}")
            plane.metrics["blocked"] += 1
            result.metadata["fleet"] = {"node_id": node_id, "state": "BLOCKED", "reason": f"stale lease: {why}"}
            result.reason = f"fleet refused stale-lease result: {why}"
            result.evidence = []
            return result

        plane.flights.transition(flight, FlightState.VERIFYING)
        ok, errors = contract.validate(result)
        capability = contract.required_capability
        if result.metadata.get("waiting_approval"):
            plane.flights.transition(flight, FlightState.BLOCKED, reason="waiting for owner approval")
            plane.metrics["blocked"] += 1
        elif ok:
            refs = [e.source for e in result.evidence if e.verified]
            plane.flights.transition(flight, FlightState.VERIFIED, reason="contract evidence verified by lower layer",
                                     evidence_refs=refs)
            plane.metrics["verified"] += 1
            plane.learning.observe(node_id, capability, verified=True)
            for e in result.evidence:
                if e.verified:
                    plane.flights.record_verified_mutation(flight, step_id=e.source.rsplit("/", 1)[-1], action=None,
                                                          evidence_ref=e.source)
        else:
            plane.flights.transition(flight, FlightState.FAILED, reason="; ".join(errors)[:300])
            plane.metrics["failed"] += 1
            plane.learning.observe(node_id, capability, verified=False)
        result.metadata["fleet"] = {"node_id": node_id, "lease_id": lease.lease_id, "fence": lease.fence,
                                    "state": plane.flights.get(contract.work_id).state.value,
                                    "placement_reason": placement.reason,
                                    "explanations": [e.to_dict() for e in placement.explanations]}
        return result

    def _lost(self, flight, contract, agent_id, node_id, lease, reason: str) -> WorkResult:
        plane = self.plane
        plane.metrics["node_lost"] += 1
        plane.flights.transition(flight, FlightState.NODE_LOST, reason=reason)
        plane.registry.set_status(node_id, NodeStatus.OFFLINE, reason=reason[:100])   # снимает аренды узла
        plane.learning.observe(node_id, contract.required_capability, verified=False, node_lost=True)
        evidence: list[Evidence] = []
        executed = False
        if self.journal_root is not None and contract.steps:
            jid = f"{contract.mission_id}__{contract.work_id}"
            if (self.journal_root / f"{jid}.json").exists():
                j = TaskJournal.load(task_id=jid, root=self.journal_root)
                plan = [step_from_dict(s) for s in contract.steps]
                evidence = _journal_evidence(j, plan)
                executed = bool(j.finished())
        return WorkResult(contract.work_id, executed=executed, evidence=evidence, produced_by=agent_id, reason=reason,
                          metadata={"infrastructure_failure": True, "fleet": {"node_id": node_id, "state": "NODE_LOST",
                                                                              "lease_id": lease.lease_id}})


def _leased_since(flight) -> float | None:
    """Момент, когда текущий fence полёта вступил в силу (последний переход в LEASED)."""
    if flight is None:
        return None
    for h in reversed(flight.history or []):
        if h.get("to") == "LEASED":
            return float(h.get("ts") or 0.0)
    return None


def _stale_fence_receipt(js, flight) -> bool:
    """TRUTH-003 §12: receipt шага записан под fence НИЖЕ текущего ПОСЛЕ того, как
    текущий fence был выдан — это зомби-воркер, вернувшийся после переназначения."""
    if flight is None or not isinstance(js.receipt, dict):
        return False
    token = js.receipt.get("fencing_token")
    if token is None:
        return False
    since = _leased_since(flight)
    if since is None or int(token) >= int(flight.fence):
        return False
    try:
        written = datetime.fromisoformat(str(js.updated_at).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return True                                                  # нечитаемое время — не доверяем
    return written > since


def _journal_evidence(j: TaskJournal, plan, *, flight=None, journal=None) -> list[Evidence]:
    out: list[Evidence] = []
    finished = {s.step_id: s for s in j.finished()}
    for step in plan:
        js = finished.get(step.step_id)
        if js is None:
            continue
        if _stale_fence_receipt(js, flight):
            if journal is not None:
                journal.emit(FleetEventType.TASK_REJECTED, mission_id=flight.mission_id, work_id=flight.work_id,
                             node_id=flight.node_id, payload={"reason": "stale fence receipt", "step_id": step.step_id,
                                                             "receipt_fence": js.receipt.get("fencing_token"),
                                                             "current_fence": flight.fence})
            continue
        from ..organization.bridges import _evidence_from_step
        if js.signature_valid(j.task_id):
            out.append(_evidence_from_step(j.task_id, step, j))
    return out
