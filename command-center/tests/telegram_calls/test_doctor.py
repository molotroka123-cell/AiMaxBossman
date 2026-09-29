"""Doctor: PASS/WARN/BLOCKED with a Russian remedy, no secrets, no model loading, no real subprocess in tests."""
from __future__ import annotations

import json
import re

import pytest

from bcc.telegram_calls import doctor
from bcc.telegram_calls.speech import factory
from bcc.telegram_calls.stopflag import StopFlag


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("BOSSMAN_TG_COMPANION_CONFIG", str(tmp_path / "no-such-companion.json"))
    for k in ("BOSSMAN_WHISPER_MODEL_PATH", "BOSSMAN_PIPER_VOICE_PATH"):
        monkeypatch.delenv(k, raising=False)


def ok_probe(argv):
    return 0, "ok"


def by_id(items):
    return {i["id"]: i for i in items}


def test_empty_install_is_blocked_with_russian_remedies(tmp_path):
    items = doctor.run_checks(tmp_path, probe=ok_probe)
    d = by_id(items)
    assert doctor.verdict(items) == "BLOCKED"
    for key in ("calls_packages", "whisper_model", "piper_voice", "companion_routes", "ntgcalls_loads"):
        assert d[key]["status"] == "BLOCKED", key
        assert re.search("[А-Яа-я]", d[key]["hint"]), key
    assert d["credentials"]["status"] == "WARN" and d["login"]["status"] == "WARN" and d["peer"]["status"] == "WARN"
    assert d["calls_enabled"]["status"] == "PASS" and d["stop_flag"]["status"] == "PASS" and d["worker"]["status"] == "PASS"
    assert all(i["status"] in {"PASS", "WARN", "BLOCKED"} for i in items)
    assert all(i["hint"] == "" for i in items if i["status"] == "PASS")           # remedy only when something is wrong


def test_stop_flag_blocks_and_clear_passes(tmp_path):
    StopFlag(tmp_path).set("owner")
    assert by_id(doctor.run_checks(tmp_path, probe=ok_probe))["stop_flag"]["status"] == "BLOCKED"
    StopFlag(tmp_path).clear()
    assert by_id(doctor.run_checks(tmp_path, probe=ok_probe))["stop_flag"]["status"] == "PASS"


def test_worker_probe_failure_is_blocked_success_passes(tmp_path):
    assert by_id(doctor.run_checks(tmp_path, probe=lambda a: (1, "ModuleNotFoundError")))["worker"]["status"] == "BLOCKED"
    assert by_id(doctor.run_checks(tmp_path, probe=ok_probe))["worker"]["status"] == "PASS"


def test_ntgcalls_load_probe_runs_only_when_installed(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(doctor, "_addon_version", lambda d, dist: "3.0.0" if dist != "telethon" else "1.45.0")

    def probe(argv):
        calls.append(argv)
        return (1, "ImportError") if "ntgcalls" in argv[-1] else (0, "ok")

    d = by_id(doctor.run_checks(tmp_path, probe=probe))
    assert d["ntgcalls_loads"]["status"] == "BLOCKED" and d["calls_packages"]["status"] == "PASS"
    assert "-I" in calls[0]                                                     # loaded in an isolated subprocess only


def test_version_mismatch_warns(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "_addon_version", lambda d, dist: "9.9.9")
    d = by_id(doctor.run_checks(tmp_path, probe=ok_probe))
    assert d["calls_versions"]["status"] == "WARN" and "9.9.9" in d["calls_versions"]["message"]


def test_all_green_engines_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "_addon_version", lambda d, dist: {"py-tgcalls": "3.0.0", "ntgcalls": "3.0.0", "telethon": "1.45.0"}[dist])
    good = {"ok": True, "verdict": "PASS", "items": [
        {"name": "stt", "level": "PASS", "ok": True, "info": {"package_installed": True}},
        {"name": "tts", "level": "PASS", "ok": True, "info": {"package_installed": True, "voice": "ru_RU-denis-medium", "language": "ru_RU"}},
        {"name": "brain", "level": "PASS", "ok": True, "info": {"routes": [{"route": "fast", "model": "m1"}]}},
        {"name": "vad", "level": "PASS", "ok": True, "info": {}}]}
    monkeypatch.setattr(factory, "doctor", lambda cfg, **k: good)
    d = by_id(doctor.run_checks(tmp_path, probe=ok_probe))
    for key in ("calls_packages", "ntgcalls_loads", "faster_whisper", "whisper_model", "piper", "piper_voice", "silero_vad", "companion_routes"):
        assert d[key]["status"] == "PASS", (key, d[key])
    assert "m1" in d["companion_routes"]["message"]


def test_non_russian_voice_warns(tmp_path, monkeypatch):
    monkeypatch.setattr(factory, "doctor", lambda cfg, **k: {"items": [
        {"name": "stt", "level": "PASS", "ok": True, "info": {"package_installed": True}},
        {"name": "tts", "level": "PASS", "ok": True, "info": {"package_installed": True, "voice": "en_US-amy", "language": "en_US"}},
        {"name": "brain", "level": "PASS", "ok": True, "info": {}},
        {"name": "vad", "level": "WARN", "ok": False, "info": {}}]})
    d = by_id(doctor.run_checks(tmp_path, probe=ok_probe))
    assert d["piper_voice"]["status"] == "WARN" and d["silero_vad"]["status"] == "WARN"


def test_login_state_is_boolean_only_and_never_leaks(tmp_path):
    from bcc.telegram_calls.account.credentials import CredentialStore
    CredentialStore(tmp_path).save_api(123456, "abcdef0123456789abcdef0123456789")
    CredentialStore(tmp_path).save_session("SESSIONSECRET-STRING", 777)
    items = doctor.run_checks(tmp_path, probe=ok_probe)
    blob = json.dumps(items, ensure_ascii=False)
    assert "SESSIONSECRET" not in blob and "abcdef0123456789" not in blob and "123456" not in blob
    d = by_id(items)
    assert d["credentials"]["status"] == "PASS" and d["login"]["status"] == "PASS"


def test_peer_states(tmp_path):
    from bcc.telegram_calls.settings import SettingsStore
    st = SettingsStore(tmp_path)
    st.set_peer(4242, "second")
    assert by_id(doctor.run_checks(tmp_path, probe=ok_probe))["peer"]["status"] == "WARN"       # selected, not confirmed
    st.confirm_peer(4242)
    assert by_id(doctor.run_checks(tmp_path, probe=ok_probe))["peer"]["status"] == "PASS"


def test_doctor_never_loads_models(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("model load attempted")
    monkeypatch.setattr(factory.FasterWhisperSTT, "_load_sync", boom, raising=False)
    monkeypatch.setattr(factory.PiperTTS, "_load_sync", boom, raising=False)
    doctor.run_checks(tmp_path, probe=ok_probe)


# ---------------------------------------------------------------- scripts/bossman_doctor.py integration
def _script_module():
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[3] / "scripts" / "bossman_doctor.py"
    spec = importlib.util.spec_from_file_location("bossman_doctor_calls_check", path)
    mod = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[spec.name] = mod                # @dataclass needs the module registered
    spec.loader.exec_module(mod)
    return mod


def test_script_check_is_warn_never_blocked_when_calls_not_ready(tmp_path, monkeypatch):
    mod = _script_module()
    monkeypatch.setenv("BCC_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(doctor, "run_checks", lambda d, **k: [
        {"id": "calls_packages", "status": "BLOCKED", "message": "x", "hint": "y"},
        {"id": "peer", "status": "WARN", "message": "x", "hint": "y"}])
    c = mod.check_telegram_calls()
    assert c.name == "telegram-calls" and c.status == "WARN" and "calls_packages" in c.detail
    assert mod.check_telegram_calls in mod.CHECKS


def test_script_check_passes_when_ready_and_warns_on_crash(tmp_path, monkeypatch):
    mod = _script_module()
    monkeypatch.setenv("BCC_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(doctor, "run_checks", lambda d, **k: [{"id": "peer", "status": "PASS", "message": "", "hint": ""}])
    ok = mod.check_telegram_calls()
    assert ok.status == "PASS" and "не проверялся" in ok.detail          # never claims a real call was verified

    def boom(d, **k):
        raise RuntimeError("secret-looking-detail")

    monkeypatch.setattr(doctor, "run_checks", boom)
    crashed = mod.check_telegram_calls()
    assert crashed.status == "WARN" and "secret-looking-detail" not in crashed.detail     # class name only, still WARN
