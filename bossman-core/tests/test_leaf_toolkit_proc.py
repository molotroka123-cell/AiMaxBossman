"""authored_by_lane (opsplug): bossman.toolkit._proc - bounded host processes: a timeout is an outcome, never an orphan.

Real child processes (a parent that spawns a grandchild), real timeouts; nothing about the unit is mocked.
"""
import asyncio
import os
import subprocess
import sys
import time

import pytest

from bossman.toolkit import _proc

psutil = pytest.importorskip("psutil")

PARENT_WITH_CHILD = (
    "import subprocess,sys,time;"
    "c=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)']);"
    "print(c.pid,flush=True);time.sleep(120)"
)


async def _spawn(code: str, **kw):
    return await asyncio.create_subprocess_exec(
        sys.executable, "-c", code, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        **_proc.tree_spawn_kwargs(), **kw)


def _gone(pid: int, wait: float = 5.0) -> bool:
    end = time.time() + wait
    while time.time() < end:
        if not psutil.pid_exists(pid):
            return True
        try:
            if psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
                return True
        except psutil.Error:
            return True
        time.sleep(0.05)
    return False


def test_spawn_kwargs_make_the_child_the_leader_of_its_own_group():
    kw = _proc.tree_spawn_kwargs()
    if os.name == "nt":
        assert kw == {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    else:
        assert kw == {"start_new_session": True}


async def test_communicate_within_returns_output_of_a_fast_process():
    proc = await _spawn("print('hello')")
    out, _ = await _proc.communicate_within(proc, 30)
    assert out.strip() == b"hello" and proc.returncode == 0


async def test_timeout_returns_none_and_kills_the_whole_tree_including_the_grandchild():
    proc = await _spawn(PARENT_WITH_CHILD)
    line = await asyncio.wait_for(proc.stdout.readline(), 20)
    grandchild = int(line.strip())
    assert psutil.pid_exists(grandchild)
    t0 = time.time()
    assert await _proc.communicate_within(proc, 1.0) is None          # timeout is an outcome, not an exception
    assert time.time() - t0 < 30
    assert proc.returncode is not None                                  # the parent really exited (awaited)
    assert _gone(grandchild), "the grandchild survived the timeout: an orphan"


async def test_cancellation_by_owner_stop_also_kills_the_tree():
    proc = await _spawn(PARENT_WITH_CHILD)
    line = await asyncio.wait_for(proc.stdout.readline(), 20)
    grandchild = int(line.strip())
    task = asyncio.ensure_future(_proc.communicate_within(proc, 60))
    await asyncio.sleep(0.5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert proc.returncode is not None and _gone(grandchild)


async def test_communicate_bounded_reports_exit_code_or_timeout():
    ok = await _spawn("import sys;print('x');sys.exit(3)")
    assert await _proc.communicate_bounded(ok, 30, "demo") == (3, b"x" + os.linesep.encode())
    slow = await _spawn("import time;time.sleep(60)")
    assert await _proc.communicate_bounded(slow, 0.5, "demo") is None
    assert slow.returncode is not None


async def test_kill_tree_on_an_already_finished_process_is_harmless():
    proc = await _spawn("pass")
    await proc.communicate()
    await _proc.kill_tree(proc)
    assert proc.returncode == 0


def test_timeout_message_and_code():
    assert _proc.TIMEOUT_CODE == 124
    assert _proc.timeout_message("git status", 2.5) == "таймаут 2.5 с: git status остановлен"
