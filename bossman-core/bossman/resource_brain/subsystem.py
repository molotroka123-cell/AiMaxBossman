"""Подсистема Resource Brain для реестра жизненного цикла (lifecycle.Subsystem).

`critical=False` — сервер обязан подниматься, даже если проба деградирует
(измерение ресурсов не должно ронять ядро). `validate()` разово проверяет, что
проба работает; `start()` поднимает фоновый цикл, эмитящий `resource.snapshot`;
`stop()` отменяет цикл и идемпотентна.
"""
from __future__ import annotations

import asyncio
import contextlib
import os

from ..obs import get_logger
from .. import events
from .brain import ResourceBrain
from .probe import ProbeAdapter, detect_probe

_log = get_logger("bossman.resource_brain")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


class ResourceBrainSubsystem:
    """Обёртка Subsystem вокруг единого `ResourceBrain` и пробы."""

    name = "resource_brain"
    critical = False

    def __init__(
        self,
        brain: ResourceBrain,
        probe: ProbeAdapter | None = None,
        *,
        interval: float | None = None,
    ) -> None:
        self._brain = brain
        self._probe = probe or detect_probe()
        # Интервал пробы: не слишком часто (шум в шине), настраивается env.
        self._interval = interval if interval is not None else _env_float(
            "BOSSMAN_RB_PROBE_INTERVAL", 5.0
        )
        self._task: asyncio.Task | None = None

    # --- Subsystem ----------------------------------------------------------

    async def validate(self) -> None:
        """Разовая проверка: проба снимает снимок и он ложится в brain."""
        snap = self._probe.snapshot(self._brain.residency.as_tuple())
        self._brain.set_snapshot(snap)
        _log.info("resource_brain validated via probe=%s", self._probe.name)

    async def start(self) -> None:
        """Поднять фоновый цикл пробы. Идемпотентно (повторный start — no-op)."""
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop(), name="resource_brain.probe")

    async def stop(self) -> None:
        """Отменить цикл и дождаться его завершения. Идемпотентна: повторный
        вызов при уже снятой задаче ничего не делает и не бросает."""
        task = self._task
        self._task = None
        if task is None:
            return
        if not task.done():
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

    # --- фоновый цикл -------------------------------------------------------

    async def _loop(self) -> None:
        """Периодически снимать снимок, подметать протухшие брони и эмитить
        `resource.snapshot`. Ошибка пробы логируется и цикл продолжается — она
        НЕ должна ронять процесс. Первый снимок эмитится сразу, до первой паузы,
        чтобы подписчик получил событие без ожидания целого интервала."""
        try:
            while True:
                try:
                    snap = self._probe.snapshot(self._brain.residency.as_tuple())
                    self._brain.set_snapshot(snap)
                    self._brain.sweep()
                    events.emit("resource.snapshot", **snap.to_event())
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 — проба не должна ронять ядро
                    _log.warning("resource probe loop error: %s", exc)
                await asyncio.sleep(self._interval)
        except asyncio.CancelledError:
            pass  # штатная остановка через stop()
