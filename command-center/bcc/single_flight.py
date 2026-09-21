"""Ожидание общей работы, которую не отменяет уход одного из ожидающих.

Приём один и тот же в шести местах продукта: одна задача делается один раз, а
ждут её несколько (две вкладки открыли /api/apps, сэмплер и HTTP-обработчик
читают метрики). Ушедший клиент не должен рвать работу остальным, поэтому
раньше здесь стоял `asyncio.shield`.

На Python 3.14 `shield` перестал годиться. Когда внешний future отменён,
`_outer_done_callback` вешает на внутреннюю задачу `_log_on_exception`, и та
БЕЗУСЛОВНО зовёт `loop.call_exception_handler` с сообщением
`"<Ошибка> exception in shielded future"` — даже если владелец задачи уже
забрал исключение своим колбэком. На 3.11 и 3.13 такого колбэка нет.

Проверено прямым прогоном одного и того же кода:
    3.11.15 errors: []
    3.13.12 errors: []
    3.14.0rc2 errors: ['OSError exception in shielded future']
и в CI: command-center pytest (py3.14), job 105833218232 —
`test_cancelled_all_waiters_do_not_leave_unhandled_exceptions` красный ровно
этим контекстом, на 3.11 и 3.12 зелёный.

`await_shared` даёт то же свойство «отмена ожидающего не отменяет работу», но
исход общей задачи доставляется через собственный future ожидающего. Никакого
`shield` — значит, и 3.14-колбэка нет. Исключение общей задачи забирается
всегда, даже когда последний ожидающий уже отменён: тогда оно по построению
никому не адресовано (следующий вызов заведёт новую задачу), а предупреждение
«Task exception was never retrieved» не появляется. Если отказ общей работы
нужно ЗАПИСАТЬ, это делает владелец задачи своим `add_done_callback` — как
`MetricsSampler._read_finished`, — а не это ожидание.
"""
from __future__ import annotations

import asyncio
from typing import Any

__all__ = ["await_shared"]


async def await_shared(task: "asyncio.Future[Any]") -> Any:
    """Дождаться чужой задачи, не отменяя её вместе с собой.

    Отмена ожидающего поднимает у него `CancelledError`, а `task` продолжает
    работу. Отмена самой `task` доходит до всех ожидающих отменой.
    """
    if task.done():
        return task.result()
    waiter: asyncio.Future[Any] = asyncio.get_running_loop().create_future()

    def _relay(finished: "asyncio.Future[Any]") -> None:
        if finished.cancelled():
            if not waiter.done():
                waiter.cancel()
            return
        # Забирается ДО проверки ожидающего: колбэк остаётся висеть на задаче и
        # после отмены ожидающего — это и есть гарантия, что осиротевший отказ
        # не станет предупреждением сборщика мусора.
        error = finished.exception()
        if waiter.done():
            return
        if error is not None:
            waiter.set_exception(error)
        else:
            waiter.set_result(finished.result())

    # Колбэк НЕ снимается при отмене ожидающего: именно он забирает исход
    # осиротевшей задачи. Держится он не дольше самой задачи.
    task.add_done_callback(_relay)
    return await waiter
