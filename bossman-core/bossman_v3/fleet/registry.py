"""Node Registry + Health/Watchdog (§10, §14).

Жизненный цикл: ONLINE → DEGRADED → DRAINING → OFFLINE. Истёкший heartbeat
переводит узел в OFFLINE и снимает его аренды; новых размещений на него нет.
Опасная работа при потере узла НЕ переигрывается автоматически — это решает
FleetResumeKernel по журналу V3 и классу побочного эффекта шага.

Регистрация узла — доверенная операция вызывающего (в этом проходе — только
in-process). Удалённый узел не может зарегистрировать себя сам: см.
node_agent.RemoteTransportUnavailable и REMOTE_TRANSPORT_PRODUCTION_READY=NO.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from .journal import FleetEventJournal
from .leases import LeaseManager
from .models import FleetEventType, Heartbeat, NodeState, NodeStatus
from .store import FleetStore


@dataclass(frozen=True)
class HealthReport:
    newly_offline: tuple[str, ...]
    reclaimed_leases: int
    degraded: tuple[str, ...]


class NodeRegistry:
    def __init__(self, store: FleetStore, leases: LeaseManager, journal: FleetEventJournal | None = None,
                 *, heartbeat_timeout_s: float = 90.0) -> None:
        self.store, self.leases, self.journal = store, leases, journal
        self.heartbeat_timeout_s = heartbeat_timeout_s

    def register(self, node: NodeState, *, now: float | None = None) -> NodeState:
        now = time.time() if now is None else now
        existing = self.store.node(node.node_id)
        node.registered_ts = existing.registered_ts if existing and existing.registered_ts else now
        node.last_heartbeat_ts = node.last_heartbeat_ts or now
        self.store.save_node(node)
        if self.journal:
            self.journal.emit(FleetEventType.NODE_REGISTERED, node_id=node.node_id,
                              payload={"hostname": node.hostname, "os": node.os_name, "ram_gb": node.ram_gb,
                                       "gpu_memory_gb": node.gpu_memory_gb, "trust": node.trust_class,
                                       "pools": sorted(node.pools)}, ts=now)
        return node

    def node(self, node_id: str) -> NodeState | None:
        return self.store.node(node_id)

    def nodes(self, *, status: NodeStatus | None = None) -> list[NodeState]:
        out = self.store.nodes()
        return [n for n in out if status is None or n.status == status]

    def heartbeat(self, hb: Heartbeat) -> NodeState:
        n = self.store.node(hb.node_id)
        if n is None:
            raise KeyError(f"unknown node {hb.node_id!r}: register before heartbeat")
        n.load = max(0.0, min(1.0, hb.load))
        n.ram_used_gb, n.gpu_memory_used_gb = hb.ram_used_gb, hb.gpu_memory_used_gb
        if hb.warm_models is not None:
            n.warm_models = set(hb.warm_models)
        if hb.active_work is not None:
            n.active_work = hb.active_work
        n.last_heartbeat_ts = hb.timestamp
        # DRAINING/OFFLINE выставляет оператор/watchdog; heartbeat не «воскрешает»
        # draining-узел и не снимает drain
        if n.status != NodeStatus.DRAINING:
            n.status = hb.status if hb.status in (NodeStatus.ONLINE, NodeStatus.DEGRADED) else n.status
            if n.status == NodeStatus.OFFLINE:
                n.status = NodeStatus.ONLINE           # узел вернулся
        self.store.save_node(n)
        return n

    def set_status(self, node_id: str, status: NodeStatus, *, reason: str = "") -> NodeState:
        n = self.store.node(node_id)
        if n is None:
            raise KeyError(node_id)
        n.status = status
        self.store.save_node(n)
        ev = {NodeStatus.OFFLINE: FleetEventType.NODE_OFFLINE, NodeStatus.DRAINING: FleetEventType.NODE_DRAINING,
              NodeStatus.DEGRADED: FleetEventType.NODE_DEGRADED}.get(status)
        if status == NodeStatus.OFFLINE:
            gone = self.leases.reclaim_node(node_id)
            if self.journal and gone:
                self.journal.emit(FleetEventType.LEASE_RECLAIMED, node_id=node_id,
                                  payload={"count": len(gone), "reason": reason})
        if self.journal and ev:
            self.journal.emit(ev, node_id=node_id, payload={"reason": reason})
        return n

    def drain(self, node_id: str, *, reason: str = "operator") -> NodeState:
        return self.set_status(node_id, NodeStatus.DRAINING, reason=reason)

    def evaluate(self, now: float) -> HealthReport:
        """Watchdog: heartbeat старше timeout → OFFLINE (+ снятие аренд); в
        ONLINE узел с перегрузом → DEGRADED (новых размещений нет)."""
        offline, degraded, reclaimed = [], [], 0
        for n in self.store.nodes():
            if n.status == NodeStatus.OFFLINE:
                continue
            if now - n.last_heartbeat_ts > self.heartbeat_timeout_s:
                reclaimed += len(self.leases.store.leases(node_id=n.node_id))
                self.set_status(n.node_id, NodeStatus.OFFLINE, reason="heartbeat_timeout")
                offline.append(n.node_id)
            elif n.status == NodeStatus.ONLINE and n.load >= 0.98:
                self.set_status(n.node_id, NodeStatus.DEGRADED, reason="overloaded")
                degraded.append(n.node_id)
        return HealthReport(tuple(offline), reclaimed, tuple(degraded))

    def adjust_active(self, node_id: str, delta: int) -> None:
        n = self.store.node(node_id)
        if n is not None:
            n.active_work = max(0, n.active_work + delta)
            self.store.save_node(n)
