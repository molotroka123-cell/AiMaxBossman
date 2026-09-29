"""PyTgCallsTransport against fake pytgcalls/ntgcalls modules that mirror the API verified from the 3.0.0 sources.

Proves: wiring, the audio frame contract (10 ms native frames), error mapping, dial-once, idempotent hangup, events.
Does NOT prove a real call (owner-live, NOT_RUN)."""
from __future__ import annotations

import asyncio
import sys

import pytest

from bcc.telegram_calls.call.pytgcalls_transport import PyTgCallsTransport, map_exception
from bcc.telegram_calls.types import (CallError, CallTransport, PeerRef, TransportEvent, TransportEventKind)

from .fakes_b import FakeEngine, FakeTelethon

PEER = PeerRef(777, "second")
K = TransportEventKind


@pytest.fixture
def eng(monkeypatch):
    return FakeEngine().install(monkeypatch)


def make(eng, **kw):
    t = PyTgCallsTransport(kw.pop("client", FakeTelethon()), watchdog_s=0.05, lost_grace_s=0.05, **kw)
    events: list[TransportEvent] = []
    audio: list[bytes] = []
    t.set_event_callback(events.append)
    t.set_audio_callback(audio.append)
    return t, events, audio


async def up(eng, **kw):
    t, events, audio = make(eng, **kw)
    await t.start()
    await t.dial(PEER, ring_timeout=5)
    return t, events, audio


def kinds(events):
    return [e.kind for e in events]


# ------------------------------------------------------------------ contract / import
def test_conforms_to_protocol_and_import_needs_no_engine(monkeypatch):
    monkeypatch.setitem(sys.modules, "pytgcalls", None)      # import would fail: still constructible
    t = PyTgCallsTransport(FakeTelethon())
    assert isinstance(t, CallTransport)
    assert t.audio_format.sample_rate == 48000 and t.audio_format.channels == 1 and t.name == "pytgcalls"


async def test_start_without_dependencies_is_dependencies_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "pytgcalls", None)
    with pytest.raises(CallError) as e:
        await PyTgCallsTransport(FakeTelethon()).start()
    assert e.value.code == "DEPENDENCIES_MISSING"


async def test_start_rejects_disconnected_or_unauthorised_client(eng):
    with pytest.raises(CallError) as e:
        await PyTgCallsTransport(FakeTelethon(connected=False)).start()
    assert e.value.code == "TELEGRAM_NETWORK"
    with pytest.raises(CallError) as e:
        await PyTgCallsTransport(FakeTelethon(authorized=False)).start()
    assert e.value.code == "NOT_LOGGED_IN"
    t = PyTgCallsTransport(FakeTelethon())                  # legit client passes
    await t.start()
    assert eng.instance.started


async def test_start_disables_github_version_check(eng):
    await PyTgCallsTransport(FakeTelethon()).start()
    assert eng.mods["session"].PyTgCallsSession.notice_displayed is True


async def test_dial_before_start_is_rejected_without_engine_call(eng):
    t, _, _ = make(eng)
    with pytest.raises(CallError) as e:
        await t.dial(PEER, ring_timeout=5)
    assert e.value.code == "WORKER_UNAVAILABLE" and "play" not in eng.names()


# ------------------------------------------------------------------ dial
async def test_dial_success_uses_verified_api_and_emits_connected(eng):
    t, events, _ = await up(eng)
    play = next(c for c in eng.calls if c[0] == "play")
    _, chat_id, stream, config = play
    assert chat_id == 777 and config.timeout == 5
    assert stream.audio_parameters.bitrate == 48000 and stream.audio_parameters.channels == 1   # bitrate == sample rate
    ExternalMedia = eng.mods["types"].ExternalMedia
    assert stream.media_path == ExternalMedia.AUDIO
    assert stream.video_flags == eng.mods["types"].MediaStream.Flags.IGNORE
    rec = next(c for c in eng.calls if c[0] == "record")
    assert rec[1] == 777 and rec[2].audio is True and rec[2].audio_parameters.bitrate == 48000
    assert eng.names().index("play") < eng.names().index("record")
    assert kinds(events) == [K.CONNECTED]
    await t.close()


async def test_ring_timeout_is_rounded_up_and_passed_to_engine(eng):
    t, _, _ = make(eng)
    await t.start()
    await t.dial(PEER, ring_timeout=2.2)
    assert next(c for c in eng.calls if c[0] == "play")[3].timeout == 3
    await t.close()


@pytest.mark.parametrize("exc_name,code", [("CallDeclined", "CALL_DECLINED"), ("CallBusy", "CALL_BUSY"),
                                            ("TimedOutAnswer", "CALL_NO_ANSWER"), ("CallDiscarded", "CALL_DISCARDED")])
async def test_engine_exceptions_map_to_call_errors(eng, exc_name, code):
    t, events, _ = make(eng)
    await t.start()
    eng.play_error = getattr(eng.mods["exc"], exc_name)("secret text 79001234567")
    with pytest.raises(CallError) as e:
        await t.dial(PEER, ring_timeout=5)
    assert e.value.code == code and "7900" not in repr(e.value.as_dict()) and "7900" not in str(e.value)
    assert K.CONNECTED not in kinds(events)
    assert "leave_call" in eng.names()                      # nothing may keep ringing after a failed dial


async def test_dial_is_exactly_once_and_never_retried(eng):
    t, _, _ = make(eng)
    await t.start()
    eng.play_error = eng.mods["exc"].CallDeclined(1)
    with pytest.raises(CallError):
        await t.dial(PEER, ring_timeout=5)
    with pytest.raises(CallError) as e:                     # the second call never reaches the engine
        await t.dial(PEER, ring_timeout=5)
    assert e.value.code == "CALL_IN_PROGRESS" and eng.play_count == 1


async def test_second_dial_after_success_also_rejected(eng):
    t, _, _ = await up(eng)
    with pytest.raises(CallError) as e:
        await t.dial(PEER, ring_timeout=5)
    assert e.value.code == "CALL_IN_PROGRESS" and eng.play_count == 1
    await t.close()


async def test_unknown_peer_is_peer_not_found_without_dialing(eng):
    t, _, _ = make(eng, client=FakeTelethon(known=(1,)))
    await t.start()
    with pytest.raises(CallError) as e:
        await t.dial(PEER, ring_timeout=5)
    assert e.value.code == "PEER_NOT_FOUND" and eng.play_count == 0


async def test_media_never_connecting_times_out_and_hangs_up(eng):
    t, _, _ = make(eng, connect_grace_s=0.1)
    await t.start()
    eng.play_delay = 5
    with pytest.raises(CallError) as e:
        await t.dial(PEER, ring_timeout=1)
    assert e.value.code == "TELEGRAM_NETWORK" and e.value.detail == "connect_timeout"
    assert "leave_call" in eng.names()


async def test_record_failure_after_connect_fails_the_dial_and_leaves(eng):
    t, events, _ = make(eng)
    await t.start()
    eng.record_error = RuntimeError("boom")
    with pytest.raises(CallError) as e:
        await t.dial(PEER, ring_timeout=5)
    assert e.value.code == "INTERNAL" and "leave_call" in eng.names() and K.CONNECTED not in kinds(events)


async def test_hangup_during_dialing_cancels_dial_and_discards(eng):
    t, events, _ = make(eng)
    await t.start()
    eng.play_delay = 5
    task = asyncio.ensure_future(t.dial(PEER, ring_timeout=30))
    await asyncio.sleep(0.05)
    await t.hangup("stop")
    with pytest.raises(CallError) as e:
        await asyncio.wait_for(task, 2)
    assert e.value.code == "CALL_DISCARDED" and "leave_call" in eng.names()
    assert kinds(events).count(K.ENDED) == 1


# ------------------------------------------------------------------ audio in
async def test_rx_frames_are_regrouped_to_20ms_pcm(eng):
    t, _, audio = await up(eng)
    ten_ms = bytes(range(256)) * 3 + bytes(192)              # 960 bytes = 10 ms @ 48 kHz mono
    assert len(ten_ms) == t.audio_format.frame_bytes(10) == 960
    await eng.instance.deliver(eng.frames(777, ten_ms))
    assert audio == []                                       # half a tick is held back
    await eng.instance.deliver(eng.frames(777, ten_ms))
    assert audio == [ten_ms + ten_ms] and len(audio[0]) == t.audio_format.frame_bytes(20)
    await t.close()


async def test_rx_ignores_outgoing_other_device_other_peer_and_pre_connect(eng):
    t, _, audio = await up(eng)
    pcm = bytes(1920)
    await eng.instance.deliver(eng.frames(777, pcm, incoming=False))
    await eng.instance.deliver(eng.frames(777, pcm, device="CAMERA"))
    await eng.instance.deliver(eng.frames(999, pcm))
    assert audio == []
    await eng.instance.deliver(eng.frames(777, pcm))         # legit control
    assert len(audio) == 1
    await t.close()


async def test_rx_after_hangup_is_dropped(eng):
    t, _, audio = await up(eng)
    await t.hangup()
    await eng.instance.deliver(eng.frames(777, bytes(1920)))
    assert audio == []


async def test_broken_audio_callback_does_not_break_the_call(eng):
    t, events, _ = await up(eng)
    t.set_audio_callback(lambda pcm: 1 / 0)
    await eng.instance.deliver(eng.frames(777, bytes(1920)))
    assert kinds(events) == [K.CONNECTED]
    await t.close()


# ------------------------------------------------------------------ audio out
async def test_send_splits_20ms_into_exact_10ms_frames(eng):
    t, _, _ = await up(eng)
    await t.send_audio(bytes(1920))
    assert [len(s) for s in eng.sent] == [960, 960]
    sf = [c for c in eng.calls if c[0] == "send_frame"]
    assert all(c[1] == 777 and c[2] == eng.mods["types"].Device.MICROPHONE for c in sf)
    await t.close()


async def test_send_buffers_remainder_and_never_sends_partial_frames(eng):
    t, _, _ = await up(eng)
    await t.send_audio(bytes(1000))
    assert [len(s) for s in eng.sent] == [960]
    await t.send_audio(bytes(920))                           # 40 + 920 = 960
    assert [len(s) for s in eng.sent] == [960, 960]
    await t.close()


async def test_send_before_connect_and_after_hangup_is_noop(eng):
    t, _, _ = make(eng)
    await t.start()
    await t.send_audio(bytes(1920))
    assert eng.sent == []
    await t.dial(PEER, ring_timeout=5)
    await t.hangup()
    await t.send_audio(bytes(1920))
    assert eng.sent == []


async def test_clear_outgoing_drops_buffered_and_in_flight_audio(eng):
    t, _, _ = await up(eng)
    eng.send_delay = 0.02
    task = asyncio.ensure_future(t.send_audio(bytes(960 * 20)))       # 200 ms in flight
    await asyncio.sleep(0.05)
    await t.clear_outgoing()
    await asyncio.wait_for(task, 2)
    assert 0 < len(eng.sent) < 20
    n = len(eng.sent)
    await t.send_audio(bytes(1920))                                   # fresh audio after the clear is sent again
    assert len(eng.sent) == n + 2
    await t.close()


async def test_send_engine_not_in_call_ends_call_once(eng):
    t, events, _ = await up(eng)
    eng.send_error = eng.mods["exc"].NotInCallError()
    await t.send_audio(bytes(1920))
    await t.send_audio(bytes(1920))
    ended = [e for e in events if e.kind == K.ENDED]
    assert len(ended) == 1 and ended[0].reason == "error"
    await t.close()


# ------------------------------------------------------------------ events
async def test_peer_hangup_update_emits_single_ended_peer_hangup(eng):
    t, events, _ = await up(eng)
    ChatUpdate = eng.mods["types"].ChatUpdate
    await eng.instance.deliver(ChatUpdate(777, ChatUpdate.Status.DISCARDED_CALL))
    await eng.instance.deliver(ChatUpdate(777, ChatUpdate.Status.DISCARDED_CALL | ChatUpdate.Status.BUSY_CALL))
    ended = [e for e in events if e.kind == K.ENDED]
    assert [e.reason for e in ended] == ["peer_hangup"]
    await t.close()
    assert [e.reason for e in events if e.kind == K.ENDED] == ["peer_hangup"]     # local hangup adds nothing


async def test_chat_update_for_other_peer_or_other_status_is_ignored(eng):
    t, events, _ = await up(eng)
    ChatUpdate = eng.mods["types"].ChatUpdate
    await eng.instance.deliver(ChatUpdate(999, ChatUpdate.Status.DISCARDED_CALL))
    await eng.instance.deliver(ChatUpdate(777, ChatUpdate.Status.INCOMING_CALL))
    assert K.ENDED not in kinds(events)
    await t.close()


async def test_watchdog_reports_silent_media_loss_as_error(eng):
    t, events, _ = await up(eng)
    eng.instance.drop_call(777)                              # py-tgcalls hung up on its own, no update
    await asyncio.sleep(0.3)
    assert [e.reason for e in events if e.kind == K.ENDED] == ["error"]
    await t.close()


async def test_watchdog_yields_to_peer_hangup_update(eng):
    t, events, _ = await up(eng)
    ChatUpdate = eng.mods["types"].ChatUpdate
    eng.instance.drop_call(777)
    await asyncio.sleep(0.06)                                # watchdog noticed, grace running
    await eng.instance.deliver(ChatUpdate(777, ChatUpdate.Status.DISCARDED_CALL))
    await asyncio.sleep(0.2)
    assert [e.reason for e in events if e.kind == K.ENDED] == ["peer_hangup"]
    await t.close()


async def test_watchdog_quiet_while_call_is_alive(eng):
    t, events, _ = await up(eng)
    await asyncio.sleep(0.25)
    assert kinds(events) == [K.CONNECTED]
    await t.close()


# ------------------------------------------------------------------ hangup / close
async def test_hangup_is_idempotent_and_emits_one_ended(eng):
    t, events, _ = await up(eng)
    await t.hangup("a")
    await t.hangup("b")
    await t.close()
    await t.close()
    assert eng.names().count("leave_call") == 1
    assert [e.reason for e in events if e.kind == K.ENDED] == ["local_hangup"]


async def test_hangup_safe_before_start_and_before_dial(eng):
    t, events, _ = make(eng)
    await t.hangup()
    await t.close()
    assert eng.names() == [] and events == []
    t2, events2, _ = make(eng)
    await t2.start()
    await t2.hangup()
    assert "leave_call" not in eng.names() and events2 == []      # nothing was ever requested


async def test_hangup_swallows_engine_errors_and_still_ends(eng):
    t, events, _ = await up(eng)
    eng.leave_error = RuntimeError("engine exploded")
    await t.hangup()
    assert t.stats["hangup_errors"] == 1
    assert [e.reason for e in events if e.kind == K.ENDED] == ["local_hangup"]


async def test_hangup_not_in_call_is_not_an_error(eng):
    t, _, _ = await up(eng)
    eng.instance.drop_call(777)
    await t.hangup()
    assert t.stats["hangup_errors"] == 0


async def test_close_removes_handler(eng):
    t, _, _ = await up(eng)
    inst = eng.instance
    await t.close()
    assert inst.handlers == []


# ------------------------------------------------------------------ mapping
def test_map_exception_by_class_name_is_secret_free():
    def cls(name, *bases):
        return type(name, bases or (Exception,), {})

    RPCError = cls("RPCError")
    for name, code in [("UserPrivacyRestrictedError", "PEER_PRIVACY"), ("UserIdInvalidError", "PEER_NOT_FOUND"),
                       ("AuthKeyUnregisteredError", "SESSION_REVOKED"), ("TelegramServerError", "TELEGRAM_NETWORK")]:
        err = map_exception(cls(name, RPCError)("+79001234567 hash abc"))
        assert err.code == code and "7900" not in err.as_dict().__repr__()
    assert map_exception(cls("SomeOtherRpc", RPCError)("x")).code == "TELEGRAM_RPC"
    assert map_exception(ConnectionResetError("x")).code == "TELEGRAM_NETWORK"
    assert map_exception(ValueError("x")).code == "INTERNAL"          # bad case: unknown stays INTERNAL, not a guess
    err = map_exception(cls("FloodWaitError", RPCError)("wait 30"))
    assert err.code == "TELEGRAM_RPC" and err.detail == "flood_wait"
    keep = CallError("CALL_BUSY")
    assert map_exception(keep) is keep
