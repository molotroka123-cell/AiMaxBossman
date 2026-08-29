"""Подсистема Stage 6 для реестра lifecycle.

`build_subsystem()` → объект с контрактом `Subsystem` (name/critical/validate/
start/stop), name="remote_client", critical=False.

validate(): при сконфигурированном живом Postgres создаёт схему (CREATE TABLE IF
NOT EXISTS) и ставит боевой PostgresDeviceStore активным сервисом. Если БД
недоступна — ловит ошибку соединения, логирует, ставит in-memory фейк (чтобы
удалённый клиент всё же работал в dev) и помечает подсистему degraded (пробросом
BossmanError; critical=False => boot ядра продолжается).
"""
from __future__ import annotations

from .. import obs
from ..errors import BossmanError
from .service import DeviceService, set_service
from .store import DDL, InMemoryDeviceStore, PostgresDeviceStore

log = obs.get_logger("bossman.remote_client")


class RemoteClientSubsystem:
    name = "remote_client"
    critical = False

    def __init__(self) -> None:
        self._degraded = False

    async def validate(self) -> None:
        # Создать схему в Postgres. Ленивый импорт db, чтобы не тянуть asyncpg-пул
        # при простом импорте пакета.
        try:
            from .. import db
            async with (await db.pool()).acquire() as conn:
                await conn.execute(DDL)
            set_service(DeviceService(PostgresDeviceStore()))
            self._degraded = False
            log.info("remote_client: postgres store ready")
        except Exception as exc:  # noqa: BLE001 — деградируем, но не роняем boot
            # Никаких секретов в сообщении; строку об ошибке чистит obs при записи.
            self._degraded = True
            set_service(DeviceService(InMemoryDeviceStore()))
            log.warning("remote_client degraded: postgres unavailable (%s)", type(exc).__name__)
            # Пробрасываем, чтобы реестр отметил подсистему degraded; critical=False
            # => общий boot продолжается, а роутер работает на in-memory фейке.
            raise BossmanError("remote_client degraded: postgres unavailable") from exc

    async def start(self) -> None:
        # Фоновых задач нет: мост событий поднимается per-request на /remote/events.
        pass

    async def stop(self) -> None:
        # Идемпотентно: собственных ресурсов не держим (пул БД закрывает ядро).
        pass


def build_subsystem() -> RemoteClientSubsystem:
    return RemoteClientSubsystem()
