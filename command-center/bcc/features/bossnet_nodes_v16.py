"""Bossnet distributed-node contracts. Transport is intentionally separate."""
from __future__ import annotations
from dataclasses import dataclass, field
from time import time


@dataclass(frozen=True)
class Node:
    node_id: str
    capabilities: frozenset[str]
    ram_gb: float
    gpu_memory_gb: float = 0
    marginal_cost_usd_h: float = 0
    privacy_class: str = "OWNER_LOCAL"
    load: float = 0
    last_heartbeat: float = 0
    owner_bound: bool = False
    revoked: bool = False

    def __post_init__(self):
        if not self.node_id or self.ram_gb < 0 or self.gpu_memory_gb < 0 or self.marginal_cost_usd_h < 0:
            raise ValueError("invalid node resources")
        if not 0 <= self.load <= 1 or self.last_heartbeat < 0:
            raise ValueError("invalid node telemetry")

    def healthy(self, now: float | None = None, ttl: float = 30) -> bool:
        now = time() if now is None else now
        return (not self.revoked and self.owner_bound
                and 0 <= now - self.last_heartbeat <= ttl and 0 <= self.load <= 1)


@dataclass(frozen=True)
class TaskNeed:
    capabilities: frozenset[str]
    min_ram_gb: float = 0
    min_gpu_memory_gb: float = 0
    max_cost_usd_h: float | None = None

    def __post_init__(self):
        if self.min_ram_gb < 0 or self.min_gpu_memory_gb < 0:
            raise ValueError("negative task resource requirement")
        if self.max_cost_usd_h is not None and self.max_cost_usd_h < 0:
            raise ValueError("negative task cost cap")


def eligible(node: Node, need: TaskNeed, *, now: float | None = None) -> bool:
    return (node.healthy(now) and need.capabilities <= node.capabilities
            and node.ram_gb >= need.min_ram_gb
            and node.gpu_memory_gb >= need.min_gpu_memory_gb
            and (need.max_cost_usd_h is None or node.marginal_cost_usd_h <= need.max_cost_usd_h))


def choose(nodes: list[Node], need: TaskNeed, *, now: float | None = None) -> Node | None:
    xs=[n for n in nodes if eligible(n,need,now=now)]
    return min(xs,key=lambda n:(n.marginal_cost_usd_h,n.load,-n.gpu_memory_gb,n.node_id)) if xs else None


def revoke_node(node: Node, *, in_flight_tasks: list[str] | tuple[str, ...] = (),
                reason: str = "owner_revoked", now: float | None = None) -> dict:
    """B15 D2/D3: immediately revoke a node and mark any in-flight tasks RECONCILE_REQUIRED (never auto-rerun side effects)."""
    from dataclasses import replace
    ts = time() if now is None else now
    revoked_node = replace(node, owner_bound=False, revoked=True)
    reconcile = [
        {"task_id": str(tid), "node_id": node.node_id, "status": "RECONCILE_REQUIRED",
         "duplicate_side_effects_blocked": True, "at": ts}
        for tid in in_flight_tasks if str(tid).strip()
    ]
    return {
        "node": revoked_node,
        "node_id": node.node_id,
        "revoked": True,
        "reason": reason,
        "revoked_at": ts,
        "in_flight_reconciliation": reconcile,
    }


def reconcile_disconnected_node(node: Node, *, in_flight_tasks: list[str] | tuple[str, ...] = (),
                                now: float | None = None, ttl: float = 30) -> dict:
    """B15 D3: if a node heartbeat expired mid-task, mark in-flight tasks RECONCILE_REQUIRED without duplicate execution."""
    ts = time() if now is None else now
    is_healthy = node.healthy(now=ts, ttl=ttl)
    if is_healthy:
        return {"node": node, "node_id": node.node_id, "disconnected": False, "in_flight_reconciliation": []}
    return {
        "node": node,
        "node_id": node.node_id,
        "disconnected": True,
        "detected_at": ts,
        "in_flight_reconciliation": [
            {"task_id": str(tid), "node_id": node.node_id, "status": "RECONCILE_REQUIRED",
             "duplicate_side_effects_blocked": True, "at": ts}
            for tid in in_flight_tasks if str(tid).strip()
        ],
    }
