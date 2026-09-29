"""Audit-round fixes of CallsManager.dial: dead worker before the write must not wedge _active."""
from __future__ import annotations

from bcc.secrets import Vault
from bcc.telegram_calls.call.manager import CallsManager

from .fakes_a import DeadHandle, until
from .test_manager import Harness, code_of


class EofHandle(DeadHandle):
    """A worker that is already dead: EOF at once and the pipe refuses writes."""

    def __init__(self):
        super().__init__()
        self.q.put_nowait(b"")

    def write(self, data: bytes):
        raise BrokenPipeError("dead")


async def test_dial_twice_with_a_worker_that_dies_before_the_write_does_not_wedge_active(tmp_path):
    spawns = []

    async def spawn(argv, env):
        spawns.append(1)
        return EofHandle()
    m = CallsManager(tmp_path, vault=Vault(tmp_path), spawn=spawn)
    Harness(tmp_path).arm()
    assert await code_of(m.dial()) == "WORKER_UNAVAILABLE"
    assert m.status()["call"] is None
    assert await code_of(m.dial()) == "WORKER_UNAVAILABLE"          # not CALL_IN_PROGRESS
    assert len(spawns) >= 1 and m.history() == []


async def test_worker_gone_between_start_and_request_clears_active(tmp_path):
    h = Harness(tmp_path)
    h.arm()
    orig = h.m.start_worker

    async def start_then_die():
        await orig()
        h.m._handle = None                                           # worker died in the gap
    h.m.start_worker = start_then_die
    assert await code_of(h.m.dial()) == "WORKER_UNAVAILABLE"
    assert h.m.status()["call"] is None
    assert await code_of(h.m.dial()) == "WORKER_UNAVAILABLE"


class DrainFails(DeadHandle):
    async def drain(self):
        raise ConnectionResetError("reset after write")


async def test_dial_that_may_have_been_written_keeps_active_and_blocks_a_second_dial(tmp_path):
    dead = DrainFails()

    async def spawn(argv, env):
        return dead
    m = CallsManager(tmp_path, vault=Vault(tmp_path), spawn=spawn)
    Harness(tmp_path).arm()
    assert await code_of(m.dial()) == "WORKER_UNAVAILABLE"
    assert dead.writes and m.status()["call"] is not None
    assert await code_of(m.dial()) == "CALL_IN_PROGRESS"             # never a second dial after a possible write
    assert await until(lambda: True)
