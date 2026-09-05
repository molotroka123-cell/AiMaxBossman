"""Resource Treasury (§15): бюджеты организации / отдела / миссии с
резервированием.

Схема reserve → commit/release та же, что в bossman.company.runtime: оценка
резервируется ДО делегирования, факт коммитится ПОСЛЕ, резерв снимается в любом
исходе. Превышение — не исключение, а решение «остановить и спросить владельца».

Честность (§15, последний абзац): казначейство считает то, что ему сообщили
нижние слои (чеки, измерители). Оно НЕ утверждает, что провайдер физически не
спишет больше — жёсткого провайдерского enforcement здесь нет и не заявляется.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .models import Resources

ORG_SCOPE = "organization"


@dataclass(frozen=True)
class TreasuryDecision:
    allowed: bool
    reason: str
    scope: str = ""                  # какой конверт не пустил
    ask_owner: bool = False


@dataclass
class Envelope:
    scope: str
    limit: Resources
    spent: Resources = field(default_factory=Resources)
    reserved: Resources = field(default_factory=Resources)

    @property
    def committed_and_reserved(self) -> Resources:
        return self.spent + self.reserved

    def remaining(self) -> Resources:
        return self.limit - self.committed_and_reserved

    def to_dict(self) -> dict[str, Any]:
        return {"scope": self.scope, "limit": self.limit.to_dict(), "spent": self.spent.to_dict(),
                "reserved": self.reserved.to_dict(), "remaining": self.remaining().to_dict()}


class ResourceTreasury:
    """Все конверты, которых касается работа: organization → department:<id>
    → mission:<id>. Проверка проходит по КАЖДОМУ; первый, кто не пускает,
    называется в решении."""

    def __init__(self) -> None:
        self._env: dict[str, Envelope] = {}

    # ------------------------------------------------------------- setup

    def set_limit(self, scope: str, limit: Resources) -> None:
        env = self._env.get(scope)
        if env is None:
            self._env[scope] = Envelope(scope=scope, limit=limit)
        else:
            env.limit = limit

    def envelope(self, scope: str) -> Envelope:
        env = self._env.get(scope)
        if env is None:
            env = self._env[scope] = Envelope(scope=scope, limit=Resources())
        return env

    def restore(self, scope: str, *, limit: Resources, spent: Resources) -> None:
        """Восстановление после рестарта: факт возвращается, резервы — нет
        (незавершённая работа перерезервирует сама)."""
        self._env[scope] = Envelope(scope=scope, limit=limit, spent=spent)

    @staticmethod
    def scopes_for(department_id: str, mission_id: str | None) -> list[str]:
        out = [ORG_SCOPE, f"department:{department_id}"]
        if mission_id:
            out.append(f"mission:{mission_id}")
        return out

    # ---------------------------------------------------------- lifecycle

    def preflight(self, scopes: list[str], estimate: Resources) -> TreasuryDecision:
        for scope in scopes:
            env = self.envelope(scope)
            ok, why = env.limit.fits(env.committed_and_reserved + estimate)
            if not ok:
                return TreasuryDecision(False, f"budget exceeded in {scope}: {why}", scope, ask_owner=True)
        return TreasuryDecision(True, "within all envelopes")

    def reserve(self, scopes: list[str], estimate: Resources) -> TreasuryDecision:
        decision = self.preflight(scopes, estimate)
        if not decision.allowed:
            return decision
        for scope in scopes:
            env = self.envelope(scope)
            env.reserved = env.reserved + estimate
        return decision

    def commit(self, scopes: list[str], estimate: Resources, actual: Resources) -> TreasuryDecision:
        """Снять резерв, записать факт. Если факт вышел за конверт — записать всё
        равно (тратить тайком нельзя, но и скрывать перерасход нельзя) и вернуть
        решение «спросить владельца»."""
        overrun: TreasuryDecision | None = None
        for scope in scopes:
            env = self.envelope(scope)
            env.reserved = env.reserved - estimate
            env.spent = env.spent + actual
            ok, why = env.limit.fits(env.spent)
            if not ok and overrun is None:
                overrun = TreasuryDecision(False, f"cost overrun in {scope}: {why}", scope, ask_owner=True)
        return overrun or TreasuryDecision(True, "committed")

    def release(self, scopes: list[str], estimate: Resources) -> None:
        for scope in scopes:
            env = self.envelope(scope)
            env.reserved = env.reserved - estimate

    # ------------------------------------------------------------- views

    def snapshot(self) -> dict[str, dict[str, Any]]:
        return {scope: env.to_dict() for scope, env in sorted(self._env.items())}
