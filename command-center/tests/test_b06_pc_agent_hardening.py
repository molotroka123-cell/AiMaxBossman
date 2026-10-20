"""Branch 6 «Computer»: PC-Agent child-process cleanup and record_screen guards (2026-10-20).

Fakes only: no real command is spawned, no screen is captured, Telegram is never opened.
"""
from __future__ import annotations

import asyncio

import pytest

import bcc.features.pc_agent as pca

from .test_pc_agent_argon import fake_svc  # noqa: F401 — fixture


class HangingProc:
    """A child that never finishes on its own (e.g. a stuck `git` waiting for input)."""

    def __init__(self, pid=424242):
        self.pid = pid
        self.returncode = None
        self.killed = False
        self._done = asyncio.Event()

    async def communicate(self):
        await self._done.wait()
        return b"", b""

    def kill(self):
        self.killed = True
        self.returncode = -9
        self._done.set()

    async def wait(self):
        await self._done.wait()
        return self.returncode


@pytest.fixture
def hanging(monkeypatch):
    proc = HangingProc()
    tree_kills: list[int] = []

    async def fake_exec(*argv, **kw):
        return proc

    monkeypatch.setattr(pca.asyncio, "create_subprocess_exec", fake_exec)
    # never call the real taskkill with a fake pid (neither via the helper nor directly)
    monkeypatch.setattr(pca, "_taskkill_tree", lambda pid: tree_kills.append(pid), raising=False)
    monkeypatch.setattr(pca.subprocess, "run",
                        lambda argv, **kw: tree_kills.append(int(argv[-1])))
    return proc, tree_kills


async def _start_run_command(fake_svc, timeout_s=60):  # noqa: F811
    rt = pca.get_runtime(fake_svc)
    job = asyncio.create_task(pca.execute_pc_step(
        fake_svc, rt, task_id=901, run_id=901, step_idx=0, tool="pc.run_command",
        args={"argv": ["git", "status"], "timeout_s": timeout_s}, approved=True))
    for _ in range(200):
        if rt.active_procs:
            break
        await asyncio.sleep(0.005)
    assert rt.active_procs, "command never started"
    return rt, job


async def test_cancelled_run_command_kills_the_child_tree(fake_svc, hanging):  # noqa: F811
    proc, tree_kills = hanging
    rt, job = await _start_run_command(fake_svc)
    job.cancel()                                   # STOP / outer timeout cancels the step
    with pytest.raises(asyncio.CancelledError):
        await job
    assert proc.killed is True, "the child kept running after its step was cancelled"
    assert tree_kills == [proc.pid]
    assert proc.pid not in rt.active_procs


async def test_timed_out_run_command_kills_and_reaps_the_child(fake_svc, hanging):  # noqa: F811
    proc, tree_kills = hanging
    rt, job = await _start_run_command(fake_svc, timeout_s=1)
    res = await asyncio.wait_for(job, timeout=10)
    assert res["ok"] is False and "таймаут" in res["error"]
    assert proc.killed is True and tree_kills == [proc.pid]
    assert proc.pid not in rt.active_procs
