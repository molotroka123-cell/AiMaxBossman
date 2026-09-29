"""Second shortcut, starting holder, PID reuse, token race (RC19 lifecycle audit).

P1-4: two shortcuts at once — the launcher that lost the data-root lock printed
«сервер не поднялся» (exit 3) instead of attaching to the winner; a holder that
was still starting was treated the same way. Its app (token, vault key) was
built BEFORE the lock, so on a first-ever double launch the loser could
rewrite the winner's token file.
P1-5: a desktop.lock of a killed launcher whose PID was reused by another
process refused every new window («окно уже запущено») while no window existed.
"""
from __future__ import annotations

import io
import json
import os

from bcc import auth, backend_lock, desktop
from bcc.config import settings

SAME = {"app": desktop.APP_IDENTITY, "version": "0.1.0", "source_identity": "PASS",
        "build_sha": "a" * 40}


def _args(tmp_path, port: int) -> list[str]:
    return ["--port", str(port), "--browser", "dummy-browser",
            "--profile", str(tmp_path / "profile"), "--no-show-token"]


def _no_own_server(started: list):
    class _MustNotStart:
        def __init__(self, host, port):
            started.append(port)

        def start(self, url, timeout=30.0):
            return False

        def stop(self):
            pass

    return _MustNotStart


def test_a_starting_holder_is_awaited_and_attached_not_a_server_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(desktop, "_local_identity", lambda: SAME)
    answers = iter([None, None, None])
    asked: list[str] = []

    def identify(url, *a, **k):
        asked.append(url)
        return next(answers, SAME) if url == "http://127.0.0.1:18051/" else None

    monkeypatch.setattr(desktop, "identify_server", identify)
    monkeypatch.setattr(desktop, "port_busy", lambda *a, **k: False)
    started: list = []
    monkeypatch.setattr(desktop, "_BackgroundServer", _no_own_server(started))
    opened: list[str] = []
    lock = backend_lock.acquire(tmp_path, host="127.0.0.1", port=18051)
    try:
        code = desktop.run(_args(tmp_path, 18052),
                           launcher=lambda browser, url, *a, **k: opened.append(url) or 0,
                           out=io.StringIO())
    finally:
        lock.release()
    assert code == 0
    assert opened == ["http://127.0.0.1:18051/"] and started == []
    assert asked.count("http://127.0.0.1:18051/") >= 4          # it waited for the holder


def test_a_silent_holder_is_named_and_no_second_server_is_started(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(desktop, "HOLDER_WAIT_S", 0.5)
    monkeypatch.setattr(desktop, "_local_identity", lambda: SAME)
    monkeypatch.setattr(desktop, "identify_server", lambda *a, **k: None)
    monkeypatch.setattr(desktop, "port_busy", lambda *a, **k: False)
    started: list = []
    monkeypatch.setattr(desktop, "_BackgroundServer", _no_own_server(started))
    out = io.StringIO()
    lock = backend_lock.acquire(tmp_path, host="127.0.0.1", port=18053)
    try:
        code = desktop.run(_args(tmp_path, 18053), launcher=lambda *a, **k: 0, out=out)
    finally:
        lock.release()
    assert code == 3 and started == []
    assert "уже запущен" in out.getvalue() and "18053" in out.getvalue()
    assert "data-root-holder-silent" in (tmp_path / "desktop-run.log").read_text(encoding="utf-8")


def test_the_launcher_that_lost_the_lock_race_attaches_to_the_winner(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(desktop, "_local_identity", lambda: SAME)
    winner: dict = {}
    monkeypatch.setattr(desktop, "identify_server",
                        lambda url, *a, **k: SAME if winner and url == "http://127.0.0.1:18055/" else None)
    monkeypatch.setattr(desktop, "port_busy", lambda *a, **k: False)

    class _LosesTheRace:
        """The other shortcut takes the data root between our probe and our acquire."""

        def __init__(self, host, port):
            self.error = None
            self.holder = None

        def start(self, url, timeout=30.0):
            winner["lock"] = backend_lock.acquire(tmp_path, host="127.0.0.1", port=18055)
            self.holder = dict(winner["lock"].info)
            self.error = str(backend_lock.BackendAlreadyRunning(self.holder))
            return False

        def stop(self):
            pass

    monkeypatch.setattr(desktop, "_BackgroundServer", _LosesTheRace)
    opened: list[str] = []
    out = io.StringIO()
    try:
        code = desktop.run(_args(tmp_path, 18054),
                           launcher=lambda browser, url, *a, **k: opened.append(url) or 0, out=out)
    finally:
        if winner:
            winner["lock"].release()
    assert code == 0, out.getvalue()
    assert opened == ["http://127.0.0.1:18055/"]
    assert "сервер не поднялся" not in out.getvalue()
    assert "lost-data-root-race" in (tmp_path / "desktop-run.log").read_text(encoding="utf-8")


def test_the_background_server_builds_no_app_before_it_holds_the_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    lock = backend_lock.acquire(tmp_path, host="127.0.0.1", port=18056)
    try:
        server = desktop._BackgroundServer("127.0.0.1", 18057)
        try:
            assert server.start("http://127.0.0.1:18057/", timeout=5) is False
        finally:
            server.stop()
    finally:
        lock.release()
    assert server.holder and server.holder["port"] == 18056
    # the loser never created (and so never rewrote) the token of this data root
    assert not (tmp_path / auth.TOKEN_FILE).exists()


def test_a_desktop_lock_with_a_reused_pid_does_not_refuse_the_window(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(desktop, "_local_identity", lambda: SAME)
    monkeypatch.setattr(desktop, "identify_server", lambda *a, **k: SAME)
    real = desktop._process_created(os.getpid())
    assert real is not None
    # our own (live) pid, but a creation time from another process
    (tmp_path / "desktop.lock").write_text(
        json.dumps({"pid": os.getpid(), "pid_created": real - 3600.0, "port": 18058}), encoding="utf-8")
    opened: list = []
    code = desktop.run(_args(tmp_path, 18058), launcher=lambda *a, **k: opened.append(a) or 0,
                       out=io.StringIO())
    assert code == 0 and len(opened) == 1
    assert "stale-lock-cleared" in (tmp_path / "desktop-run.log").read_text(encoding="utf-8")


def test_a_desktop_lock_of_the_same_live_process_still_refuses_a_second_window(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(desktop, "_local_identity", lambda: SAME)
    monkeypatch.setattr(desktop, "identify_server", lambda *a, **k: SAME)
    (tmp_path / "desktop.lock").write_text(
        json.dumps({"pid": os.getpid(), "pid_created": desktop._process_created(os.getpid()),
                    "port": 18059}), encoding="utf-8")
    opened: list = []
    code = desktop.run(_args(tmp_path, 18059), launcher=lambda *a, **k: opened.append(a) or 0,
                       out=io.StringIO())
    assert code == 0 and opened == []
    assert "refused-second-window" in (tmp_path / "desktop-run.log").read_text(encoding="utf-8")


def test_the_window_writes_its_creation_time_into_desktop_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(desktop, "_local_identity", lambda: SAME)
    monkeypatch.setattr(desktop, "identify_server", lambda *a, **k: SAME)
    seen: dict = {}

    def launcher(*a, **k):
        seen.update(json.loads((tmp_path / "desktop.lock").read_text(encoding="utf-8")))
        return 0

    desktop.run(_args(tmp_path, 18060), launcher=launcher, out=io.StringIO())
    assert seen["pid"] == os.getpid()
    assert abs(seen["pid_created"] - desktop._process_created(os.getpid())) < 1.0


def test_two_first_starts_never_rewrite_each_others_token(tmp_path):
    """The loser checked `exists()` before the winner wrote; O_TRUNC then
    replaced the winner's token on disk while the winner kept its own in memory."""
    (tmp_path / auth.TOKEN_FILE).write_text("winner-token", encoding="utf-8")

    class _CheckedTooEarly(type(tmp_path)):
        def exists(self, *a, **k):
            return False

    loser = object.__new__(auth.TokenAuth)
    loser.path = _CheckedTooEarly(tmp_path / auth.TOKEN_FILE)
    token, created = loser._load_or_create()
    assert (token, created) == ("winner-token", False)
    assert (tmp_path / auth.TOKEN_FILE).read_text(encoding="utf-8") == "winner-token"
