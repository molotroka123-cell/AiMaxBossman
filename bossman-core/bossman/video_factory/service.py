"""Сервис Video Factory: связывает VideoFactory, ограниченную очередь и воркеры.

Один на процесс `FACTORY` — синглтон, общий для подсистемы (жизненный цикл,
воркеры) и роутера (создание/чтение джоб). Аналогично `resource_brain.BRAIN`.

Сверка после рестарта: аренды Resource Brain эфемерны (в памяти, пусты на
рестарте), поэтому любая джоба на диске в состоянии RUNNING по определению
без аренды — помечаем её INTERRUPTED (зеркалит runner.mark_interrupted).
"""
from __future__ import annotations

import asyncio
import contextlib
import os
from pathlib import Path

from .. import events
from ..obs import get_logger
from .model import SCENE_PLANNED, SCENE_RUNNING, JobState, VideoJob
from .pipeline import VideoFactory
from .queue import BoundedJobQueue

_log = get_logger("bossman.video_factory")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def _default_root() -> Path:
    raw = os.getenv("BOSSMAN_VIDEO_ROOT")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".bossman" / "video-factory"


class VideoFactoryService:
    """Обвязка вокруг `VideoFactory`: очередь, воркеры, старт/стоп, сверка."""

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        brain=None,
        provider=None,
        queue_size: int | None = None,
        workers: int | None = None,
        backoff: float = 1.0,
    ) -> None:
        self._root = Path(root) if root is not None else None
        self._brain = brain
        self._provider = provider
        self._factory: VideoFactory | None = None  # ленивая сборка (без сайд-эффектов на импорте)
        self.queue = BoundedJobQueue(maxsize=queue_size or _env_int("BOSSMAN_VIDEO_QUEUE", 8))
        self._nworkers = workers or _env_int("BOSSMAN_VIDEO_WORKERS", 1)
        self._backoff = backoff
        self._workers: list[asyncio.Task] = []
        self._running = False

    # --- ленивый VideoFactory ----------------------------------------------

    @property
    def root(self) -> Path:
        return self._root if self._root is not None else _default_root()

    @property
    def factory(self) -> VideoFactory:
        """Собрать VideoFactory при первом обращении (создаёт корень на диске).
        Импорт модуля не трогает ФС — только реальное использование."""
        if self._factory is None:
            self._factory = VideoFactory(self.root, brain=self._brain, provider=self._provider)
        return self._factory

    def ensure_root(self) -> None:
        self.factory  # noqa: B018 — материализует фабрику и корень

    # --- API создания/постановки в очередь ---------------------------------

    def create_and_enqueue(self, title: str, prompts, *, duration_s: float = 5.0) -> VideoJob:
        """Создать джобу и поставить в ограниченную очередь. Полна → QueueFull."""
        job = self.factory.create(title, prompts, duration_s=duration_s)
        self.factory.mark_queued(job)
        self.queue.enqueue(job.id)  # errors.QueueFull, если переполнена
        events.emit("video.job", job_id=job.id, state=job.state.value, queued=True)
        return job

    # --- жизненный цикл -----------------------------------------------------

    async def start(self) -> None:
        """Создать корень, свести незавершённое, поднять воркеров. Идемпотентно."""
        self.ensure_root()
        self.reconcile()
        if self._running:
            return
        self._running = True
        for i in range(self._nworkers):
            self._workers.append(
                asyncio.create_task(self._worker_loop(), name=f"video_factory.worker.{i}")
            )

    async def stop(self) -> None:
        """Снять воркеров и освободить любые удержанные аренды. Идемпотентна."""
        self._running = False
        for t in self._workers:
            if not t.done():
                t.cancel()
        for t in self._workers:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        self._workers.clear()
        # Ни одной осиротевшей брони: finally в _generate_once уже снимает аренду,
        # но снимаем всё ещё раз на случай отмены между acquire и try.
        if self._factory is not None:
            self._factory.release_all_leases()

    def reconcile(self) -> None:
        """Пометить джобы, зависшие в RUNNING (после аварии), как INTERRUPTED и
        сбросить их RUNNING-сцены в PLANNED, чтобы возобновление сгенерировало
        НОВЫЙ дубль, а не считало старую попытку завершённой."""
        for job in self.factory.iter_jobs():
            if job.state == JobState.RUNNING:
                for s in job.scenes:
                    if s.status == SCENE_RUNNING:
                        s.status = SCENE_PLANNED
                job.state = JobState.INTERRUPTED
                self.factory.save(job)
                events.emit("video.job", job_id=job.id, state=job.state.value, reconciled=True)
                _log.info("reconciled interrupted video job %s", job.id)

    async def _worker_loop(self) -> None:
        """Достаём job_id из ограниченной очереди и исполняем джобу. Ошибки
        логируем — воркер не должен падать; backpressure (ResourceExhausted)
        возвращает джобу в очередь с задержкой."""
        while self._running:
            try:
                job_id = await self.queue.get()
            except asyncio.CancelledError:
                raise
            requeued = False
            try:
                from .. import errors

                job = self.factory.load(job_id)
                await self.factory.run_job(job)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                from .. import errors

                if isinstance(exc, errors.ResourceExhausted):
                    # backpressure: вернуть в очередь (если есть место) и подождать
                    with contextlib.suppress(Exception):
                        self.queue.enqueue(job_id)
                        requeued = True
                    await asyncio.sleep(self._backoff)
                else:
                    _log.warning("video job %s failed in worker: %s", job_id, exc)
            finally:
                self.queue.task_done()
            _ = requeued
