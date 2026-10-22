"""Owner 21.10: «Я остановил задачу, но когда перезашёл, история удалилась — там были заметки».

Приватность (правило владельца 21.10): тесты синтетические. В файлах сессий здесь лежит
только метка CANARY, никаких настоящих чатов, заметок или фото.

Что проверяется (каждый пункт — путь, по которому история могла пропасть или выглядеть пропавшей):

1. STOP (задачи чата, сессии агента, общий STOP компьютера) не трогает файл сессии: все ходы
   с меткой на месте до и после перезапуска backend.
2. Служебные ходы агента (`/improve`, CODE) имели один и тот же task_id=0, а запись хода
   отбрасывала «уже записанный» ход — второй и следующие ходы молча терялись, хотя ответ
   говорил «записано». Теперь у каждого свой номер и все они доходят до файла и перезапуска.
3. Нечитаемый на миг файл (антивирус, CLI заменяет файл) — это не «чат удалён» (404) и не
   «пустой список»: чтение повторяется, при стойкой ошибке отдаётся последняя целая копия
   или честная ошибка «занят», а запись хода не теряет сообщение.
4. Запись не затирает историю, если в файле `turns` неожиданного вида: отказ вместо замены на `[]`.
5. Перед каждой заменой файла сессии прежняя целая версия остаётся рядом (`<id>.json.bak`);
   удаление чата навсегда убирает и её.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from bcc.features import chat_threads as ct
from bcc.terminal_cli.chat import Session
from .conftest import client_for, start_app
from .test_chat_threads import _answer, _new_thread, _send, _sessions, _stack
from .test_mobile_opencode_agent import _setup_gemma_stack, mini_repo  # noqa: F401  (fixture re-export)

CANARY = "CANARY-NOTE-7731-alpha"


def _file(env, thread_id: str) -> dict:
    return json.loads((_sessions(env) / f"{thread_id}.json").read_text(encoding="utf-8"))


def _texts(turns: list[dict]) -> list[str]:
    return [t["text"] for t in turns]


async def _thread_with_stopped_turn(env) -> tuple[str, list[int]]:
    """Два завершённых хода и третий, который владелец остановил. Возвращает (thread_id, task_ids)."""
    await _stack(env.client)
    tid = (await _new_thread(env, title="synthetic"))["id"]
    ids: list[int] = []
    for i in range(2):
        resp = await _send(env, tid, f"{CANARY} turn {i}", f"req-hist-{i:04d}")
        assert resp.status_code == 200, resp.text
        task_id = resp.json()["task"]["id"]
        await _answer(env, task_id, f"answer {i}")
        ids.append(task_id)
    resp = await _send(env, tid, f"{CANARY} turn 2 (stopped)", "req-hist-0002")
    assert resp.status_code == 200, resp.text
    ids.append(resp.json()["task"]["id"])
    return tid, ids


# --------------------------------------------------------------- 1. STOP не трогает историю


async def test_thread_stop_keeps_every_turn_before_and_after_restart(env):
    tid, ids = await _thread_with_stopped_turn(env)
    stop = await env.client.post(f"/api/chat/threads/{tid}/stop")
    assert stop.status_code == 200 and stop.json() == {"ok": True, "task_id": ids[2], "status": "stopped"}

    assert _texts(_file(env, tid)["turns"]) == [f"{CANARY} turn 0", f"{CANARY} turn 1", f"{CANARY} turn 2 (stopped)"]
    got = (await env.client.get(f"/api/chat/threads/{tid}")).json()
    assert got["turn_count"] == 3
    assert [t["status"] for t in got["turns"]] == ["completed", "completed", "stopped"]
    assert [t["text"] for t in got["turns"]] == _texts(_file(env, tid)["turns"]), "остановленный ход показан с текстом владельца"
    listed = (await env.client.get("/api/chat/threads")).json()
    assert listed["total"] == 1 and listed["items"][0]["turns"] == 3
    assert listed["items"][0]["last"] == {"task_id": ids[2], "status": "stopped"}

    app2, svc2 = await start_app(env.settings, start_workers=False)        # «перезашёл»: тот же data_dir
    try:
        async with client_for(app2, svc2) as client2:
            again = (await client2.get(f"/api/chat/threads/{tid}")).json()
            assert [t["text"] for t in again["turns"]] == [f"{CANARY} turn 0", f"{CANARY} turn 1",
                                                           f"{CANARY} turn 2 (stopped)"]
            assert (await client2.get("/api/chat/threads")).json()["total"] == 1
    finally:
        await svc2.stop()


async def test_global_computer_stop_does_not_hide_or_block_the_history(env):
    tid, _ids = await _thread_with_stopped_turn(env)
    assert (await env.client.post("/api/computer/stop")).json()["stopped"] is True
    try:
        listed = (await env.client.get("/api/chat/threads")).json()
        assert listed["total"] == 1 and listed["items"][0]["turns"] == 3
        got = await env.client.get(f"/api/chat/threads/{tid}")
        assert got.status_code == 200 and got.json()["turn_count"] == 3
        # и запись во время общего STOP: сообщение владельца попадает в историю (текст не теряется)
        sent = await _send(env, tid, f"{CANARY} written during global STOP", "req-hist-gstop")
        assert sent.status_code == 200, sent.text
        assert f"{CANARY} written during global STOP" in _texts(_file(env, tid)["turns"])
    finally:
        await env.client.post("/api/computer/resume")


async def test_agent_session_stop_keeps_turns_across_restart(env, mini_repo: Path):
    await _setup_gemma_stack(env, mini_repo)
    sid = (await env.client.post("/api/agent/sessions", json={"title": "synthetic agent", "mode": "AGENT"})).json()["id"]
    ids = []
    for i in range(2):
        msg = await env.client.post(f"/api/agent/sessions/{sid}/messages", json={
            "text": f"{CANARY} agent msg {i}", "mode": "AGENT", "client_request_id": f"req-ag-{i:04d}"})
        assert msg.status_code == 200, msg.text
        ids.append(msg.json()["task_id"])
    await _answer(env, ids[0], "answer 0")
    stop = await env.client.post(f"/api/agent/sessions/{sid}/stop")
    assert stop.status_code == 200 and stop.json()["stopped_tasks"] == [ids[1]]
    assert _texts(_file(env, sid)["turns"]) == [f"{CANARY} agent msg 0", f"{CANARY} agent msg 1"]

    app2, svc2 = await start_app(env.settings, start_workers=False)
    try:
        async with client_for(app2, svc2) as client2:
            loaded = (await client2.get(f"/api/agent/sessions/{sid}")).json()
            assert [(t["text"], t["status"]) for t in loaded["turns"]] == [
                (f"{CANARY} agent msg 0", "completed"), (f"{CANARY} agent msg 1", "stopped")]
            assert (await client2.get("/api/agent/sessions")).json()["total"] == 1
    finally:
        await svc2.stop()


# --------------------------------------------------------------- 2. служебные ходы агента не склеиваются


async def test_every_improve_turn_is_kept_not_only_the_first(env, mini_repo: Path):
    await _setup_gemma_stack(env, mini_repo)
    sid = (await env.client.post("/api/agent/sessions", json={"title": "synthetic improve", "mode": "AGENT"})).json()["id"]
    first = await env.client.post(f"/api/agent/sessions/{sid}/messages", json={
        "text": f"{CANARY} real message", "mode": "AGENT", "client_request_id": "req-imp-real"})
    assert first.status_code == 200, first.text
    idxs = []
    for i in range(3):
        resp = await env.client.post(f"/api/agent/sessions/{sid}/messages", json={
            "text": f"/improve {CANARY} cycle {i}", "client_request_id": f"req-imp-{i:04d}"})
        assert resp.status_code == 200, resp.text
        idxs.append(resp.json()["turn"]["idx"])
    assert idxs == [1, 2, 3], "каждому служебному ходу — своя строка в файле, ответ не врёт про idx"

    turns = _file(env, sid)["turns"]
    assert _texts(turns) == [f"{CANARY} real message", *[f"/improve {CANARY} cycle {i}" for i in range(3)]]
    ids = [t["task_id"] for t in turns]
    assert len(set(ids)) == len(ids), "task_id служебных ходов различаются"
    assert all(i <= 0 for i in ids[1:]) and ids[0] > 0, "служебные ходы не маскируются под настоящие задачи"

    detail = (await env.client.get(f"/api/agent/sessions/{sid}")).json()
    assert detail["turn_count"] == 4
    assert [t["text"] for t in detail["turns"]][1:] == [f"/improve {CANARY} cycle {i}" for i in range(3)]

    app2, svc2 = await start_app(env.settings, start_workers=False)
    try:
        async with client_for(app2, svc2) as client2:
            assert (await client2.get(f"/api/agent/sessions/{sid}")).json()["turn_count"] == 4
    finally:
        await svc2.stop()


def test_synthetic_turns_survive_a_cli_rewrite(tmp_path):
    """Терминал перезаписывает файл из памяти и подмешивает чужие ходы по task_id: у служебных ходов они разные."""
    cli = Session.open(tmp_path, None)
    cli.add(1, "from cli")
    path = cli.path
    for i in range(3):
        ct._append_turn_sync(path, cli.id, {**ct._new_turn(ct.next_synthetic_task_id(), f"synthetic {i}", []),
                                            "synthetic_status": "x"})
    cli.add(2, "cli again")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert _texts(data["turns"]) == ["from cli", "synthetic 0", "synthetic 1", "synthetic 2", "cli again"]


# --------------------------------------------------------------- 3. «нечитаемо на миг» ≠ «удалено»


def _flaky_reader(real, fail_times: int, calls: list):
    def reader(path: Path):
        calls.append(path.name)
        if len(calls) <= fail_times:
            return None                                       # как OSError/обрыв JSON в _read_session_raw
        return real(path)
    return reader


async def test_a_momentarily_unreadable_file_is_retried_not_reported_as_deleted(env, monkeypatch):
    tid, _ids = await _thread_with_stopped_turn(env)
    state = ct._state(env.svc)
    state.sessions.clear()                                    # как после перезапуска: кэша нет
    calls: list[str] = []
    monkeypatch.setattr(ct, "_read_session_raw", _flaky_reader(ct._read_session_raw, 2, calls))
    got = await env.client.get(f"/api/chat/threads/{tid}")
    assert got.status_code == 200, "два сбоя чтения подряд пережиты повтором, а не 422 «повреждён»"
    assert got.json()["turn_count"] == 3


async def test_list_keeps_a_thread_whose_file_cannot_be_read_right_now(env, monkeypatch):
    tid, _ids = await _thread_with_stopped_turn(env)
    assert (await env.client.get("/api/chat/threads")).json()["total"] == 1            # кэш прогрет
    path = _sessions(env) / f"{tid}.json"
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")        # mtime/размер сменились
    monkeypatch.setattr(ct, "_read_session_raw", lambda p: None)                       # чтение стойко не удаётся
    listed = (await env.client.get("/api/chat/threads")).json()
    assert listed["total"] == 1, "список не превращается в «чатов нет», пока файл лишь не читается"
    assert listed["items"][0]["turns"] == 3
    got = await env.client.get(f"/api/chat/threads/{tid}")
    assert got.status_code == 200 and got.json()["turn_count"] == 3, "виден последний целиком прочитанный вид"


async def test_unreadable_without_any_copy_says_busy_never_not_found(env, monkeypatch):
    tid, _ids = await _thread_with_stopped_turn(env)
    ct._state(env.svc).sessions.clear()
    monkeypatch.setattr(ct, "_read_session_raw", lambda p: None)
    got = await env.client.get(f"/api/chat/threads/{tid}")
    assert got.status_code in (422, 503), "файл на месте, но не читается: НЕ 404 «чат не найден»"
    assert got.json()["error"]["code"] == "CHAT_THREAD_UNREADABLE"
    assert "не найден" not in got.json()["error"]["message"]


async def test_send_survives_a_momentary_read_failure_on_the_write_path(env, monkeypatch):
    tid, _ids = await _thread_with_stopped_turn(env)
    calls: list[str] = []
    real = ct._read_session_raw
    armed = {"on": False}

    def reader(path: Path):
        if armed["on"] and path.suffix == ".json" and len(calls) < 2:
            calls.append(path.name)
            return None
        return real(path)

    monkeypatch.setattr(ct, "_read_session_raw", reader)
    # подготовка отправки читает файл ДО записи хода: взводим сбой только на запись
    original_append = ct._append_turn_sync

    def append(path, thread_id, turn):
        armed["on"] = True
        try:
            return original_append(path, thread_id, turn)
        finally:
            armed["on"] = False

    monkeypatch.setattr(ct, "_append_turn_sync", append)
    resp = await _send(env, tid, f"{CANARY} survives a hiccup", "req-hist-hiccup")
    assert resp.status_code == 200, resp.text
    assert f"{CANARY} survives a hiccup" in _texts(_file(env, tid)["turns"]), "сообщение записано, а не «чат удалён»"


async def test_a_really_missing_file_is_still_404(env):
    tid, _ids = await _thread_with_stopped_turn(env)
    (_sessions(env) / f"{tid}.json").unlink()
    assert (await env.client.get(f"/api/chat/threads/{tid}")).status_code == 404


# --------------------------------------------------------------- 4. запись не затирает неожиданное


async def test_append_refuses_to_replace_turns_of_an_unexpected_shape(tmp_path):
    path = tmp_path / "20261021-120000-abcdef.json"
    odd = {"id": "20261021-120000-abcdef", "turns": {"0": {"task_id": 1, "text": f"{CANARY} keep me"}}}
    path.write_text(json.dumps(odd), encoding="utf-8")
    with pytest.raises(ct.SessionUnreadable):
        ct._append_turn_sync(path, "20261021-120000-abcdef", {"task_id": 9, "text": "new", "at": "t"})
    assert json.loads(path.read_text(encoding="utf-8")) == odd, "файл не тронут"


# --------------------------------------------------------------- 5. прежняя версия рядом


async def test_previous_version_stays_next_to_the_file_and_purge_removes_it(env):
    tid, _ids = await _thread_with_stopped_turn(env)
    path = _sessions(env) / f"{tid}.json"
    backup = path.with_name(f"{tid}.json.bak")
    assert backup.is_file()
    assert _texts(json.loads(backup.read_text(encoding="utf-8"))["turns"]) == [
        f"{CANARY} turn 0", f"{CANARY} turn 1"], ".bak — файл до последней записи"
    assert tid in [i["id"] for i in (await env.client.get("/api/chat/threads")).json()["items"]]
    assert [p.name for p in _sessions(env).glob("*.json")] == [path.name], ".bak не считается чатом"
    purged = await env.client.delete(f"/api/chat/threads/{tid}", params={"purge": 1})
    assert purged.json()["purged"] is True
    assert not path.exists() and not backup.exists(), "«удалить навсегда» не оставляет копий чата"
