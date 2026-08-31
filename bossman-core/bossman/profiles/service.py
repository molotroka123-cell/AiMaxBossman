"""ProfileService — процессный синглтон + мост enforcement.

Даёт enforcement-точкам чистые колбэки, не создавая жёсткой связности:
* `computer_access_check(device_id)` — бросает CapabilityDenied, если профиль,
  привязанный к устройству, запрещает управление компьютером. Если профиля нет —
  поведение задаётся `strict` (по умолчанию False: неизвестное устройство = хозяин
  локальной сессии, не режем существующие локальные потоки).
"""
from __future__ import annotations

from . import gate
from .models import CapabilityDecision, Profile
from .store import ProfileStore


class ProfileService:
    def __init__(self, store: ProfileStore, *, strict_unknown_device: bool = False) -> None:
        self.store = store
        self.strict = strict_unknown_device

    # ---- lookups ----
    def for_device(self, device_id: str | None) -> Profile | None:
        return self.store.by_device(device_id)

    def for_telegram(self, telegram_user_id: str | None) -> Profile | None:
        return self.store.by_telegram(telegram_user_id)

    # ---- decisions ----
    def decide_device(self, device_id: str | None, capability: str,
                      *, source: str = "local") -> CapabilityDecision:
        prof = self.for_device(device_id)
        if prof is None:
            # Нет профиля. ЛОКАЛЬНЫЙ источник (не strict) — это хозяин, не режем.
            # Любой НЕ-локальный источник (remote/telegram) без профиля —
            # fail-CLOSED: не выдаём возможность «на авось» (Security Hardening V1.1).
            if source == "local" and not self.strict:
                return CapabilityDecision(True, "нет профиля (локальный хозяин)", capability, None)
            return CapabilityDecision(
                False, f"нет профиля для не-локального источника {source!r}", capability, None)
        return gate.decide(prof, capability)

    def computer_access_check(self, device_id: str | None, source: str = "local") -> None:
        """Бросает gate.CapabilityDenied, если управление компом запрещено профилю."""
        d = self.decide_device(device_id, "computer.control", source=source)
        if not d.allow:
            raise gate.CapabilityDenied(d)


_SERVICE: ProfileService | None = None


def get_service() -> ProfileService | None:
    return _SERVICE


def set_service(svc: ProfileService | None) -> None:
    global _SERVICE
    _SERVICE = svc


class ProfilesUnavailable(PermissionError):
    """Профильный gate недоступен, а источник не-локальный → fail-closed."""


def computer_access_check(device_id: str | None, source: str = "local") -> None:
    """Модульный колбэк для инъекции в computer_operator.

    Сервис поднят → делегируем профильному решению (с учётом source).
    Сервис НЕ поднят:
      * локальный источник → no-op (локального хозяина не режем, как раньше);
      * НЕ-локальный источник → fail-CLOSED: без работающего gate удалённое
        управление компом не создаётся (Security Hardening V1.1, H2/H7).
    """
    svc = _SERVICE
    if svc is None:
        if source != "local":
            raise ProfilesUnavailable(
                f"profiles gate unavailable; computer control denied for source {source!r}")
        return
    svc.computer_access_check(device_id, source)
