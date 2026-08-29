"""Подсистема Video Factory для реестра жизненного цикла (lifecycle.Subsystem).

`critical=False` — сервер обязан подниматься даже без видео: если ffmpeg
отсутствует, `validate()` бросает, реестр помечает подсистему `degraded` и НЕ
запускает воркеров, но ядро живёт. `start()` поднимает ограниченных воркеров;
`stop()` снимает их и освобождает любые удержанные аренды (без осиротевших
броней). На старте сервис сверяет незавершённые джобы (INTERRUPTED).
"""
from __future__ import annotations

from .. import errors
from ..obs import get_logger
from .ffmpeg import ffmpeg_available
from .service import VideoFactoryService

_log = get_logger("bossman.video_factory")


class VideoFactorySubsystem:
    """Обёртка Subsystem вокруг процессного `VideoFactoryService`."""

    name = "video_factory"
    critical = False

    def __init__(self, service: VideoFactoryService) -> None:
        self._service = service
        self.degraded_reason: str | None = None

    async def validate(self) -> None:
        """Проверить наличие ffmpeg. Нет бинаря → деградация (бросаем; реестр
        пометит degraded и не запустит воркеров — но boot не рушится)."""
        if not ffmpeg_available():
            self.degraded_reason = "ffmpeg binary not available"
            _log.warning("video_factory degraded: %s", self.degraded_reason)
            raise errors.VideoProviderFailed(
                "ffmpeg not available; video_factory degraded (no video generation)"
            )
        self._service.ensure_root()
        _log.info("video_factory validated (ffmpeg present)")

    async def start(self) -> None:
        await self._service.start()

    async def stop(self) -> None:
        await self._service.stop()
