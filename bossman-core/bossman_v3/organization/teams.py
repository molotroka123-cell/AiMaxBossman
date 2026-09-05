"""Dynamic Organization Graph + Adaptive Team Formation (§8, §10, §13).

Команда — временная, под миссию: набор слотов (роль → agent_id) с рёбрами
ownership / delegation / review. Размер команды пропорционален риску:

    LOW    → executor
    MEDIUM → executor + независимый reviewer
    HIGH   → lead + executor + независимый reviewer + risk (если отдел требует)

Больше агентов не значит лучше (§10): шаблон минимальный, а расширяется только
политикой отдела (`require_reviewer`, `require_risk_review`). После миссии
команда распускается (`dissolved=True`), а улики миссии остаются в store.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .contracts import DelegationContract
from .marketplace import CapabilityMarketplace
from .models import (EXECUTOR, LEAD, REVIEWER, RISK, Department, RiskTier)


@dataclass
class MissionTeam:
    team_id: str
    mission_id: str
    department_id: str
    slots: dict[str, str] = field(default_factory=dict)        # роль → agent_id
    edges: list[dict[str, str]] = field(default_factory=list)  # {"from","to","kind"}
    risk: RiskTier = RiskTier.LOW
    dissolved: bool = False
    unfilled: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.unfilled

    @property
    def members(self) -> list[str]:
        return sorted(set(self.slots.values()))

    def link(self, src: str, dst: str, kind: str) -> None:
        self.edges.append({"from": src, "to": dst, "kind": kind})

    def to_dict(self) -> dict[str, Any]:
        return {"team_id": self.team_id, "mission_id": self.mission_id, "department_id": self.department_id,
                "slots": dict(self.slots), "edges": list(self.edges), "risk": self.risk.value,
                "dissolved": self.dissolved, "unfilled": list(self.unfilled)}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "MissionTeam":
        return cls(team_id=str(raw["team_id"]), mission_id=str(raw["mission_id"]),
                   department_id=str(raw["department_id"]), slots=dict(raw.get("slots") or {}),
                   edges=list(raw.get("edges") or []), risk=RiskTier(raw.get("risk", "low")),
                   dissolved=bool(raw.get("dissolved", False)), unfilled=list(raw.get("unfilled") or []))


def required_roles(risk: RiskTier, department: Department) -> list[str]:
    roles = [EXECUTOR]
    if risk != RiskTier.LOW or department.require_reviewer:
        roles.append(REVIEWER)
    if risk == RiskTier.HIGH:
        roles.insert(0, LEAD)
        if department.require_risk_review:
            roles.append(RISK)
    return roles


class AdaptiveTeamFormer:
    def __init__(self, marketplace: CapabilityMarketplace) -> None:
        self.marketplace = marketplace

    def form(self, *, team_id: str, mission_id: str, department: Department,
             contract: DelegationContract, min_tier: str = "deterministic",
             exclude: set[str] | None = None) -> MissionTeam:
        team = MissionTeam(team_id=team_id, mission_id=mission_id,
                           department_id=department.department_id, risk=contract.risk)
        excl = set(exclude or ())
        producer: str | None = None
        for role in required_roles(contract.risk, department):
            if role in (REVIEWER, RISK):
                if producer is None:
                    team.unfilled.append(role)
                    continue
                decision = self.marketplace.route_reviewer(contract, producer_id=producer, role=role)
            else:
                decision = self.marketplace.route(contract, role=role,
                                                  min_tier=min_tier if role == EXECUTOR else "deterministic",
                                                  exclude=excl)
            if not decision.ok:
                team.unfilled.append(role)
                continue
            agent_id = decision.selected[0]
            team.slots[role] = agent_id
            if role == EXECUTOR:
                producer = agent_id
        # рёбра графа: lead владеет и делегирует; reviewer/risk ревьюят производителя
        lead, exe = team.slots.get(LEAD), team.slots.get(EXECUTOR)
        if lead and exe:
            team.link(lead, exe, "delegation")
        for role in (REVIEWER, RISK):
            rid = team.slots.get(role)
            if rid and exe:
                team.link(rid, exe, "review")
        owner = lead or exe
        if owner:
            team.link(owner, contract.work_id, "ownership")
        return team
