"""Findings of the independent audit (2026-09-30): races between STOP / settings and a dial, single flight, failing STOP writes.

Every test here is RED on the code before the fix (the audit reproduced each one with a script) and has its paired control:
the legitimate path still works. The REAL ``Worker`` is used (scripted engines, loopback line, offline account); the heavy parts
(Whisper, Piper, Telegram) are not involved: what is proven is the control flow, not call quality.
"""
from __future__ import annotations

import asyncio
import dataclasses
import time
from types import SimpleNamespace

import pytest

from bcc.events import EventBus
from bcc.features import telegram_calls as feature
from bcc.secrets import Vault
from bcc.telegram_calls.account.credentials import CredentialStore
from bcc.telegram_calls.account.stopflag import CallState
from bcc.telegram_calls.audio.vad import EnergyVAD
from bcc.telegram_calls.call.loopback import LoopbackTransport
from bcc.telegram_calls.call.manager import CallsManager
from bcc.telegram_calls.call.offline_mode import OFFLINE_ME_ID, OFFLINE_PEER_ID, OFFLINE_PHONE
from bcc.telegram_calls.call.worker import Worker
from bcc.telegram_calls.settings import CallSettings, save_settings
from bcc.telegram_calls.speech.factory import Engines
from bcc.telegram_calls.types import CallError, Outcome, Turn

from .fakes import ScriptedBrain, ScriptedSTT, ToneTTS
from .test_api_calls import PREFIX, mgr, offline_mode, ready  # noqa: F401 - the autouse fixture is re-used
from .test_manager import fake_ops, make, prep  # noqa: F401
from .test_session import build, until

API_HASH = "0123456789abcdef" * 2
OFFLINE_SESSION = "OFFLINE-TEST-SESSION-" + "0" * 40


class SpyLine(LoopbackTransport):
    """A loopback line that counts (and can delay) what the worker does with it."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.closed = False


@dataclasses.dataclass
class Rig:
    worker: Worker
    gate: asyncio.Event
    entered: asyncio.Event
    lines: list[SpyLine]
    home: object
    events: list[dict]

    async def finish(self) -> None:
        """Hang a live call up and wait for its record (keeps the tests from leaking tasks)."""
        if self.worker.session is not None:
            self.worker.session.hangup("test")
        if self.worker._call_task is not None:
            await asyncio.wait({self.worker._call_task}, timeout=10)


@pytest.fixture()
def rig(tmp_path, monkeypatch):
    monkeypatch.setenv("BCC_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("BOSSMAN_TELEGRAM_CALLS_HOME", raising=False)
    monkeypatch.delenv("BOSSMAN_VAULT_KEY", raising=False)
    home = tmp_path / "telegram-calls"
    store = CredentialStore(home, vault=Vault(tmp_path))
    store.save_api(1234567, API_HASH)
    store.save_session(OFFLINE_SESSION, OFFLINE_ME_ID, OFFLINE_PHONE)
    save_settings(CallSettings(enabled=True, peer_user_id=OFFLINE_PEER_ID, peer_label="second"), home)
    gate = asyncio.Event()
    gate.set()
    entered = asyncio.Event()                                       # set when the worker is inside the (slow) engine build
    lines: list[SpyLine] = []
    events: list[dict] = []

    async def engines(settings, mode):
        entered.set()
        await gate.wait()
        return Engines(stt=ScriptedSTT([]), tts=ToneTTS(), brain=ScriptedBrain([]), vad=EnergyVAD())

    def transport(client):
        line = SpyLine()
        lines.append(line)
        return line

    worker = Worker(home, write=events.append, engines_factory=engines, transport_factory=transport, mode="offline_test")
    return Rig(worker, gate, entered, lines, home, events)


# ------------------------------------------------------------------ F1: STOP while the dial is still being built

async def test_a_stop_during_the_engine_build_aborts_the_dial_and_nothing_rings(rig):
    rig.gate.clear()
    dial = asyncio.create_task(rig.worker.op_dial({}))
    await asyncio.wait_for(rig.entered.wait(), 3)
    stop = asyncio.create_task(rig.worker.op_stop({"timeout": 5.0}))
    await asyncio.sleep(0.1)
    assert not stop.done(), "a dial still being built is not a confirmed hangup: STOP waits for it (bounded)"
    rig.gate.set()
    with pytest.raises(CallError) as err:
        await dial
    assert err.value.code == "STOP_ACTIVE"
    reply = await stop
    assert reply["hangup_confirmed"] is True and reply["stop_flag"] is True
    assert rig.worker.session is None and rig.worker._call_task is None
    assert sum(line.dial_calls for line in rig.lines) == 0, "the phone never rang"
    assert all(line.closed for line in rig.lines), "the half-built line was released"
    assert rig.worker.state.stop_is_set()


async def test_a_stop_whose_file_cannot_be_written_still_aborts_the_dial(rig, monkeypatch):
    def boom(self, by="owner"):
        raise PermissionError("empty DACL")
    monkeypatch.setattr(CallState, "set_stop", boom)
    rig.gate.clear()
    dial = asyncio.create_task(rig.worker.op_dial({}))
    await asyncio.wait_for(rig.entered.wait(), 3)
    rig.worker.stop_now("owner")                                  # must not raise
    rig.gate.set()
    with pytest.raises(CallError) as err:
        await dial
    assert err.value.code == "STOP_ACTIVE" and sum(line.dial_calls for line in rig.lines) == 0


async def test_op_stop_does_not_claim_a_confirmed_hangup_while_the_build_outlasts_its_timeout(rig):
    rig.gate.clear()
    dial = asyncio.create_task(rig.worker.op_dial({}))
    await asyncio.wait_for(rig.entered.wait(), 3)
    reply = await rig.worker.op_stop({"timeout": 0.2})
    assert reply["hangup_confirmed"] is False
    rig.gate.set()
    with pytest.raises(CallError):
        await dial


async def test_paired_control_without_a_stop_the_dial_goes_ahead_exactly_once(rig):
    rig.gate.clear()
    dial = asyncio.create_task(rig.worker.op_dial({}))
    await asyncio.wait_for(rig.entered.wait(), 3)
    rig.gate.set()
    result = await dial
    assert result["accepted"] is True
    assert await until(lambda: rig.lines and rig.lines[0].dial_calls == 1, 3)
    await rig.finish()
    assert [line.dial_calls for line in rig.lines] == [1]


# ------------------------------------------------------------------ F2: single flight

async def test_two_overlapping_dials_are_one_call(rig):
    rig.gate.clear()
    first = asyncio.create_task(rig.worker.op_dial({}))
    await asyncio.wait_for(rig.entered.wait(), 3)
    with pytest.raises(CallError) as err:
        await asyncio.wait_for(rig.worker.op_dial({}), 2.0)           # refused at once (a second dial would wait for the gate)
    assert err.value.code == "CALL_IN_PROGRESS"
    rig.gate.set()
    await first
    assert await until(lambda: rig.lines and rig.lines[0].dial_calls == 1, 3)
    await rig.finish()
    assert len(rig.lines) == 1 and rig.lines[0].dial_calls == 1, "one transport, one dial"


# ------------------------------------------------------------------ F8 (worker side): settings changed under a building dial

async def test_settings_changed_during_the_build_are_honoured_before_the_phone_can_ring(rig):
    rig.gate.clear()
    dial = asyncio.create_task(rig.worker.op_dial({}))
    await asyncio.wait_for(rig.entered.wait(), 3)
    save_settings(CallSettings(enabled=False, peer_user_id=OFFLINE_PEER_ID, peer_label="second"), rig.home)
    rig.gate.set()
    with pytest.raises(CallError) as err:
        await dial
    assert err.value.code == "NOT_ENABLED" and sum(line.dial_calls for line in rig.lines) == 0


# ------------------------------------------------------------------ F11: a dial that fails early does not spend the confirmation

async def test_a_dial_that_fails_before_ringing_keeps_the_uncertain_state(rig):
    rig.worker.state.note_call_finished("c-old000000001", Outcome.UNKNOWN)
    assert rig.worker.state.is_uncertain()

    async def broken(settings, mode):
        raise CallError("TTS_UNAVAILABLE", detail="voice_not_configured")
    rig.worker._engines_factory = broken
    with pytest.raises(CallError) as err:
        await rig.worker.op_dial({"confirm_unknown": True})
    assert err.value.code == "TTS_UNAVAILABLE"
    assert rig.worker.state.is_uncertain(), "the confirmation was not spent: nothing was dialled"
    with pytest.raises(CallError) as again:
        await rig.worker.op_dial({})
    assert again.value.code in ("UNCERTAIN_PREVIOUS_CALL", "TTS_UNAVAILABLE")
    assert again.value.code == "UNCERTAIN_PREVIOUS_CALL", "a plain dial still needs the explicit confirmation"


async def test_paired_control_a_dial_that_goes_ahead_does_spend_the_confirmation(rig):
    rig.worker.state.note_call_finished("c-old000000001", Outcome.UNKNOWN)
    await rig.worker.op_dial({"confirm_unknown": True})
    assert await until(lambda: rig.lines and rig.lines[0].dial_calls == 1, 3)
    await rig.finish()
    assert not rig.worker.state.is_uncertain() or rig.worker.state._state().get("last_outcome") == "completed"


# ------------------------------------------------------------------ F16: the end of a call cannot leave the worker «in a call»

async def test_a_failing_state_write_at_the_end_of_a_call_still_frees_the_worker_and_emits_the_record(rig, monkeypatch):
    def boom(self, call_id, outcome):
        raise OSError("disk full")
    monkeypatch.setattr(CallState, "note_call_finished", boom)
    await rig.worker.op_dial({})
    assert await until(lambda: rig.worker.session is not None and rig.lines and rig.lines[0].dial_calls == 1, 3)
    await rig.finish()
    assert rig.worker.session is None, "the dial guard would refuse everything until a restart"
    assert [e for e in rig.events if e.get("event") == "record"], "the record still reached the manager"


async def test_a_non_utf8_history_file_does_not_leave_the_worker_in_a_call(rig):
    """RED before 2026-10-08: append_history raised UnicodeDecodeError (not OSError) in the worker's finally, so
    ``session`` stayed set (every later dial: CALL_IN_PROGRESS until a restart) and the record event was never emitted."""
    rig.worker.state.home.mkdir(parents=True, exist_ok=True)
    (rig.worker.state.home / "history.jsonl").write_bytes(b'{"call_id": "c-old", "note": "\xcf\xf0\xe8\xe2\xe5\xf2"}\n')
    await rig.worker.op_dial({})
    assert await until(lambda: rig.worker.session is not None and rig.lines and rig.lines[0].dial_calls == 1, 3)
    await rig.finish()
    assert rig.worker.session is None, "the dial guard would refuse everything until a restart"
    records = [e for e in rig.events if e.get("event") == "record"]
    assert records, "the record still reached the manager"
    call_id = records[-1]["record"]["call_id"]
    assert rig.worker.state.history(1)[0]["call_id"] == call_id, "and it was written to history"


# ------------------------------------------------------------------ F14: offline mode never touches a real session

async def test_offline_mode_refuses_to_start_over_a_real_session(tmp_path, monkeypatch):
    monkeypatch.setenv("BCC_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("BOSSMAN_TELEGRAM_CALLS_HOME", raising=False)
    monkeypatch.delenv("BOSSMAN_VAULT_KEY", raising=False)
    home = tmp_path / "telegram-calls"
    store = CredentialStore(home, vault=Vault(tmp_path))
    store.save_api(1234567, API_HASH)
    store.save_session("1A" + "Qz9_x-" * 20, 111, "+79990000000")                 # shaped like a REAL session string
    offline = Worker(home, write=lambda e: None, mode="offline_test")
    with pytest.raises(CallError) as err:
        await offline.op_hello({})
    assert err.value.detail == "offline_mode_with_real_session"
    normal = Worker(home, write=lambda e: None, mode="")
    assert (await normal.op_hello({}))["mode"] == "telegram", "a normal worker is not affected"
    store.save_session(OFFLINE_SESSION, OFFLINE_ME_ID, OFFLINE_PHONE)
    assert (await offline.op_hello({}))["mode"] == "offline_test", "the synthetic session is fine (paired control)"


# ------------------------------------------------------------------ F3 / F7: the session

class SlowStart(LoopbackTransport):
    def __init__(self):
        super().__init__()
        self.release = asyncio.Event()
        self.entered = asyncio.Event()

    async def start(self):
        self.entered.set()
        await self.release.wait()
        await super().start()


async def test_a_stop_during_transport_start_never_rings_the_phone():
    line = SlowStart()
    s, *_ = build(transport=line)
    task = asyncio.create_task(s.run())
    await asyncio.wait_for(line.entered.wait(), 3)
    s.stop("owner_stop")
    line.release.set()
    rec = await asyncio.wait_for(task, 10)
    assert line.dial_calls == 0 and rec.outcome == Outcome.STOPPED


async def test_paired_control_without_a_stop_the_session_dials_once_after_a_slow_start():
    line = SlowStart()
    s, *_ = build(transport=line)
    task = asyncio.create_task(s.run())
    await asyncio.wait_for(line.entered.wait(), 3)
    line.release.set()
    assert await until(lambda: line.dial_calls == 1, 3)
    s.hangup()
    await asyncio.wait_for(task, 10)
    assert line.dial_calls == 1


class HangingSummary(ScriptedBrain):
    async def summarize(self, turns):
        self.summarize_calls += 1
        await asyncio.Event().wait()


async def test_a_stop_during_the_summary_ends_it_at_once_and_a_stop_before_it_starts_no_model_call():
    brain = HangingSummary([])
    s, *_ = build(brain=brain, summary_timeout_s=8.0)
    s._history.append(Turn("user", "привет"))
    s._outcome = Outcome.COMPLETED                                 # decided earlier (peer hangup): the teardown is summarising
    asyncio.get_running_loop().call_later(0.15, s.stop)
    t0 = time.monotonic()
    summary = await s._summary()
    assert time.monotonic() - t0 < 3.0, "STOP interrupts the summary instead of waiting for the model"
    assert summary.generated_by == "mechanical" and brain.summarize_calls == 1

    late = HangingSummary([])
    s2, *_ = build(brain=late)
    s2._history.append(Turn("user", "привет"))
    s2._outcome = Outcome.COMPLETED
    s2.stop()
    assert (await s2._summary()).generated_by == "mechanical" and late.summarize_calls == 0


async def test_paired_control_a_summary_without_a_stop_is_the_models():
    s, *_ = build(brain=ScriptedBrain([]))
    s._history.append(Turn("user", "привет"))
    s._outcome = Outcome.COMPLETED
    assert (await s._summary()).generated_by == "fixture-llm"


# ------------------------------------------------------------------ F4 / F10: the manager

async def test_a_stop_whose_file_cannot_be_written_still_hangs_up_and_blocks_dialing_until_resume(make, monkeypatch, tmp_path):
    m = make()
    prep(m)
    await m.contacts()                                              # the fake worker is running

    def boom(by="owner"):
        raise PermissionError("empty DACL")
    monkeypatch.setattr(m.state, "set_stop", boom)
    out = await m.stop("owner")
    assert out["stop_flag"] is False, "the reply never pretends the STOP survives a restart"
    assert "stop" in [e["op"] for e in fake_ops(tmp_path)], "the worker still got the stop op"
    assert (await m.status())["stop"]["call"] is True
    with pytest.raises(CallError) as err:
        await m.dial()
    assert err.value.code == "STOP_ACTIVE"
    monkeypatch.undo()
    await m.resume()
    assert (await m.status())["stop"]["call"] is False


async def test_a_worker_that_cannot_even_start_does_not_leave_the_dial_pending(tmp_path):
    data = tmp_path / "data"
    m = CallsManager(data, vault=Vault(data), worker_argv=[str(tmp_path / "absent-binary.exe")])
    prep(m)
    with pytest.raises(CallError) as first:
        await m.dial()
    assert first.value.code == "WORKER_UNAVAILABLE"
    assert m._dial_pending is False and m._active_call is None
    with pytest.raises(CallError) as second:
        await m.dial()
    assert second.value.code == "WORKER_UNAVAILABLE", "not stuck in CALL_IN_PROGRESS until a backend restart"
    await m.shutdown()


# ------------------------------------------------------------------ F8 / F9 / F12: the API

async def test_settings_peer_and_credentials_are_frozen_while_a_dial_is_in_flight(env):
    await ready(env)
    m = mgr(env)
    m._dial_pending = True
    try:
        assert (await env.client.put(f"{PREFIX}/settings", json={"enabled": False})).status_code == 409
        assert (await env.client.put(f"{PREFIX}/peer", json={"user_id": OFFLINE_PEER_ID, "confirm": True})).status_code == 409
        assert (await env.client.delete(f"{PREFIX}/peer")).status_code == 409
        assert (await env.client.post(f"{PREFIX}/credentials", json={"api_id": 7654321, "api_hash": API_HASH})).status_code == 409
    finally:
        m._dial_pending = False
    assert (await env.client.put(f"{PREFIX}/settings", json={"enabled": False})).status_code == 200, "paired control"


async def test_choosing_a_different_peer_switches_calls_off_choosing_the_same_one_does_not(env):
    await ready(env)
    m = mgr(env)
    m.save_settings(dataclasses.replace(m.settings(), peer_user_id=999, peer_label="someone else", enabled=True))
    changed = await env.client.put(f"{PREFIX}/peer", json={"user_id": OFFLINE_PEER_ID, "confirm": True})
    assert changed.status_code == 200 and changed.json()["enabled"] is False, "the new person was never enabled by the owner"
    assert (await env.client.put(f"{PREFIX}/settings", json={"enabled": True})).json()["enabled"] is True
    same = await env.client.put(f"{PREFIX}/peer", json={"user_id": OFFLINE_PEER_ID, "confirm": True})
    assert same.json()["enabled"] is True, "re-selecting the same peer keeps the owner's choice"


def test_an_unreadable_global_stop_state_fails_closed_for_dialing_only():
    svc = SimpleNamespace()                                         # no settings at all: reading the STOP state raises
    assert feature.global_stop_active(svc) is False, "the watcher / status must not hang calls up on an unreadable state"
    assert feature.global_stop_active(svc, fail_closed=True) is True, "a dial is refused when the STOP state cannot be read"


async def test_one_failing_stop_does_not_end_the_global_stop_watcher():
    class Flaky:
        def __init__(self):
            self.calls = 0
            self.active_call = None
            self.running = False

        async def stop(self, reason):
            self.calls += 1
            if self.calls == 1:
                raise PermissionError("first STOP fails")

        async def shutdown(self):
            pass

    bus = EventBus()
    svc = SimpleNamespace(bus=bus)
    rt = SimpleNamespace(manager=Flaky())
    watcher = asyncio.create_task(feature._watch_global_stop(svc, rt))
    await asyncio.sleep(0)
    await bus.emit("computer.stop")
    await until(lambda: rt.manager.calls >= 1, 3)
    await asyncio.sleep(0.05)
    assert not watcher.done(), "the watcher survived the failing stop"
    await bus.emit("computer.stop")
    assert await until(lambda: rt.manager.calls == 2, 3), "the NEXT global STOP still reaches the calls manager"
    watcher.cancel()
    await asyncio.wait({watcher})
