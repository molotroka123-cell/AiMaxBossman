"""Event-Driven Organization (§16) — ограниченные реакции на события.

Событие ≠ разрешение на побочный эффект. Событие может породить только ЗАДАЧУ
(делегационный контракт) по заранее объявленному шаблону реакции; сам контракт
дальше идёт обычным путём — казначейство, маршрутизация, политика V2/V3,
одобрения владельца. Никакой прямой связи «событие → инструмент» здесь нет.

Гарантии:
  * дедуп по ключу события (idempotency) — повтор webhooks/ретраев CI не
    создаёт вторую задачу;
  * разрешения — реагируем только на виды событий из реестра реакций;
  * ограниченные ретраи — `max_attempts` в контракте реакции;
  * backpressure — не больше `max_open_per_kind` открытых реакций одного вида.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from .contracts import DelegationContract, EscalationPolicy, EvidenceRequirement
from .models import Resources, RiskTier, TaskState

ContractFactory = Callable[[Mapping[str, Any]], DelegationContract]


@dataclass(frozen=True)
class Reaction:
    event_kind: str                     # ci.failed | change.verified | schedule.report | failure.critical
    department_id: str
    capability: str
    goal_template: str                  # формат с полями события
    evidence: tuple[EvidenceRequirement, ...] = ()
    success_criteria: tuple[str, ...] = ("reaction task verified",)
    risk: RiskTier = RiskTier.LOW
    budget: Resources = field(default_factory=lambda: Resources(usd=0.5, tokens=20_000, compute_seconds=600))
    max_attempts: int = 2
    max_open_per_kind: int = 5
    side_effect: bool = True
    verified_only: bool = False         # реагировать только на события с verified=True


@dataclass(frozen=True)
class EventOutcome:
    accepted: bool
    reason: str
    work_id: str | None = None
    duplicate: bool = False


def event_key(kind: str, payload: Mapping[str, Any]) -> str:
    """Идемпотентный ключ: явный `idempotency_key` события, иначе отпечаток
    его содержимого без временных полей."""
    if payload.get("idempotency_key"):
        return f"{kind}:{payload['idempotency_key']}"
    body = {k: v for k, v in dict(payload).items() if k not in ("ts", "received_at", "delivery_id")}
    return f"{kind}:" + hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()[:24]


class EventIntake:
    def __init__(self, store, reactions: list[Reaction]) -> None:
        self.store = store
        self._reactions = {r.event_kind: r for r in reactions}

    def reactions(self) -> list[Reaction]:
        return list(self._reactions.values())

    def _open_count(self, kind: str) -> int:
        open_states = {TaskState.PLANNED.value, TaskState.ASSIGNED.value, TaskState.EXECUTING.value,
                       TaskState.VERIFYING.value, TaskState.WAITING_APPROVAL.value}
        return sum(1 for w in self.store.works()
                   if w["contract"].metadata.get("event_kind") == kind and w["state"] in open_states)

    def accept(self, kind: str, payload: Mapping[str, Any], *, mission_id: str) -> tuple[EventOutcome, DelegationContract | None]:
        reaction = self._reactions.get(kind)
        key = event_key(kind, payload)
        if reaction is None:
            self.store.record_event(key, kind, "rejected:no_reaction", dict(payload))
            return EventOutcome(False, f"no registered reaction for event kind {kind!r}"), None
        if reaction.verified_only and not payload.get("verified"):
            self.store.record_event(key, kind, "rejected:unverified", dict(payload))
            return EventOutcome(False, "reaction requires a verified event"), None
        work_id = f"evt-{key.split(':', 1)[1][:16]}"
        if self.store.event(key) is not None:
            # дедуп ДО backpressure: повтор — не новая нагрузка
            return EventOutcome(False, "duplicate event (already accepted)", work_id, duplicate=True), None
        if self._open_count(kind) >= reaction.max_open_per_kind:
            # событие НЕ записывается — при разгрузке его можно подать снова с тем же ключом
            return EventOutcome(False, f"backpressure: {reaction.max_open_per_kind} open reactions of {kind!r}"), None
        if not self.store.record_event(key, kind, "accepted", dict(payload)):
            return EventOutcome(False, "duplicate event (already accepted)", work_id, duplicate=True), None
        goal = reaction.goal_template.format(**{k: str(v) for k, v in dict(payload).items()})
        contract = DelegationContract(
            work_id=work_id, mission_id=mission_id, department_id=reaction.department_id, goal=goal,
            required_capability=reaction.capability, success_criteria=list(reaction.success_criteria),
            evidence_required=list(reaction.evidence), budget=reaction.budget, risk=reaction.risk,
            escalation=EscalationPolicy(max_attempts=reaction.max_attempts, on_failure="fail"),
            side_effect=reaction.side_effect, inputs={"event": dict(payload)},
            metadata={"event_kind": kind, "event_key": key})
        return EventOutcome(True, "reaction task created", work_id), contract
