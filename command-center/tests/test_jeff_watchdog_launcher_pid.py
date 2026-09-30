"""Watchdog health with a launcher process: the heartbeat may be written by a DESCENDANT of the spawned pid.

A Windows venv `python.exe` is a redirector: the pid that `subprocess.Popen` returns (and the watchdog remembers) is the launcher,
the real interpreter that writes `heartbeat.json` is its child. The watchdog used to require beat.pid == spawned pid, so a healthy
Jeff was declared unhealthy after the start grace and killed every few minutes (live on 2026-09-30: 16 restarts in an hour)."""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from bcc.pit import heartbeat as hb


def watchdog(tmp_path: Path, beat: dict | None) -> hb.Watchdog:
    return hb.Watchdog(tmp_path, spawn=lambda: None, is_running=lambda home: False, beat_reader=lambda home: beat)


def test_a_heartbeat_from_the_child_of_the_spawned_launcher_is_healthy(tmp_path, monkeypatch):
    monkeypatch.setattr(hb, "_descendant_pids", lambda pid: {4242} if pid == 100 else set())
    assert watchdog(tmp_path, {"pid": 4242, "availability": "up"})._healthy(100) is True


def test_the_same_pid_is_still_healthy(tmp_path):
    assert watchdog(tmp_path, {"pid": 100, "availability": "up"})._healthy(100) is True


def test_a_foreign_pid_or_a_stale_beat_is_still_unhealthy(tmp_path, monkeypatch):
    """Negative controls: only the spawned process and ITS descendants count, and the beat must be fresh."""
    monkeypatch.setattr(hb, "_descendant_pids", lambda pid: {4242})
    assert watchdog(tmp_path, {"pid": 777, "availability": "up"})._healthy(100) is False       # not ours
    assert watchdog(tmp_path, {"pid": 4242, "availability": "stale"})._healthy(100) is False   # ours, but not beating
    assert watchdog(tmp_path, None)._healthy(100) is False


def test_descendants_of_a_real_process_tree_and_of_a_dead_pid():
    child = subprocess.Popen([sys.executable, "-c",
                              "import subprocess, sys, time; "
                              "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); time.sleep(30)"])
    try:
        deadline = time.time() + 15
        found: set[int] = set()
        while time.time() < deadline and not found:
            found = hb._descendant_pids(child.pid)
            time.sleep(0.2)
        assert found, "the grandchild of the spawned process is found"
        assert child.pid not in found
    finally:
        child.kill()
        child.wait(timeout=10)
    assert hb._descendant_pids(child.pid) == set() or child.pid not in hb._descendant_pids(child.pid)
    assert hb._descendant_pids("not-a-pid") == set()
