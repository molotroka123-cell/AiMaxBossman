"""pc.run_command child tree (audit 28.10): `taskkill /T` walks a snapshot of the tree, so a grandchild born after
the snapshot (or orphaned) survives. The Job Object kills every member at once. Proven here with a real grandchild."""
from __future__ import annotations

import asyncio
import os
import sys
import time

import psutil
import pytest

from bcc.features import pc_agent
from bcc.v2.terminal_control import _CREATE_SUSPENDED, _win_bind_and_resume, _win_job_close

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object")

PARENT = (
    "import subprocess, sys, time\n"
    "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
    "print(c.pid, flush=True)\n"
    "time.sleep(60)\n"
)


def test_reap_with_job_kills_the_grandchild():
    async def run() -> int:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", PARENT, stdout=asyncio.subprocess.PIPE, creationflags=_CREATE_SUSPENDED)
        job = _win_bind_and_resume(proc)
        assert job is not None
        grandchild = int((await asyncio.wait_for(proc.stdout.readline(), 20)).decode().strip())
        assert psutil.pid_exists(grandchild)
        await pc_agent._reap_child(proc, proc.pid, job)
        _win_job_close(job)
        return grandchild

    gc = asyncio.run(run())
    deadline = time.time() + 5
    while psutil.pid_exists(gc) and time.time() < deadline:
        time.sleep(0.1)
    assert not psutil.pid_exists(gc), "grandchild survived the job kill"


def test_reap_without_job_keeps_old_signature():
    async def run() -> None:
        proc = await asyncio.create_subprocess_exec(sys.executable, "-c", "import time; time.sleep(60)")
        await pc_agent._reap_child(proc, proc.pid)
        assert proc.returncode is not None

    asyncio.run(run())
