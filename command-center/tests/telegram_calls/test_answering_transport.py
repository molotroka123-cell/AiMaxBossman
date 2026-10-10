"""The incoming-call hook of the transports: ``LoopbackLine`` (emulator) and ``PyTgCallsLine`` / ``PyTgCallsTransport.accept``
(py-tgcalls) against a FAKE engine. The py-tgcalls path is NOT live-tested: these tests pin what the code does with the
updates the library source says it delivers; they are no evidence that a real incoming call works.
"""
from __future__ import annotations

import asyncio
import types as pytypes

import pytest

from bcc.telegram_calls.call import pytgcalls_transport as pt
from bcc.telegram_calls.call.loopback import LoopbackLine, LoopbackTransport
from bcc.telegram_calls.types import (AnswerableTransport, CallError, CallLine, GONE_ANSWERED_ELSEWHERE, GONE_CALLER_HANGUP, GONE_UNKNOWN,
                                      IncomingCall, TransportEventKind)

from .test_pytgcalls_transport import FakeEngine, exc, install_fake_pytgcalls


class IncomingEngine(FakeEngine):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.incoming_cb = self.left_cb = None

    def set_incoming_handler(self, cb):
        self.incoming_cb = cb

    def set_left_handler(self, cb):
        self.left_cb = cb


def mk_line(**kw):
    eng = IncomingEngine(**kw)
    line = pt.PyTgCallsLine(eng)
    rung, gone = [], []
    line.set_incoming_callback(rung.append)
    line.set_gone_callback(lambda ref, why: gone.append((ref, why)))
    return line, eng, rung, gone


def kinds(events):
    return [(e.kind, e.reason) for e in events]


# ------------------------------------------------------------------ the protocols
def test_both_engines_satisfy_the_incoming_protocols():
    assert isinstance(LoopbackLine(), CallLine) and isinstance(LoopbackTransport(), AnswerableTransport)
    line, eng, *_ = mk_line()
    assert isinstance(line, CallLine) and isinstance(line.new_transport(IncomingCall("r", 5)), AnswerableTransport)
    assert LoopbackLine.live_tested is False and pt.PyTgCallsLine.live_tested is False, "the real path is marked NOT live-tested"


# ------------------------------------------------------------------ py-tgcalls line (fake engine)
async def test_an_incoming_update_becomes_one_incoming_call_and_a_duplicate_is_ignored():
    line, eng, rung, gone = mk_line()
    await line.listen()
    assert eng.calls == ["start"]
    eng.incoming_cb(4242)
    eng.incoming_cb(4242)                                            # the library may repeat the update
    assert len(rung) == 1 and rung[0].caller_id == 4242 and rung[0].transport == "telegram" and rung[0].known
    assert not any(c[0] in ("play", "record") for c in eng.calls if isinstance(c, tuple)), "listening never answers"


async def test_a_call_that_stops_ringing_is_reported_gone_but_an_answered_call_is_not():
    line, eng, rung, gone = mk_line()
    await line.listen()
    eng.incoming_cb(4242)
    eng.left_cb(4242, False)                                         # not answered: the caller gave up / answered elsewhere
    assert gone == [(rung[0].call_ref, GONE_UNKNOWN)], "py-tgcalls does not tell why; the line does not guess"
    eng.left_cb(4242, False)
    assert len(gone) == 1, "reported once"
    eng.incoming_cb(4243)
    tr = line.new_transport(rung[1])                                 # answered: from now on the transport owns the end of the call
    eng.left_cb(4243, False)
    assert len(gone) == 1 and isinstance(tr, pt.PyTgCallsTransport)


async def test_reject_declines_a_ringing_call_and_is_quiet_when_it_is_already_gone():
    line, eng, rung, gone = mk_line()
    await line.listen()
    eng.incoming_cb(4242)
    await line.reject(rung[0], "stop")
    assert ("leave", 4242) in eng.calls
    eng.leave_error = exc("NotInCallError")                          # already gone: not an error for us
    await line.reject(rung[0])
    eng.left_cb(4242, False)
    assert gone == [], "a call we declined ourselves is not reported as 'gone'"


async def test_a_dead_engine_at_listen_is_a_stable_error_without_leaking_text():
    line, eng, *_ = mk_line()

    async def boom():
        raise exc("RuntimeError")
    eng.start = boom
    with pytest.raises(CallError) as ei:
        await line.listen()
    assert "+7900" not in str(ei.value.as_dict())


# ------------------------------------------------------------------ the per-call transport answers, it never dials
async def test_accept_plays_into_the_ringing_call_then_records_and_never_rings_out():
    line, eng, rung, gone = mk_line()
    await line.listen()
    eng.incoming_cb(4242)
    tr = line.new_transport(rung[0])
    ev = []
    tr.set_event_callback(ev.append)
    await tr.start()                                                 # the shared app is already running: not started twice
    await tr.accept(rung[0], answer_timeout=20)
    assert eng.calls == ["start", ("play", 4242, 20), ("record", 4242)]
    assert kinds(ev) == [(TransportEventKind.CONNECTED, "")], "an incoming call never reports RINGING (nobody is being rung)"
    assert tr._connected is True


async def test_a_transport_answers_at_most_once_and_cannot_dial_after_answering():
    line, eng, rung, gone = mk_line()
    eng.incoming_cb = None
    call = IncomingCall("r", 4242, "", 0.0, "telegram")
    tr = line.new_transport(call)
    await tr.accept(call, answer_timeout=5)
    with pytest.raises(CallError) as ei:
        await tr.accept(call, answer_timeout=5)
    assert ei.value.detail == "accept_twice"
    with pytest.raises(CallError):
        await tr.dial(pt.PeerRef(4242, ""), ring_timeout=5)          # one instance, one call: a dial after an answer is refused
    assert [c for c in eng.calls if isinstance(c, tuple) and c[0] == "play"] == [("play", 4242, 5)]


@pytest.mark.parametrize("name,code", [("CallDiscarded", "CALL_DISCARDED"), ("TimedOutAnswer", "CALL_NO_ANSWER"),
                                       ("TelegramServerError", "TELEGRAM_NETWORK"), ("CallDeclined", "CALL_DECLINED")])
async def test_accept_failures_map_to_stable_codes_without_leaking_text(name, code):
    line, eng, *_ = mk_line(play_error=exc(name))
    call = IncomingCall("r", 4242, "", 0.0, "telegram")
    tr = line.new_transport(call)
    with pytest.raises(CallError) as ei:
        await tr.accept(call, answer_timeout=5)
    assert ei.value.code == code and "+7900" not in str(ei.value.as_dict())
    assert not tr._connected


async def test_accept_without_a_caller_id_is_refused_before_anything_is_played():
    line, eng, *_ = mk_line()
    call = IncomingCall("r", None, "", 0.0, "telegram")
    tr = line.new_transport(call)
    with pytest.raises(CallError) as ei:
        await tr.accept(call, answer_timeout=5)
    assert ei.value.code == "CALL_DISCARDED" and not any(isinstance(c, tuple) for c in eng.calls)


async def test_accept_that_hangs_is_cut_off_and_not_reported_as_connected():
    line, eng, *_ = mk_line(play_delay=2.0)
    line_tr = pt.PyTgCallsTransport(eng, connect_grace_s=0.1)
    call = IncomingCall("r", 4242, "", 0.0, "telegram")
    with pytest.raises(CallError) as ei:
        await line_tr.accept(call, answer_timeout=0.1)
    assert ei.value.code == "CALL_NO_ANSWER" and not line_tr._connected


async def test_hanging_up_before_the_answer_finished_declines_the_call():
    line, eng, rung, gone = mk_line(play_delay=1.0)
    call = IncomingCall("r", 4242, "", 0.0, "telegram")
    tr = line.new_transport(call)
    task = asyncio.create_task(tr.accept(call, answer_timeout=5))
    await asyncio.sleep(0.05)
    await tr.hangup("local")                                         # STOP while the answer is in progress
    assert ("leave", 4242) in eng.calls
    task.cancel()
    with pytest.raises((asyncio.CancelledError, CallError)):
        await task


async def test_the_peer_hanging_up_after_the_answer_ends_the_call_once():
    line, eng, rung, gone = mk_line()
    call = IncomingCall("r", 4242, "", 0.0, "telegram")
    tr = line.new_transport(call)
    ev = []
    tr.set_event_callback(ev.append)
    await tr.accept(call, answer_timeout=5)
    tr._on_call_left(False)
    tr._on_call_left(False)
    assert kinds(ev)[-1] == (TransportEventKind.ENDED, "peer_hangup") and sum(1 for k, _ in kinds(ev) if k == TransportEventKind.ENDED) == 1


# ------------------------------------------------------------------ the real engine class wires the incoming update (fake pytgcalls)
async def test_engine_routes_incoming_and_left_updates_to_the_line_and_resets_per_call_state(monkeypatch):
    log, Session, Status = install_fake_pytgcalls(monkeypatch)
    eng = pt.PyTgCallsEngine(client=object())
    incoming, left, ends = [], [], []
    eng.set_incoming_handler(incoming.append)
    eng.set_left_handler(lambda uid, busy: left.append((uid, busy)))
    eng.set_end_handler(ends.append)
    on_incoming = log["updates"][2][1]
    on_left = log["updates"][1][1]
    await on_incoming(None, pytypes.SimpleNamespace(chat_id=4242))
    assert incoming == [4242]
    await on_left(None, pytypes.SimpleNamespace(chat_id=4242, status=Status.BUSY_CALL))
    assert left == [(4242, True)] and ends == [True]
    await on_left(None, pytypes.SimpleNamespace(chat_id=4243, status=Status.BUSY_CALL))
    assert left[-1] == (4243, True) and ends == [True], "the transport end is reported once per call ..."
    await eng.play(4243, 20)                                          # ... and again after a new call started on the shared app
    await on_left(None, pytypes.SimpleNamespace(chat_id=4243, status=Status.BUSY_CALL))
    assert ends == [True, True]
    await eng.start()
    await eng.start()
    assert eng._app.started is True


# ------------------------------------------------------------------ the loopback line (what the emulator tests stand on)
async def test_loopback_line_rings_only_while_listening_and_tracks_what_was_done_to_each_call():
    line = LoopbackLine()
    rung, gone = [], []
    line.set_incoming_callback(rung.append)
    line.set_gone_callback(lambda r, w: gone.append((r, w)))
    line.ring(1)
    assert rung == [], "a line nobody listens on delivers nothing"
    await line.listen()
    a, b, c = line.ring(1), line.ring(2), line.ring(3)
    line.caller_hangup(a)
    line.owner_answers_elsewhere(b)
    await line.reject(c, "stop")
    assert [x.call_id if hasattr(x, "call_id") else x.caller_id for x in rung] == [1, 2, 3]
    assert gone == [(a.call_ref, GONE_CALLER_HANGUP), (b.call_ref, GONE_ANSWERED_ELSEWHERE)] and line.rejected == [(c.call_ref, "stop")]


async def test_loopback_accept_is_refused_for_a_caller_who_already_hung_up_and_works_for_a_ringing_call():
    line = LoopbackLine()
    await line.listen()
    gone_call, live = line.ring(1), line.ring(2)
    line.caller_hangup(gone_call)
    t_gone, t_live = line.new_transport(gone_call), line.new_transport(live)
    with pytest.raises(CallError) as ei:
        await t_gone.accept(gone_call, answer_timeout=5)
    assert ei.value.code == "CALL_DISCARDED"
    await t_live.accept(live, answer_timeout=5)
    assert t_live.media_up and t_live.dial_calls == 0 and line.accepted == [live.call_ref]
    await t_live.hangup()
    await t_live.close()
