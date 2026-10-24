"""Telegram /stop also halts the Bossman tasks delegated from Telegram (function probe 24.10).

Before: console_halt stopped the computer, the PC-agent, image jobs and Claude/Codex, but a task delegated with
/confirm kept running. Offline: fake Telegram, fake core, real Store; only SUBMITTED delegations are touched, a
finished one is not counted, a pause (full=False) does not stop tasks.
"""
from __future__ import annotations

import asyncio

from bcc.telegram_companion.config import Person, Settings
from bcc.telegram_companion.service import Companion
from bcc.telegram_companion.store import Store

OWNER = Person(11111, 11111, 'owner', None)


class FakeTelegram:
    async def send(self, person, text, keyboard=None):
        pass

    async def call(self, method, payload=None, *a, **k):
        return True


class FakeCore:
    def __init__(self, running):
        self.running, self.stop_calls = set(running), []

    async def computer_stop(self):
        return {"stopped": True}

    async def task_stop(self, task_id):
        self.stop_calls.append(task_id)
        if task_id in self.running:
            self.running.discard(task_id)
            return True
        return False                                  # already finished: nothing to stop


def _companion(tmp_path, running):
    s = Settings((OWNER,), local_url='http://127.0.0.1:8083/v1', local_model='best', bot_token='x')
    store = Store(tmp_path)
    for nonce, task_id, phase in (("n1", 501, "submitted"), ("n2", 502, "submitted"), ("n3", 503, "pending")):
        store.db.execute("INSERT INTO proposals (id, who, body, phase, expires, task_id) VALUES (?,?,?,?,?,?)",
                         (nonce, "owner", store.seal({}), phase, 9e12, task_id))
    core = FakeCore(running)
    return Companion(s, store, FakeTelegram(), core, None), core


def _text(out):
    return out if isinstance(out, str) else getattr(out, "text", str(out))


def test_full_stop_halts_running_delegated_tasks_only(tmp_path):
    c, core = _companion(tmp_path, running={501})
    out = asyncio.run(c.console_halt(OWNER, full=True))
    assert sorted(core.stop_calls) == [501, 502], "only submitted delegations are asked to stop"
    assert core.running == set() and "Поручения из Telegram остановлены: 1" in _text(out)


def test_pause_does_not_stop_tasks(tmp_path):
    c, core = _companion(tmp_path, running={501})
    asyncio.run(c.console_halt(OWNER, full=False))
    assert core.stop_calls == [] and core.running == {501}
