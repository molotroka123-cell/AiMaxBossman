"""backend.lock under concurrent probes (RC19 lifecycle audit, P0-2).

`running_backend()` — the probe the window, the terminal and the rc19 script
run — takes the same byte for a moment and reads backend.json. Measured on
Windows before the fix (one process probing in a loop, another acquiring):
~2 % of acquires failed, half with a false `BackendAlreadyRunning` (the server
exits 5 «уже запущен» although nothing runs) and half with WinError 5 from
`os.replace(backend.json)` while a probe had it open.
"""
from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from bcc import backend_lock

ROOT = Path(__file__).resolve().parents[1]


def _hold_byte(data: Path, seconds: float, taken: threading.Event) -> None:
    handle = backend_lock._open(data)
    assert backend_lock._try_lock(handle)
    taken.set()
    time.sleep(seconds)
    backend_lock._unlock(handle)
    handle.close()


def test_acquire_waits_out_a_probe_that_holds_the_byte(tmp_path):
    taken = threading.Event()
    probe = threading.Thread(target=_hold_byte, args=(tmp_path, 0.15, taken))
    probe.start()
    assert taken.wait(5)
    try:
        lock = backend_lock.acquire(tmp_path, host="127.0.0.1", port=18101)
    finally:
        probe.join(5)
    try:
        assert backend_lock.running_backend(tmp_path)["port"] == 18101
    finally:
        lock.release()


def test_a_real_holder_is_still_refused_after_the_grace(tmp_path, monkeypatch):
    monkeypatch.setattr(backend_lock, "ACQUIRE_GRACE_S", 0.1)
    first = backend_lock.acquire(tmp_path, host="127.0.0.1", port=18102)
    try:
        t0 = time.monotonic()
        with pytest.raises(backend_lock.BackendAlreadyRunning):
            backend_lock.acquire(tmp_path, host="127.0.0.1", port=18103)
        assert time.monotonic() - t0 < 2.0
    finally:
        first.release()


def test_acquire_survives_a_reader_holding_backend_json_open(tmp_path):
    (tmp_path / backend_lock.INFO_NAME).write_text('{"pid": 1, "port": 1}', encoding="utf-8")
    opened = threading.Event()

    def reader():
        with open(tmp_path / backend_lock.INFO_NAME, "rb"):
            opened.set()
            time.sleep(0.2)

    t = threading.Thread(target=reader)
    t.start()
    assert opened.wait(5)
    try:
        lock = backend_lock.acquire(tmp_path, host="127.0.0.1", port=18104)
    finally:
        t.join(5)
    try:
        assert backend_lock._read_info(tmp_path)["port"] == 18104
    finally:
        lock.release()


def test_a_failed_info_write_does_not_leave_the_root_locked(tmp_path, monkeypatch):
    def broken(*_a, **_k):
        raise PermissionError("backend.json занят")

    monkeypatch.setattr(backend_lock, "_write_info", broken)
    with pytest.raises(PermissionError) as failure:
        backend_lock.acquire(tmp_path, host="127.0.0.1", port=18105)
    monkeypatch.undo()
    # `failure` keeps the failed frame (and its handle) alive, like a caller
    # that logs the exception: the byte must be free anyway.
    assert failure.value is not None
    backend_lock.acquire(tmp_path, host="127.0.0.1", port=18106).release()


def test_a_held_lock_with_a_dead_holders_info_is_starting_not_that_port(tmp_path, monkeypatch):
    monkeypatch.setattr(backend_lock, "ACQUIRE_GRACE_S", 0.1)
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait(30)
    (tmp_path / backend_lock.INFO_NAME).write_text(
        f'{{"pid": {dead.pid}, "host": "127.0.0.1", "port": 18107}}', encoding="utf-8")
    taken = threading.Event()
    holder = threading.Thread(target=_hold_byte, args=(tmp_path, 1.0, taken))
    holder.start()
    try:
        assert taken.wait(5)
        info = backend_lock.running_backend(tmp_path)
        assert info is not None and info.get("starting") is True
        assert info.get("port") is None
    finally:
        holder.join(5)


def test_connect_host_never_returns_an_unconnectable_bind_address():
    assert backend_lock.connect_host("0.0.0.0") == "127.0.0.1"
    assert backend_lock.connect_host("::") == "127.0.0.1"
    assert backend_lock.connect_host(None) == "127.0.0.1"
    assert backend_lock.connect_host("127.0.0.1") == "127.0.0.1"
    assert backend_lock.connect_host("100.64.0.7") == "100.64.0.7"


_PROBER = """
import sys, time
from pathlib import Path
from bcc import backend_lock
d = Path(sys.argv[1]); end = time.monotonic() + float(sys.argv[2])
print("ready", flush=True)
while time.monotonic() < end:
    backend_lock.running_backend(d)
"""


def test_acquire_under_a_probing_loop_never_fails(tmp_path):
    """The measured race, bounded: a second process probes non-stop while this
    one acquires and releases. Before the fix ~2 % of acquires failed."""
    import os
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT), env.get("PYTHONPATH", "")])
    prober = subprocess.Popen([sys.executable, "-c", _PROBER, str(tmp_path), "4"],
                              env=env, stdout=subprocess.PIPE, text=True)
    try:
        assert prober.stdout.readline().strip() == "ready"
        failures: list[str] = []
        done = 0
        end = time.monotonic() + 2.5
        while time.monotonic() < end:
            try:
                backend_lock.acquire(tmp_path, host="127.0.0.1", port=18108).release()
                done += 1
            except Exception as exc:  # noqa: BLE001 — every kind of failure counts
                failures.append(type(exc).__name__)
        assert done > 50, done
        assert failures == []
    finally:
        prober.kill()
        prober.wait(30)
