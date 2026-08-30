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
    def decide_device(self, device_id: str | None, capability: str) -> CapabilityDecision:
        prof = self.for_device(device_id)
        if prof is None and not self.strict:
            # Нет профиля и не strict → это локальный хозяин, не режем.
            return CapabilityDecision(True, "нет профиля (локальный хозяин)", capability, None)
        return gate.decide(prof, capability)

    def computer_access_check(self, device_id: str | None) -> None:
        """Бросает gate.CapabilityDenied, если управление компом запрещено профилю."""
        d = self.decide_device(device_id, "computer.control")
        if not d.allow:
            raise gate.CapabilityDenied(d)


_SERVICE: ProfileService | None = None


def get_service() -> ProfileService | None:
    return _SERVICE


def set_service(svc: ProfileService | None) -> None:
    global _SERVICE
    _SERVICE = svc


def computer_access_check(device_id: str | None) -> None:
    """Модульный колбэк для инъекции в computer_operator (no-op, если сервиса нет)."""
    svc = _SERVICE
    if svc is not None:
        svc.computer_access_check(device_id)
