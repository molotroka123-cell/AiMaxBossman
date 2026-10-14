"""/poker_app: owner-only control of the public Poker Train host; strangers only get the «play in Jeff» hint."""
from __future__ import annotations

import json
import os
from types import SimpleNamespace

from bcc.telegram_companion import poker_app_bridge as pab
from bcc.telegram_companion.store import Store

OWNER = SimpleNamespace(role="owner")
GUEST = SimpleNamespace(role="guest")


def test_guest_cannot_start_or_stop_the_host(tmp_path, monkeypatch):
    monkeypatch.setattr(pab, "DATA", tmp_path)
    started = []
    monkeypatch.setattr(pab.subprocess, "Popen", lambda *a, **k: started.append(a))
    for arg in ("", "вкл", "выкл"):
        assert "только владельцу" in pab.poker_app_text(GUEST, arg)
    assert started == [] and not (tmp_path / "STOP").exists()


def test_start_refuses_without_a_game_build(tmp_path, monkeypatch):
    monkeypatch.setattr(pab, "DATA", tmp_path)
    monkeypatch.setattr(pab.subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("spawned")))
    assert "Нет сборки игры" in pab.poker_app_text(OWNER, "вкл")


def test_start_spawns_the_separate_host_process(tmp_path, monkeypatch):
    monkeypatch.setattr(pab, "DATA", tmp_path)
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "index.html").write_text("x", encoding="utf-8")
    calls = []
    monkeypatch.setattr(pab.subprocess, "Popen", lambda argv, **k: calls.append((argv, k["cwd"])))
    assert "Запускаю хост покера" in pab.poker_app_text(OWNER, "вкл")
    [(argv, cwd)] = calls
    assert argv[1:] == ["-m", "pokerhost", "run"] and cwd.endswith(os.path.join("apps", "poker-train-host"))


def test_status_and_stop_of_a_running_host(tmp_path, monkeypatch):
    monkeypatch.setattr(pab, "DATA", tmp_path)
    (tmp_path / "state.json").write_text(json.dumps({"status": "running", "url": "https://a.trycloudflare.com",
                                                     "pid": os.getpid(), "port": 1, "started": 0,
                                                     "buttons": {"jeff_all_users": True}}), encoding="utf-8")
    status = pab.poker_app_text(OWNER, "")
    assert "РАБОТАЕТ" in status and "https://a.trycloudflare.com" in status
    assert "Кнопка в боте Jeff для всех: да" in status and "у вас в пульте: НЕТ" in status
    assert "Останавливаю" in pab.poker_app_text(OWNER, "выкл") and (tmp_path / "STOP").exists()


def test_status_when_off(tmp_path, monkeypatch):
    monkeypatch.setattr(pab, "DATA", tmp_path)
    assert "выключен" in pab.poker_app_text(OWNER, "")


def test_poker_app_is_a_fast_control_lane_command():
    import inspect
    assert '"/poker_app"' in inspect.getsource(Store)
