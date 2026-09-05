"""Fleet Digital Twin (§22) — одно машиночитаемое состояние флота из durable
store. Ни одного вычисленного «для красоты» поля: всё читается из таблиц.

  какие узлы есть / кто online / железо / модели / что загружено / кто занят /
  что арендовано / какая миссия где / что заблокировано / что можно перенести /
  сколько ресурсов осталось
"""
from __future__ import annotations

from typing import Any

from .models import FlightState, NodeStatus


class FleetDigitalTwin:
    def __init__(self, plane) -> None:
        self.plane = plane

    def snapshot(self, now: float) -> dict[str, Any]:
        p = self.plane
        nodes = p.registry.nodes()
        leases = p.store.leases()
        flights = p.store.flights()
        live = [l for l in leases if l.alive(now)]
        by_node_flights = {}
        for f in flights:
            by_node_flights.setdefault(f.node_id, []).append(f)
        migratable = [f.work_id for f in flights if f.state in (FlightState.NODE_LOST, FlightState.QUEUED)]
        return {
            "timestamp": now,
            "nodes": [{**n.to_dict(), "heartbeat_age_s": round(now - n.last_heartbeat_ts, 1) if n.last_heartbeat_ts else None,
                       "remaining": {"ram_gb": round(n.ram_free_gb, 1), "gpu_memory_gb": round(n.gpu_free_gb, 1),
                                     "concurrency": max(0, n.max_concurrency - n.active_work)},
                       "busy_with": [f.work_id for f in by_node_flights.get(n.node_id, [])
                                     if f.state in (FlightState.DISPATCHED, FlightState.EXECUTING)],
                       "leases": [l.to_dict() for l in live if l.node_id == n.node_id]} for n in nodes],
            "online_nodes": [n.node_id for n in nodes if n.status == NodeStatus.ONLINE],
            "offline_nodes": [n.node_id for n in nodes if n.status == NodeStatus.OFFLINE],
            "draining_nodes": [n.node_id for n in nodes if n.status == NodeStatus.DRAINING],
            "warm_models": {n.node_id: sorted(n.warm_models) for n in nodes},
            "active_leases": [l.to_dict() for l in live],
            "missions_by_node": {n.node_id: sorted({f.mission_id for f in by_node_flights.get(n.node_id, [])
                                                    if f.state not in (FlightState.VERIFIED, FlightState.FAILED, FlightState.CANCELLED)})
                                 for n in nodes},
            "flights": {f.work_id: {"mission_id": f.mission_id, "state": f.state.value, "node_id": f.node_id,
                                    "attempt": f.attempt, "verified_steps": list(f.verified_steps)} for f in flights},
            "blocked": [f.work_id for f in flights if f.state == FlightState.BLOCKED],
            "migratable": migratable,
            "queue": p.store.queue(),
            "dead_letters": [d["work_id"] for d in p.store.dead_letters()],
            "verified_mutations": len(p.store.verified_mutations()),
            "duplicate_preventions": p.flights.duplicate_preventions,
            "metrics": dict(p.metrics),
            "remote_transport_production_ready": False,
        }
