"""py-tgcalls transport against a fake engine (behaviour) and against fake ``pytgcalls`` modules (engine wiring).

The real library is exercised separately by ``deps.engine_selfcheck`` in the sandbox/doctor; these tests pin the
contract we rely on, so a refactor cannot silently violate it (exact frames, no double dial, decline vs peer hangup...).
"""
from __future__ import annotations

import asyncio
import sys
import types as pytypes

import pytest

from bcc.telegram_calls.call import pytgcalls_transport as pt
from bcc.telegram_calls.types import CallError, PeerRef, TransportEventKind

PEER = PeerRef(4242, "second")
FRAME = b"\x01\x00" * 480          # 10 ms at 48 kHz


def exc(name, base=Exception):
    return type(name, (base,), {})("secret text +79001234567")


class FakeEngine:
    def __init__(self, *, play_error=None, play_delay=0.0, record_error=None):
        self.play_error, self.play_delay, self.record_error = play_error, play_delay, record_error
        self.calls, self.frames_sent = [], []
        self.frame_cb = self.end_cb = self.media_cb = None
        self.leave_error = None

    async def start(self):
        self.calls.append("start")

    async def play(self, user_id, ring_timeout):
        self.calls.append(("play", user_id, ring_timeout))
        if self.play_delay:
            await asyncio.sleep(self.play_delay)
        if self.play_error:
            raise self.play_error

    async def record(self, user_id):
        self.calls.append(("record", user_id))
        if self.record_error:
            raise self.record_error

    async def send_frame(self, user_id, pcm):
        self.frames_sent.append((user_id, pcm))

    async def leave_call(self, user_id):
        self.calls.append(("leave", user_id))
        if self.leave_error:
            raise self.leave_error

    def set_frame_handler(self, cb):
        self.frame_cb = cb

    def set_end_handler(self, cb):
        self.end_cb = cb

    def set_media_handler(self, cb):
        self.media_cb = cb

    async def close(self):
        self.calls.append("close")


def mk(**kw):
    eng = FakeEngine(**kw)
    tr = pt.PyTgCallsTransport(eng, connect_grace_s=0.3)
    events = []
    tr.set_event_callback(events.append)
    return tr, eng, events


def kinds(events):
    return [(e.kind, e.reason) for e in events]


async def test_dial_play_then_record_then_connected_and_contract_constants():
    tr, eng, ev = mk()
    await tr.start()
    await tr.dial(PEER, ring_timeout=45)
    assert eng.calls == ["start", ("play", 4242, 45), ("record", 4242)]
    assert kinds(ev) == [(TransportEventKind.RINGING, ""), (TransportEventKind.CONNECTED, "")]
    assert (tr.audio_format.sample_rate, tr.rx_sample_rate, tr.frame_ms) == (48000, 16000, 10)
    assert tr.audio_format.sample_rate % 100 == 0                    # ntgcalls crashed at 22050 Hz


@pytest.mark.parametrize("name,code", [("CallDeclined", "CALL_DECLINED"), ("CallBusy", "CALL_BUSY"), ("TimedOutAnswer", "CALL_NO_ANSWER"),
                                       ("CallDiscarded", "CALL_DECLINED"), ("UserPrivacyRestrictedError", "PEER_PRIVACY"),
                                       ("TelegramServerError", "TELEGRAM_NETWORK")])
async def test_library_exceptions_map_to_stable_codes_without_leaking_text(name, code):
    tr, eng, ev = mk(play_error=exc(name))
    with pytest.raises(CallError) as ei:
        await tr.dial(PEER, ring_timeout=5)
    assert ei.value.code == code and "+7900" not in str(ei.value.as_dict())
    assert TransportEventKind.CONNECTED not in [k for k, _ in kinds(ev)]


async def test_a_decline_reported_as_a_chat_update_during_dial_does_not_look_like_a_finished_call():
    tr, eng, ev = mk(play_delay=0.05, play_error=exc("CallDeclined"))
    task = asyncio.create_task(tr.dial(PEER, ring_timeout=5))
    await asyncio.sleep(0.01)
    eng.end_cb(False)                                                # py-tgcalls also emits the LEFT_CALL update
    with pytest.raises(CallError) as ei:
        await task
    assert ei.value.code == "CALL_DECLINED"
    assert all(reason != "peer_hangup" for _, reason in kinds(ev)), "a decline is not a completed call"


async def test_dial_timeout_is_not_claimed_as_declined_or_no_answer():
    tr, eng, ev = mk(play_delay=2.0)
    with pytest.raises(CallError) as ei:
        await tr.dial(PEER, ring_timeout=0.05)                        # 0.05 + 0.3 grace
    assert ei.value.code == "TELEGRAM_NETWORK" and ei.value.detail == "dial_timeout"


async def test_a_transport_instance_dials_at_most_once():
    tr, eng, ev = mk()
    await tr.dial(PEER, ring_timeout=5)
    with pytest.raises(CallError):
        await tr.dial(PEER, ring_timeout=5)
    assert [c for c in eng.calls if isinstance(c, tuple) and c[0] == "play"] == [("play", 4242, 5)]


async def test_send_audio_is_exactly_one_10ms_frame_bytes_and_ignored_before_connect():
    tr, eng, ev = mk()
    await tr.send_audio(FRAME)                                        # not connected yet: silently nothing
    assert eng.frames_sent == []
    await tr.dial(PEER, ring_timeout=5)
    await tr.send_audio(FRAME)
    assert eng.frames_sent == [(4242, FRAME)]
    for bad in (FRAME[:-2], FRAME * 2, b""):                          # short data is an over-read, long data is truncated by the engine
        with pytest.raises(ValueError):
            await tr.send_audio(bad)
    assert len(eng.frames_sent) == 1


async def test_incoming_frames_flow_only_while_connected():
    tr, eng, ev = mk()
    got = []
    tr.set_audio_callback(got.append)
    eng.frame_cb(b"early")
    await tr.dial(PEER, ring_timeout=5)
    eng.frame_cb(b"a" * 320)
    assert got == [b"a" * 320]
    tr.set_audio_callback(None)
    eng.frame_cb(b"b" * 320)                                          # no callback: dropped, not an error


async def test_peer_hangup_ends_once_and_later_frames_are_ignored():
    tr, eng, ev = mk()
    got = []
    tr.set_audio_callback(got.append)
    await tr.dial(PEER, ring_timeout=5)
    eng.end_cb(False)
    eng.end_cb(False)
    assert kinds(ev).count((TransportEventKind.ENDED, "peer_hangup")) == 1
    eng.frame_cb(b"x" * 320)
    assert got == []                                                  # frames keep arriving after the call died: never fed on
    await tr.send_audio(FRAME)
    assert eng.frames_sent == []


async def test_media_drop_followed_by_discard_is_a_connection_loss_not_a_peer_hangup():
    tr, eng, ev = mk()
    await tr.dial(PEER, ring_timeout=5)
    eng.media_cb(False)
    eng.end_cb(False)                                                 # py-tgcalls discards the call itself after a drop
    assert (TransportEventKind.DISCONNECTED, "") in kinds(ev)
    assert kinds(ev)[-1] == (TransportEventKind.ENDED, "connection_lost")


async def test_hangup_is_idempotent_and_emits_one_local_end():
    tr, eng, ev = mk()
    await tr.dial(PEER, ring_timeout=5)
    await tr.hangup()
    await tr.hangup()
    assert [c for c in eng.calls if isinstance(c, tuple) and c[0] == "leave"] == [("leave", 4242)]
    assert kinds(ev).count((TransportEventKind.ENDED, "local_hangup")) == 1


async def test_hangup_of_an_already_gone_call_is_not_an_error_but_other_failures_surface():
    tr, eng, ev = mk()
    await tr.dial(PEER, ring_timeout=5)
    eng.leave_error = exc("NotInCallError")
    await tr.hangup()
    tr2, eng2, _ = mk()
    await tr2.dial(PEER, ring_timeout=5)
    eng2.leave_error = exc("SomethingElse")
    with pytest.raises(CallError):
        await tr2.hangup()


async def test_hangup_before_any_dial_never_touches_the_engine():
    tr, eng, ev = mk()
    await tr.hangup()
    assert eng.calls == []


async def test_clear_outgoing_is_recorded_but_cannot_recall_audio():
    tr, eng, ev = mk()
    await tr.dial(PEER, ring_timeout=5)
    await tr.clear_outgoing()
    assert tr.cleared == 1 and eng.frames_sent == []


def test_map_call_error_is_by_class_name_and_generic_for_unknown():
    assert pt.map_call_error(exc("PeerFloodError")).code == "TELEGRAM_RPC"
    assert pt.map_call_error(exc("ConnectionNotFound")).code == "CONNECTION_LOST"
    assert pt.map_call_error(asyncio.TimeoutError()).code == "TELEGRAM_NETWORK"
    unknown = pt.map_call_error(exc("BrandNewLibraryError"))
    assert unknown.code == "TELEGRAM_RPC" and "+7900" not in str(unknown.as_dict())


# ---------------------------------------------------------------- engine wiring against fake pytgcalls modules

def install_fake_pytgcalls(monkeypatch):
    log = {"updates": [], "play": None, "record": None, "sent": [], "left": []}

    class Flag(int):
        def __and__(self, other):
            return Flag(int(self) & int(other))
    Status = pytypes.SimpleNamespace(BUSY_CALL=Flag(4), LEFT_CALL=Flag(7), INCOMING_CALL=Flag(32))
    ChatUpdate = pytypes.SimpleNamespace(Status=Status)
    Device = pytypes.SimpleNamespace(MICROPHONE="mic")
    Direction = pytypes.SimpleNamespace(INCOMING="in")
    filters = pytypes.SimpleNamespace(stream_frame=lambda d, dev: ("stream_frame", d, dev),
                                      chat_update=lambda f: ("chat_update", f))

    class PyTgCalls:
        def __init__(self, client):
            self.client = client
            self.started = False

        def on_update(self, flt):
            def deco(fn):
                log["updates"].append((flt, fn))
                return fn
            return deco

        async def _handle_connection_changed(self, chat_id, net_state):
            log["orig_conn"] = net_state

        async def start(self):
            self.started = True

        async def play(self, chat_id, stream, config):
            log["play"] = (chat_id, stream, config)

        async def record(self, chat_id, stream):
            log["record"] = (chat_id, stream)

        async def send_frame(self, chat_id, device, data):
            assert type(data) is bytes
            log["sent"].append((chat_id, device, data))

        async def leave_call(self, chat_id):
            log["left"].append(chat_id)

    class Session:
        notice_displayed = False

    AudioParameters = lambda rate, ch: ("AP", rate, ch)          # noqa: E731
    mod = pytypes.ModuleType("pytgcalls")
    mod.PyTgCalls, mod.filters = PyTgCalls, filters
    sess = pytypes.ModuleType("pytgcalls.pytgcalls_session")
    sess.PyTgCallsSession = Session
    types_ = pytypes.ModuleType("pytgcalls.types")
    types_.ChatUpdate, types_.Device, types_.Direction = ChatUpdate, Device, Direction
    types_.CallConfig = lambda timeout: ("CFG", timeout)
    types_.ExternalMedia = pytypes.SimpleNamespace(AUDIO="ext-audio")
    types_.MediaStream = lambda media, audio_parameters: ("MS", media, audio_parameters)
    types_.RecordStream = lambda audio, audio_parameters: ("RS", audio, audio_parameters)
    raw = pytypes.ModuleType("pytgcalls.types.raw")
    raw.AudioParameters = AudioParameters
    for name, m in (("pytgcalls", mod), ("pytgcalls.pytgcalls_session", sess), ("pytgcalls.types", types_), ("pytgcalls.types.raw", raw)):
        monkeypatch.setitem(sys.modules, name, m)
    return log, Session, Status


async def test_engine_registers_handlers_wraps_connection_changes_and_disables_phone_home(monkeypatch):
    log, Session, Status = install_fake_pytgcalls(monkeypatch)
    eng = pt.PyTgCallsEngine(client=object())
    assert Session.notice_displayed is True                          # no stdout banner, no version check to GitHub raw
    assert [flt[0] for flt, _ in log["updates"]] == ["stream_frame", "chat_update", "chat_update"]   # frames, call left, incoming call
    assert log["updates"][2][0][1] == Status.INCOMING_CALL
    frames, ends, media = [], [], []
    eng.set_frame_handler(frames.append)
    eng.set_end_handler(ends.append)
    eng.set_media_handler(media.append)
    on_frames, on_left = log["updates"][0][1], log["updates"][1][1]
    await on_frames(None, pytypes.SimpleNamespace(frames=[pytypes.SimpleNamespace(frame=b"f1"), pytypes.SimpleNamespace(frame=b"f2")]))
    assert frames == [b"f1", b"f2"]
    await on_left(None, pytypes.SimpleNamespace(status=Status.BUSY_CALL))
    await on_left(None, pytypes.SimpleNamespace(status=Status.BUSY_CALL))
    assert ends == [True]                                            # once, with the busy flag
    state = pytypes.SimpleNamespace(state=pytypes.SimpleNamespace(name="DISCONNECTED"))
    await eng._app._handle_connection_changed(4242, state)
    assert media == [False] and log["orig_conn"] is state            # observed AND still delegated to py-tgcalls


async def test_engine_play_record_and_send_use_the_verified_parameters(monkeypatch):
    log, Session, Status = install_fake_pytgcalls(monkeypatch)
    eng = pt.PyTgCallsEngine(client=object())
    await eng.start()
    await eng.play(4242, 45)
    assert log["play"] == (4242, ("MS", "ext-audio", ("AP", 48000, 1)), ("CFG", 45))
    await eng.record(4242)
    assert log["record"] == (4242, ("RS", True, ("AP", 16000, 1)))
    await eng.send_frame(4242, bytearray(FRAME))                      # a bytearray would raise TypeError in py-tgcalls
    assert log["sent"] == [(4242, "mic", bytes(FRAME))]
    await eng.leave_call(4242)
    assert log["left"] == [4242]


def test_engine_reports_missing_dependency_as_a_stable_error(monkeypatch):
    for name in ("pytgcalls", "pytgcalls.pytgcalls_session", "pytgcalls.types", "pytgcalls.types.raw"):
        monkeypatch.setitem(sys.modules, name, None)                  # import raises
    with pytest.raises(CallError) as ei:
        pt.PyTgCallsEngine(client=object())
    assert ei.value.code == "DEPENDENCIES_MISSING"
