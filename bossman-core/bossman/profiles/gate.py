"""Capability gate — единственный источник истины «можно ли профилю capability».

Правила (fail-safe, deny-by-default):
* профиль отсутствует / выключен → DENY всего;
* неизвестная capability (нет в CAPABILITY_TOGGLE) → DENY;
* известная capability разрешена ⇔ соответствующий тумблер включён.

Это чистая функция без побочных эффектов и без сети. Enforcement-точки
(computer_operator, tool-grants) вызывают её и уважают результат.
"""
from __future__ import annotations

from .models import CAPABILITY_TOGGLE, CapabilityDecision, Profile


def decide(profile: Profile | None, capability: str) -> CapabilityDecision:
    cap = str(capability or "")
    if profile is None:
        return CapabilityDecision(False, "нет профиля (deny-by-default)", cap, None)
    if not profile.enabled:
        return CapabilityDecision(False, f"профиль '{profile.id}' выключен", cap, None)
    toggle = CAPABILITY_TOGGLE.get(cap)
    if toggle is None:
        return CapabilityDecision(False, f"неизвестная capability '{cap}' (deny-by-default)", cap, None)
    allowed = bool(profile.toggles.get(toggle, False))
    if allowed:
        return CapabilityDecision(True, f"тумблер '{toggle}' включён", cap, toggle)
    return CapabilityDecision(False, f"тумблер '{toggle}' выключен → доступ запрещён", cap, toggle)


class CapabilityDenied(PermissionError):
    """Профилю запрещена запрошенная capability (enforcement-точки бросают это)."""

    def __init__(self, decision: CapabilityDecision) -> None:
        super().__init__(decision.reason)
        self.decision = decision


def enforce(profile: Profile | None, capability: str) -> None:
    """Бросает CapabilityDenied, если capability запрещена. Иначе возвращает None."""
    d = decide(profile, capability)
    if not d.allow:
        raise CapabilityDenied(d)
