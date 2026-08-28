from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

PolicyName = Literal["performance", "balanced", "maximum_local", "low_power"]

@dataclass(slots=True)
class Reservation:
    owner: str
    memory_mb: int
    gpu_memory_mb: int = 0
    kind: str = "model"
    idle: bool = False

@dataclass(slots=True)
class ResourceSnapshot:
    total_memory_mb: int
    used_system_mb: int
    reserve_floor_mb: int = 16_000
    reservations: list[Reservation] = field(default_factory=list)

    @property
    def reserved_mb(self) -> int:
        return sum(max(0, r.memory_mb) for r in self.reservations)

    @property
    def available_for_new_mb(self) -> int:
        return max(0, self.total_memory_mb - self.used_system_mb - self.reserve_floor_mb - self.reserved_mb)

@dataclass(slots=True)
class ResourcePlan:
    action: str
    allowed: bool
    unload: list[str] = field(default_factory=list)
    explanation: list[str] = field(default_factory=list)

def plan_memory(snapshot: ResourceSnapshot, request_mb: int, *,
                policy: PolicyName = "balanced") -> ResourcePlan:
    if request_mb <= snapshot.available_for_new_mb:
        return ResourcePlan("start", True, explanation=[
            f"need {request_mb}MB; free budget {snapshot.available_for_new_mb}MB"
        ])

    idle = sorted((r for r in snapshot.reservations if r.idle),
                  key=lambda r: r.memory_mb, reverse=True)
    freed = 0
    unload: list[str] = []
    for r in idle:
        freed += r.memory_mb
        unload.append(r.owner)
        if request_mb <= snapshot.available_for_new_mb + freed:
            break

    if request_mb <= snapshot.available_for_new_mb + freed and policy in (
        "performance", "balanced", "maximum_local"
    ):
        return ResourcePlan("unload_idle_then_start", True, unload=unload, explanation=[
            f"need {request_mb}MB",
            f"unload idle: {', '.join(unload)}",
            f"would free {freed}MB",
        ])

    if policy == "low_power":
        return ResourcePlan("queue", False, explanation=["low-power policy avoids heavy replacement"])
    return ResourcePlan("queue_or_ask", False, explanation=[
        f"need {request_mb}MB; insufficient safe budget",
        f"system floor preserved: {snapshot.reserve_floor_mb}MB",
    ])
