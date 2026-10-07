"""The answering machine inside the REAL ``Worker`` (no subprocess, no Telegram): ops, STOP, hangup, the dial guard, history, events.

The line is the loopback one and the engines are fakes; what is proven is the worker's wiring around ``AnsweringMachine``.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from bcc.telegram_calls.audio.vad import EnergyVAD
from bcc.telegram_calls.call import worker as worker_mod
from bcc.telegram_calls.call.loopback import LoopbackLine
from bcc.telegram_calls.settings import CallSettings, save_settings
from bcc.telegram_calls.types import CallError, PeerRef

from .answering_rig import GREETING, MessageBrain
from .fakes import ScriptedSTT, ToneTTS

RATE = 16000
LABEL = "Иван Секретный"


class AnsweringWorker(worker_mod.Worker):
    def __init__(self, home, **kw):
        self.events: list[dict] = []
        super().__init__(home, write=self.events.append, mode=kw.pop("mode", "offline_test"), **kw)
        self.line = LoopbackLine()
        self.engines = []

    async def _build_line(self):
        return self.line

    async def _answering_engines(self, settings, mode):
        eng = worker_mod.Engines(stt=ScriptedSTT(["Здравствуйте, это Иван", "Перезвоните мне", "До свидания"]), tts=ToneTTS(ms_per_char=18),
                                 brain=MessageBrain(["Кто вы?", "Понял, передам.", "Хорошо. [конец]"]), vad=EnergyVAD())
        self.engines.append(eng)
        return eng


@pytest.fixture
async def make(tmp_path, monkeypatch):
    workers: list[AnsweringWorker] = []

    def factory(*, enabled: bool = True, **kw):
        home = tmp_path / f"w{len(workers)}" / "telegram-calls"
        save_settings(CallSettings(answering_machine=enabled, answer_ring_delay_s=1, answer_greeting=GREETING), home)
        w = AnsweringWorker(home, **kw)
        workers.append(w)
        return w
    monkeypatch.setattr(worker_mod, "check_dial", lambda settings, ctx, confirm_unknown=False: _guard(ctx))
    yield factory
    for w in workers:
        await w.op_shutdown({})


def _guard(ctx):
    if ctx.active_call:
        raise CallError("CALL_IN_PROGRESS")
    return PeerRef(4242, "second")


async def until(cond, timeout=8.0):
    end = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < end:
        if cond():
            return True
        await asyncio.sleep(0.02)
    return False


async def ring_and_answer(w: AnsweringWorker, caller=5001):
    call = w.line.ring(caller, LABEL)
    assert await until(lambda: w.session is not None and w.session.record.state.value == "active", 6), "not answered"
    return call


# ------------------------------------------------------------------ arming
async def test_arming_needs_the_owners_setting_and_no_stop(make):
    off = make(enabled=False)
    with pytest.raises(CallError) as ei:
        await off.op_answering_start({})
    assert ei.value.code == "ANSWERING_NOT_ENABLED" and off.answering is None and off.line.listen_calls == 0
    on = make()
    on.state.set_stop("owner")
    with pytest.raises(CallError) as ei:
        await on.op_answering_start({})
    assert ei.value.code == "STOP_ACTIVE" and on.line.listen_calls == 0
    on.state.clear_stop()                                                  # the legitimate path passes
    res = await on.op_answering_start({})
    assert res["answering"]["armed"] is True and on.line.listening and (await on.op_status({}))["answering"]["armed"] is True
    assert any(e.get("event") == "answering" and e["data"]["kind"] == "armed" for e in on.events)


async def test_a_real_telegram_worker_refuses_the_offline_simulation_and_the_offline_one_allows_it(make):
    real = make(mode="")
    with pytest.raises(CallError):
        await real.op_answering_simulate({})                               # not offline: refused (and nothing is armed either)
    offline = make()
    await offline.op_answering_start({})
    assert "call_ref" in await offline.op_answering_simulate({"caller_id": 5001})


# ------------------------------------------------------------------ an answered call
async def test_an_answered_call_is_recorded_as_incoming_and_never_marks_the_next_dial_uncertain(make):
    w = make()
    await w.op_answering_start({})
    call = await ring_and_answer(w)
    view = (await w.op_status({}))["call"]
    assert view["direction"] == "incoming" and view["state"] == "active"
    w.line.caller_hangup(call)
    assert await until(lambda: w.session is None and w._last_record is not None, 8)
    rec = w._last_record
    assert rec["direction"] == "incoming" and rec["outcome"] == "completed"
    hist = w.state.history(5)
    assert hist and hist[0]["direction"] == "incoming" and "transcript" not in json.dumps(hist[0])
    assert not (w.home / "state.json").exists(), "nothing was dialled: no in-flight marker, no 'uncertain' for the next dial"
    assert not w.state.is_uncertain()
    assert any(e.get("event") == "record" for e in w.events)


async def test_events_about_incoming_calls_carry_no_caller_no_text(make):
    w = make()
    await w.op_answering_start({})
    call = await ring_and_answer(w)
    w.line.caller_hangup(call)
    assert await until(lambda: w.answering.last_report_id is not None, 8)
    seen_by_the_manager = [e for e in w.events if e.get("event") in ("answering", "call_event")]
    blob = json.dumps(seen_by_the_manager, ensure_ascii=False)
    assert LABEL not in blob and "5001" not in blob and "Перезвоните" not in blob
    kinds = [e["data"]["kind"] for e in w.events if e.get("event") == "answering"]
    assert {"armed", "ringing", "answering", "report"} <= set(kinds)


# ------------------------------------------------------------------ the dial guard and hangup
async def test_the_owner_cannot_dial_while_an_incoming_call_rings_or_is_answered(make):
    w = make()
    await w.op_answering_start({})
    call = w.line.ring(5001, LABEL)
    await asyncio.sleep(0.3)                                               # ringing: still inside the ring delay
    assert w.answering.busy
    with pytest.raises(CallError) as ei:
        await w.op_dial({})
    assert ei.value.code == "CALL_IN_PROGRESS"
    await ring_and_answer_existing(w, call)
    with pytest.raises(CallError) as ei:
        await w.op_dial({})
    assert ei.value.code == "CALL_IN_PROGRESS" and all(t.dial_calls == 0 for t in w.line.transports.values())
    assert (await w.op_hangup({}))["ended"] is True
    assert await until(lambda: w.session is None and not w.answering.busy, 6)


async def ring_and_answer_existing(w, call):
    assert await until(lambda: w.session is not None and w.session.record.state.value == "active", 6)


# ------------------------------------------------------------------ STOP
async def test_op_stop_hangs_up_an_answered_call_confirms_it_and_blocks_until_resume(make):
    w = make()
    await w.op_answering_start({})
    call = await ring_and_answer(w)
    res = await w.op_stop({"reason": "owner_stop", "timeout": 4})
    assert res["hangup_confirmed"] is True and w.session is None and w.state.stop_is_set()
    assert w.answering.status()["stopped"] is True
    call2 = w.line.ring(5002, "Другой")
    await asyncio.sleep(1.5)
    assert call2.call_ref not in w.line.accepted, "STOP disables auto-answer"
    with pytest.raises(CallError) as ei:
        await w.op_answering_start({})
    assert ei.value.code == "STOP_ACTIVE"
    await w.op_resume({})
    call3 = w.line.ring(5003, "Третий")
    assert await until(lambda: call3.call_ref in w.line.accepted, 6), "after resume the machine answers again"
    await w.op_stop({"timeout": 4})


async def test_op_stop_while_ringing_declines_the_call_and_confirms(make):
    w = make()
    save_settings(CallSettings(answering_machine=True, answer_ring_delay_s=5, answer_greeting=GREETING), w.home)
    await w.op_answering_start({})
    call = w.line.ring(5001, LABEL)
    await asyncio.sleep(0.4)
    res = await w.op_stop({"timeout": 3})
    assert res["hangup_confirmed"] is True and w.line.rejected == [(call.call_ref, "stop")] and w.line.accepted == []


async def test_disarming_stops_answering_new_calls_but_keeps_the_listener(make):
    w = make()
    await w.op_answering_start({})
    await w.op_answering_stop({})
    assert w.answering.armed is False and w.line.closed is False
    w.line.ring(5001, LABEL)
    await asyncio.sleep(1.5)
    assert w.line.accepted == [] and w.line.rejected == []
    await w.op_answering_start({})                                         # re-arming works without a second listener
    assert w.line.listen_calls == 1 and w.answering.armed is True
