"""Jeff closeout 10.10, gap 4: Jeff creates long tasks from a conversation (explicit request only) and resumes them.

Before: the Jeff 1.5 task store existed (test_jeff_1_5_tasks.py) but nothing created a task from a chat and nothing
resumed one after a restart. Fakes only: fake model drafter, fake sender, real TaskStore files in tmp.
A crash is a BaseException the store does not catch, exactly what a killed process leaves behind.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from bcc.pit import jeff_settings as js
from bcc.pit.j2 import J2Pipeline, TurnContext
from bcc.pit.j2 import longtask as LT
from bcc.pit.tasks import TaskStore

PK = "c" * 64
OTHER = "d" * 64


class Crash(BaseException):
    """Process killed mid-step."""


class Drafter:
    def __init__(self, text="План: день 1 — Кремль; день 2 — Свияжск.", crash_first=False, fail=0):
        self.text, self.crash_first, self.fail, self.calls = text, crash_first, fail, []

    async def __call__(self, person_key, goal):
        self.calls.append((person_key, goal))
        if self.crash_first:
            self.crash_first = False
            raise Crash()
        if self.fail:
            self.fail -= 1
            raise TimeoutError("model slow")
        return self.text


class Sender:
    def __init__(self, result="sent", crash=False):
        self.result, self.crash, self.sent = result, crash, []

    async def __call__(self, person_key, text, key):
        if self.crash:
            self.crash = False
            raise Crash()
        self.sent.append((person_key, text, key))
        return self.result


def module(tmp_path, drafter=None, sender=None, allowed=None):
    return LT.LongTaskModule(TaskStore(tmp_path), drafter=drafter or Drafter(), sender=sender or Sender(),
                             allowed=allowed)


def ctx(text, key=PK, memory=True):
    return TurnContext(person_key=key, who="tg:1", text=text, memory_enabled=memory)


async def settle(mod):
    for _ in range(50):
        pending = [t for t in mod._runs.values() if not t.done()]
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)


def test_explicit_request_creates_runs_and_delivers_once(tmp_path):
    mod, sender = None, Sender()

    async def scenario():
        nonlocal mod
        mod = module(tmp_path, sender=sender)
        reply = (await mod.pre_route(ctx("поставь задачу: составь план поездки в Казань на 2 дня"))).reply
        await settle(mod)
        return reply
    reply = asyncio.run(scenario())
    assert "Поставил задачу 1" in reply and "Казань" in reply
    assert len(sender.sent) == 1 and sender.sent[0][0] == PK
    assert "Задача готова" in sender.sent[0][1] and "Свияжск" in sender.sent[0][1]
    task = TaskStore(tmp_path).list(PK)[0]
    assert task["state"] == "DONE" and task["steps"][0]["kind"] == "jeff_work"
    listing = asyncio.run(mod.pre_route(ctx("мои задачи"))).reply
    assert "1. готово — составь план поездки" in listing
    assert "Свияжск" in asyncio.run(mod.pre_route(ctx("результат задачи 1"))).reply


@pytest.mark.parametrize("text", ["какие у меня задачи на сегодня?", "задача: купить хлеб", "поставь будильник",
                                  "мне надо поставить задачу начальнику"])
def test_ordinary_talk_never_creates_a_task(tmp_path, text):
    mod = module(tmp_path)
    assert asyncio.run(mod.pre_route(ctx(text))) is None
    assert TaskStore(tmp_path).list(PK) == []


def test_restart_resumes_a_task_killed_mid_work(tmp_path):
    drafter, sender = Drafter(crash_first=True), Sender()

    async def first_process():
        mod = module(tmp_path, drafter=drafter, sender=sender)
        await mod.pre_route(ctx("поставь задачу: напиши резюме книги"))
        await settle(mod)
    asyncio.run(first_process())                                  # the crash killed the run, nothing delivered
    task = TaskStore(tmp_path).list(PK)[0]
    assert task["state"] == "RUNNING" and sender.sent == []

    async def second_process():
        mod = module(tmp_path, drafter=drafter, sender=sender)   # new process, same files
        assert mod.resume_all() == 1
        await settle(mod)
    asyncio.run(second_process())
    assert TaskStore(tmp_path).list(PK)[0]["state"] == "DONE"
    assert len(sender.sent) == 1 and len(drafter.calls) == 2      # work redone (no external effect), sent once


def test_a_delivery_in_flight_at_the_crash_is_never_repeated(tmp_path):
    sender = Sender(crash=True)

    async def first_process():
        mod = module(tmp_path, sender=sender)
        await mod.pre_route(ctx("поставь задачу: подбери 5 книг по истории"))
        await settle(mod)
    asyncio.run(first_process())

    async def second_process():
        mod = module(tmp_path, sender=sender)
        mod.resume_all()
        await settle(mod)
        return mod
    mod = asyncio.run(second_process())
    task = TaskStore(tmp_path).list(PK)[0]
    assert task["state"] == "UNKNOWN_OUTCOME" and sender.sent == []          # no blind repeat
    assert "доставка не подтверждена" in asyncio.run(mod.pre_route(ctx("мои задачи"))).reply
    assert "Свияжск" in asyncio.run(mod.pre_route(ctx("результат задачи 1"))).reply
    assert mod.resume_all() == 0                                              # UNKNOWN waits for a human


def test_slow_model_is_retried_then_fails_honestly(tmp_path):
    async def scenario():
        mod = module(tmp_path, drafter=Drafter(fail=5))
        await mod.pre_route(ctx("поставь задачу: длинный отчёт"))
        await settle(mod)
    asyncio.run(scenario())
    task = TaskStore(tmp_path).list(PK)[0]
    assert task["state"] == "FAILED"


def test_jeff_window_result_is_kept_for_the_next_turn(tmp_path):
    async def scenario():
        mod = module(tmp_path, sender=Sender(result="undeliverable"))
        await mod.pre_route(ctx("поставь задачу: план тренировок"))
        await settle(mod)
        return mod
    mod = asyncio.run(scenario())
    assert TaskStore(tmp_path).list(PK)[0]["state"] == "DONE"
    assert "Свияжск" in asyncio.run(mod.pre_route(ctx("результат задачи 1"))).reply


def test_consent_limits_cancel_and_forget(tmp_path):
    mod = module(tmp_path, drafter=Drafter())
    off = asyncio.run(mod.pre_route(ctx("поставь задачу: что-нибудь", memory=False))).reply
    assert "память у тебя выключена" in off and TaskStore(tmp_path).list(PK) == []
    secret = asyncio.run(mod.pre_route(ctx("поставь задачу: войди с паролем sk-or-v1-" + "a" * 40))).reply
    assert "пароль или ключ" in secret

    async def many():
        m = module(tmp_path, drafter=Drafter(), allowed=lambda k: True)
        m._schedule = lambda owner, task_id: False                 # keep them open
        replies = [(await m.pre_route(ctx(f"поставь задачу: задача номер {i}"))).reply for i in range(4)]
        return m, replies
    m, replies = asyncio.run(many())
    assert "уже 3 незавершённые" in replies[-1]
    assert asyncio.run(m.pre_route(ctx("отмени задачу 2"))).reply == "Отменил задачу 2."
    assert m._listed(PK)[1]["state"] == "FAILED"
    assert asyncio.run(m.pre_route(ctx("мои задачи", key=OTHER))).reply.startswith("Задач нет")   # isolation
    assert "Удалил" in asyncio.run(m.pre_route(ctx("удали мои задачи"))).reply
    assert TaskStore(tmp_path).list(PK) == []


def test_switched_off_module_neither_answers_nor_runs(tmp_path):
    sender = Sender()

    async def scenario():
        mod = module(tmp_path, sender=sender)
        pipe = J2Pipeline([mod], module_switch=lambda name: name != "longtask")
        assert await pipe.pre_route(ctx("поставь задачу: план")) is None
        TaskStore(tmp_path).create(PK, "план", [{"id": "work", "kind": "jeff_work"},
                                                {"id": "deliver", "kind": "jeff_deliver"}])
        mod.resume_all()
        await settle(mod)
    asyncio.run(scenario())
    assert sender.sent == [] and TaskStore(tmp_path).list(PK)[0]["state"] == "PLANNED"


# ------------------------------------------------------------------------------ through the real runtime
def test_runtime_end_to_end_and_delete_me_removes_tasks(tmp_path, monkeypatch):
    from .test_pit_runtime import FakeAdapter, make_runtime, message, warm
    monkeypatch.delenv(js.ENV_PATH, raising=False)
    js._cache.clear()
    runtime = make_runtime(tmp_path, adapter=FakeAdapter("Готовый план: шаг 1, шаг 2."))
    person = runtime.settings.people[0]
    key = runtime.vault.key_for_telegram(person.user_id)
    warm(runtime, key)
    sent: list[str] = []

    async def send(p, text, *a, **kw):
        sent.append(text)
        return 1
    monkeypatch.setattr(runtime.telegram, "send", send)

    async def scenario():
        reply = await runtime.handle(person, message("поставь задачу: составь план переезда", message_id=300))
        mod = next(m for m in runtime.j2.modules if m.name == "longtask")
        await settle(mod)
        return reply
    reply = asyncio.run(scenario())
    assert "Поставил задачу 1" in reply
    assert any("Задача готова" in t and "шаг 2" in t for t in sent)
    assert (runtime.home / "tasks" / key).is_dir()
    asyncio.run(runtime.handle(person, message("/delete_me", message_id=301)))
    asyncio.run(runtime.handle(person, message("подтверждаю", message_id=302)))
    assert not (runtime.home / "tasks" / key).exists()
    asyncio.run(runtime.close())
