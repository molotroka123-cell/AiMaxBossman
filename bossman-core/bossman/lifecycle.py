"""Реестр подсистем (общий шов для этапов 4–7).

Раньше startup()/shutdown() в api.py правились руками под каждую новую
подсистему (браузер, gateway, context_engine) — это не масштабируется и легко
забыть закрыть ресурс. Теперь каждая подсистема регистрирует
validate/start/stop, а api.py только прогоняет реестр.

Семантика надёжности:
- `critical=True` — падение validate()/start() ПРЕРЫВАЕТ загрузку (например,
  Postgres: без него ядро работать не может).
- `critical=False` — падение логируется, подсистема помечается `degraded`, но
  ядро поднимается (например, Video/Remote: сервер живёт и без них).
Остановка — в обратном порядке, каждая stop() в try/except (одна упавшая
не должна мешать остальным закрыться).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from .errors import BossmanError

log = logging.getLogger("bossman.lifecycle")


@runtime_checkable
class Subsystem(Protocol):
    name: str
    critical: bool

    async def validate(self) -> None:
        """Проверка предпосылок (доступность зависимостей, схема БД). Бросает при
        неготовности. Не должна иметь побочных эффектов, кроме создания схемы."""

    async def start(self) -> None:
        """Запуск фоновых задач/пулов. Идемпотентно там, где возможно."""

    async def stop(self) -> None:
        """Грациозная остановка. Обязана быть идемпотентной и не бросать на
        повторный вызов."""


@dataclass
class SubsystemState:
    subsystem: Subsystem
    started: bool = False
    degraded: bool = False
    error: str | None = None


class SubsystemRegistry:
    def __init__(self) -> None:
        self._states: list[SubsystemState] = []

    def register(self, sub: Subsystem) -> None:
        if any(s.subsystem.name == sub.name for s in self._states):
            raise ValueError(f"подсистема уже зарегистрирована: {sub.name}")
        self._states.append(SubsystemState(sub))

    def clear(self) -> None:
        """Только для тестов: сбросить реестр."""
        self._states.clear()

    async def start_all(self) -> None:
        for st in self._states:
            sub = st.subsystem
            try:
                await sub.validate()
                await sub.start()
                st.started = True
                st.degraded = False
                st.error = None
                log.info("subsystem started: %s", sub.name)
            except BaseException as exc:  # noqa: BLE001 — критичная подсистема должна ронять boot
                st.error = f"{type(exc).__name__}: {exc}"
                if getattr(sub, "critical", False):
                    log.error("critical subsystem failed, aborting boot: %s (%s)", sub.name, st.error)
                    # уже поднятые останавливаем перед пробросом
                    await self.stop_all()
                    if isinstance(exc, BossmanError):
                        raise
                    raise BossmanError(f"critical subsystem '{sub.name}' failed: {st.error}") from exc
                st.degraded = True
                log.warning("optional subsystem degraded: %s (%s)", sub.name, st.error)

    async def stop_all(self) -> None:
        for st in reversed(self._states):
            if not st.started:
                continue
            try:
                await st.subsystem.stop()
                st.started = False
                log.info("subsystem stopped: %s", st.subsystem.name)
            except Exception as exc:  # noqa: BLE001 — одна упавшая stop() не мешает остальным
                log.warning("subsystem stop failed: %s (%s)", st.subsystem.name, exc)

    def status(self) -> list[dict]:
        return [
            {
                "name": st.subsystem.name,
                "critical": getattr(st.subsystem, "critical", False),
                "started": st.started,
                "degraded": st.degraded,
                "error": st.error,
            }
            for st in self._states
        ]


# Единый реестр процесса ядра. Подсистемы этапов 4–7 регистрируются в api.py
# ДО startup(), а startup() вызывает registry.start_all().
registry = SubsystemRegistry()
