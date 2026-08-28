"""Внешняя отправка человеку: защита от дубля обязана переживать перезапуск.

Раздел 2 мастер-аудита: дедупликация в памяти процесса НЕ годится. Процесс
умирает между «отправили» и «записали, что отправили» — и это ровно тот момент,
когда защита нужна. После рестарта память пуста, а сообщение уже ушло.

Тесты ниже написаны ДО правки и падают на текущем коде.
"""
from __future__ import annotations

import asyncio

import pytest

from bcc.features import tools_openclaw as oc
from bcc.v2.openclaw_bridge import idempotency_key

CANARY = "BOSSMAN_CANARY_SECRET_91f03f_DO_NOT_LEAK"


def test_key_is_derived_from_payload_not_from_provider_call_id():
    """`call_id` выдаёт провайдер модели, и он НЕ переживает наш повтор.

    После падения до чекпоинта движок возвращает run в очередь, модель повторяет
    вызов, провайдер выдаёт новый id. Ключ на call_id становится другим, и
    человек получает второе сообщение. Ключ на payload — тот же.
    """
    a = idempotency_key(mission_id=7, run_id=42, call_id="провайдер-выдал-первый",
                        payload={"channel": "telegram", "contact": "@owner",
                                 "message": "привет"})
    b = idempotency_key(mission_id=7, run_id=42, call_id="провайдер-выдал-второй",
                        payload={"channel": "telegram", "contact": "@owner",
                                 "message": "привет"})
    assert a == b, "ключ изменился от смены call_id — повтор уйдёт вторым сообщением"

    other = idempotency_key(mission_id=7, run_id=42, call_id="провайдер-выдал-первый",
                            payload={"channel": "telegram", "contact": "@client",
                                     "message": "привет"})
    assert a != other, "разным получателям достался один ключ"


def test_send_handler_actually_passes_the_payload():
    """Дефект: функция научилась выводить ключ из payload, но боевой вызов
    по-прежнему передавал call_id. Защита была описана и не подключена."""
    import inspect
    src = inspect.getsource(oc._tool_send)
    assert "payload=" in src, (
        "обработчик отправки не передаёт payload в idempotency_key — "
        "ключ выводится из call_id провайдера и не переживает повтор")


async def test_outbox_survives_process_restart(env):
    """Память процесса очищается перезапуском, таблица — нет."""
    from bcc.features.tools_openclaw import outbox_reserve, outbox_mark_sent

    key = "bossman-test-restart"
    first = await outbox_reserve(env.svc, key=key, channel="telegram",
                                 contact="@owner", body="привет", run_id=1)
    assert first.fresh is True
    await outbox_mark_sent(env.svc, key=key, result={"delivered": True})

    # «перезапуск»: всё, что жило в памяти, забыто
    oc._RESERVED = {} if hasattr(oc, "_RESERVED") else None
    second = await outbox_reserve(env.svc, key=key, channel="telegram",
                                  contact="@owner", body="привет", run_id=1)
    assert second.fresh is False, "после перезапуска повтор считается новой отправкой"
    assert second.state == "SENT"
    assert second.result == {"delivered": True}


async def test_two_workers_race_and_exactly_one_wins(env):
    from bcc.features.tools_openclaw import outbox_reserve

    key = "bossman-test-race"
    results = await asyncio.gather(*(
        outbox_reserve(env.svc, key=key, channel="telegram", contact="@owner",
                       body="привет", run_id=1) for _ in range(8)))
    winners = [r for r in results if r.fresh]
    assert len(winners) == 1, f"победителей должно быть ровно один, а их {len(winners)}"


async def test_timeout_after_possible_delivery_is_unknown_not_retry(env):
    """Обрыв ПОСЛЕ отправки тела запроса — не отказ. Слепой повтор запрещён."""
    from bcc.features.tools_openclaw import outbox_reserve, outbox_mark_unknown

    key = "bossman-test-unknown"
    await outbox_reserve(env.svc, key=key, channel="telegram", contact="@owner",
                         body="привет", run_id=1)
    await outbox_mark_unknown(env.svc, key=key, detail="соединение закрыто после запроса")

    again = await outbox_reserve(env.svc, key=key, channel="telegram",
                                 contact="@owner", body="привет", run_id=1)
    assert again.fresh is False
    assert again.state == "UNKNOWN"
    assert again.needs_human is True, "неизвестное состояние обязано требовать человека"


async def test_same_key_different_body_is_a_collision(env):
    """Одобрение выдано на конкретный текст конкретному человеку."""
    from bcc.features.tools_openclaw import OutboxCollision, outbox_reserve

    key = "bossman-test-collision"
    await outbox_reserve(env.svc, key=key, channel="telegram", contact="@owner",
                         body="привет", run_id=1)
    with pytest.raises(OutboxCollision):
        await outbox_reserve(env.svc, key=key, channel="telegram", contact="@owner",
                             body="совсем другой текст", run_id=1)
    with pytest.raises(OutboxCollision):
        await outbox_reserve(env.svc, key=key, channel="telegram", contact="@client",
                             body="привет", run_id=1)


async def test_outbox_never_stores_the_message_body_in_clear(env):
    """Текст сообщения человеку — не то, что нужно хранить вечно в журнале."""
    import sqlalchemy as sa
    from bcc.features.tools_openclaw import channel_outbox, outbox_reserve

    await outbox_reserve(env.svc, key="bossman-test-canary", channel="telegram",
                         contact="@owner", body=CANARY, run_id=1)
    async with env.svc.db.session() as s:
        rows = (await s.execute(sa.select(channel_outbox))).mappings().all()
    dump = repr([dict(r) for r in rows])
    assert CANARY not in dump, "тело сообщения легло в таблицу открытым текстом"
