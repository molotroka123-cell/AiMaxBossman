"""MEDIA-LIFECYCLE regressions: the engine process must not outlive its owner.

The hole the owner pointed at: `reconcile_orphans` only runs when the NEXT Bossman
process starts.  Between the moment the backend dies and that next start, nothing
bounds the engine — it keeps holding RAM/GPU for as long as it likes.  Cleaning up
"next time" is not a lifecycle.

Everything here is OFFLINE: the "engine" is a Python sleeper, the "backend" is a
Python child of pytest that is hard-killed (TerminateProcess — the crash case, no
atexit, no finally).  No GPU, no model, no foreign process is ever touched.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import signal
import os
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from bcc.studio.providers import sdcpp

CC = str(Path(__file__).resolve().parents[1])
CORE = str(Path(CC).parent / "bossman-core")

MODEL = {"id": "sdcpp:z-image-turbo", "surface": "image", "deadline_seconds": 900}

# A "backend" that spawns one engine child through the provider's real spawn path,
# publishes its pid, and then blocks forever.  Killing it is the crash we reproduce.
OWNER_SCRIPT = r'''
import asyncio, json, pathlib, sys, time
sys.path[:0] = [%(cc)r, %(core)r]
from bcc.studio.providers import sdcpp

root = pathlib.Path(sys.argv[1])
model = {"id": "sdcpp:z-image-turbo", "surface": "image", "deadline_seconds": 900}
prov = sdcpp.SdCppProvider({"root": root, "bin": root / "bin", "manifest": {}}, root, model)
job = {"canceled": False,
       "argv": [sys.executable, "-c", "import time; time.sleep(900)"]}
proc = asyncio.run(prov._spawn(job))
(root / "engine.pid").write_text(json.dumps({"pid": proc.pid}), encoding="utf-8")
time.sleep(900)
'''


def _spawn_owner(tmp_path: Path) -> tuple[psutil.Process, psutil.Process]:
    """Start the fake backend, wait for its engine child, return (owner, engine)."""
    script = tmp_path / "owner.py"
    script.write_text(OWNER_SCRIPT % {"cc": CC, "core": CORE}, encoding="utf-8")
    owner = subprocess.Popen([sys.executable, str(script), str(tmp_path)],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    marker = tmp_path / "engine.pid"
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if marker.is_file():
            break
        if owner.poll() is not None:
            raise AssertionError(f"fake backend died: {owner.stdout.read().decode('utf-8', 'replace')}")
        time.sleep(0.05)
    else:
        owner.kill()
        raise AssertionError("fake backend never reported an engine pid")
    pid = json.loads(marker.read_text(encoding="utf-8"))["pid"]
    return psutil.Process(owner.pid), psutil.Process(pid)


def _gone(proc: psutil.Process, timeout: float) -> bool:
    try:
        proc.wait(timeout=timeout)
        return True
    except psutil.TimeoutExpired:
        return False
    except psutil.NoSuchProcess:
        return True


@pytest.mark.skipif(os.name != "nt", reason="kernel-enforced job objects are a Windows mechanism")
def test_engine_dies_with_its_owner_not_at_the_next_restart(tmp_path):
    """MEDIA-LIFECYCLE regression.

    Backend hard-killed while the engine runs.  Before the fix the engine kept
    running (only a future `reconcile_orphans` would have reaped it, i.e. never
    until someone restarts Bossman).  The engine must be gone within seconds,
    enforced by the kernel, without any resident service.
    """
    owner, engine = _spawn_owner(tmp_path)
    assert engine.is_running(), "the fake engine never started"
    owner.kill()                       # TerminateProcess: no atexit, no finally, a real crash
    assert _gone(owner, 15), "the fake backend survived kill()"
    assert _gone(engine, 20), (
        f"orphan window reproduced: engine pid {engine.pid} outlived its dead owner; "
        "nothing bounds it until the next Bossman start")


def test_sidecar_records_the_deadline_and_the_task_it_belongs_to(tmp_path):
    """An orphan can only be judged overdue if its budget was written down.

    The sidecar is the only thing a later process can read, so it must carry the
    owner's task id and the absolute deadline, next to pid/create_time/argv.
    """
    prov = sdcpp.SdCppProvider({"root": tmp_path, "bin": tmp_path / "bin", "manifest": {}},
                               tmp_path, MODEL, studio_job_id=4242)
    work = prov.work
    record = {"version": 1, "rid": "a" * 16, "pid": None, "create_time": None,
              "argv": ["x"], "exe": None, "raw": str(work / "a.png"), "init": None,
              "started": time.time(), "studio_job_id": prov.studio_job_id,
              "model": MODEL["id"], "owner_pid": os.getpid(), "owner_create_time": 1.0,
              "deadline_s": 120.0, "deadline_at": time.time() - 1}
    side = sdcpp._sidecar_path(work, record["rid"])
    sdcpp._write_sidecar(side, record)
    loaded = json.loads(side.read_text(encoding="utf-8"))
    assert loaded["studio_job_id"] == 4242
    assert loaded["deadline_at"] < time.time()

    # Owner is dead (pid 1.0 create_time never matches), pid is absent -> the record is
    # reported as overdue, not silently dropped: the owner must see the budget was blown.
    report = sdcpp.reconcile_orphans(work, kill=False)
    assert record["rid"] in report["overdue"], report


# ----------------------------------------------------------------------------- MEDIA-RESTART layer 1
# Port of e2183fc3: binding AFTER the engine starts leaves a window in which the engine runs
# outside the job — a backend that dies inside it leaves an unbound engine, and anything the
# engine spawns in it is born outside the job. The engine must be created suspended, bound,
# and only then resumed. CREATE_SUSPENDED is Windows-only; here it is simulated with SIGSTOP by a
# fake `_create_subprocess`, so the ORDER is checked on every platform with a real process.

_CREATE_SUSPENDED = 0x00000004


def _suspending_spawner(spawned: list):
    async def fake(*argv, **kwargs):
        flags = kwargs.pop("creationflags", 0)
        proc = await asyncio.create_subprocess_exec(*argv, **kwargs)
        spawned.append(proc)
        if flags & _CREATE_SUSPENDED:                 # what the Windows kernel does for us
            os.kill(proc.pid, signal.SIGSTOP)
            # CREATE_SUSPENDED is synchronous; SIGSTOP delivery is not — wait until it took.
            ps, end = psutil.Process(proc.pid), time.monotonic() + 5
            while ps.status() != psutil.STATUS_STOPPED and time.monotonic() < end:
                await asyncio.sleep(0.005)
        return proc
    return fake


def _engine_job():
    return {"canceled": False, "argv": [sys.executable, "-c", "import time; time.sleep(60)"]}


async def _reap(spawned):
    for p in spawned:
        with contextlib.suppress(ProcessLookupError):
            p.kill()
        await p.wait()


@pytest.mark.skipif(os.name == "nt", reason="SIGSTOP stands in for CREATE_SUSPENDED off Windows")
async def test_engine_is_bound_before_it_runs_a_single_step(tmp_path, monkeypatch):
    spawned, at_bind = [], []
    monkeypatch.setattr(sdcpp, "_create_subprocess", _suspending_spawner(spawned))
    # Pretend the owner job exists (it is Windows-only); raising=False so that the red run on the
    # pre-port code (no such hook) still reaches the ordering assertion below.
    monkeypatch.setattr(sdcpp, "_suspend_until_bound", lambda: True, raising=False)

    def bind(pid, **_):
        at_bind.append(psutil.Process(pid).status())
        return True
    monkeypatch.setattr(sdcpp, "bind_to_owner_lifetime", bind)

    prov = sdcpp.SdCppProvider({"root": tmp_path, "bin": tmp_path / "bin", "manifest": {}}, tmp_path, MODEL)
    job = _engine_job()
    try:
        proc = await prov._spawn(job)
        assert at_bind == [psutil.STATUS_STOPPED], (
            f"engine was already running when it was bound to the owner job: {at_bind}")
        assert job["lifecycle_bound"] is True
        assert psutil.Process(proc.pid).status() != psutil.STATUS_STOPPED, "engine never resumed"
    finally:
        await _reap(spawned)


@pytest.mark.skipif(os.name == "nt", reason="SIGSTOP stands in for CREATE_SUSPENDED off Windows")
async def test_engine_that_cannot_be_resumed_is_killed_not_left_suspended(tmp_path, monkeypatch):
    spawned = []
    monkeypatch.setattr(sdcpp, "_create_subprocess", _suspending_spawner(spawned))
    monkeypatch.setattr(sdcpp, "_suspend_until_bound", lambda: True, raising=False)
    monkeypatch.setattr(sdcpp, "bind_to_owner_lifetime", lambda pid, **_: True)

    def broken_resume(pid):
        raise OSError("resume refused")
    monkeypatch.setattr(sdcpp, "_resume_process", broken_resume, raising=False)

    prov = sdcpp.SdCppProvider({"root": tmp_path, "bin": tmp_path / "bin", "manifest": {}}, tmp_path, MODEL)
    try:
        with pytest.raises(OSError):
            await prov._spawn(_engine_job())
        assert len(spawned) == 1
        assert spawned[0].returncode is not None, "a suspended engine was left behind"
        assert not psutil.pid_exists(spawned[0].pid) or _gone(psutil.Process(spawned[0].pid), 10)
    finally:
        await _reap(spawned)


@pytest.mark.skipif(os.name != "nt", reason="kernel-enforced job objects are a Windows mechanism")
def test_child_the_engine_spawns_at_once_also_dies_with_the_owner(tmp_path):
    """On the real kernel: the engine starts a grandchild immediately; with the suspended spawn
    it is born inside the job and must die with the owner."""
    grand = tmp_path / "grand.pid"
    engine_code = ("import subprocess, sys, time, pathlib; "
                   "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(900)']); "
                   f"pathlib.Path({str(grand)!r}).write_text(str(p.pid)); time.sleep(900)")
    script = tmp_path / "owner.py"
    script.write_text((OWNER_SCRIPT % {"cc": CC, "core": CORE}).replace(
        '"import time; time.sleep(900)"', repr(engine_code)), encoding="utf-8")
    owner = subprocess.Popen([sys.executable, str(script), str(tmp_path)],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and not grand.is_file():
        time.sleep(0.05)
    assert grand.is_file(), "the engine never started its child"
    child = psutil.Process(int(grand.read_text(encoding="utf-8")))
    owner.kill()
    assert _gone(psutil.Process(owner.pid), 15)
    assert _gone(child, 20), f"engine grandchild pid {child.pid} outlived its dead owner"
