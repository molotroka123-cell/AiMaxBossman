"""Windows Job Object tree containment (audit 28.10, 6.2 gap 3): proven on REAL processes of this machine.

Each test starts a Python parent that spawns a grandchild; the job must take them all, and nothing may escape.
Skipped off Windows. Only processes started by these tests are touched.
"""
from __future__ import annotations

import subprocess
import sys
import time

import pytest

from bcc.security import win_job as wj

pytestmark = pytest.mark.skipif(not wj.IS_WINDOWS, reason="Windows Job Objects")
psutil = pytest.importorskip("psutil")

# A parent that FIRST thing spawns a grandchild (so a start-up race would let it escape), prints its pid, then sleeps.
PARENT = (
    "import subprocess, sys, time\n"
    "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
    "print(g.pid, flush=True)\n"
    "time.sleep(120)\n"
)


def _alive(pid: int) -> bool:
    try:
        return psutil.Process(pid).is_running() and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def _wait_dead(pids, timeout=5.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if not any(_alive(p) for p in pids):
            return True
        time.sleep(0.1)
    return False


def _start_tree(job):
    p = wj.spawn_in_job([sys.executable, "-c", PARENT], job, stdout=subprocess.PIPE, text=True)
    grandchild = int(p.stdout.readline().strip())
    return p, grandchild


def test_terminate_kills_the_parent_and_the_grandchild_born_at_start():
    with wj.JobObject() as job:
        p, g = _start_tree(job)
        assert _alive(p.pid) and _alive(g)
        assert job.active_count() >= 2                      # the grandchild is inside the job: no start-up race
        job.terminate()
        assert _wait_dead([p.pid, g]), "something in the tree survived TerminateJobObject"


def test_closing_the_last_handle_kills_the_tree_kill_on_close():
    job = wj.JobObject()
    p, g = _start_tree(job)
    assert _alive(g)
    job.close()                                             # e.g. Bossman crashes: the kernel closes the handle
    assert _wait_dead([p.pid, g]), "kill-on-close did not kill the tree"
    assert job.closed


def test_a_process_outside_the_job_is_not_touched_negative_control():
    outsider = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        with wj.JobObject() as job:
            p, g = _start_tree(job)
            job.terminate()
            assert _wait_dead([p.pid, g])
        assert _alive(outsider.pid), "the job killed a process that was never in it"
    finally:
        outsider.kill()


def test_active_process_limit_stops_a_fork_bomb():
    code = ("import subprocess, sys\n"
            "n = 0\n"
            "for _ in range(8):\n"
            "    try:\n"
            "        subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); n += 1\n"
            "    except OSError:\n"
            "        break\n"
            "print(n, flush=True)\n"
            "import time; time.sleep(30)\n")
    with wj.JobObject(active_process_limit=3) as job:
        p = wj.spawn_in_job([sys.executable, "-c", code], job, stdout=subprocess.PIPE, text=True)
        spawned = int(p.stdout.readline().strip())
        assert spawned <= 2, f"{spawned} children started under an active-process limit of 3 (parent included)"
        assert job.active_count() <= 3
        job.terminate()


def test_memory_limit_denies_a_big_allocation():
    code = ("try:\n"
            "    b = bytearray(400 * 1024 * 1024)\n"
            "    for i in range(0, len(b), 4096): b[i] = 1\n"
            "    print('ALLOC_OK', flush=True)\n"
            "except MemoryError:\n"
            "    print('DENIED', flush=True)\n")
    with wj.JobObject(memory_limit_bytes=150 * 1024 * 1024) as job:
        p = wj.spawn_in_job([sys.executable, "-c", code], job, stdout=subprocess.PIPE, text=True)
        out, _ = p.communicate(timeout=60)
    assert "ALLOC_OK" not in out, "a 400 MiB allocation succeeded under a 150 MiB job limit"


def test_closed_job_and_bad_arguments_are_refused():
    job = wj.JobObject()
    job.close()
    with pytest.raises(wj.JobObjectError):
        job.active_count()
    job.close()                                             # closing twice is harmless
    with pytest.raises(ValueError):
        wj.JobObject(active_process_limit=0)
    with pytest.raises(ValueError):
        wj.JobObject(memory_limit_bytes=1024)
