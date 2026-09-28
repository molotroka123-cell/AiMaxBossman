"""The terminal attaches only to the Command Center of ITS data root and build.

RC19 owner run: an old backend of another data root sat on 8800; discovery
accepted any Command Center marker there, `bossman start` answered «Bossman
уже работает», and every call came back 401. Now a server is used only when
its pid is the holder of <data>/backend.lock and it is not a proven other
build; everything else is refused by name («другая папка данных» /
«другая сборка»).
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from bcc import backend_lock
from bcc.terminal_cli import api_client, launch

ROOT = Path(__file__).resolve().parents[1]
MARK = api_client.APP_IDENTITY
UNKNOWN = {"source_identity": "SOURCE_IDENTITY_UNKNOWN", "build_sha": None}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("BOSSMAN_URL", raising=False)
    monkeypatch.delenv("BCC_HOST", raising=False)
    monkeypatch.setenv("BCC_PORT", "18200")
    monkeypatch.setattr(api_client, "_local_build", lambda: dict(UNKNOWN), raising=False)


def _ident(pid, sha=None):
    return {"app": MARK, "version": "0.1.0", "pid": pid,
            "source_identity": "PASS" if sha else "SOURCE_IDENTITY_UNKNOWN", "build_sha": sha}


def test_a_command_center_of_another_data_root_on_the_default_port_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(api_client, "identify", lambda url, *a, **k: _ident(4242))
    with pytest.raises(api_client.BossmanError) as err:
        api_client.discover(None, tmp_path, wait_holder=0)
    assert "другой папки данных" in err.value.message
    assert err.value.code == "foreign_backend" and err.value.kind == "disconnected"


def test_the_holder_of_this_data_root_is_accepted(tmp_path, monkeypatch):
    lock = backend_lock.acquire(tmp_path, host="127.0.0.1", port=18201)
    asked = []
    monkeypatch.setattr(api_client, "identify",
                        lambda url, *a, **k: asked.append(url) or _ident(os.getpid()))
    try:
        target = api_client.discover(None, tmp_path, wait_holder=0)
    finally:
        lock.release()
    assert target.url == "http://127.0.0.1:18201"
    assert asked == ["http://127.0.0.1:18201"]          # never the default port


def test_a_server_that_is_not_the_lock_holder_is_another_data_root(tmp_path, monkeypatch):
    lock = backend_lock.acquire(tmp_path, host="127.0.0.1", port=18202)
    monkeypatch.setattr(api_client, "identify", lambda url, *a, **k: _ident(os.getpid() + 1))
    try:
        with pytest.raises(api_client.BossmanError) as err:
            api_client.discover(None, tmp_path, wait_holder=0)
    finally:
        lock.release()
    assert "другой папки данных" in err.value.message


def test_a_proven_other_build_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(api_client, "_local_build",
                        lambda: {"source_identity": "PASS", "build_sha": "a" * 40})
    lock = backend_lock.acquire(tmp_path, host="127.0.0.1", port=18203)
    monkeypatch.setattr(api_client, "identify", lambda url, *a, **k: _ident(os.getpid(), "b" * 40))
    try:
        with pytest.raises(api_client.BossmanError) as err:
            api_client.discover(None, tmp_path, wait_holder=0)
    finally:
        lock.release()
    assert "другая сборка" in err.value.message and "bbbbbbbbbbbb" in err.value.message


def test_an_unproven_build_is_not_a_mismatch(tmp_path, monkeypatch):
    lock = backend_lock.acquire(tmp_path, host="127.0.0.1", port=18204)
    monkeypatch.setattr(api_client, "identify", lambda url, *a, **k: _ident(os.getpid(), "b" * 40))
    try:
        assert api_client.discover(None, tmp_path, wait_holder=0).url == "http://127.0.0.1:18204"
    finally:
        lock.release()


def test_an_old_server_without_a_pid_on_our_data_root_is_named_another_build(tmp_path, monkeypatch):
    lock = backend_lock.acquire(tmp_path, host="127.0.0.1", port=18205)
    monkeypatch.setattr(api_client, "identify", lambda url, *a, **k: _ident(None))
    try:
        with pytest.raises(api_client.BossmanError) as err:
            api_client.discover(None, tmp_path, wait_holder=0)
    finally:
        lock.release()
    assert "сборка" in err.value.message


def test_a_starting_holder_is_awaited_not_replaced_by_the_default_port(tmp_path, monkeypatch):
    lock = backend_lock.acquire(tmp_path, host="0.0.0.0", port=18206)
    calls = []

    def identify(url, *a, **k):
        calls.append(url)
        return _ident(os.getpid()) if len(calls) > 3 else None

    monkeypatch.setattr(api_client, "identify", identify)
    try:
        target = api_client.discover(None, tmp_path, wait_holder=10)
    finally:
        lock.release()
    assert target.url == "http://127.0.0.1:18206"       # bind address mapped, holder awaited
    assert set(calls) == {"http://127.0.0.1:18206"}


def test_a_remote_url_without_a_local_holder_is_left_to_the_token(tmp_path, monkeypatch):
    monkeypatch.setattr(api_client, "identify", lambda url, *a, **k: _ident(7))
    target = api_client.discover("http://100.64.0.7:8800", tmp_path, wait_holder=0)
    assert target.url == "http://100.64.0.7:8800"


def test_start_never_calls_another_data_roots_server_already_running(tmp_path, monkeypatch):
    monkeypatch.setattr(launch, "identify", lambda url, *a, **k: _ident(4242))
    popen = []
    monkeypatch.setattr(launch.subprocess, "Popen", lambda *a, **k: popen.append(a))
    with pytest.raises(api_client.BossmanError) as err:
        launch.start_backend(data_dir=str(tmp_path), port=18200, wait_seconds=1)
    assert "другой папки данных" in err.value.message and popen == []


def test_start_that_lost_the_race_attaches_to_the_winner(tmp_path, monkeypatch):
    """P2-9: our child exits 5 because another launcher took the data root in
    the same second — that is a running Bossman, not a failed start."""
    winner = {}

    class _Child:
        pid = 1
        returncode = 5

        def __init__(self, *a, **k):
            winner["lock"] = backend_lock.acquire(tmp_path, host="127.0.0.1", port=18208)

        def poll(self):
            return 5

    monkeypatch.setattr(launch.subprocess, "Popen", _Child)
    monkeypatch.setattr(launch, "_port_busy", lambda url: False)
    monkeypatch.setattr(launch, "identify",
                        lambda url, *a, **k: _ident(os.getpid()) if winner and url.endswith(":18208") else None)
    try:
        info = launch.start_backend(data_dir=str(tmp_path), port=18207, wait_seconds=5)
    finally:
        if winner:
            winner["lock"].release()
    assert info["url"] == "http://127.0.0.1:18208" and info["already_running"] is True


# ------------------------------------------------------------ real processes

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_real_backend_of_another_data_root_is_refused_and_its_own_accepted(tmp_path, monkeypatch):
    mine, other = tmp_path / "mine", tmp_path / "other"
    mine.mkdir()
    other.mkdir()
    port = _free_port()
    env = dict(os.environ)
    env.update(BCC_DATA_DIR=str(other), BCC_TOKEN_STDOUT="0",
               PYTHONPATH=os.pathsep.join([str(ROOT), env.get("PYTHONPATH", "")]))
    proc = subprocess.Popen([sys.executable, "-m", "bcc.app", "--host", "127.0.0.1", "--port", str(port)],
                            env=env, cwd=str(other), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    monkeypatch.setenv("BCC_PORT", str(port))
    try:
        deadline = time.monotonic() + 90
        while api_client.identify(f"http://127.0.0.1:{port}") is None:
            assert proc.poll() is None, "backend exited"
            assert time.monotonic() < deadline, "backend did not start"
            time.sleep(0.3)
        with pytest.raises(api_client.BossmanError) as err:
            api_client.discover(None, mine, wait_holder=0)
        assert "другой папки данных" in err.value.message
        target = api_client.discover(None, other, wait_holder=5)
        assert target.url == f"http://127.0.0.1:{port}"
        assert target.identity["pid"] == backend_lock.running_backend(other)["pid"]
    finally:
        _kill_tree(proc)


def _kill_tree(proc: subprocess.Popen) -> None:
    """The venv python.exe on Windows is a redirector: the server is its child."""
    import psutil
    try:
        children = psutil.Process(proc.pid).children(recursive=True)
    except psutil.Error:
        children = []
    for child in children:
        try:
            child.kill()
        except psutil.Error:
            pass
    proc.kill()
    proc.wait(30)
