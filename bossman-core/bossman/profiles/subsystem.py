"""Подсистема profiles для реестра lifecycle.

`build_subsystem()` → объект с контрактом Subsystem (name/critical/validate/
start/stop), name="profiles", critical=False. validate(): поднимает durable
ProfileStore под workspace_dir и ставит процессный ProfileService. Секретов и
внешних соединений нет → деградировать нечему.
"""
from __future__ import annotations

from .. import obs
from ..config import settings
from .service import ProfileService, set_service
from .store import ProfileStore

log = obs.get_logger("bossman.profiles")


class ProfilesSubsystem:
    name = "profiles"
    # critical=True: профильный gate — security-контур (управление компом,
    # личные данные). Если он не поднялся, загрузка ПРЕРЫВАЕТСЯ громко, а не
    # деградирует в permissive (Security Hardening V1.1, H2/H7).
    critical = True

    async def validate(self) -> None:
        root = settings.workspace_dir / "_profiles"
        set_service(ProfileService(ProfileStore(root)))
        log.info("profiles: store ready at %s", root)

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass


def build_subsystem() -> ProfilesSubsystem:
    return ProfilesSubsystem()
