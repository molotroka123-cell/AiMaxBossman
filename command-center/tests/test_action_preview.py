"""Предпросмотр действия: называет конкретные строки и ничего не меняет.

Главное, что здесь доказывается, — не форма ответа, а два свойства:
предпросмотр не пишет в БД (снимок всех таблиц до и после совпадает) и не путает
«изменений не будет» с «предпросмотра нет».
"""
from __future__ import annotations

import sqlalchemy as sa

from bcc.db import metadata
from bcc.features import action_preview

from .helpers import make_stack


async def dump_all(svc) -> dict[str, list[str]]:
    """Снимок содержимого ВСЕХ таблиц. Строки нормализуем в отсортированные
    строковые представления: доказываем неизменность содержимого, а не порядка."""
    out: dict[str, list[str]] = {}
    async with svc.db.session() as s:
        for name, table in sorted(metadata.tables.items()):
            rows = (await s.execute(sa.select(table))).fetchall()
            out[name] = sorted(repr(dict(r._mapping)) for r in rows)
    return out


async def post_preview(env, action: str, target_id=None, params: dict | None = None):
    return await env.client.post("/api/preview", json={
        "action": action, "target_id": target_id, "params": params or {}})


def find(changes: list[dict], table: str, row_id, field: str) -> dict | None:
    for c in changes:
        if c["table"] == table and c["row_id"] == row_id and c["field"] == field:
            return c
    return None


async def test_flag_off_does_not_preview(env, monkeypatch):
    """Выключенный флаг: ответ {"enabled": false} и ни одного предпросмотра —
    поведение приложения ровно такое, как до модуля."""
    monkeypatch.delenv(action_preview.FLAG, raising=False)
    stack = await make_stack(env.client)
    before = await dump_all(env.svc)

    r = await post_preview(env, "task.stop", stack["task"]["id"])
    assert r.status_code == 200 and r.json() == {"enabled": False}
    assert await dump_all(env.svc) == before


async def test_preview_names_the_rows_that_will_change_and_reality_agrees(env, monkeypatch):
    """Предпросмотр остановки называет конкретные строки (tasks и её queued-run),
    и после настоящей остановки эти строки выглядят ровно как обещано."""
    monkeypatch.setenv(action_preview.FLAG, "1")
    stack = await make_stack(env.client)
    task_id = stack["task"]["id"]
    run_id = (await env.client.get(f"/api/tasks/{task_id}")).json()["runs"][0]["id"]

    body = (await post_preview(env, "task.stop", task_id)).json()
    assert body["available"] is True
    task_status = find(body["changes"], "tasks", task_id, "status")
    run_status = find(body["changes"], "task_runs", run_id, "status")
    assert task_status["op"] == "update"
    assert (task_status["before"], task_status["after"]) == ("queued", "stopped")
    assert (run_status["before"], run_status["after"]) == ("queued", "stopped")
    # отметки времени честно помечены как «значение будет известно при выполнении»
    assert find(body["changes"], "tasks", task_id, "updated_at")["after_known"] is False

    assert (await env.client.post(f"/api/tasks/{task_id}/stop")).status_code == 200
    after = (await env.client.get(f"/api/tasks/{task_id}")).json()
    assert after["task"]["status"] == "stopped"
    assert [r for r in after["runs"] if r["id"] == run_id][0]["status"] == "stopped"


async def test_preview_changes_nothing_at_all(env, monkeypatch):
    """Ни одна строка ни одной таблицы не меняется предпросмотром — и ни одного
    события не уходит в шину. Чувствительность самой проверки доказана тем, что
    настоящее действие тот же снимок ломает."""
    monkeypatch.setenv(action_preview.FLAG, "1")
    stack = await make_stack(env.client)
    task_id = stack["task"]["id"]
    approval = (await env.client.post("/api/approvals", json={
        "kind": "deploy", "preview": "выкатить релиз", "task_id": task_id})).json()

    emitted: list[str] = []
    original = env.svc.bus.emit

    async def spy(kind, /, **data):
        emitted.append(kind)
        return await original(kind, **data)

    env.svc.bus.emit = spy

    before = await dump_all(env.svc)
    assert (await post_preview(env, "task.stop", task_id)).status_code == 200
    assert (await post_preview(env, "approval.decide", approval["id"],
                               {"approve": True})).status_code == 200
    assert (await post_preview(env, "agent.delete", stack["agent"]["id"])).status_code == 200
    assert (await post_preview(env, "task.stop", 10 ** 6)).status_code == 404
    assert await dump_all(env.svc) == before
    assert emitted == []

    # тот же снимок обязан замечать настоящее изменение — иначе сравнение пустое
    await env.client.post(f"/api/tasks/{task_id}/stop")
    assert await dump_all(env.svc) != before


async def test_unknown_action_is_unavailable_not_empty(env, monkeypatch):
    """Действие без зарегистрированного предпросмотра: available=false и
    changes=null. Пустого списка изменений тут быть не должно."""
    monkeypatch.setenv(action_preview.FLAG, "1")
    body = (await post_preview(env, "email.send", 1)).json()

    assert body["available"] is False
    assert body["changes"] is None and body["change_count"] is None
    assert body["reason"] == action_preview.UNAVAILABLE
    assert "email.send" not in body["known_actions"]


async def test_no_op_action_gives_empty_changes_distinguishable_from_unknown(env, monkeypatch):
    """Уже решённое подтверждение: решение принимается один раз, поэтому повтор
    не тронет ни одной строки — это ПУСТОЙ список, а не «предпросмотра нет»."""
    monkeypatch.setenv(action_preview.FLAG, "1")
    approval = (await env.client.post("/api/approvals", json={
        "kind": "deploy", "preview": "выкатить релиз"})).json()
    await env.client.post(f"/api/approvals/{approval['id']}", json={"approve": True})

    noop = (await post_preview(env, "approval.decide", approval["id"],
                               {"approve": True})).json()
    unknown = (await post_preview(env, "email.send", 1)).json()

    assert noop["available"] is True and noop["changes"] == [] and noop["change_count"] == 0
    assert unknown["available"] is False and unknown["changes"] is None
    assert noop["available"] != unknown["available"]
    assert noop["changes"] != unknown["changes"]

    # и это правда: повторное решение действительно ничего не меняет
    before = await dump_all(env.svc)
    await env.client.post(f"/api/approvals/{approval['id']}", json={"approve": False})
    assert await dump_all(env.svc) == before


async def test_missing_target_is_an_explicit_error(env, monkeypatch):
    """Несуществующая цель — внятная ошибка 404, а не «изменений не будет»."""
    monkeypatch.setenv(action_preview.FLAG, "1")

    for action in ("task.stop", "agent.delete", "schedule.delete"):
        r = await post_preview(env, action, 424242, {"approve": True})
        assert r.status_code == 404, action
        assert "не найдена" in r.json()["error"]["message"]

    # цель вообще не названа — тоже отказ, а не пустой предпросмотр
    without_target = await post_preview(env, "task.stop", None)
    assert without_target.status_code == 400
    assert "target_id" in without_target.json()["error"]["message"]


async def test_delete_preview_lists_cascade_rows_and_predicts_reality(env, monkeypatch):
    """Удаление агента: предпросмотр называет и саму строку, и задачу, у которой
    обнулится agent_id. После настоящего удаления состояние совпадает с обещанным."""
    monkeypatch.setenv(action_preview.FLAG, "1")
    stack = await make_stack(env.client)
    agent_id, task_id = stack["agent"]["id"], stack["task"]["id"]

    body = (await post_preview(env, "agent.delete", agent_id)).json()
    assert body["reversible"] is False
    assert find(body["changes"], "agents", agent_id, "*")["op"] == "delete"
    link = find(body["changes"], "tasks", task_id, "agent_id")
    assert link["op"] == "update" and link["before"] == agent_id and link["after"] is None

    assert (await env.client.delete(f"/api/agents/{agent_id}")).status_code == 200
    assert (await env.client.get(f"/api/tasks/{task_id}")).json()["task"]["agent_id"] is None


async def test_schedule_delete_preview_covers_the_task_link(env, monkeypatch):
    """Тот же вывод из схемы для расписания: строка удаляется, tasks.schedule_id
    обнуляется — и после удаления так и происходит."""
    monkeypatch.setenv(action_preview.FLAG, "1")
    task = (await env.client.post("/api/tasks", json={
        "prompt": "по расписанию", "run_now": False,
        "schedule": {"name": "каждые 5 минут", "kind": "interval",
                     "interval_minutes": 5}})).json()
    task_id, schedule_id = task["task"]["id"], task["schedule"]["id"]

    body = (await post_preview(env, "schedule.delete", schedule_id)).json()
    assert find(body["changes"], "schedules", schedule_id, "*")["op"] == "delete"
    assert find(body["changes"], "tasks", task_id, "schedule_id")["after"] is None

    assert (await env.client.delete(f"/api/schedules/{schedule_id}")).status_code == 200
    assert (await env.client.get(f"/api/tasks/{task_id}")).json()["task"]["schedule_id"] is None


async def test_registered_actions_are_listed_honestly(env, monkeypatch):
    """Список видов действий с предпросмотром отдаётся явно: молчание читалось бы
    как «предпросмотр есть для всего»."""
    monkeypatch.setenv(action_preview.FLAG, "1")
    body = (await env.client.get("/api/preview/actions")).json()
    assert body["enabled"] is True
    assert set(body["actions"]) == set(action_preview.REGISTRY)
    assert "task.stop" in body["actions"]
