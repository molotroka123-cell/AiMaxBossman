"""SECREM: payload события проходит `plugin_security.redact` ДО персиста в
таблицу events и ДО broadcast в WS-очереди подписчиков.

Канарейка BOSSMAN_TEST_SECRET_9F31A7 под ключом api_key (и на глубине) не
должна оказаться ни в строке events, ни в сообщении очереди, ни в recent().
"""
from __future__ import annotations

import json

import sqlalchemy as sa

from bcc.db import Database, events as events_t
from bcc.events import EventBus

CANARY = "BOSSMAN_TEST_SECRET_9F31A7"


async def _db(tmp_path) -> Database:
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'ev.sqlite'}")
    await db.create_all()
    return db


async def test_emit_redacts_before_persist_and_broadcast(tmp_path):
    db = await _db(tmp_path)
    bus = EventBus(db)
    q = bus.subscribe()

    msg = await bus.emit("provider.updated", provider="openai", api_key=CANARY,
                         nested={"Authorization": f"Bearer {CANARY}", "note": "ok"},
                         items=[{"refresh_token": CANARY}])

    # 1) возвращённое и разосланное сообщение
    ws_msg = q.get_nowait()
    assert ws_msg is msg
    assert CANARY not in json.dumps(ws_msg, ensure_ascii=False)
    assert ws_msg["api_key"] == "***REDACTED***"
    assert ws_msg["nested"]["Authorization"] == "***REDACTED***"
    assert ws_msg["items"][0]["refresh_token"] == "***REDACTED***"
    assert ws_msg["nested"]["note"] == "ok" and ws_msg["provider"] == "openai"  # не-секреты целы
    assert ws_msg["kind"] == "provider.updated"

    # 2) строка в таблице events
    async with db.session() as s:
        rows = (await s.execute(sa.select(events_t))).fetchall()
    assert len(rows) == 1
    stored = rows[0]._mapping
    assert CANARY not in json.dumps(stored["data"], ensure_ascii=False, default=str)
    assert stored["data"]["api_key"] == "***REDACTED***"

    # 3) история для новых подписчиков
    recent = await bus.recent()
    assert recent and CANARY not in json.dumps(recent, ensure_ascii=False, default=str)
    await db.engine.dispose()


async def test_transient_event_is_redacted_on_ws_path_too(tmp_path):
    """TRANSIENT-виды в БД не пишутся, но в WS-ленту идут — тоже через redact."""
    db = await _db(tmp_path)
    bus = EventBus(db)
    q = bus.subscribe()
    await bus.emit("run.log", line="x", token=CANARY)
    assert CANARY not in json.dumps(q.get_nowait(), ensure_ascii=False)
    async with db.session() as s:
        assert (await s.execute(sa.select(sa.func.count()).select_from(events_t))).scalar() == 0
    await db.engine.dispose()


async def test_bus_without_db_still_redacts():
    bus = EventBus(None)
    q = bus.subscribe()
    await bus.emit("x", password=CANARY)
    assert q.get_nowait()["password"] == "***REDACTED***"
