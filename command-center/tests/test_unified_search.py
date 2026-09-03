"""Unified Search: одна точка поиска по всему, что система записала.

Тесты проверяют не форму ответа, а обещания модуля: происхождение у каждой
строки, безопасность запроса (экранирование LIKE и параметризация) и честность
усечения — «нашлось 3 из многих» не должно выглядеть как «нашлось всё».
"""
from __future__ import annotations

import sqlalchemy as sa

from bcc.db import (approvals as approvals_t, missions as missions_t,
                    run_events as run_events_t, task_runs as runs_t, tasks as tasks_t, utcnow)
from bcc.features import unified_search

MARK = "zqmarker"


async def _seed(env, mark: str = MARK) -> dict:
    """По одной строке в каждый источник, все — через env.svc."""
    await env.svc.bus.emit("unified.probe", note=f"{mark} in activity event")
    ids: dict[str, int] = {}
    async with env.svc.db.session() as s:
        r = await s.execute(sa.insert(tasks_t).values(
            title=f"{mark} task title", prompt=f"prompt with {mark} inside", status="draft"))
        ids["task"] = int(r.inserted_primary_key[0])
        r = await s.execute(sa.insert(runs_t).values(
            task_id=ids["task"], attempt=0, status="completed",
            result=f"run result mentioning {mark}", started_at=utcnow(), finished_at=utcnow()))
        ids["run"] = int(r.inserted_primary_key[0])
        r = await s.execute(sa.insert(run_events_t).values(
            run_id=ids["run"], kind="log", message=f"log line about {mark}"))
        ids["run_event"] = int(r.inserted_primary_key[0])
        r = await s.execute(sa.insert(approvals_t).values(
            task_id=ids["task"], kind="review", preview=f"approve {mark} change", status="pending"))
        ids["approval"] = int(r.inserted_primary_key[0])
        r = await s.execute(sa.insert(missions_t).values(
            title=f"{mark} mission", goal=f"goal referencing {mark}", status="draft"))
        ids["mission"] = int(r.inserted_primary_key[0])
        await s.commit()
    return ids


async def _search(env, q: str, **params) -> tuple[int, dict]:
    query = {"q": q, **params}
    r = await env.client.get("/api/search", params=query)
    return r.status_code, r.json()


async def test_flag_off_answers_but_does_not_search(env, monkeypatch):
    """Выключенный флаг: ручка читается, но поиска нет — ни результатов, ни ошибок."""
    monkeypatch.delenv(unified_search.FLAG, raising=False)
    await _seed(env)
    code, body = await _search(env, MARK)
    assert code == 200 and body == {"enabled": False}
    # даже отказ по пустому запросу не должен выдавать существование модуля
    assert (await _search(env, ""))[1] == {"enabled": False}


async def test_finds_row_in_every_source_with_provenance(env, monkeypatch):
    """Одна строка находится в каждом источнике, и у каждой видно происхождение."""
    monkeypatch.setenv(unified_search.FLAG, "1")
    ids = await _seed(env)
    code, body = await _search(env, MARK, limit=50, total=200)
    assert code == 200 and body["enabled"] is True

    found = {g["source"]: g for g in body["sources"] if g["count"]}
    for name in ("events", "tasks", "task_runs", "approvals"):
        assert name in found, f"источник {name} не нашёл посеянную строку"
    assert {"run_events", "missions"} <= set(found)

    for hit in body["results"]:
        assert hit["source"] and isinstance(hit["id"], int)
        assert hit["ts"], f"строка {hit['source']}:{hit['id']} без времени"
        assert MARK in hit["excerpt"].lower()
        assert hit["path"].startswith("/api/")

    # ссылка ведёт на конкретную строку, а не на источник вообще
    task_hit = found["tasks"]["results"][0]
    assert task_hit["id"] == ids["task"] and task_hit["path"] == f"/api/tasks/{ids['task']}"
    assert found["task_runs"]["results"][0]["path"] == f"/api/runs/{ids['run']}"
    assert found["run_events"]["results"][0]["path"] == f"/api/runs/{ids['run']}/events"


async def test_like_wildcards_are_escaped_not_executed(env, monkeypatch):
    """Запрос из одних спецсимволов LIKE не возвращает базу, а литерал «%_» ищется."""
    monkeypatch.setenv(unified_search.FLAG, "1")
    await _seed(env)
    async with env.svc.db.session() as s:
        r = await s.execute(sa.insert(tasks_t).values(
            title="progress 50%_done", prompt="literal wildcards", status="draft"))
        literal_id = int(r.inserted_primary_key[0])
        await s.commit()

    assert (await _search(env, MARK, limit=50))[1]["total"] > 0     # база не пуста

    for wild in ("%_%", "%%%", "___", "\\_%"):
        code, body = await _search(env, wild, limit=50, total=200)
        assert code == 200
        assert body["total"] == 0, f"«{wild}» сработал как джокер: {body['total']} строк"

    code, body = await _search(env, "0%_d", limit=50)
    assert code == 200 and body["total"] == 1
    assert body["results"][0]["source"] == "tasks" and body["results"][0]["id"] == literal_id


async def test_sql_injection_does_not_break_or_leak(env, monkeypatch):
    """Внедрение SQL проходит как обычный текст: ни ошибки, ни лишних строк."""
    monkeypatch.setenv(unified_search.FLAG, "1")
    await _seed(env)
    for payload in ("' OR 1=1 --", "'; DROP TABLE tasks; --", "\" OR \"a\"=\"a", "1' UNION SELECT 1"):
        code, body = await _search(env, payload, limit=50, total=200)
        assert code == 200, f"поиск сломался на {payload!r}"
        assert body["total"] == 0, f"{payload!r} вернул {body['total']} строк"

    # таблицы на месте, обычный поиск продолжает работать
    assert (await _search(env, MARK, limit=50))[1]["total"] >= 6


async def test_truncation_is_reported_and_limits_hold(env, monkeypatch):
    """Превышение предела помечено truncated, а выдача реально ограничена."""
    monkeypatch.setenv(unified_search.FLAG, "1")
    for i in range(7):
        await env.svc.bus.emit("unified.probe", note=f"{MARK} noisy event {i}")

    code, body = await _search(env, MARK, limit=3, total=200)
    assert code == 200
    events = [g for g in body["sources"] if g["source"] == "events"][0]
    assert events["count"] == 3 and len(events["results"]) == 3
    assert events["truncated"] is True
    assert body["truncated"] is True and "events" in body["truncated_sources"]

    # общий предел режет так же честно и не даёт шумному источнику всё занять
    await _seed(env)
    code, body = await _search(env, MARK, limit=50, total=2)
    assert code == 200 and body["total"] == 2 and len(body["results"]) == 2
    assert body["truncated"] is True and body["truncated_sources"]

    # без превышения флага усечения нет
    code, body = await _search(env, MARK, limit=50, total=200)
    assert code == 200 and body["truncated"] is False and body["truncated_sources"] == []


async def test_empty_and_too_short_query_rejected(env, monkeypatch):
    """Пустой и однобуквенный запрос — осмысленный отказ, а не выгрузка базы."""
    monkeypatch.setenv(unified_search.FLAG, "1")
    await _seed(env)
    for bad in ("", "   ", "z"):
        code, body = await _search(env, bad)
        assert code == 400, f"запрос {bad!r} не отклонён"
        assert "results" not in body
    code, body = await _search(env, "x" * (unified_search.MAX_QUERY_CHARS + 1))
    assert code == 400
