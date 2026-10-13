"""/work in the pult: owner-only entry point of the Qwen L1 operator (stage 2.2).

Proves with fakes: guests get nothing and the core is never called; STOP/pause/lock blocks the command like the
other generation commands; the owner's decision buttons answer only operator_decision approvals; STOP cancels the
running operator; no new key appears in the companion config (older installed companions crash on unknown keys).
"""
from __future__ import annotations

import asyncio
import dataclasses

from bcc.telegram_companion import operator_bridge as ob
from bcc.telegram_companion.config import Person, Settings
from bcc.telegram_companion.service import Companion
from bcc.telegram_companion.store import Store

OWNER = Person(11111, 11111, 'owner', None)
GUEST = Person(22222, 22222, 'guest', None)


def msg(p, text):
    return {'from': {'id': p.user_id, 'is_bot': False}, 'chat': {'id': p.chat_id, 'type': 'private'}, 'text': text}


class FakeTelegram:
    def __init__(self):
        self.sent = []

    async def send(self, person, text, keyboard=None):
        self.sent.append((str(text), keyboard))

    async def call(self, *a, **k):
        return True


class FakeCore:
    def __init__(self, kinds=None, statuses=("running", "completed")):
        self.started, self.decided, self.cancelled = [], [], []
        self.kinds = kinds or {}
        self.statuses = list(statuses)
        self.rec = {"id": "abc123abc123", "lab": {"branch": "lab/op-abc123abc123"}, "decisions": [],
                    "model_calls": [{"role": "planner", "model": "bossman-community-qwen-uncensored:latest",
                                     "tokens_in": 10, "tokens_out": 5}], "commit_sha": "f" * 40,
                    "verifier": {"verdict": "APPROVE", "independence": "different_family_free_cloud"},
                    "promotion": {"reason": "rule 4"}}

    async def operator_start(self, task):
        self.started.append(task)
        return {"id": self.rec["id"], "lab": self.rec["lab"], "status": "queued"}

    async def operator_task(self, task_id):
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        if status == "running":
            self.rec["decisions"] = [{"approval_id": 7, "status": "PENDING", "category": "promotion",
                                      "action": "git push", "why": "w", "recommendation": "r", "concerns": "c"}]
        return {**self.rec, "status": status}

    async def operator_cancel(self, task_id):
        self.cancelled.append(task_id)
        return {}

    async def approval(self, aid):
        return {"id": aid, "kind": self.kinds.get(aid, "x")}

    async def decide_approval(self, aid, approve, by):
        self.decided.append((aid, approve, by))
        return {"id": aid}


def build(tmp_path, core):
    s = Settings((OWNER, GUEST), local_url='http://127.0.0.1:8083/v1', local_model='best', bot_token='x')
    tg = FakeTelegram()
    return Companion(s, Store(tmp_path), tg, core, None), tg


def test_guest_gets_nothing_and_core_is_never_called(tmp_path):
    core = FakeCore()
    c, _tg = build(tmp_path, core)
    for text in ('/work поправь доки', '/work_yes 7', '/work_stop', '/work_status'):
        assert asyncio.run(c.handle(GUEST, msg(GUEST, text))) == ob.WORK_DENIED
    assert core.started == [] and core.decided == []


def test_stop_pause_lock_blocks_new_work(tmp_path):
    core = FakeCore()
    c, _tg = build(tmp_path, core)
    c.store.put("delegation_locked", True)
    out = asyncio.run(c.handle(OWNER, msg(OWNER, '/work поправь доки')))
    assert out == ob.WORK_LOCKED and core.started == []


def test_work_starts_reports_decisions_with_buttons_and_final_receipt(tmp_path, monkeypatch):
    monkeypatch.setattr(ob, "POLL_SECONDS", 0.01)
    core = FakeCore()
    c, tg = build(tmp_path, core)

    async def main():
        reply = await c.handle(OWNER, msg(OWNER, '/work поправь докстринг'))
        await c.agent_jobs["operator"]
        return reply

    reply = asyncio.run(main())
    assert core.started == ['поправь докстринг']
    assert 'abc123abc123' in getattr(reply, 'text', str(reply))
    texts = [t for t, _k in tg.sent]
    assert any('Решение владельца' in t and 'Рекомендация оператора' in t and 'Опасения' in t for t in texts)
    buttons = next(k for t, k in tg.sent if 'Решение владельца' in t)
    commands = [c.buttons[data[2:]][1] for row in buttons for _label, data in row]
    assert '/work_yes 7' in commands and '/work_no 7' in commands
    final = texts[-1]
    assert 'bossman-community-qwen-uncensored' in final and '$0.00' in final and 'lab/op-abc123abc123' in final
    assert 'заблокировано' in final or 'правило 4' in final


def test_decision_buttons_only_answer_operator_decisions(tmp_path):
    core = FakeCore(kinds={7: ob.DECISION_KIND, 9: "terminal.run"})
    c, _tg = build(tmp_path, core)
    assert 'Разрешено' in asyncio.run(c.handle(OWNER, msg(OWNER, '/work_yes 7')))
    assert 'Отклонено' in asyncio.run(c.handle(OWNER, msg(OWNER, '/work_no 7')))
    assert 'не решение оператора' in asyncio.run(c.handle(OWNER, msg(OWNER, '/work_yes 9')))
    assert core.decided == [(7, True, "owner-pult"), (7, False, "owner-pult")]


def test_stop_cancels_running_operator_and_backend_task(tmp_path, monkeypatch):
    monkeypatch.setattr(ob, "POLL_SECONDS", 0.05)
    core = FakeCore(statuses=("running",))
    c, _tg = build(tmp_path, core)

    async def main():
        await c.handle(OWNER, msg(OWNER, '/work долгая задача'))
        await asyncio.sleep(0.1)
        assert c.cancel_agents() == 1
        try:
            await c.agent_jobs["operator"]
        except asyncio.CancelledError:
            pass

    asyncio.run(main())
    assert core.cancelled == ["abc123abc123"]


def test_no_new_companion_config_keys():
    fields = {f.name for f in dataclasses.fields(Settings)}
    assert not any('operator' in f or f.startswith('work') for f in fields)


def test_a_task_held_by_the_backend_is_named_and_can_be_stopped_not_a_bare_conflict(tmp_path):
    """13.10 live: an API-started operator task held the single slot and the owner's /work answered
    «Действие не подтверждено: CONFLICT». Now the reply names the holder and gives stop/status buttons."""
    from bcc.telegram_companion.config import CompanionError

    class BusyCore(FakeCore):
        async def operator_start(self, task):
            raise CompanionError("CONFLICT")

        async def operator_active(self):
            return {"id": "11026bf88acb", "status": "running", "task": "поправь is_loopback_host"}

    core = BusyCore()
    app, tg = build(tmp_path, core)

    async def go():
        first = await app.handle(OWNER, msg(OWNER, '/work сделай что-нибудь'))
        stop = await app.handle(OWNER, msg(OWNER, '/work_stop 11026bf88acb'))
        return first, stop

    first, stop = asyncio.run(go())
    text = first.text if hasattr(first, 'text') else str(first)
    assert 'CONFLICT' not in text and '11026bf88acb' in text and 'Остановить' in str(getattr(first, 'keyboard', '')) + text
    assert core.cancelled == ['11026bf88acb']
    assert 'Остановка запрошена' in (stop if isinstance(stop, str) else getattr(stop, 'text', str(stop)))
