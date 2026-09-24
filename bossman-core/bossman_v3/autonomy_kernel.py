"""Bossman 1.5 autonomy kernel: bind society, graph and resource routing.

This is an orchestration planner, not a permission system. Existing Bossman
approval/privacy/STOP policies remain authoritative.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bossman_v3.operating_graph import PersonalOperatingGraph
from bossman_v3.resource_manager import (
    AutonomousResourceManager, RouteCandidate, RouteDecision, RoutePolicy,
)
from bossman_v3.society import PersistentAgentSociety


@dataclass(frozen=True)
class AutonomyPlan:
    task_id: str
    task_class: str
    team: tuple[str, ...]
    route: RouteDecision
    skill_refs: tuple[str, ...]
    memory_refs: tuple[str, ...]
    graph_context: dict[str, Any]
    owner_input_required: bool = False


class BossmanAutonomyKernel:
    def __init__(self, data_dir: Path):
        root = Path(data_dir)
        self.society = PersistentAgentSociety(root / "society" / "roles.json")
        self.graph = PersonalOperatingGraph(root / "operating-graph" / "graph.json")
        self.resources = AutonomousResourceManager()

    def plan(self, *, task_id: str, task_class: str, route_candidates: list[RouteCandidate],
             route_policy: RoutePolicy, graph_refs: list[tuple[str, str]] | None = None,
             required_roles: tuple[str, ...] = (), max_team: int = 3,
             snapshot=None, owner_input_required: bool = False) -> AutonomyPlan:
        team = self.society.select_team(task_class, max_members=max_team, required=required_roles)
        skill_refs, memory_refs = [], []
        for role_name in team:
            role = self.society.roles[role_name]
            skill_refs.extend(role.skill_refs)
            memory_refs.extend(role.memory_refs)
        route = self.resources.choose(route_candidates, route_policy, snapshot=snapshot)
        context = self.graph.context(graph_refs or [])
        return AutonomyPlan(
            task_id=task_id,
            task_class=task_class,
            team=tuple(team),
            route=route,
            skill_refs=tuple(dict.fromkeys(skill_refs)),
            memory_refs=tuple(dict.fromkeys(memory_refs)),
            graph_context=context,
            owner_input_required=bool(owner_input_required),
        )

    def record(self, plan: AutonomyPlan, *, verified_success: bool,
               verifier_rejected: bool = False, latency_s: float = 0.0,
               cost_usd: float = 0.0, branch: str | None = None,
               benchmark_ref: str | None = None, skill_ref: str | None = None,
               source_ref: str = "") -> None:
        for role in plan.team:
            self.society.record_outcome(
                role, plan.task_class, verified_success=verified_success,
                verifier_rejected=verifier_rejected, latency_s=latency_s,
                cost_usd=cost_usd,
            )
        primary = plan.team[0] if plan.team else "unknown"
        self.graph.record_task_result(
            task_id=plan.task_id, role=primary, branch=branch,
            benchmark_ref=benchmark_ref, skill_ref=skill_ref,
            verified_success=verified_success, source_ref=source_ref,
        )
