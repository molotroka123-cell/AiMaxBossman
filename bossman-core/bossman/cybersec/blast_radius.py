"""Sandbox / Blast Radius Controller — ограничение радиуса поражения действия.

Не второй Policy: контроллер НЕ разрешает ничего. Он только УЖЕСТОЧАЕТ — по
классу побочного эффекта и уровню риска решает, требуется ли изоляция,
подтверждение владельца или полный запрет. Итог комбинируется с решением
Policy по правилу «побеждает более строгое».
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class SideEffect(IntEnum):
    READ_ONLY = 0
    IDEMPOTENT_WRITE = 1
    REVERSIBLE_WRITE = 2
    IRREVERSIBLE = 3
    EXTERNAL_EGRESS = 4


class Containment(IntEnum):
    ALLOW = 0            # обычный путь
    SANDBOX = 1          # только в одноразовой песочнице
    REQUIRE_APPROVAL = 2 # подтверждение владельца
    DENY = 3             # запрещено на этом уровне доверия


@dataclass(frozen=True)
class BlastDecision:
    containment: Containment
    reason: str

    @property
    def allowed_without_owner(self) -> bool:
        return self.containment in (Containment.ALLOW, Containment.SANDBOX)


def assess(side_effect: SideEffect, *, risk: str = "low",
           in_sandbox: bool = False, untrusted_origin: bool = False) -> BlastDecision:
    """Определить минимально необходимое ограничение.

    Правила (fail-closed, от строгого к мягкому):
    * недоверенное происхождение + необратимость/выход наружу → DENY;
    * необратимость или egress → подтверждение владельца;
    * высокий/критический риск вне песочницы → песочница;
    * иначе → обычный путь.
    """
    if untrusted_origin and side_effect >= SideEffect.IRREVERSIBLE:
        return BlastDecision(Containment.DENY,
                             "irreversible/egress action originating from untrusted input")
    if side_effect >= SideEffect.IRREVERSIBLE:
        return BlastDecision(Containment.REQUIRE_APPROVAL,
                             "irreversible or externally visible effect requires the owner")
    if risk in {"high", "critical"} and not in_sandbox:
        return BlastDecision(Containment.SANDBOX, f"{risk} risk must run in a disposable sandbox")
    if untrusted_origin and side_effect >= SideEffect.REVERSIBLE_WRITE:
        return BlastDecision(Containment.SANDBOX, "write originating from untrusted input is confined")
    return BlastDecision(Containment.ALLOW, "within normal blast radius")


def combine(policy_allows: bool, decision: BlastDecision) -> Containment:
    """Комбинация с Policy: контроллер может только УЖЕСТОЧИТЬ, не ослабить."""
    if not policy_allows:
        return Containment.DENY
    return decision.containment
