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
