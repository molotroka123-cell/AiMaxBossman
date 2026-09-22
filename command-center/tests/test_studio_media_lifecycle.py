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

import json
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
