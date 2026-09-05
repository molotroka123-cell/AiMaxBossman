"""V3 → замороженный V2: адаптеры на живом Command Center.

Здесь нет ни фейкового исполнителя, ни фейкового верификатора. Поднимается
настоящий `bcc` (Command Center V2) на временной SQLite, и цепочка V3 водит
его через публичный API: реальный `terminal.run` порождает реальный процесс,
реальная `bcc/v2/verification` перечитывает реальный файл, ASK проходит через
настоящую очередь approvals, и чек ложится в настоящую таблицу `tool_calls`.

Пропускается, если `bcc` не установлен (Bossman Core CI ставит только ядро) —
это честный skip с причиной, не тихий xfail.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

bcc = pytest.importorskip("bcc", reason="Command Center (bcc) не установлен рядом с ядром")

import httpx  # noqa: E402
import sqlalchemy as sa  # noqa: E402

from bossman_v3.adapters.command_center import CommandCenterRuntime, build_agent  # noqa: E402
from bossman_v3.contracts import TypedAction  # noqa: E402
from bossman_v3.execution import CompoundRunner, PlanStep  # noqa: E402
from bossman_v3.memory import TaskJournal  # noqa: E402


# ------------------------------------------------------------------ fixture

class Live:
    def __init__(self, rt, app, svc, work: Path, task: dict, agent: dict, run_id: int):
        self.rt, self.app, self.svc, self.work = rt, app, svc, work
        self.task, self.agent, self.run_id = task, agent, run_id

    def agent_(self):
        return build_agent(self.rt, self.svc, task=self.task, agent=self.agent, run_id=self.run_id)

    def approve_all_pending(self) -> int:
        pending = self.rt.call(self.svc.approvals.list(status="pending", limit=100))
        for row in pending:
            self.rt.call(self.svc.approvals.decide(row["id"], True, by="owner-test"))
        return len(pending)

    def tool_calls(self) -> list[dict]:
        from bcc.db import tool_calls as t
        async def q():
            async with self.svc.db.session() as s:
                return [dict(r._mapping) for r in (await s.execute(sa.select(t))).fetchall()]
        return self.rt.call(q())


@pytest.fixture
def live(tmp_path):
    from bcc.api import create_app
    from bcc.auth import HEADER
    from bcc.config import Settings
    from bcc.db import settings_kv

    rt = CommandCenterRuntime()
    data = tmp_path / "data"
    settings = Settings(data_dir=data, database_url=f"sqlite+aiosqlite:///{data / 'bcc.db'}",
                       ui_dir=tmp_path / "no-ui")
    work = tmp_path / "work"
    work.mkdir()

    async def boot():
        app = create_app(settings, announce_token=False, start_workers=False)
        svc = app.state.svc
        await svc.start()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t",
                                     headers={HEADER: svc.auth.token}) as c:
            pr = (await c.post("/api/providers", json={"name": "p", "kind": "openai_compat",
                  "base_url": "http://127.0.0.1:8080/v1", "api_key": "sk-test-abcd"})).json()
            m = (await c.post("/api/models", json={"provider_id": pr["id"], "name": "m",
                  "alias": "v3-live"})).json()
            ag = (await c.post("/api/agents", json={"name": "v3", "system_prompt": "-",
                  "model_id": m["id"], "max_steps": 4})).json()
            # В V2 нет GET /agents/{id}; PATCH сам возвращает обновлённую строку.
            ag = (await c.patch(f"/api/agents/{ag['id']}", json={"tools": ["terminal.run"],
                                "permissions": {"terminal.run": True}})).json()
            task = (await c.post("/api/tasks", json={"title": "v3", "prompt": "цепочка v3",
                    "agent_id": ag["id"], "run_now": True})).json()["task"]
        async with svc.db.session() as s:
            await s.execute(sa.insert(settings_kv).values(
                key="terminal.roots", value_enc=svc.vault.encrypt(json.dumps([str(work)]))))
            await s.commit()
            from bcc.db import task_runs
            run_id = (await s.execute(sa.select(task_runs.c.id).where(
                task_runs.c.task_id == task["id"]))).scalar_one()
        return app, svc, task, ag, run_id

    app, svc, task, ag, run_id = rt.call(boot())
    yield Live(rt, app, svc, work, task, ag, run_id)
    rt.call(svc.stop())
    rt.close()


def _write(work: Path, name: str, content: str = "1") -> TypedAction:
    return TypedAction(
        action_type="terminal.run",
        args={"command": f"python -c \"open('{name}','w').write('{content}')\"",
              "mode": "project_host", "cwd": str(work),
              "expect": {"kind": "file", "target": str(work / name), "expect": {"exists": True}}})


def _plan(work: Path) -> list[PlanStep]:
    return [PlanStep("s1", "создать a.txt", _write(work, "a.txt")),
            PlanStep("s2", "создать b.txt", _write(work, "b.txt"))]


def _journal(tmp_path, work) -> TaskJournal:
    return TaskJournal.start(task_id="v3-live", plan=[(s.step_id, s.intent) for s in _plan(work)],
                             root=tmp_path / "journal")


# ------------------------------------------------------------------- tests

def test_ask_goes_through_v2_approvals_and_nothing_runs_before_owner_decides(live, tmp_path):
    """project_host в V2 — всегда ASK. Первый прогон обязан остановиться на
    ожидании владельца, и НИЧЕГО не должно исполниться."""
    res = CompoundRunner(live.agent_(), _journal(tmp_path, live.work)).run(_plan(live.work))

    assert res.completed is False and res.blocked_at == "s1"
    assert not (live.work / "a.txt").exists()
    assert live.rt.call(live.svc.approvals.list(status="pending", limit=10)), "запрос не создан"


def test_real_chain_completes_after_owner_approval_with_real_files_and_receipts(live, tmp_path):
    j = _journal(tmp_path, live.work)
    plan = _plan(live.work)

    # владелец одобряет по одному действию — как в настоящем UI
    for _ in range(3):
        res = CompoundRunner(live.agent_(), j).run(plan)
        if res.completed:
            break
        assert live.approve_all_pending() >= 1

    assert res.completed is True
    assert (live.work / "a.txt").read_text() == "1"
    assert (live.work / "b.txt").read_text() == "1"

    rows = live.tool_calls()
    executed = [r for r in rows if r["status"] == "executed" and r["effect"] == "v3"]
    assert len(executed) == 2 and {r["source"] for r in executed} == {"terminal"}


def test_restart_between_steps_resumes_from_the_unfinished_one(live, tmp_path):
    j = _journal(tmp_path, live.work)
    plan = _plan(live.work)

    CompoundRunner(live.agent_(), j).run(plan)          # s1 -> ожидание
    live.approve_all_pending()
    CompoundRunner(live.agent_(), j).run(plan)          # s1 исполнен, s2 -> ожидание
    assert (live.work / "a.txt").exists()
    before = len([r for r in live.tool_calls() if r["status"] == "executed"])
    del j                                               # процесс умер

    revived = TaskJournal.load(task_id="v3-live", root=tmp_path / "journal")
    assert revived.next_step().step_id == "s2"
    live.approve_all_pending()
    res = CompoundRunner(live.agent_(), revived).run(plan)

    assert res.completed is True
    after = len([r for r in live.tool_calls() if r["status"] == "executed"])
    assert after == before + 1, "после рестарта s1 исполнился второй раз"


def test_tool_ran_but_expected_effect_absent_is_not_completed(live, tmp_path):
    """Граница V2, перенесённая в V3 через адаптер: команда реально отработала
    (exit 0), но объявленный эффект не наступил — шаг не подтверждён."""
    bad = TypedAction(action_type="terminal.run",
                      args={"command": "python -c \"open('x.txt','w').write('1')\"",
                            "mode": "project_host", "cwd": str(live.work),
                            "expect": {"kind": "file", "target": str(live.work / "y.txt"),
                                       "expect": {"exists": True}}})
    plan = [PlanStep("s1", "написать y.txt", bad)]
    j = TaskJournal.start(task_id="v3-live", plan=[("s1", "написать y.txt")],
                          root=tmp_path / "journal")

    CompoundRunner(live.agent_(), j).run(plan)
    live.approve_all_pending()
    res = CompoundRunner(live.agent_(), j).run(plan)

    assert (live.work / "x.txt").exists()               # инструмент реально сработал
    assert res.completed is False and res.blocked_at == "s1"
    assert j.next_step().step_id == "s1"                # и шаг остался незакрытым


def test_step_without_declared_expectation_is_never_verified(live, tmp_path):
    a = TypedAction(action_type="terminal.run",
                    args={"command": "python -c \"open('z.txt','w').write('1')\"",
                          "mode": "project_host", "cwd": str(live.work)})
    j = TaskJournal.start(task_id="v3-live", plan=[("s1", "без ожидания")], root=tmp_path / "journal")
    plan = [PlanStep("s1", "без ожидания", a)]

    CompoundRunner(live.agent_(), j).run(plan)
    live.approve_all_pending()
    res = CompoundRunner(live.agent_(), j).run(plan)

    assert res.completed is False
    assert "ожидани" in res.reason


def test_denied_by_v2_policy_never_executes(live, tmp_path):
    """DENY решает V2 (`hard_deny` в tools_terminal): force-push запрещён
    без исключений. Адаптер не должен ни исполнить, ни спросить владельца."""
    denied = TypedAction(action_type="terminal.run",
                         args={"command": "git push --force origin main", "mode": "project_host",
                               "cwd": str(live.work)})
    j = TaskJournal.start(task_id="v3-live", plan=[("s1", "force push")], root=tmp_path / "journal")
    res = CompoundRunner(live.agent_(), j).run([PlanStep("s1", "force push", denied)])

    assert res.completed is False and "PolicyDenied" in res.reason
    assert not live.rt.call(live.svc.approvals.list(status="pending", limit=10))
    assert live.tool_calls() == []
