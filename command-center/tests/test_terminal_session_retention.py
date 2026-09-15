"""Память не должна расти на каждую команду владельца, которую он уже выполнил.

Дефект нашёлся не юнит-тестом, а длинной владельческой задачей: 368 команд
подряд на установленном продукте, и RSS рос линейно всё это время. Замер
`tracemalloc` на самом `TerminalManager` назвал виновника точно — ~5.5 КБ
трассируемой кучи на команду не возвращались, и держали их объекты процессов
в `self.sessions`, который не чистился никогда. Агент, работающий сутки, платит
за это сотнями мегабайт.

История команд от этого не страдает: она в БД, а в памяти лежит только живой
процесс и хвост его вывода. Поэтому вытесняются ТОЛЬКО завершённые сессии —
и это здесь проверяется в обе стороны.
"""
from __future__ import annotations

import asyncio

import pytest
import sqlalchemy as sa

from bcc.v2.tables import terminal_sessions as term_t
from bcc.v2.terminal_control import RETAIN_FINISHED, TerminalManager, TerminalPolicy


class _Fake:
    """Сессия без процесса: измеряется политика вытеснения, а не запуск команд."""

    def __init__(self, sid: str, finished: bool, code: int | None = 0) -> None:
        self.id, self.finished, self.exit_code = sid, finished, code
        self.cwd, self.cmd, self.mode, self.output = "/tmp", f"echo {sid}", "project_host", []
        self.proc = type("P", (), {"pid": 1234})()


def _manager(finished: int, running: int = 0) -> TerminalManager:
    mgr = TerminalManager()
    for i in range(finished):
        mgr.sessions[f"fin-{i:05d}"] = _Fake(f"fin-{i:05d}", True)
    for i in range(running):
        mgr.sessions[f"run-{i:05d}"] = _Fake(f"run-{i:05d}", False, None)
    return mgr


def test_without_retirement_the_dictionary_grows_with_every_command() -> None:
    """Обратный контроль: словарь действительно растёт один к одному.

    Без этого тест ниже мог бы быть зелёным просто потому, что сессии не
    появляются вовсе, и ничего бы не доказывал."""
    mgr = _manager(RETAIN_FINISHED + 500)
    assert len(mgr.sessions) == RETAIN_FINISHED + 500


def test_finished_sessions_beyond_the_cap_are_evicted_oldest_first() -> None:
    mgr = _manager(RETAIN_FINISHED + 500)
    retired = mgr.retire_finished()
    assert len(retired) == 500
    assert len(mgr.sessions) == RETAIN_FINISHED
    assert [r["id"] for r in retired] == [f"fin-{i:05d}" for i in range(500)]
    # уцелели именно САМЫЕ СВЕЖИЕ
    assert "fin-00500" in mgr.sessions and "fin-00499" not in mgr.sessions


def test_a_running_session_is_never_evicted_however_many_there_are() -> None:
    """Выбросить запись работающего процесса — потерять над ним контроль."""
    mgr = _manager(finished=0, running=RETAIN_FINISHED + 300)
    assert mgr.retire_finished() == []
    assert len(mgr.sessions) == RETAIN_FINISHED + 300


def test_running_sessions_do_not_count_against_the_finished_budget() -> None:
    mgr = TerminalManager()
    for i in range(RETAIN_FINISHED + 50):
        mgr.sessions[f"fin-{i:05d}"] = _Fake(f"fin-{i:05d}", True)
        mgr.sessions[f"run-{i:05d}"] = _Fake(f"run-{i:05d}", False, None)
    mgr.retire_finished()
    alive = [s for s in mgr.sessions.values() if not s.finished]
    done = [s for s in mgr.sessions.values() if s.finished]
    assert len(alive) == RETAIN_FINISHED + 50, "живые сессии пострадали"
    assert len(done) == RETAIN_FINISHED


def test_nothing_is_evicted_while_the_run_is_short() -> None:
    mgr = _manager(RETAIN_FINISHED)
    assert mgr.retire_finished() == [] and len(mgr.sessions) == RETAIN_FINISHED


@pytest.mark.anyio
@pytest.mark.parametrize("failure_at", ["open", "write", "commit"])
async def test_database_failure_keeps_final_status_for_retry(failure_at) -> None:
    """Нельзя выселить единственную копию статуса до долговечной записи."""
    from types import SimpleNamespace
    from bcc.features.terminal import _retire_finished

    mgr = _manager(RETAIN_FINISHED + 1)

    class Database:
        failure = failure_at
        commits = 0

        def session(self):
            return self

        async def __aenter__(self):
            if self.failure == "open":
                raise OSError("database unavailable")
            return self

        async def __aexit__(self, *args):
            pass

        async def execute(self, statement):
            if self.failure == "write":
                raise OSError("database unavailable")
            assert "fin-00000" in mgr.sessions, "удалено ещё до записи"

        async def commit(self):
            if self.failure == "commit":
                raise OSError("database unavailable")
            assert "fin-00000" in mgr.sessions, "удалено ещё до commit"
            self.commits += 1

    db = Database()
    svc = SimpleNamespace(db=db)
    with pytest.raises(OSError, match="database unavailable"):
        await _retire_finished(svc, mgr)
    assert len(mgr.sessions) == RETAIN_FINISHED + 1
    assert mgr.status("fin-00000")["exit_code"] == 0
    assert db.commits == 0

    db.failure = None
    await _retire_finished(svc, mgr)
    assert len(mgr.sessions) == RETAIN_FINISHED
    assert "fin-00000" not in mgr.sessions
    assert db.commits == 1


@pytest.mark.anyio
async def test_a_real_long_run_stops_growing_and_the_database_keeps_the_history(env) -> None:
    """Через HTTP, как это делает владелец: много команд подряд.

    Проверяется и то, ради чего вытеснение вообще допустимо, — что запись о
    забытой команде осталась в базе, причём ЗАВЕРШЁННОЙ. Раньше строку
    закрывал только GET статуса, которого могло не случиться.
    """
    svc = env.svc
    root = str(svc.settings.data_dir)
    approvals: list[str] = []

    async def one(i: int) -> str:
        payload = {"mode": "project_host", "command": f"echo retention-{i}", "cwd": root}
        first = await env.client.post("/api/terminal/run", json=payload)
        if first.status_code == 200:
            return first.json()["session_id"]
        assert first.status_code == 202, first.text
        approval = first.json()["error"]["approval_id"]
        await env.client.post(f"/api/approvals/{approval}", json={"approve": True, "by": "owner"})
        approvals.append(approval)
        answer = await env.client.post("/api/terminal/run",
                                       json=dict(payload, approval_id=approval))
        assert answer.status_code == 200, answer.text
        return answer.json()["session_id"]

    total = RETAIN_FINISHED + 40
    first_id = None
    for i in range(total):
        sid = await one(i)
        if first_id is None:
            first_id = sid
        for _ in range(400):                      # дождаться завершения команды
            if svc.terminal.sessions.get(sid) is None or svc.terminal.sessions[sid].finished:
                break
            await asyncio.sleep(0.005)

    assert len(svc.terminal.sessions) <= RETAIN_FINISHED + 1, (
        f"словарь сессий вырос до {len(svc.terminal.sessions)} за {total} команд")

    gone = await env.client.get(f"/api/terminal/sessions/{first_id}")
    assert gone.status_code == 404, "самая старая сессия обязана была уйти из памяти"

    async with svc.db.session() as s:
        row = (await s.execute(sa.select(term_t.c.status, term_t.c.exit_code)
                               .where(term_t.c.id == first_id))).first()
    assert row is not None, "история команды потеряна вместе с памятью"
    assert row[0] == "finished", f"строка осталась в статусе {row[0]!r}"
    assert row[1] == 0, f"код возврата забытой команды не записан: {row[1]!r}"

    listed = await env.client.get("/api/terminal/sessions")
    assert listed.status_code == 200 and listed.json(), "история команд не отдаётся"
