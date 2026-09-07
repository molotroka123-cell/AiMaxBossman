"""Мягкая остановка фоновых петель: сначала попросить, потом рвать.

Зачем. Фоновая петля (планировщик, сэмплер метрик, тик фичи) почти всегда
что-то пишет в базу. `task.cancel()` во время такого запроса рвёт соединение
внутри драйвера, и вернуть его в пул после этого нельзя ничем: SQLAlchemy само
не знает, в каком оно состоянии, а `dispose()` до выданных соединений не
достаёт. Каждая остановка оставляла так по паре соединений на петлю — сборщик
мусора потом ругался «aiosqlite.Connection was deleted before being closed».

Поэтому у остановки две фазы. Сначала поднимается флаг, и петля выходит сама —
в своей же точке, дописав начатое. И только те, кто за отведённое время не
вышел, отменяются жёстко, как и раньше: остановка обязана быть конечной.

Флаг проверяется в двух местах: перед работой и вместо `sleep`. Спать простым
`asyncio.sleep` нельзя — петля с шагом в минуту узнала бы об остановке через
минуту, и мягкая фаза выродилась бы в ожидание предела.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone


async def sleep_or_stop(stop: asyncio.Event | None, seconds: float) -> bool:
    """Поспать `seconds` или проснуться раньше по флагу.

    Возвращает True, если пора выходить.
    """
    if stop is None:
        await asyncio.sleep(seconds)
        return False
    if stop.is_set():
        return True
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
        return True
    except asyncio.TimeoutError:
        return False


def stopping(stop: asyncio.Event | None) -> bool:
    return stop is not None and stop.is_set()


# ---------- V6 §A: трасса старта ----------

class StartupTrace:
    """Измеренные фазы старта процесса (`Services.start`).

    Что записываем: имя фазы и её длительность по монотонным часам, в порядке
    выполнения. Фаза, упавшая исключением, записывается с `error`, чтобы в
    отчёте было видно, ГДЕ старт сломался, а не только что он сломался.

    Чего НЕ делаем (V6 «no invented numbers»): пока `finish()` не вызван,
    `ready` = False и `total_ms` = None — отсутствие измерения не выдаётся за
    ноль. Трасса неизменяема после `finish()`: повторный старт того же
    процесса заводит НОВУЮ трассу, а не дописывает старую.
    """

    def __init__(self) -> None:
        self.phases: list[dict] = []
        self.ready = False
        self._t0: float | None = None
        self.total_ms: float | None = None
        self.ready_at: str | None = None

    def begin(self) -> None:
        self._t0 = time.perf_counter()

    def phase(self, name: str) -> "_Phase":
        if self.ready:
            raise RuntimeError("startup trace is finished; phases are immutable")
        return _Phase(self, name)

    def finish(self) -> None:
        if self._t0 is None:
            raise RuntimeError("startup trace was never begun")
        if self.ready:
            return
        self.total_ms = round((time.perf_counter() - self._t0) * 1000, 2)
        self.ready_at = datetime.now(timezone.utc).isoformat()
        self.ready = True

    def to_dict(self) -> dict:
        return {"ready": self.ready, "total_ms": self.total_ms, "ready_at": self.ready_at,
                "phases": [dict(p) for p in self.phases]}


class _Phase:
    def __init__(self, trace: StartupTrace, name: str) -> None:
        self._trace = trace
        self._name = name
        self._start = 0.0

    async def __aenter__(self) -> "_Phase":
        self._start = time.perf_counter()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        row: dict = {"name": self._name,
                     "ms": round((time.perf_counter() - self._start) * 1000, 2)}
        if exc is not None:
            row["error"] = f"{type(exc).__name__}: {exc}"[:300]
        self._trace.phases.append(row)
        return False
