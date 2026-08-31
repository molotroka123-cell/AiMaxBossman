"""Security Memory — типизированный ВИД поверх каноничной памяти отказов.

Второго хранилища здесь НЕТ. Инциденты кладутся в каноничную таблицу `failures`
через `bossman.failure_memory` (Postgres, единственный durable-store). Этот
модуль лишь задаёт устойчивую форму записи инцидента и выборку по ней, чтобы
защитные решения опирались на прошлые эпизоды.

Секреты в память не попадают: текст проходит канонический `obs.redact`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..obs import redact
from .ids import IDSResult

SECURITY_ERROR_CLASS = "security_incident"


@dataclass(frozen=True)
class SecurityIncident:
    scenario_id: str
    attack_class: str
    severity: str
    defense: str
    contained: bool
    evidence_ref: str = ""

    def as_failure_kwargs(self, task_id: str) -> dict[str, Any]:
        """Отобразить инцидент на контракт каноничной failure_memory."""
        return {
            "task_id": task_id,
            "symptom": redact(f"{self.attack_class}: {self.scenario_id}")[:2000],
            "error_class": SECURITY_ERROR_CLASS,
            "root_cause": redact(self.attack_class)[:2000],
            "attempted_fix": redact(self.defense)[:2000],
            "result": "contained" if self.contained else "not_contained",
            "environment": {"severity": self.severity, "evidence_ref": self.evidence_ref},
        }


async def record_incident(incident: SecurityIncident, *, task_id: str) -> Any:
    """Записать инцидент в КАНОНИЧНУЮ failure-память (ленивый импорт)."""
    from .. import failure_memory
    kw = incident.as_failure_kwargs(task_id)
    env = kw.pop("environment")
    return await failure_memory.record_failure(
        kw["task_id"], kw["symptom"], kw["error_class"], kw["root_cause"],
        kw["attempted_fix"], kw["result"], environment=env)


async def past_incidents(task_id: str | None = None, *, limit: int = 50) -> list[Any]:
    """Прошлые инциденты из каноничной памяти (для условной защиты)."""
    from .. import failure_memory
    return await failure_memory.query_failures(
        task_id=task_id, error_class=SECURITY_ERROR_CLASS, limit=limit)


def incident_from_ids(scenario_id: str, attack_class: str, ids: IDSResult,
                      defense: str, contained: bool, evidence_ref: str = "") -> SecurityIncident:
    return SecurityIncident(scenario_id, attack_class, ids.severity, defense,
                            contained, evidence_ref)
