"""Video Factory (Этап 7) — устойчивый конвейер генерации видео.

Публичный контракт (на нём держатся приёмочные тесты; НЕ менять сигнатуры):
`from bossman.video_factory import VideoFactory, VideoJob, Scene, JobState`.

Пакет также экспонирует для api.py два атрибута-шва (как остальные подсистемы
этапов 4–7):
- `build_subsystem()` — фабрика подсистемы (lifecycle.Subsystem);
- `router` — APIRouter (`/video/...`).

Один на процесс `FACTORY` — синглтон сервиса, общий для подсистемы (воркеры) и
роутера (создание/чтение джоб). Durable-истина — `job.json` на диске; никакого
второго durable-хранилища.
"""
from __future__ import annotations

from .model import JobState, Scene, VideoJob
from .pipeline import VideoFactory
from .providers import (
    GuardedBrowserProvider,
    SyntheticFFmpegProvider,
    VideoProvider,
    assert_browser_provider_allowed,
)
from .service import VideoFactoryService
from .subsystem import VideoFactorySubsystem

# Процессный синглтон сервиса. Конструктор без сайд-эффектов (VideoFactory
# собирается лениво при первом использовании — импорт не трогает диск).
FACTORY = VideoFactoryService()


def build_subsystem() -> VideoFactorySubsystem:
    """Фабрика подсистемы для реестра жизненного цикла. Возвращает объект,
    удовлетворяющий протоколу lifecycle.Subsystem, связанный с синглтоном
    `FACTORY`."""
    return VideoFactorySubsystem(FACTORY)


# Роутер импортируем терпимо: пакет обязан импортироваться даже без FastAPI.
try:  # pragma: no cover - зависит от наличия fastapi
    from .routes import router
except Exception:  # noqa: BLE001
    router = None  # type: ignore[assignment]


__all__ = [
    # контракт прототипа
    "VideoFactory",
    "VideoJob",
    "Scene",
    "JobState",
    # провайдеры и шов политики
    "VideoProvider",
    "SyntheticFFmpegProvider",
    "GuardedBrowserProvider",
    "assert_browser_provider_allowed",
    # швы подсистемы
    "VideoFactoryService",
    "VideoFactorySubsystem",
    "build_subsystem",
    "router",
    "FACTORY",
]
