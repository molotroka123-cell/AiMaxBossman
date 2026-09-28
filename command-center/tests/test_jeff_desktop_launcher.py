"""Jeff launcher: build-bound attach, own server, never the Command Center backend."""
from __future__ import annotations

import io
import os

import pytest

from bcc import jeff_desktop
from bcc.pit.config import config_path, save_setup
from bcc.telegram_companion.config import Person


def _configured(tmp_path):
    save_setup(config_path(tmp_path), people=[Person(user_id=1, chat_id=1, role="owner")],
               chat_models=["x/y:free"], provider_base_url="https://openrouter.ai/api/v1",
               core_url="http://127.0.0.1:8800", web_only=True, provider_key="k")
    return tmp_path


def test_unconfigured_data_dir_explains_setup(tmp_path):
    out = io.StringIO()
    assert jeff_desktop.run(["--data-dir", str(tmp_path), "--port", "8859"], out=out) == 3
    assert "web-setup" in out.getvalue()


def test_refuses_a_jeff_server_of_another_build(tmp_path, monkeypatch):
    _configured(tmp_path)
    monkeypatch.setattr(jeff_desktop, "local_identity", lambda: {
        "app": "bossman-jeff-web-v1", "source_identity": "PASS", "build_sha": "a" * 40})
    monkeypatch.setattr(jeff_desktop, "_get_json", lambda url, timeout=2.0: {
        "app": "bossman-jeff-web-v1", "source_identity": "PASS", "build_sha": "b" * 40})
    out = io.StringIO()
    opened = []
    code = jeff_desktop.run(["--data-dir", str(tmp_path), "--port", "8859"],
                            launcher=lambda *a, **k: opened.append(a) or 0, out=out)
    assert code == 7 and opened == []


def test_attaches_to_same_build_and_never_stops_it(tmp_path, monkeypatch):
    _configured(tmp_path)
    ident = {"app": "bossman-jeff-web-v1", "source_identity": "PASS", "build_sha": "c" * 40}
    monkeypatch.setattr(jeff_desktop, "local_identity", lambda: dict(ident))
    monkeypatch.setattr(jeff_desktop, "_get_json", lambda url, timeout=2.0: dict(ident))
    monkeypatch.setattr(jeff_desktop, "JeffServer", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("must not start a second server")))
    opened = []
    code = jeff_desktop.run(["--data-dir", str(tmp_path), "--port", "8859", "--browser", "chrome.exe"],
                            launcher=lambda browser, url, profile, **k: opened.append((url, profile)) or 0,
                            out=io.StringIO())
    assert code == 0
    url, profile = opened[0]
    assert url == "http://127.0.0.1:8859/jeff.html"
    assert profile.name == "jeff-desktop-profile"


def test_foreign_service_on_port_is_not_reused(tmp_path, monkeypatch):
    _configured(tmp_path)
    monkeypatch.setattr(jeff_desktop, "_get_json", lambda url, timeout=2.0: None)
    monkeypatch.setattr(jeff_desktop, "_port_answers", lambda url: True)
    assert jeff_desktop.run(["--data-dir", str(tmp_path), "--port", "8859"], out=io.StringIO()) == 4


def test_command_center_identity_is_not_a_jeff_server(tmp_path, monkeypatch):
    _configured(tmp_path)
    local = {"app": "bossman-jeff-web-v1", "source_identity": "PASS", "build_sha": "d" * 40}
    cc = {"app": "bossman-command-center-build-bound-v1", "source_identity": "PASS", "build_sha": "d" * 40}
    assert jeff_desktop.same_build(local, cc) is False


def test_relaunch_with_open_jeff_window_does_not_open_a_second_one(tmp_path, monkeypatch):
    """J2: a second launch on a profile that a browser still holds opens no window."""
    _configured(tmp_path)
    ident = {"app": "bossman-jeff-web-v1", "source_identity": "PASS", "build_sha": "e" * 40}
    monkeypatch.setattr(jeff_desktop, "local_identity", lambda: dict(ident))
    monkeypatch.setattr(jeff_desktop, "_get_json", lambda url, timeout=2.0: dict(ident))
    monkeypatch.setattr(jeff_desktop, "profile_in_use", lambda profile: True)
    opened = []
    out = io.StringIO()
    code = jeff_desktop.run(["--data-dir", str(tmp_path), "--port", "8859", "--browser", "chrome.exe"],
                            launcher=lambda *a, **k: opened.append(a) or 0, out=out)
    assert code == 0 and opened == []
    assert "уже открыто" in out.getvalue()


@pytest.mark.skipif(os.name != "nt", reason="Chromium's Windows lockfile semantics")
def test_profile_in_use_detects_an_exclusively_held_lockfile(tmp_path):
    import ctypes
    from ctypes import wintypes
    profile = tmp_path / "profile"
    profile.mkdir()
    assert jeff_desktop.profile_in_use(profile) is False
    lock = profile / "lockfile"
    lock.write_bytes(b"")
    assert jeff_desktop.profile_in_use(profile) is False          # stale file: free
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    handle = kernel32.CreateFileW(str(lock), 0x40000000, 0, None, 3, 0x80, None)   # share mode 0
    assert handle not in (None, wintypes.HANDLE(-1).value)
    try:
        assert jeff_desktop.profile_in_use(profile) is True
    finally:
        kernel32.CloseHandle(handle)
    assert jeff_desktop.profile_in_use(profile) is False


def test_shortcut_launch_finds_the_data_root_piper_voice(tmp_path):
    env = {}
    jeff_desktop.default_voice_env(tmp_path, env)
    assert env == {}                                   # nothing there: text/system voice fallback
    voice = tmp_path / "voice"
    (voice / "piper").mkdir(parents=True)
    for name in ("piper/piper.exe", "ru_RU-denis-medium.onnx", "ru_RU-denis-medium.onnx.json"):
        (voice / name).write_bytes(b"x")
    env = {"BOSSMAN_PIT_TTS_MODEL_PATH": "explicit.onnx"}
    jeff_desktop.default_voice_env(tmp_path, env)
    assert env["BOSSMAN_PIT_TTS_EXECUTABLE"].endswith("piper.exe")
    assert env["BOSSMAN_PIT_TTS_MODEL_PATH"] == "explicit.onnx"    # explicit setting wins
