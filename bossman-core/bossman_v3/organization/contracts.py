"""Delegation Contract 2.0 (§11).

Контракт — объективное определение «сделано» для дочерней работы: цель, входы,
ограничения, поставляемое, требуемая способность, критерии успеха, требуемые
улики, бюджет, риск, приоритет, родитель, зависимости, политика эскалации.

Правило валидации одно и оно не смягчается: дочерняя работа принята только когда
КАЖДЫЙ требуемый вид улики присутствует и подтверждён нижним слоем. Прозы
исполнителя («сделал», «проверил») в валидации не существует — `claims` даже
не читаются. Это перенос инварианта V2:

    SIDE_EFFECT_REQUIRED && VERIFIED_SIDE_EFFECT == FALSE → TASK_SUCCESS == FALSE
"""
from __future__ import annotations

import hashlib
import math
import json
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Any, Mapping

from bossman.company.model import EvidenceRequirement as _CompanyEvidenceRequirement

from .models import EXECUTOR, Evidence, Resources, RiskTier, WorkResult

# Источники, которым разрешено ставить verified=True. Любая улика с verified=True
# из другого источника считается НЕподтверждённой: доверие — свойство слоя, а не
# флага в словаре.
TRUSTED_EVIDENCE_SOURCES = ("journal:", "bcc.v2.verification", "bossman_v3.verifier")  # информационно (EH-01: решает подпись)


class EvidenceRequirement(_CompanyEvidenceRequirement):
    """ОДИН тип требования к уликам на весь репозиторий: канонический —
    bossman.company.model.EvidenceRequirement (зеркало ExpectedState V2);
    здесь только сериализация для durable store. Второго датакласса нет."""

    def __init__(self, kind: str, target: str = "", expect: Mapping[str, Any] | None = None) -> None:
        super().__init__(kind, target, dict(expect or {}))

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "target": self.target, "expect": dict(self.expect)}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "EvidenceRequirement":
        return cls(str(raw["kind"]), str(raw.get("target", "")), dict(raw.get("expect") or {}))


@dataclass(frozen=True)
class EscalationPolicy:
    max_attempts: int = 2
    on_failure: str = "escalate_tier"     # escalate_tier | ask_owner | fail
    on_budget_exceeded: str = "ask_owner"
    notify_roles: tuple[str, ...] = ("lead",)

    def to_dict(self) -> dict[str, Any]:
        return {"max_attempts": self.max_attempts, "on_failure": self.on_failure,
                "on_budget_exceeded": self.on_budget_exceeded, "notify_roles": list(self.notify_roles)}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any] | None) -> "EscalationPolicy":
        raw = dict(raw or {})
        return cls(int(raw.get("max_attempts", 2)), str(raw.get("on_failure", "escalate_tier")),
                   str(raw.get("on_budget_exceeded", "ask_owner")),
                   tuple(raw.get("notify_roles") or ("lead",)))


@dataclass
class DelegationContract:
    work_id: str
    mission_id: str
    department_id: str
    goal: str
    required_capability: str
    success_criteria: list[str]
    evidence_required: list[EvidenceRequirement]
    budget: Resources = field(default_factory=Resources)
    risk: RiskTier = RiskTier.LOW
    priority: int = 5                          # 0 — самый срочный
    deadline: str = ""                         # ISO-8601 или пусто
    inputs: dict[str, Any] = field(default_factory=dict)
    constraints: list[str] = field(default_factory=list)
    deliverables: list[str] = field(default_factory=list)
    required_role: str = EXECUTOR
    parent_id: str | None = None
    dependencies: list[str] = field(default_factory=list)   # work_id, которые обязаны быть COMPLETED
    escalation: EscalationPolicy = field(default_factory=EscalationPolicy)
    side_effect: bool = True                   # False — чисто информационная работа
    # План шагов для нижнего слоя (V3 PlanStep в сериализованном виде). Пустой —
    # исполнитель получает контракт «как есть» и сам строит план.
    steps: list[dict[str, Any]] = field(default_factory=list)
    # Требования к МЕСТУ исполнения (для Fleet): приватность и ресурсы. Флот
    # решает «где», организация — «кто»; контракт лишь объявляет, что нужно.
    privacy: str = "private"                   # private | local_only | internal | public
    placement: dict[str, Any] = field(default_factory=dict)   # capabilities/pools/min_ram_gb/min_gpu_memory_gb/required_models/...
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.risk, str) and not isinstance(self.risk, RiskTier):
            self.risk = RiskTier(self.risk)
        if self.privacy not in ("private", "local_only", "internal", "public"):
            raise ValueError(f"unknown privacy level {self.privacy!r}")

    # ------------------------------------------------------------ identity

    def digest(self) -> str:
        """Канонический отпечаток: по нему один и тот же контракт узнаётся
        после рестарта и не делегируется второй раз."""
        body = self.to_dict()
        body["metadata"] = {k: v for k, v in body["metadata"].items() if k not in ("runtime", "v2", "fleet_dispatch")}
        raw = json.dumps(body, sort_keys=True, ensure_ascii=False, allow_nan=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    # ---------------------------------------------------------- well-formed

    def problems(self) -> list[str]:
        """Что мешает делегировать контракт вообще (до маршрутизации)."""
        out: list[str] = []
        if not self.goal.strip():
            out.append("goal is empty")
        if not self.required_capability.strip():
            out.append("required_capability is empty")
        if not self.success_criteria:
            out.append("success_criteria are empty")
        if self.side_effect and not self.evidence_required:
            out.append("side-effect work must declare evidence_required")
        if self.escalation.max_attempts < 1:
            out.append("escalation.max_attempts must be >= 1")
        import math
        for f in Resources._FIELDS:                               # O003: отрицательные/NaN/inf не проходят admission
            v = getattr(self.budget, f, 0)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0 or not math.isfinite(float(v)):
                out.append(f"budget.{f} must be a finite non-negative number")
                break
        return out

    # ------------------------------------------------------------ validate

    def validate(self, result: WorkResult) -> tuple[bool, list[str]]:
        """Принять или отклонить исход. Читаются только `evidence` и `executed`;
        `claims` не участвуют по построению."""
        errors: list[str] = []
        if result.work_id != self.work_id:
            errors.append("work_id mismatch")
        if self.side_effect and not result.executed:
            errors.append("nothing was executed by the lower layer")
        trusted = [e for e in result.evidence if e.verified and _trusted(e) and self._bound(e)]
        untrusted_verified = [e for e in result.evidence if e.verified and not _trusted(e)]
        for e in untrusted_verified:
            # EH-01: без подписи — «unsigned verified evidence»; с подписью, но не
            # доверенного signer'а или битой — «signature invalid». Оба — fail-closed.
            why = "unsigned verified evidence" if not e.sig else "signature invalid"
            errors.append(f"evidence {e.kind}:{e.ref} claims verified from untrusted source {e.source!r} ({why})")
        by_kind: dict[str, list[Evidence]] = {}
        for e in trusted:
            by_kind.setdefault(e.kind, []).append(e)
        for req in self.evidence_required:
            candidates = by_kind.get(req.kind, [])
            if req.target:
                candidates = [c for c in candidates if _same_target(c.ref, req.target)]
            if req.expect:
                candidates = [c for c in candidates if all(
                    c.binding.get("verified_expect", {}).get(k) == v for k, v in req.expect.items())]
            if not candidates:
                have = [e for e in result.evidence if e.kind == req.kind]
                if have and not any(h.verified for h in have):
                    errors.append(f"unverified evidence:{req.kind}")
                else:
                    errors.append(f"missing evidence:{req.kind}" + (f"@{req.target}" if req.target else ""))
        return not errors, errors

    def _bound(self, evidence: Evidence) -> bool:
        b = evidence.binding
        if (b.get("mission_id") != self.mission_id or b.get("work_id") != self.work_id
                or b.get("contract_digest") != self.digest() or not b.get("action_digest")
                or not b.get("attempt_id") or not b.get("verification_passed")):
            return False
        try:
            observed = datetime.fromisoformat(evidence.observed_at)
            started = datetime.fromisoformat(b["started_at"])
            age_limit = float(self.metadata.get("evidence_max_age_seconds", 300))
            now = datetime.now(timezone.utc)
            return (math.isfinite(age_limit) and 0 < age_limit <= 86400
                    and observed.tzinfo is not None and started.tzinfo is not None
                    and started <= observed <= now and (now - observed).total_seconds() <= age_limit)
        except (ValueError, TypeError, KeyError):
            return False

    # ------------------------------------------------------------ persist

    def to_dict(self) -> dict[str, Any]:
        return {"work_id": self.work_id, "mission_id": self.mission_id, "department_id": self.department_id,
                "goal": self.goal, "required_capability": self.required_capability,
                "success_criteria": list(self.success_criteria),
                "evidence_required": [e.to_dict() for e in self.evidence_required],
                "budget": self.budget.to_dict(), "risk": self.risk.value, "priority": self.priority,
                "deadline": self.deadline, "inputs": dict(self.inputs), "constraints": list(self.constraints),
                "deliverables": list(self.deliverables), "required_role": self.required_role,
                "parent_id": self.parent_id, "dependencies": list(self.dependencies),
                "escalation": self.escalation.to_dict(), "side_effect": self.side_effect,
                "steps": [dict(s) for s in self.steps], "privacy": self.privacy,
                "placement": dict(self.placement), "metadata": dict(self.metadata)}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "DelegationContract":
        return cls(work_id=str(raw["work_id"]), mission_id=str(raw["mission_id"]),
                   department_id=str(raw["department_id"]), goal=str(raw.get("goal", "")),
                   required_capability=str(raw.get("required_capability", "")),
                   success_criteria=list(raw.get("success_criteria") or []),
                   evidence_required=[EvidenceRequirement.from_dict(e) for e in raw.get("evidence_required") or []],
                   budget=Resources.from_dict(raw.get("budget")), risk=RiskTier(raw.get("risk", "low")),
                   priority=int(raw.get("priority", 5)), deadline=str(raw.get("deadline", "")),
                   inputs=dict(raw.get("inputs") or {}), constraints=list(raw.get("constraints") or []),
                   deliverables=list(raw.get("deliverables") or []),
                   required_role=str(raw.get("required_role", EXECUTOR)), parent_id=raw.get("parent_id"),
                   dependencies=list(raw.get("dependencies") or []),
                   escalation=EscalationPolicy.from_dict(raw.get("escalation")),
                   side_effect=bool(raw.get("side_effect", True)),
                   steps=[dict(s) for s in raw.get("steps") or []], privacy=str(raw.get("privacy", "private")),
                   placement=dict(raw.get("placement") or {}), metadata=dict(raw.get("metadata") or {}))


def _same_target(ref: str, target: str) -> bool:
    """Only the exact declared evidence target can satisfy a contract."""
    return ref == target



def _trusted(e: Evidence) -> bool:
    """EH-01: улика доверена ⇔ HMAC валиден И signer ∈ TRUSTED_SIGNERS. Префикс
    `source` больше ничего не доказывает — он остаётся для чтения человеком."""
    return e.signature_valid()


def consensus(results: list[WorkResult], *, minimum_verified: int = 2) -> bool:
    """Кворум имеет смысл только из НЕЗАВИСИМО подтверждённых результатов;
    согласие агентов само по себе уликой не является (мандат §4)."""
    return sum(1 for r in results if r.verified) >= minimum_verified
