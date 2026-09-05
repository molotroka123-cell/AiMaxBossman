"""CEO Control Plane (§17) — одно машиночитаемое состояние организации.

Не телеметрия ради красоты: каждое поле — прямой ответ на вопрос владельца и
читается из durable store, поэтому одинаково после рестарта.

  Что делает Bossman?               → active_missions, working_agents
  Какие миссии активны?             → active_missions
  Кто владеет миссией?              → mission.department_id, team owner
  Какие агенты работают?            → working_agents
  Что заблокировано?                → blocked
  Что ждёт подтверждения/владельца? → waiting_approval
  Сколько бюджета осталось?         → treasury
  Какая команда проваливается?      → failing_agents
  Что реально завершено?            → verified_completed (только verified=True)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .learning import OrganizationalLearning
from .models import MissionState, TaskState
from .store import OrganizationStore
from .treasury import ResourceTreasury


@dataclass(frozen=True)
class OrganizationSnapshot:
    departments: tuple[dict[str, Any], ...]
    agents: tuple[dict[str, Any], ...]
    active_missions: tuple[dict[str, Any], ...]
    working_agents: tuple[dict[str, Any], ...]
    blocked: tuple[dict[str, Any], ...]
    waiting_approval: tuple[dict[str, Any], ...]
    verified_completed: tuple[dict[str, Any], ...]
    failed: tuple[dict[str, Any], ...]
    treasury: dict[str, Any]
    failing_agents: tuple[dict[str, Any], ...]
    teams: tuple[dict[str, Any], ...]
    counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {k: (list(v) if isinstance(v, tuple) else v) for k, v in self.__dict__.items()}


def snapshot(store: OrganizationStore, treasury: ResourceTreasury, learning: OrganizationalLearning) -> OrganizationSnapshot:
    works = store.works()
    results = {r.work_id: r for r in store.results()}
    missions = store.missions()
    by_mission_dept = {m["mission_id"]: m["department_id"] for m in missions}

    def brief(w: dict[str, Any]) -> dict[str, Any]:
        c = w["contract"]
        return {"work_id": w["work_id"], "mission_id": w["mission_id"], "department_id": w["department_id"],
                "goal": c.goal, "capability": c.required_capability, "risk": c.risk.value,
                "assigned": list(w["assigned"]), "attempts": w["attempts"],
                "reason": str((c.metadata.get("runtime") or {}).get("last_reason", ""))}

    active = tuple({**{k: m[k] for k in ("mission_id", "title", "department_id", "state", "source")}}
                   for m in missions if m["state"] in (MissionState.ACTIVE.value, MissionState.BLOCKED.value,
                                                       MissionState.RECEIVED.value))
    working = tuple({"agent_id": aid, "work_id": w["work_id"], "mission_id": w["mission_id"]}
                    for w in works if w["state"] in (TaskState.EXECUTING.value, TaskState.VERIFYING.value)
                    for aid in w["assigned"])
    blocked = tuple(brief(w) for w in works if w["state"] == TaskState.BLOCKED.value)
    waiting = tuple(brief(w) for w in works if w["state"] == TaskState.WAITING_APPROVAL.value)
    failed = tuple(brief(w) for w in works if w["state"] == TaskState.FAILED.value)
    verified = tuple({**brief(w), "evidence": [e.to_dict() for e in results[w["work_id"]].evidence]}
                     for w in works
                     if w["state"] == TaskState.COMPLETED.value and w["work_id"] in results
                     and results[w["work_id"]].verified)
    return OrganizationSnapshot(
        departments=tuple(d.to_dict() for d in store.departments()),
        agents=tuple(a.to_dict() for a in store.agents()),
        active_missions=active, working_agents=working, blocked=blocked, waiting_approval=waiting,
        verified_completed=verified, failed=failed, treasury=treasury.snapshot(),
        failing_agents=tuple(learning.failing_agents()),
        teams=tuple(store.teams(include_dissolved=False)),
        counts={"missions": len(missions), "active_missions": len(active), "works": len(works),
                "blocked": len(blocked), "waiting_approval": len(waiting), "failed": len(failed),
                "verified_completed": len(verified), "owners": len(set(by_mission_dept.values()))})
