"""Engineering lease: one writer across processes, TTL/heartbeat, stale takeover, orphan kill."""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from bcc.autonomy.journal import Journal
from bcc.autonomy.lease import EngineeringLease, LeaseBusy, LeaseLost, kill_process_group, pid_alive

CC = Path(__file__).resolve().parents[1]


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def lease(tmp_path, clock=None, alive=None, killer=None, pid=4242):
    alive_set = alive if alive is not None else {4242}
    return EngineeringLease(tmp_path, journal=Journal(tmp_path / "j"), clock=clock or Clock(),
                            killer=killer or (lambda p: alive_set.discard(p)),
                            is_alive=lambda p: p in alive_set, pid=pid)


def test_acquire_busy_release(tmp_path):
    lz = lease(tmp_path)
    t = lz.acquire("JEFF-1", "claude", ttl_s=60)
    assert lz.current() == t
    with pytest.raises(LeaseBusy) as exc:
        lz.acquire("JEFF-2", "codex", ttl_s=60)
    assert exc.value.current.holder == "claude"
    assert lz.release(t) == {"released": True, "orphans_killed": []}
    assert lz.current() is None
    assert lz.acquire("JEFF-2", "codex", ttl_s=60).holder == "codex"


def test_release_with_foreign_token_is_refused(tmp_path):
    lz = lease(tmp_path)
    lz.acquire("JEFF-1", "claude", ttl_s=60)
    with pytest.raises(LeaseLost):
        lz.release("not-the-token")
    assert lz.current() is not None


def test_ttl_expiry_heartbeat_and_takeover_kills_orphans(tmp_path):
    clock = Clock()
    alive = {4242, 777}
    killed = []
    lz = lease(tmp_path, clock, alive, killer=lambda p: (killed.append(p), alive.discard(p)))
    t = lz.acquire("JEFF-1", "claude", ttl_s=30)
    t = lz.register_process(t, 777)
    clock.t += 20
    t = lz.heartbeat(t)
    clock.t += 20                                   # 20s since heartbeat < 30: still held
    with pytest.raises(LeaseBusy):
        lz.acquire("JEFF-2", "codex", ttl_s=30)
    clock.t += 11                                   # heartbeat expired
    new = lz.acquire("JEFF-2", "codex", ttl_s=30)
    assert new.holder == "codex" and killed == [777]
    kinds = [e["kind"] for e in lz.journal.entries()]
    assert "lease.expired" in kinds and "lease.takeover" in kinds
    with pytest.raises(LeaseLost):
        lz.heartbeat(t)


def test_dead_holder_is_stale(tmp_path):
    alive = {4242}
    lz = lease(tmp_path, alive=alive)
    lz.acquire("JEFF-1", "claude", ttl_s=3600)
    alive.discard(4242)
    assert lz.current() is None


def test_release_kills_workers_that_are_still_running(tmp_path):
    alive = {4242, 9}
    lz = lease(tmp_path, alive=alive)
    t = lz.register_process(lz.acquire("JEFF-1", "codex", ttl_s=60), 9)
    assert lz.release(t)["orphans_killed"] == [9]


def test_release_fails_when_a_worker_cannot_be_killed(tmp_path):
    alive = {4242, 9}
    lz = lease(tmp_path, alive=alive, killer=lambda p: None)
    t = lz.register_process(lz.acquire("JEFF-1", "codex", ttl_s=60), 9)
    with pytest.raises(LeaseLost):
        lz.release(t)
    assert lz.current() is not None


def test_unreadable_lease_file_is_expired(tmp_path):
    lz = lease(tmp_path)
    lz.path.write_text("{garbage")
    assert lz.current() is None and not lz.path.exists()


# ------------------------------------------------------------ real processes

ACQUIRE = """
import sys, time, pathlib
from bcc.autonomy.lease import EngineeringLease, LeaseBusy
root, go, hold = sys.argv[1], pathlib.Path(sys.argv[2]), float(sys.argv[3])
while not go.exists():
    time.sleep(0.01)
lz = EngineeringLease(root)
try:
    t = lz.acquire("JEFF-1", "w", ttl_s=60)
except LeaseBusy:
    print("BUSY", flush=True); sys.exit(0)
print("GOT", flush=True)
time.sleep(hold)
if hold >= 0.5:
    lz.release(t)
"""


def test_exactly_one_writer_across_processes(tmp_path):
    go = tmp_path / "go"
    procs = [subprocess.Popen([sys.executable, "-c", ACQUIRE, str(tmp_path / "lease"), str(go), "2"],
                              cwd=CC, stdout=subprocess.PIPE, text=True) for _ in range(5)]
    go.write_text("1")
    outs = [p.communicate(timeout=60)[0].strip() for p in procs]
    assert sorted(outs) == ["BUSY"] * 4 + ["GOT"], outs


def test_crashed_holder_process_is_taken_over(tmp_path):
    go = tmp_path / "go"
    go.write_text("1")
    p = subprocess.run([sys.executable, "-c", ACQUIRE, str(tmp_path / "lease"), str(go), "0"],
                       cwd=CC, capture_output=True, text=True, timeout=60)
    assert p.stdout.strip() == "GOT"                         # exited without releasing
    lz = EngineeringLease(tmp_path / "lease", journal=Journal(tmp_path / "j"))
    t = lz.acquire("JEFF-2", "codex", ttl_s=60)
    assert t.holder == "codex"
    assert any(e["payload"].get("reason") == "holder process is gone" for e in lz.journal.entries())
    lz.release(t)


def test_default_killer_kills_a_real_orphan_tree(tmp_path):
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        assert pid_alive(child.pid)
        kill_process_group(child.pid)
        child.wait(timeout=10)
        deadline = time.monotonic() + 5
        while pid_alive(child.pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not pid_alive(child.pid)
    finally:
        if child.poll() is None:
            child.kill()
