"""Очередь подтверждений: необратимое — только с подтверждением (принцип 5).

Действие с пометкой confirm не выполняется без нажатия; отклонённое — не выполняется
никогда. Раннер задачи ждёт решения; решение приходит из UI или Telegram."""
from __future__ import annotations

import asyncio
import os
from typing import Any

from . import db, events, telegram

#: Сколько ждать решения, если вызывающий не указал иначе. Сутки — сознательно
#: щедрое окно: сузить его значит отнять у владельца право решить позже, а не
#: починить что-либо. Вынесено в переменную окружения, потому что «правильный»
#: срок зависит от того, кто и как быстро отвечает, а не от кода.
DEFAULT_TIMEOUT_SECONDS = 24 * 3600
TIMEOUT_ENV = "BOSSMAN_APPROVAL_TIMEOUT_SECONDS"

#: Шаг опроса строки: сначала часто (решение, принятое в первые секунды, должно
#: подхватываться почти сразу), дальше реже. Прежний плоский шаг в 2 c стоил
#: 43 200 запросов на одно суточное ожидание и ничего не давал после первой
#: минуты.
POLL_MIN_SECONDS = 0.5
POLL_MAX_SECONDS = 15.0

#: Отметки прожитого ожидания, на которых запрос кладётся владельцу ЗАНОВО.
#: Это и есть главный отказ этого места: `ask_approval` уходит один раз, при
#: создании, и если то сообщение потеряно (диспетчер лежал, чат заглушен,
#: сообщение пролистано) — задача досиживает весь таймаут, и никто больше не
#: спрошен. Повторный вопрос не расширяет прав: это тот же вопрос ещё раз.
REMIND_AFTER_SECONDS = (60, 300, 900, 3600, 6 * 3600, 12 * 3600)


async def create(kind: str, preview: str, *, task_id: int | None = None,
                 run_id: int | None = None, tool: str | None = None,
                 payload: dict | None = None) -> int:
    row = await db.fetchrow(
        """INSERT INTO approvals (task_id, run_id, kind, tool, payload, preview)
           VALUES ($1,$2,$3,$4,$5,$6) RETURNING id""",
        task_id, run_id, kind, tool, payload, preview)
    approval_id = row["id"]
    events.emit("approval.created", id=approval_id, kind=kind, tool=tool, preview=preview[:500])
    await telegram.ask_approval(approval_id, preview)
    return approval_id


async def decide(approval_id: int, approve: bool, decided_by: str) -> dict | None:
    row = await db.fetchrow(
        """UPDATE approvals SET status=$2, decided_by=$3, decided_at=now()
           WHERE id=$1 AND status='pending' RETURNING *""",
        approval_id, "approved" if approve else "rejected", decided_by)
    if row:
        events.emit("approval.decided", id=approval_id, status=row["status"], by=decided_by)
    return row


def _default_timeout() -> int:
    """Таймаут по умолчанию из окружения; мусор в переменной — не повод падать
    и не повод молча ждать вечно, поэтому откат на суточное значение."""
    try:
        value = int(os.getenv(TIMEOUT_ENV, "") or DEFAULT_TIMEOUT_SECONDS)
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS
    return value if value >= 0 else DEFAULT_TIMEOUT_SECONDS


def _field(row: Any, name: str) -> Any:
    """Строка приходит и как asyncpg.Record, и как dict (тесты, фейки)."""
    try:
        return row[name]
    except (KeyError, IndexError, TypeError):
        return None


async def _remind(approval_id: int, row: Any, waited: float) -> None:
    """Спросить владельца ещё раз и сказать вслух, сколько уже ждём."""
    events.emit("approval.waiting", id=approval_id, waited_s=int(waited),
                approval_kind=_field(row, "kind"), tool=_field(row, "tool"))
    try:
        await telegram.ask_approval(approval_id, str(_field(row, "preview") or ""))
    except Exception as exc:                                  # noqa: BLE001
        # Сломанный канал уведомлений не должен снимать ожидание: тогда
        # неотвеченное подтверждение превратилось бы в выполненное действие.
        # Факт неудачи виден в потоке событий, ожидание продолжается.
        events.emit("approval.remind_failed", id=approval_id,
                    error=f"{type(exc).__name__}: {exc}")


async def wait(approval_id: int, timeout_s: int | None = None) -> dict:
    """Ждать решения. Пока висит — задача в waiting_approval, ничего не выполняется.

    Ожидание молчаливым не бывает: на отметках REMIND_AFTER_SECONDS запрос
    уходит владельцу заново и в поток событий идёт approval.waiting с прожитым
    временем. Ограничено оно двумя счётчиками сразу — суммой запрошенных пауз и
    настоящими часами: прежний `range(timeout_s // 2)` со `sleep(2)` не считал
    время запроса к БД, поэтому реальный таймаут был заметно больше
    номинального.
    """
    timeout = _default_timeout() if timeout_s is None else max(0, int(timeout_s))
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    reminders = [mark for mark in REMIND_AFTER_SECONDS if mark < timeout]
    elapsed = 0.0
    delay = POLL_MIN_SECONDS
    while True:
        row = await db.fetchrow("SELECT * FROM approvals WHERE id=$1", approval_id)
        if row is None:
            # Строки нет — решать нечего и некому. Раньше это крутилось до
            # самого таймаута и лишь потом отдавало тот же синтетический ответ.
            events.emit("approval.missing", id=approval_id)
            return {"id": approval_id, "status": "expired"}
        if row["status"] != "pending":
            return row
        now = loop.time()
        if elapsed >= timeout or now >= deadline:
            break
        waited = max(elapsed, now - (deadline - timeout))
        while reminders and waited >= reminders[0]:
            reminders.pop(0)
            await _remind(approval_id, row, waited)
        pause = max(0.0, min(delay, timeout - elapsed, deadline - now))
        await asyncio.sleep(pause)
        elapsed += pause
        delay = min(delay * 2, POLL_MAX_SECONDS)
    # Гонка на границе таймаута: decide() мог записать решение ровно перед этим
    # UPDATE. Тогда строка уже не pending, UPDATE ничего не меняет, и возвращать
    # литерал "expired" нельзя — это стёрло бы настоящее решение владельца из
    # ответа исполнителю. Возвращаем факт из строки, а не из намерения.
    await db.execute("UPDATE approvals SET status='expired' WHERE id=$1 AND status='pending'",
                     approval_id)
    row = await db.fetchrow("SELECT * FROM approvals WHERE id=$1", approval_id)
    return row or {"id": approval_id, "status": "expired"}
