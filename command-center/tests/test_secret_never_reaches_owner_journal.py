"""Ключ владельца не доходит ни до живой ленты, ни до таблиц истории.

BL-099. Чистка шины работала по ИМЕНАМ полей (`api_key`, `token`, …), а ключ
приезжает внутри ЗНАЧЕНИЯ: предпросмотр команды на подтверждение собирается из
самой команды владельца (`bcc/features/terminal.py`), кладётся в `preview` и
уходит и подписчикам WS, и в таблицу `events`. Имя поля при этом безобидное.
Распознаватель по ВИДУ токена у продукта уже был (`plugin_security.redact_text`,
им чистится диагностический архив) — он просто не был подключён к этим двум
путям.

Проверка не слепая: контрольный тест показывает, что чистка ТОЛЬКО по именам
ключей этот же секрет пропускает. Без него «не нашли» ничего бы не значило.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from bcc import engine as engine_module
from bcc.events import EventBus
from bcc.plugin_security import redact

# Собирается из кусков: целиком в исходнике не лежит и сканеру секретов не виден.
OWNER_KEY = "sk-" + "or-v1-" + "0badc0de0badc0de0badc0de0badc0de0bad"
PREVIEW = f"[host] curl -H 'Authorization: Bearer {OWNER_KEY}' https://openrouter.ai/api/v1/me"


class Recorder:
    """Не приёмка SQLite: наблюдаем ровно то, что продукт кладёт в строку."""

    def __init__(self):
        self.rows: list[dict] = []

    def session(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, statement):
        self.rows.append(dict(statement.compile().params))
        return SimpleNamespace(first=lambda: None, fetchall=lambda: [])

    async def commit(self):
        return None


def _all_text(*objects) -> str:
    """Оба представления JSON: ensure_ascii прячет кириллицу, но не латиницу ключа."""
    dumped = [json.dumps(o, ensure_ascii=ascii_, default=str)
              for o in objects for ascii_ in (True, False)]
    return "\n".join(dumped)


def test_the_key_name_scrubber_alone_lets_this_secret_through():
    """Контроль: если бы он ловил, тесты ниже проходили бы и без починки."""
    leaked = redact({"preview": PREVIEW})
    assert OWNER_KEY in _all_text(leaked), "контроль устарел: секрет ловится и по имени поля"


def test_a_secret_inside_a_value_reaches_neither_the_feed_nor_the_events_table():
    async def run():
        db = Recorder()
        bus = EventBus(db)
        queue = bus.subscribe()
        message = await bus.emit("approval.created", id=7, tool="terminal", preview=PREVIEW)
        live = [queue.get_nowait() for _ in range(queue.qsize())]
        assert live, "подписчик не получил событие — проверять нечего"
        assert db.rows, "строка истории не записана — проверять нечего"
        haystack = _all_text(message, live, db.rows)
        assert OWNER_KEY not in haystack, "ключ владельца в ленте или в истории"
        assert "REDACTED" in haystack, "предпросмотр исчез целиком вместо чистки"
        # Чистка не съедает остальное: владельцу видно, ЧТО именно он одобряет.
        assert "curl" in haystack and "openrouter.ai" in haystack
    asyncio.run(run())


def test_a_run_log_line_is_scrubbed_before_it_reaches_run_events():
    async def run():
        db = Recorder()
        bus = EventBus(None)
        seen = bus.subscribe()
        task_engine = engine_module.TaskEngine.__new__(engine_module.TaskEngine)
        task_engine.db, task_engine.bus = db, bus
        await task_engine._log(1, "info", "tool", f"вывод инструмента: {PREVIEW}",
                               {"команда": PREVIEW})
        live = [seen.get_nowait() for _ in range(seen.qsize())]
        assert db.rows and live, "ни строки run_events, ни события — проверять нечего"
        haystack = _all_text(db.rows, live)
        assert OWNER_KEY not in haystack, "ключ владельца в строке журнала прогона"
        assert "REDACTED" in haystack
    asyncio.run(run())
