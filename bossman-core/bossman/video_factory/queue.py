"""Ограниченная очередь джоб/сцен: фикс-размер asyncio.Queue + воркеры.

Ограничение размера — защита бокса от OOM: неограниченный fan-out видео-джоб
съел бы память/диск. Переполнение → `errors.QueueFull` (503, retryable), а не
рост в бесконечность.
"""
from __future__ import annotations

import asyncio

from .. import errors


class BoundedJobQueue:
    """Тонкая обёртка над `asyncio.Queue(maxsize)`: enqueue переводит
    `QueueFull` в доменную `errors.QueueFull`."""

    def __init__(self, maxsize: int = 8) -> None:
        self.maxsize = max(1, int(maxsize))
        self._q: asyncio.Queue[str] = asyncio.Queue(maxsize=self.maxsize)

    def enqueue(self, job_id: str) -> None:
        """Положить джобу в очередь без ожидания. Полна → `errors.QueueFull`."""
        try:
            self._q.put_nowait(job_id)
        except asyncio.QueueFull as exc:
            raise errors.QueueFull(
                f"video job queue full (maxsize={self.maxsize})",
                extra={"maxsize": self.maxsize},
            ) from exc

    async def get(self) -> str:
        return await self._q.get()

    def task_done(self) -> None:
        self._q.task_done()

    def qsize(self) -> int:
        return self._q.qsize()

    def full(self) -> bool:
        return self._q.full()
