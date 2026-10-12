"""Jeff: a turn that ended in a state Jeff cannot prove is announced to the participant - once, honestly.

Two sources: ``interrupted_unknown`` (the process died mid-turn) and ``delivery_unknown`` (a send ended
unconfirmed), plus a long task whose delivery ended UNKNOWN_OUTCOME.  The rules under test:

* the participant is told, in plain Russian, that Jeff does not know whether the answer arrived;
* the notice never claims delivery and the original reply is never resent (a resend could be a duplicate);
* at most once (a crash mid-send is not repeated; failures are bounded), never after owner STOP, never for a
  blocked participant, never for an update older than a day, erased by /forget.
No network: Telegram is a fake that records sends.
"""
from __future__ import annotations

import asyncio
import time

from bcc.pit import runtime as rt
from bcc.pit.j2 import TurnContext
from bcc.pit.j2 import longtask as LT
from bcc.pit.tasks import TaskStore

from .test_pit_runtime import make_runtime, message


class FakeTelegram:
    def __init__(self, fail: int = 0, message_id: int = 77):
        self.sent: list[tuple[int, str]] = []
        self.fail, self.message_id, self.attempts = fail, message_id, 0

    async def send(self, person, text, **kw):
        self.attempts += 1
        if self.fail:
            self.fail -= 1
            raise OSError("network down")
        self.sent.append((person.user_id, text))
        return self.message_id


def _interrupted(tmp_path, *, finish: str | None = None):
    """A runtime whose single participant has one update in flight; ``finish`` ends it unknown instead of a crash."""
    runtime = make_runtime(tmp_path)
    runtime.telegram = FakeTelegram()
    person = runtime.settings.people[0]
    body = message("расскажи про Казань")
    assert runtime.store.ingest(5, person.key, body) is True
    assert runtime.store.claim(person.key, "chat")[0] == 5
    if finish:
        runtime.store.finish(5, finish)
    else:
        runtime.store.recover()                     # what the next process does: processing -> interrupted_unknown
    return runtime, person


def _state(runtime, update_id: int = 5):
    row = runtime.store.db.execute("SELECT state, attempts FROM interruption_notices WHERE update_id=?",
                                   (update_id,)).fetchone()
    return (row["state"], row["attempts"]) if row else None


def test_a_turn_killed_mid_flight_is_announced_once_without_claiming_delivery(tmp_path):
    runtime, person = _interrupted(tmp_path)
    assert runtime.store.db.execute("SELECT phase FROM inbox WHERE id=5").fetchone()["phase"] == "interrupted_unknown"
    assert asyncio.run(runtime._announce_interruptions()) == 1
    (user_id, text), = runtime.telegram.sent
    assert user_id == person.user_id
    assert "не знаю, успел ли ответ" in text and "напиши сообщение ещё раз" in text
    low = text.lower()
    assert "доставлен" not in low and "отправил" not in low and "ответил" not in low     # no false confirmation
    assert "Казань" not in text                                                          # never quotes the message
    assert _state(runtime) == ("sent", 1)
    assert asyncio.run(runtime._announce_interruptions()) == 0                          # once, not again
    assert len(runtime.telegram.sent) == 1


def test_an_unconfirmed_send_gets_its_own_notice(tmp_path):
    runtime, _ = _interrupted(tmp_path, finish="delivery_unknown")
    assert asyncio.run(runtime._announce_interruptions()) == 1
    assert "связь оборвалась" in runtime.telegram.sent[0][1]
    assert "Повторно я его не отправляю" in runtime.telegram.sent[0][1]


def test_owner_stop_is_never_turned_into_a_retry_invitation(tmp_path):
    runtime = make_runtime(tmp_path)
    runtime.telegram = FakeTelegram()
    person = runtime.settings.people[0]
    runtime.store.ingest(5, person.key, message("долгий вопрос"))
    runtime.store.claim(person.key, "chat")
    (runtime.home / rt.STOP_FLAG).write_text("stop", encoding="utf-8")
    runtime.store.finish(5, "delivery_unknown")             # the worker unwinding under STOP
    assert _state(runtime)[0] == "suppressed_stop"
    assert asyncio.run(runtime._announce_interruptions()) == 0
    (runtime.home / rt.STOP_FLAG).unlink()
    assert asyncio.run(runtime._announce_interruptions()) == 0                          # not after the flag is gone
    assert runtime.telegram.sent == []


def test_a_failing_send_is_retried_a_bounded_number_of_times_and_then_left_alone(tmp_path, monkeypatch):
    runtime, _ = _interrupted(tmp_path)
    runtime.telegram = FakeTelegram(fail=99)
    monkeypatch.setattr(rt.PITStore, "NOTICE_RETRY_S", 0.0)
    for _ in range(6):
        asyncio.run(runtime._announce_interruptions())
    assert runtime.telegram.attempts == rt.PITStore.NOTICE_MAX_ATTEMPTS == 3
    assert runtime.telegram.sent == [] and _state(runtime)[0] == "unknown"


def test_a_crash_in_the_middle_of_the_send_is_not_repeated_blindly(tmp_path):
    runtime, person = _interrupted(tmp_path)
    assert runtime.store.begin_interruption_notice(5, person.key, person.user_id, person.chat_id, time.time())
    assert _state(runtime)[0] == "sending"                  # the process died here: nobody knows if it went out
    assert asyncio.run(runtime._announce_interruptions()) == 0
    assert runtime.telegram.sent == []


def test_an_unverified_send_is_not_claimed_and_not_repeated(tmp_path):
    runtime, _ = _interrupted(tmp_path)
    runtime.telegram = FakeTelegram(message_id=0)           # the transport gave no message id
    assert asyncio.run(runtime._announce_interruptions()) == 0
    assert _state(runtime)[0] == "unknown"
    assert asyncio.run(runtime._announce_interruptions()) == 0
    assert runtime.telegram.attempts == 1


def test_an_old_update_a_blocked_participant_and_forget_get_no_notice(tmp_path, monkeypatch):
    old, _ = _interrupted(tmp_path / "old")
    old.store.db.execute("UPDATE inbox SET created=? WHERE id=5", (time.time() - 2 * 24 * 3600,))
    assert asyncio.run(old._announce_interruptions()) == 0 and _state(old)[0] == "expired"

    blocked, _ = _interrupted(tmp_path / "blocked")
    monkeypatch.setattr(blocked, "_blocked", lambda user_id, chat_id: True)
    assert asyncio.run(blocked._announce_interruptions()) == 0 and _state(blocked)[0] == "suppressed_blocked"
    assert blocked.telegram.sent == []

    erased, person = _interrupted(tmp_path / "erased")
    erased.store.forget(person.key)
    assert _state(erased) is None
    assert asyncio.run(erased._announce_interruptions()) == 0 and erased.telegram.sent == []


def test_a_scrubbed_body_still_reaches_a_configured_participant(tmp_path):
    runtime, person = _interrupted(tmp_path, finish="delivery_unknown")
    runtime.store.scrub_inbox(5)                            # memory off: the body is erased after the turn
    assert asyncio.run(runtime._announce_interruptions()) == 1      # addressing lives in the notice table
    assert runtime.telegram.sent[0][0] == person.user_id


def test_the_heartbeat_counts_unannounced_turns_without_text(tmp_path):
    runtime, _ = _interrupted(tmp_path)
    assert runtime.heartbeat_snapshot()["interruptions"]["unannounced"] == 1
    asyncio.run(runtime._announce_interruptions())
    snapshot = runtime.heartbeat_snapshot()["interruptions"]
    assert snapshot["unannounced"] == 0 and snapshot["sent"] == 1


# -- long task: UNKNOWN_OUTCOME ------------------------------------------------------------------
PK = "c" * 64


class Crash(BaseException):
    """Process killed mid-step (a BaseException the task store does not catch)."""


class Sender:
    def __init__(self, crash: bool = False, result="sent"):
        self.crash, self.result, self.sent = crash, result, []

    async def __call__(self, person_key, text, key):
        if self.crash:
            self.crash = False
            raise Crash()
        self.sent.append((person_key, text, key))
        return self.result


async def _drafter(person_key, goal):
    return "План: день 1 — Кремль; день 2 — Свияжск."


def _ctx(text):
    return TurnContext(person_key=PK, who="tg:1", text=text, memory_enabled=True)


async def _settle(mod):
    for _ in range(50):
        pending = [t for t in mod._runs.values() if not t.done()]
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)


def _unknown_delivery(tmp_path):
    """Process 1 dies while sending the result; process 2 finds UNKNOWN_OUTCOME. Returns the second module."""
    crashing = Sender(crash=True)

    async def first():
        mod = LT.LongTaskModule(TaskStore(tmp_path), drafter=_drafter, sender=crashing)
        await mod.pre_route(_ctx("поставь задачу: подбери 5 книг по истории"))
        await _settle(mod)
    asyncio.run(first())
    sender = Sender()
    mod = LT.LongTaskModule(TaskStore(tmp_path), drafter=_drafter, sender=sender)

    async def second():
        mod.resume_all()
        await _settle(mod)
    asyncio.run(second())
    assert TaskStore(tmp_path).list(PK)[0]["state"] == "UNKNOWN_OUTCOME" and sender.sent == []
    return mod, sender


def test_unknown_outcome_is_announced_once_and_nothing_is_resent(tmp_path):
    mod, sender = _unknown_delivery(tmp_path)
    assert asyncio.run(mod.announce_unknown()) == 1
    (_, text, key), = sender.sent
    assert "не знаю, дошёл ли он до тебя" in text and "результат задачи 1" in text
    assert "Свияжск" not in text                              # the result itself is not pushed blindly
    assert "доставлен" not in text.lower()
    assert asyncio.run(mod.announce_unknown()) == 0           # marker: once
    assert len(sender.sent) == 1 and TaskStore(tmp_path).list(PK)[0]["state"] == "UNKNOWN_OUTCOME"


def test_the_participant_closes_unknown_outcome_with_their_own_words(tmp_path):
    mod, sender = _unknown_delivery(tmp_path)

    async def got():
        reply = (await mod.pre_route(_ctx("получил результат задачи 1"))).reply
        await _settle(mod)
        return reply
    assert "до тебя дошёл" in asyncio.run(got())
    assert TaskStore(tmp_path).list(PK)[0]["state"] == "DONE" and sender.sent == []      # nothing sent again


def test_not_received_sends_the_result_one_more_time_only_after_the_participant_says_so(tmp_path):
    mod, sender = _unknown_delivery(tmp_path)

    async def not_got():
        reply = (await mod.pre_route(_ctx("не получил результат задачи 1"))).reply
        await _settle(mod)
        return reply
    assert "ещё один раз" in asyncio.run(not_got())
    assert TaskStore(tmp_path).list(PK)[0]["state"] == "DONE"
    assert len(sender.sent) == 1 and "Свияжск" in sender.sent[0][1]


def test_confirmation_words_do_nothing_for_a_task_that_is_not_unknown(tmp_path):
    mod = LT.LongTaskModule(TaskStore(tmp_path), drafter=_drafter, sender=Sender())

    async def scenario():
        await mod.pre_route(_ctx("поставь задачу: план тренировок"))
        await _settle(mod)
        return (await mod.pre_route(_ctx("получил результат задачи 1"))).reply
    assert "не ждёт подтверждения" in asyncio.run(scenario())
