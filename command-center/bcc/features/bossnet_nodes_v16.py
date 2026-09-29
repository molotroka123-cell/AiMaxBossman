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

    def __post_init__(self):
        if not self.node_id or self.ram_gb < 0 or self.gpu_memory_gb < 0 or self.marginal_cost_usd_h < 0:
            raise ValueError("invalid node resources")
        if not 0 <= self.load <= 1 or self.last_heartbeat < 0:
            raise ValueError("invalid node telemetry")

    def healthy(self, now: float | None = None, ttl: float = 30) -> bool:
        now = time() if now is None else now
        return self.owner_bound and 0 <= now - self.last_heartbeat <= ttl and 0 <= self.load <= 1


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
