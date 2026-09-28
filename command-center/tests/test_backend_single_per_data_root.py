"""One backend per data root (owner desktop defect, 2026-09-28).

The UX shortcut started a server on :8801 and the CMD shortcut another on
:8800 — two builds writing one data root. Every guard was per port. These
tests pin the per-data-root lock and that the window and the terminal attach
to the holder instead of starting a second server.
"""
from __future__ import annotations

import io
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from bcc import backend_lock, desktop
from bcc.config import settings
from bcc.terminal_cli import api_client

ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_second_acquire_on_same_data_root_is_refused_with_holder_info(tmp_path):
    first = backend_lock.acquire(tmp_path, host="127.0.0.1", port=18001, build_sha="a" * 40)
    try:
        with pytest.raises(backend_lock.BackendAlreadyRunning) as info:
            backend_lock.acquire(tmp_path, host="127.0.0.1", port=18002)
        assert info.value.info["port"] == 18001
        assert "18001" in str(info.value)
        assert backend_lock.running_backend(tmp_path)["port"] == 18001
    finally:
        first.release()
    assert backend_lock.running_backend(tmp_path) is None
    backend_lock.acquire(tmp_path, host="127.0.0.1", port=18003).release()


def test_stale_info_without_a_held_lock_is_ignored(tmp_path):
    (tmp_path / backend_lock.LOCK_NAME).write_bytes(b"\0")
    (tmp_path / backend_lock.INFO_NAME).write_text('{"pid": 1, "port": 1}', encoding="utf-8")
    assert backend_lock.running_backend(tmp_path) is None
    assert api_client._holder_port(tmp_path) is None


def test_terminal_discovery_prefers_the_data_root_holder(tmp_path, monkeypatch):
    monkeypatch.delenv("BOSSMAN_URL", raising=False)
    monkeypatch.setenv("BCC_PORT", "8800")
    lock = backend_lock.acquire(tmp_path, host="127.0.0.1", port=18011)
    try:
        assert api_client.candidate_urls(None, tmp_path)[0] == "http://127.0.0.1:18011"
    finally:
        lock.release()
    assert api_client.candidate_urls(None, tmp_path) == ["http://127.0.0.1:8800"]


def test_terminal_never_starts_a_second_server_for_a_held_data_root(tmp_path, monkeypatch):
    from bcc.terminal_cli import launch
    monkeypatch.setattr(launch, "identify", lambda *a, **k: None)
    popen = []
    monkeypatch.setattr(launch.subprocess, "Popen", lambda *a, **k: popen.append(a))
    lock = backend_lock.acquire(tmp_path, host="127.0.0.1", port=18021)
    try:
        with pytest.raises(api_client.BossmanError) as err:
            launch.start_backend(data_dir=str(tmp_path), port=18022, wait_seconds=1)
        assert "18021" in err.value.message and popen == []
    finally:
        lock.release()


def test_window_attaches_to_the_holder_port_instead_of_its_own(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    same = {"app": desktop.APP_IDENTITY, "version": "0.1.0", "source_identity": "PASS",
            "build_sha": "a" * 40}
    monkeypatch.setattr(desktop, "_local_identity", lambda: same)
    asked = []
    monkeypatch.setattr(desktop, "identify_server", lambda url, *a, **k: asked.append(url) or same)
    opened = []
    lock = backend_lock.acquire(tmp_path, host="127.0.0.1", port=18031)
    try:
        code = desktop.run(["--port", "18032", "--browser", "dummy-browser",
                            "--profile", str(tmp_path / "profile"), "--no-show-token"],
                           launcher=lambda browser, url, *a, **k: opened.append(url) or 0,
                           out=io.StringIO())
    finally:
        lock.release()
    assert code == 0
    assert opened == ["http://127.0.0.1:18031/"]
    assert "http://127.0.0.1:18032/" not in asked
    assert "attach-data-root-backend port=18031" in (tmp_path / "desktop-run.log").read_text(encoding="utf-8")


def _start(data: Path, port: int) -> subprocess.Popen:
    env = os.environ.copy()
    env["BCC_DATA_DIR"] = str(data)
    env["BCC_TOKEN_STDOUT"] = "0"
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT), env.get("PYTHONPATH", "")])
    return subprocess.Popen([sys.executable, "-m", "bcc.app", "--host", "127.0.0.1", "--port", str(port)],
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=str(data))


def _wait_holder(data: Path, timeout: float = 60.0) -> dict | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        holder = backend_lock.running_backend(data)
        if holder:
            return holder
        time.sleep(0.2)
    return None


def test_real_processes_second_server_exits_5_and_crash_frees_the_root(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    first_port, second_port = _free_port(), _free_port()
    first = _start(data, first_port)
    try:
        holder = _wait_holder(data)
        assert holder and holder["port"] == first_port and holder["pid"] == first.pid
        second = _start(data, second_port)
        _, err = second.communicate(timeout=60)
        assert second.returncode == 5
        assert str(first_port) in err.decode("utf-8", "replace")
    finally:
        first.kill()                       # crash: no cleanup code runs
        first.communicate(timeout=30)
    assert backend_lock.running_backend(data) is None
    third = _start(data, second_port)
    try:
        holder = _wait_holder(data)
        assert holder and holder["port"] == second_port
    finally:
        third.kill()
        third.communicate(timeout=30)
