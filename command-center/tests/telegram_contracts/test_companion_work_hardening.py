"""Package B pult side: per-task ctx, queueing with cancel, resilient watcher + heartbeat, real decision outcome."""
from __future__ import annotations

import asyncio

from bcc.telegram_companion import operator_bridge as ob
from bcc.telegram_companion.config import CompanionError

from test_companion_work_command import OWNER, FakeCore, build, msg


def text_of(x):
    return x.text if hasattr(x, "text") else str(x)


def test_parse_ctx():
    assert ob.parse_ctx("ctx=64k сделай X") == (65536, "сделай X", "")
    assert ob.parse_ctx("ctx=256k X")[0] == 262144
    assert ob.parse_ctx("ctx=65536 X")[0] == 65536
    assert ob.parse_ctx("просто задача") == (None, "просто задача", "")
    assert ob.parse_ctx("ctx=1k X")[2]
    assert ob.parse_ctx("ctx=999k X")[2]


def test_work_passes_ctx_to_the_core(tmp_path, monkeypatch):
    monkeypatch.setattr(ob, "POLL_SECONDS", 0.01)

    class Core(FakeCore):
        async def operator_start(self, task, num_ctx=None):
            self.started.append((task, num_ctx))
            return {"id": self.rec["id"], "lab": self.rec["lab"], "status": "queued"}

    core = Core()
    c, _tg = build(tmp_path, core)

    async def go():
        r = await c.handle(OWNER, msg(OWNER, "/work ctx=64k разбери модуль"))
        await c.agent_jobs["operator"]
        return r

    reply = asyncio.run(go())
    assert core.started == [("разбери модуль", 65536)] and "64k" in text_of(reply)


def test_second_work_is_queued_not_refused_and_can_be_cancelled(tmp_path, monkeypatch):
    monkeypatch.setattr(ob, "POLL_SECONDS", 0.02)

    class Core(FakeCore):
        def __init__(self):
            super().__init__(statuses=("running",))
            self.n = 0

        async def operator_start(self, task, num_ctx=None):
            self.n += 1
            self.started.append(task)
            rid = f"{self.n:012x}"
            return {"id": rid, "lab": {"branch": f"lab/op-{rid}"}, "status": "queued",
                    "queue_position": (self.n - 1) or None}

        async def operator_task(self, task_id):
            return {**self.rec, "id": task_id, "status": "running", "decisions": [], "steps": [1, 2]}

        async def operator_queue(self):
            return [{"id": "000000000001", "status": "running", "task": "a"},
                    {"id": "000000000002", "status": "queued", "queue_position": 1, "task": "b"}]

    core = Core()
    c, _tg = build(tmp_path, core)

    async def go():
        first = await c.handle(OWNER, msg(OWNER, "/work первая"))
        second = await c.handle(OWNER, msg(OWNER, "/work вторая"))
        status = await c.handle(OWNER, msg(OWNER, "/work_status"))
        cancel = await c.handle(OWNER, msg(OWNER, "/work_stop 000000000002"))
        c.cancel_agents()
        await asyncio.sleep(0.05)
        return first, second, status, cancel

    first, second, status, cancel = asyncio.run(go())
    assert core.started == ["первая", "вторая"]
    assert "очереди" in text_of(second) and "000000000002" in text_of(second)
    assert "Очередь оператора" in text_of(status) and "№1" in text_of(status)
    assert "Остановка запрошена" in text_of(cancel) and "000000000002" in core.cancelled


def test_watcher_survives_network_outage_and_sends_heartbeat_and_receipt(tmp_path, monkeypatch):
    monkeypatch.setattr(ob, "POLL_SECONDS", 0.01)
    monkeypatch.setattr(ob, "BACKOFF_BASE", 0.01)
    monkeypatch.setattr(ob, "BACKOFF_MAX", 0.02)
    monkeypatch.setattr(ob, "OUTAGE_NOTICE_S", 0.05)
    monkeypatch.setattr(ob, "HEARTBEAT_SECONDS", 0.05)

    class Flaky(FakeCore):
        def __init__(self):
            super().__init__()
            self.calls = 0

        async def operator_task(self, task_id):
            self.calls += 1
            if 3 <= self.calls <= 25:                       # a long NETWORK_UNAVAILABLE stretch
                raise CompanionError("NETWORK_UNAVAILABLE")
            if self.calls < 60:
                return {**self.rec, "status": "running", "decisions": [], "steps": [1, 2, 3],
                        "created_at": 0, "phase": {"name": "coder"}}
            return {**self.rec, "status": "completed", "decisions": []}

    core = Flaky()
    c, tg = build(tmp_path, core)

    async def go():
        await c.handle(OWNER, msg(OWNER, "/work длинная"))
        await asyncio.wait_for(c.agent_jobs["operator"], 20)

    asyncio.run(go())
    texts = [t for t, _k in tg.sent]
    assert any("связь с Bossman потеряна" in t for t in texts)
    assert any("восстановлена" in t for t in texts)
    assert any("работает: шаг 3" in t for t in texts)
    assert "Оператор Qwen · задача" in texts[-1] and "completed" in texts[-1]


def test_final_receipt_is_retried_until_telegram_takes_it(tmp_path, monkeypatch):
    monkeypatch.setattr(ob, "POLL_SECONDS", 0.01)
    monkeypatch.setattr(ob, "BACKOFF_BASE", 0.01)
    monkeypatch.setattr(ob, "BACKOFF_MAX", 0.01)
    core = FakeCore(statuses=("completed",))
    c, tg = build(tmp_path, core)
    real = tg.send
    fails = {"n": 3}

    async def flaky(person, text, keyboard=None):
        if fails["n"] > 0:
            fails["n"] -= 1
            raise CompanionError("NETWORK_UNAVAILABLE")
        await real(person, text, keyboard)
    tg.send = flaky

    async def go():
        await c.handle(OWNER, msg(OWNER, "/work задача"))
        await c.agent_jobs["operator"]

    asyncio.run(go())
    assert any("Оператор Qwen · задача" in t for t, _k in tg.sent) and fails["n"] == 0


def test_yes_answer_shows_the_real_outcome_not_a_promise(tmp_path, monkeypatch):
    monkeypatch.setattr(ob, "OUTCOME_WAIT_S", 0.05)
    d = {"approval_id": 7, "status": "PENDING", "category": "protected_file", "executable": False,
         "action": "изменить файл docs/constitution/X.md"}

    class Core(FakeCore):
        resolved = False

        async def operator_task(self, task_id):
            dd = dict(d)
            if self.resolved:
                dd.update(status="APPROVED_NOT_EXECUTED", note="конституцию правит только владелец")
            return {**self.rec, "decisions": [dd], "status": "running"}

        async def decide_approval(self, aid, approve, by):
            self.resolved = True
            return await super().decide_approval(aid, approve, by)

    core = Core(kinds={7: ob.DECISION_KIND})
    c, _tg = build(tmp_path, core)
    c.store.put("operator_last_task", "abc123abc123")
    code = c.issue_work_grant(OWNER, 7, dict(d))
    out = text_of(asyncio.run(c.handle(OWNER, msg(OWNER, f"/work_yes 7 {code}"))))
    assert "НЕ выполнено" in out and "продолжит" not in out

    class Slow(Core):
        async def decide_approval(self, aid, approve, by):      # backend has not recorded it yet
            return {"id": aid}

    (tmp_path / "b").mkdir()
    c2, _ = build(tmp_path / "b", Slow(kinds={7: ob.DECISION_KIND}))
    c2.store.put("operator_last_task", "abc123abc123")
    code2 = c2.issue_work_grant(OWNER, 7, None)
    out2 = text_of(asyncio.run(c2.handle(OWNER, msg(OWNER, f"/work_yes 7 {code2}"))))
    assert "НЕ выполняет" in out2 and "конце текущего шага" in out2 and "продолжит" not in out2
    assert "в конце ТЕКУЩЕГО шага" in ob.decision_text("t", {})
