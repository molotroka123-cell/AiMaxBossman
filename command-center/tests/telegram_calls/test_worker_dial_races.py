"""Worker.op_dial: a STOP that arrives while the engines load must win, and a second dial must not start a second call."""
from __future__ import annotations

import asyncio

import pytest

from bcc.telegram_calls.audio.vad import EnergyVAD
from bcc.telegram_calls.call import worker as worker_mod
from bcc.telegram_calls.call.loopback import LoopbackTransport
from bcc.telegram_calls.types import CallError, PeerRef

from .fakes import ScriptedBrain, ScriptedSTT, ToneTTS


def _engines():
    return worker_mod.Engines(stt=ScriptedSTT(()), tts=ToneTTS(), brain=ScriptedBrain(()), vad=EnergyVAD())


class SlowWorker(worker_mod.Worker):
    """A worker whose model loading can be held open, with no Telegram and no credentials."""

    def __init__(self, home, gate: asyncio.Event):
        super().__init__(home, write=lambda msg: None, mode="offline_test", engines_factory=self._slow_engines)
        self.gate = gate
        self.transports: list[LoopbackTransport] = []

    async def _slow_engines(self, settings, mode):
        await self.gate.wait()
        return _engines()

    async def _build_transport(self, engines):
        tr = LoopbackTransport(ring_s=30.0)
        self.transports.append(tr)
        return tr


@pytest.fixture
def slow(tmp_path, monkeypatch):
    monkeypatch.setattr(worker_mod, "check_dial", lambda settings, ctx, confirm_unknown=False: PeerRef(4242, "second"))
    return SlowWorker(tmp_path / "telegram-calls", asyncio.Event())


async def test_stop_while_the_engines_load_means_the_phone_never_rings(slow):
    dial = asyncio.create_task(slow.op_dial({}))
    await asyncio.sleep(0.05)
    slow.stop_now("owner_stop")                          # no session exists yet: only the durable flag can carry the STOP
    slow.gate.set()
    with pytest.raises(CallError) as err:
        await dial
    assert err.value.code == "STOP_ACTIVE"
    assert slow.session is None and all(t.dial_calls == 0 for t in slow.transports)


async def test_a_second_dial_while_the_first_is_arming_is_refused(slow):
    first = asyncio.create_task(slow.op_dial({}))
    await asyncio.sleep(0.05)
    with pytest.raises(CallError) as err:
        await slow.op_dial({})
    assert err.value.code == "CALL_IN_PROGRESS"
    slow.gate.set()
    await first
    await slow.op_stop({"timeout": 2.0})
    assert sum(t.dial_calls for t in slow.transports) <= 1, "exactly one dial for one owner action"
