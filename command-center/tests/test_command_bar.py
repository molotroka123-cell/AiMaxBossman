"""Командная строка и фоновые задачи: доказательства, а не заявления.

Проверяется семь свойств, ради которых модуль вообще написан:
каталог собран из настоящих маршрутов; непонятый ввод не выполняет ничего;
разбор не меняет состояние; необратимое действие без подтверждения не
выполняется; фоновая задача переживает уход со страницы и перезапуск процесса;
остановка даёт состояние stopped, а не исчезновение; упавшая задача не мешает
соседней. Плюс два запрета: при выключенном флаге не делается ничего, и
значения параметров не выходят наружу.
"""
from __future__ import annotations

import asyncio
import json

import pytest
import sqlalchemy as sa

from bcc.db import metadata
from bcc.features import command_bar as cb

from .conftest import client_for, make_settings, start_app, wait_for

SECRET = "sk-command-bar-secret-9f2c1a"


@pytest.fixture(autouse=True)
def _flag_on(monkeypatch):
    """Флаг по умолчанию выключен, поэтому включаем его явно на каждый тест."""
    monkeypatch.setenv(cb.FLAG, "1")


# --------------------------------------------------------------- вспомогательное

async def _snapshot(env) -> dict:
    """Состояние приложения: число строк во всех таблицах + список фоновых задач.

    Снимок нужен ровно для одного утверждения — «разбор ничего не изменил», —
    поэтому берётся по метаданным схемы, а не по списку таблиц в голове.
    """
    counts: dict[str, int] = {}
    async with env.svc.db.session() as s:
        for name, table in sorted(metadata.tables.items()):
            counts[name] = int((await s.execute(sa.select(sa.func.count()).select_from(table)))
                               .scalar_one())
    tasks = (await env.client.get("/api/command-bar/tasks")).json()["tasks"]
    return {"rows": counts, "tasks": tasks}


async def _make_task(env, title: str = "тестовая") -> int:
    res = await env.client.post("/api/tasks", json={"prompt": "ничего не делать",
                                                    "title": title, "run_now": False})
    assert res.status_code == 200, res.text
    return int(res.json()["task"]["id"])


async def _make_agent(env, name: str = "агент") -> int:
    res = await env.client.post("/api/agents", json={"name": name})
    assert res.status_code == 200, res.text
    return int(res.json()["id"])


async def _finished(env, task_id: str) -> dict:
    """Дождаться конца фоновой задачи, не угадывая время её работы."""
    async def check():
        row = (await env.client.get(f"/api/command-bar/tasks/{task_id}")).json()["task"]
        return row if row["state"] in ("done", "failed", "stopped") else None
    return await wait_for(check, timeout=15.0)


async def _await_state(store, task_id: str, state: str) -> dict:
    async def check():
        return store.tasks[task_id]["state"] == state
    await wait_for(check, timeout=15.0)
    return store.tasks[task_id]


# --------------------------------------------------------------- 1. каталог

async def test_catalog_is_built_from_the_real_routes(env):
    """Каждая возможность — настоящий маршрут приложения, и наоборот."""
    body = (await env.client.get("/api/command-bar")).json()
    assert body["enabled"] is True and body["count"] > 100

    real = {(r.path, m) for r in cb._walk_routes(env.app.routes)
            for m in (r.methods or set()) if m not in ("HEAD", "OPTIONS")}
    for cap in body["capabilities"]:
        assert (cap["path"], cap["method"]) in real, f"выдуманная возможность: {cap['id']}"

    ids = [c["id"] for c in body["capabilities"]]
    assert len(ids) == len(set(ids)), "имена возможностей обязаны быть различимы"
    # Конкретные маршруты из bcc/api.py обязаны быть на месте: каталог,
    # потерявший половину приложения, тоже «непротиворечив».
    by_id = {c["id"]: c for c in body["capabilities"]}
    assert by_id["tasks.action"]["path"] == "/api/tasks/{task_id}/{action}"
    assert by_id["agents.delete"]["method"] == "DELETE"
    assert by_id["tasks.create"]["body_fields"][:1] == ["prompt"], "поля тела читаются у маршрута"
    assert "tasks.stop" not in by_id, "такого маршрута нет — и предлагать его нельзя"

    # Псевдонимы указывают только на существующие возможности.
    for word, target in body["aliases"].items():
        assert target in by_id, f"псевдоним «{word}» ведёт в никуда"


async def test_a_removed_route_disappears_from_the_catalog(env):
    """Каталог — зеркало маршрутов, а не их копия: список не переживает маршрут."""
    catalog = cb.build_catalog(env.app)
    assert "agents.delete" in catalog
    trimmed = {cid: cap for cid, cap in catalog.items() if cid != "agents.delete"}
    match, suggestions = cb.match_command("agents.delete", trimmed)
    assert match is None and suggestions, "исчезнувшая возможность не выполняется"


# --------------------------------------------------------------- 2. непонятый ввод

async def test_unknown_input_executes_nothing_and_offers_options(env):
    before = await _snapshot(env)
    res = await env.client.post("/api/command-bar/parse", json={"text": "абракадабра 12"})
    body = res.json()
    assert res.status_code == 200
    assert body["understood"] is False and body["intent"] is None and body["executed"] is False
    assert body["suggestions"], "«не понял» без вариантов оставляет владельца ни с чем"

    run = await env.client.post("/api/command-bar/run", json={"text": "абракадабра 12"})
    assert run.status_code == 400, run.text
    assert await _snapshot(env) == before, "непонятая команда не имеет права ничего менять"


async def test_ambiguous_prefix_is_refused_not_guessed(env):
    """Похожее действие вместо названного — худший ответ командной строки."""
    body = (await env.client.post("/api/command-bar/parse", json={"text": "tasks."})).json()
    assert body["understood"] is False
    assert {"tasks.list", "tasks.create", "tasks.action"} <= set(body["suggestions"])


async def test_alias_and_prefix_resolve_deterministically(env):
    task_id = await _make_task(env)
    by_alias = (await env.client.post("/api/command-bar/parse",
                                      json={"text": f"остановить {task_id}"})).json()
    assert by_alias["intent"]["capability"]["id"] == "tasks.action"
    assert by_alias["intent"]["match"] == {"how": "alias", "alias": "остановить"}

    by_name = (await env.client.post("/api/command-bar/parse",
                                     json={"text": f"tasks.action {task_id} stop"})).json()
    assert by_name["intent"]["capability"]["id"] == "tasks.action"
    assert by_name["intent"]["match"]["how"] == "exact"


async def test_unknown_parameter_is_named_not_dropped(env):
    body = (await env.client.post("/api/command-bar/parse",
                                  json={"text": "tasks.list питание=да"})).json()
    assert body["understood"] is False and "неизвестный параметр" in body["message"]


# --------------------------------------------------------------- 3. разбор не меняет

async def test_parse_changes_nothing_at_all(env):
    """Снимок состояния до и после разбора совпадает — в том числе для
    необратимых действий, где строится настоящий предпросмотр."""
    task_id = await _make_task(env)
    agent_id = await _make_agent(env)
    before = await _snapshot(env)

    for text in (f"остановить {task_id}", f"agents.delete {agent_id}", "система",
                 "разрешить 1", "tasks.list", "абракадабра"):
        res = await env.client.post("/api/command-bar/parse", json={"text": text})
        assert res.status_code == 200, res.text
        assert res.json()["executed"] is False

    assert await _snapshot(env) == before, "разбор обязан быть чтением, и только чтением"


async def test_intent_reuses_the_existing_action_preview(env):
    """Второй предпросмотр не написан: показывается настоящий Preview соседа."""
    task_id = await _make_task(env)
    intent = (await env.client.post("/api/command-bar/parse",
                                    json={"text": f"остановить {task_id}"})).json()["intent"]
    assert intent["reversible_source"] == "action_preview"
    assert intent["reversible"] is True and intent["requires_confirmation"] is False
    preview = intent["preview"]
    assert preview["available"] is True
    changed = {(c["table"], c["field"]) for c in preview["changes"]}
    assert ("tasks", "status") in changed, preview


# --------------------------------------------------------------- 4. подтверждение

async def test_irreversible_action_is_impossible_without_confirmation(env):
    agent_id = await _make_agent(env, "жертва")
    parsed = (await env.client.post("/api/command-bar/parse",
                                    json={"text": f"agents.delete {agent_id}"})).json()
    intent = parsed["intent"]
    assert intent["requires_confirmation"] is True and intent["reversible"] is False

    refused = await env.client.post("/api/command-bar/run",
                                    json={"intent_id": parsed["intent_id"]})
    assert refused.status_code == 412, refused.text
    assert (await env.client.get("/api/agents")).json(), "агент обязан остаться на месте"
    assert (await env.client.get("/api/command-bar/tasks")).json()["tasks"] == [], \
        "отказ не имеет права заводить фоновую задачу"

    # Тот же путь, но подтверждённый отдельным шагом — выполняется.
    ok = await env.client.post("/api/command-bar/run",
                               json={"intent_id": parsed["intent_id"], "confirm": True})
    assert ok.status_code == 200, ok.text
    row = await _finished(env, ok.json()["task"]["id"])
    assert row["state"] == "done" and row["confirmed"] is True
    assert (await env.client.get("/api/agents")).json() == []


async def test_reversible_action_needs_no_confirmation(env):
    """Гейт обязан уметь пропускать: иначе подтверждение станет привычкой."""
    task_id = await _make_task(env)
    parsed = (await env.client.post("/api/command-bar/parse",
                                    json={"text": f"остановить {task_id}"})).json()
    res = await env.client.post("/api/command-bar/run", json={"intent_id": parsed["intent_id"]})
    assert res.status_code == 200, res.text
    row = await _finished(env, res.json()["task"]["id"])
    assert row["state"] == "done", row


async def test_command_bar_refuses_to_run_itself(env):
    """Самозапуск — прямая дорога к рекурсии; маршрут виден, но не исполняется."""
    parsed = (await env.client.post("/api/command-bar/parse",
                                    json={"text": "command-bar.run.post"})).json()
    assert parsed["intent"]["runnable"] is False
    assert "сам" in parsed["intent"]["blocked_reason"]
    res = await env.client.post("/api/command-bar/run", json={"intent_id": parsed["intent_id"]})
    assert res.status_code == 400


# --------------------------------------------------------------- 5. фоновые задачи

async def test_task_survives_leaving_the_page(env):
    """Состояние задачи живёт на сервере: новый клиент видит ту же задачу."""
    started = (await env.client.post("/api/command-bar/run", json={"text": "система"})).json()
    task_id = started["task"]["id"]
    await _finished(env, task_id)

    async with client_for(env.app, env.svc) as fresh:     # «вернулись на страницу»
        again = (await fresh.get(f"/api/command-bar/tasks/{task_id}")).json()["task"]
        listing = (await fresh.get("/api/command-bar/tasks")).json()["tasks"]
    assert again["id"] == task_id and again["state"] == "done"
    assert again["result"]["status"] == 200
    assert task_id in {t["id"] for t in listing}


async def test_task_list_survives_a_restart_of_the_process(env):
    """Список держится файлом в data_dir, поэтому переживает перезапуск."""
    started = (await env.client.post("/api/command-bar/run", json={"text": "система"})).json()
    task_id = started["task"]["id"]
    await _finished(env, task_id)
    assert (env.settings.data_dir / cb.STORE_DIRNAME / cb.STORE_FILENAME).is_file()

    app2, svc2 = await start_app(env.settings, start_workers=False)   # тот же data_dir
    try:
        async with client_for(app2, svc2) as client2:
            rows = (await client2.get("/api/command-bar/tasks")).json()["tasks"]
    finally:
        await svc2.stop()
    assert task_id in {t["id"] for t in rows}


async def test_stop_gives_the_state_stopped_and_not_a_disappearance(env):
    store = cb.store_for(env.svc)

    async def slow():
        await asyncio.sleep(30)
        return {"ok": True}

    task = store.create(capability="tasks.list", title="долгая работа", arguments=[],
                        confirmed=False)
    store.spawn(env.svc, task["id"], slow)
    await _await_state(store, task["id"], "running")

    res = await env.client.post(f"/api/command-bar/tasks/{task['id']}/stop")
    assert res.status_code == 200, res.text
    stopped = res.json()["task"]
    assert stopped["state"] == "stopped" and stopped["finished_at"], stopped
    assert "владельц" in stopped["error"]

    listing = (await env.client.get("/api/command-bar/tasks")).json()["tasks"]
    assert task["id"] in {t["id"] for t in listing}, "остановленная задача не исчезает"


async def test_stopping_an_unknown_task_is_a_plain_404(env):
    assert (await env.client.post("/api/command-bar/tasks/нет-такой/stop")).status_code == 404


async def test_a_failing_task_disturbs_neither_the_app_nor_its_neighbour(env):
    """Отказ задачи — состояние failed, а не падение соседней задачи и сервера."""
    store = cb.store_for(env.svc)

    async def boom():
        raise RuntimeError("внутренний отказ исполнителя")

    bad = store.create(capability="tasks.list", title="упадёт", arguments=[], confirmed=False)
    store.spawn(env.svc, bad["id"], boom)

    good = (await env.client.post("/api/command-bar/run", json={"text": "система"})).json()
    missing = (await env.client.post("/api/command-bar/run",
                                     json={"text": "tasks.get 999999"})).json()

    await _await_state(store, bad["id"], "failed")
    good_row = await _finished(env, good["task"]["id"])
    missing_row = await _finished(env, missing["task"]["id"])

    assert store.tasks[bad["id"]]["error"].startswith("RuntimeError")
    assert good_row["state"] == "done", good_row
    assert missing_row["state"] == "failed" and missing_row["result"]["status"] == 404
    assert (await env.client.get("/api/system")).status_code == 200, "приложение живо"


# --------------------------------------------------------------- 6. секреты

async def test_entered_values_never_come_back_out(env):
    """Значение параметра может быть ключом: наружу выходит отпечаток, не значение."""
    text = f"providers.create name=локальный kind=openai_compat api_key={SECRET}"
    parsed = (await env.client.post("/api/command-bar/parse", json={"text": text})).json()
    assert parsed["understood"] is True
    assert SECRET not in json.dumps(parsed, ensure_ascii=False)
    api_key = next(a for a in parsed["intent"]["arguments"] if a["name"] == "api_key")
    assert api_key["shown"] is False and api_key["value"] is None
    assert api_key["fingerprint"].startswith(f"{len(SECRET)} симв.")

    run = await env.client.post("/api/command-bar/run", json={"intent_id": parsed["intent_id"],
                                                              "confirm": True})
    assert run.status_code == 200, run.text
    await _finished(env, run.json()["task"]["id"])

    seen = [json.dumps(run.json(), ensure_ascii=False),
            (await env.client.get("/api/command-bar/tasks")).text,
            (await env.client.get(f"/api/command-bar/tasks/{run.json()['task']['id']}")).text,
            (await env.client.get("/api/activity")).text,
            (env.settings.data_dir / cb.STORE_DIRNAME / cb.STORE_FILENAME).read_text("utf-8")]
    for where, blob in zip(("run", "tasks", "task", "activity", "файл"), seen):
        assert SECRET not in blob, f"секрет уцелел в {where}"


async def test_row_references_are_still_readable(env):
    """Полная маскировка бесполезна: владелец обязан видеть, ЧТО останавливает."""
    task_id = await _make_task(env)
    intent = (await env.client.post("/api/command-bar/parse",
                                    json={"text": f"остановить {task_id}"})).json()["intent"]
    by_name = {a["name"]: a for a in intent["arguments"]}
    assert by_name["task_id"]["value"] == task_id and by_name["task_id"]["kind"] == "reference"
    assert by_name["action"]["value"] == "stop" and by_name["action"]["kind"] == "preset"
    assert str(task_id) in intent["summary"]


# --------------------------------------------------------------- 7. выключенный флаг

async def test_nothing_happens_while_the_flag_is_off(tmp_path, monkeypatch):
    monkeypatch.setenv(cb.FLAG, "0")
    settings = make_settings(tmp_path)
    app, svc = await start_app(settings, start_workers=False)
    try:
        async with client_for(app, svc) as client:
            catalog = (await client.get("/api/command-bar")).json()
            assert catalog["enabled"] is False and catalog["capabilities"] == []
            assert (await client.get("/api/command-bar/tasks")).json() == {"enabled": False,
                                                                          "tasks": []}
            for path, payload in (("/api/command-bar/parse", {"text": "система"}),
                                  ("/api/command-bar/run", {"text": "система"}),
                                  ("/api/command-bar/tasks/xxx/stop", None)):
                res = await client.post(path, json=payload)
                assert res.status_code == 409, f"{path}: {res.status_code}"
        assert not (settings.data_dir / cb.STORE_DIRNAME).exists(), \
            "выключенная фича не пишет на диск"
    finally:
        await svc.stop()
