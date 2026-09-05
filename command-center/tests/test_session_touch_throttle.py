"""Отметка `last_seen` не должна стоить записи в БД на каждый запрос.

Открытие панели — это полтора-три десятка параллельных вызовов /api, и каждый
делал UPDATE + COMMIT. На SQLite запись сериализует всё остальное, поэтому
запросы ждали не своих данных, а чужого коммита."""
from __future__ import annotations

import sqlalchemy as sa

from bcc.db import sessions as sessions_t


async def test_repeated_requests_write_last_seen_once(env):
    store = env.svc.sessions
    sess = await store.create("тест")
    sid = sess["id"]

    async def stamp():
        async with env.svc.db.session() as s:
            row = (await s.execute(sa.select(sessions_t.c.last_seen)
                                   .where(sessions_t.c.id == sid))).first()
        return row[0]

    await store.touch(sid)
    first = await stamp()
    for _ in range(30):                      # столько же, сколько даёт одна загрузка панели
        await store.touch(sid)
    assert await stamp() == first, "каждый запрос всё ещё пишет в БД"

    # интервал прошёл — отметка обновляется, поле не «замерзает» навсегда
    store._touched[sid] = store._touched[sid] - store.TOUCH_INTERVAL_S - 1
    await store.touch(sid)
    assert await stamp() > first


async def test_revoked_session_forgets_its_throttle(env):
    """Отзыв снимает отметку: иначе восстановленная запись могла бы пропустить
    первую запись после возвращения."""
    store = env.svc.sessions
    sid = (await store.create("тест"))["id"]
    await store.touch(sid)
    assert sid in store._touched
    await store.revoke(sid)
    assert sid not in store._touched
