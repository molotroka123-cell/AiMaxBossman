"""S6: the repository doctor (scripts/bossman_doctor.py) has a Telegram-calls row that can only be PASS or WARN."""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

DOCTOR = Path(__file__).resolve().parents[3] / "scripts" / "bossman_doctor.py"


@pytest.fixture()
def doctor():
    spec = importlib.util.spec_from_file_location("bossman_doctor_under_test", DOCTOR)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod              # dataclasses look the module up by name
    try:
        spec.loader.exec_module(mod)
        yield mod
    finally:
        sys.modules.pop(spec.name, None)


def test_the_row_is_registered(doctor):
    assert doctor.check_telegram_calls in doctor.CHECKS


def _voice_rows(monkeypatch, status):
    """The S6 voice/ACL rows are an input of the doctor row; pin them so these tests do not depend on this machine's voice files."""
    from bcc.telegram_calls import doctor_rows
    rows = [{"check": name, "status": status, "detail": f"{name}: test", "remedy": "x"}
            for name in ("Распознавание речи (Whisper)", "Голос (Piper)", "Локальная модель Jeff", "Права на файлы звонков")]
    monkeypatch.setattr(doctor_rows, "jeff_call_rows", lambda data_dir, **kw: rows)


S6_APPLIED = "voice_rows" in DOCTOR.read_text(encoding="utf-8")


def test_all_packages_present_is_a_pass(doctor, monkeypatch):
    monkeypatch.setattr(doctor, "_importable", lambda module: True)
    _voice_rows(monkeypatch, "PASS")
    check = doctor.check_telegram_calls()
    assert check.status == doctor.PASS and check.facts["module"] is True


def test_missing_addon_packages_are_a_warning_with_a_remedy(doctor, monkeypatch):
    monkeypatch.setattr(doctor, "_importable", lambda module: module == "bcc.telegram_calls")
    check = doctor.check_telegram_calls()
    assert check.status == doctor.WARN
    assert "telethon" in check.detail and "bossman call install" in check.remedy


def test_a_missing_module_is_still_only_a_warning_never_blocked(doctor, monkeypatch):
    monkeypatch.setattr(doctor, "_importable", lambda module: False)
    assert doctor.check_telegram_calls().status == doctor.WARN


# ---------------------------------------------------------------- S6 voice rows (bcc.telegram_calls.doctor_rows): PASS/WARN only

def test_the_voice_rows_are_only_ever_pass_or_warn(tmp_path, monkeypatch):
    from bcc.oss import whisper
    from bcc.telegram_calls import doctor_rows as dr
    for name in ("BOSSMAN_PIT_TTS_EXECUTABLE", "BOSSMAN_PIT_TTS_MODEL_PATH", "BOSSMAN_WHISPER_MODEL_PATH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(whisper, "status", lambda: {"status": "unavailable", "reason": "Whisper model is incomplete"})
    rows = dr.jeff_call_rows(tmp_path)
    assert [r["check"] for r in rows] == ["Распознавание речи (Whisper)", "Голос (Piper)", "Локальная модель Jeff", "Права на файлы звонков"]
    assert {r["status"] for r in rows} <= {"PASS", "WARN"}, "never BLOCKED: calls are an add-on, not a condition of Bossman"
    assert all(r["remedy"] for r in rows if r["status"] == "WARN")
    blob = " ".join(r["detail"] + r["remedy"] for r in rows)
    assert str(tmp_path) not in blob, "no local paths in the rows"


def test_an_unhealthy_acl_is_a_warning_here_while_the_calls_doctor_itself_blocks(tmp_path, monkeypatch):
    from bcc.telegram_calls import doctor_rows as dr
    from bcc.telegram_calls.hardening import AclReport
    home = tmp_path / "telegram-calls"
    home.mkdir()
    monkeypatch.setattr(dr, "check_calls_home", lambda h, k=None: [AclReport(str(h / "credentials.enc"), False, "unexpected principal(s): x", os.name)])
    row = dr.acl_row(home)
    assert row["status"] == "WARN" and "credentials.enc" in row["detail"] and row["remedy"]
    monkeypatch.setattr(dr, "check_calls_home", lambda h, k=None: [AclReport(str(h), True, "owner-only", os.name)])
    assert dr.acl_row(home)["status"] == "PASS"
    assert dr.acl_row(tmp_path / "absent")["status"] == "PASS", "nothing to protect yet"


def test_the_local_model_row_reports_the_route_honestly(tmp_path, monkeypatch):
    import httpx
    from bcc.pit import config as pit_config
    from bcc.telegram_calls import doctor_rows as dr

    class S:
        local_url = "http://127.0.0.1:11434/v1"
        local_models = ("qwen3:8b",)

    monkeypatch.setattr(pit_config, "load", lambda path: S())
    monkeypatch.setattr(httpx, "get", lambda *a, **k: type("R", (), {"status_code": 200})())
    assert dr.model_row(tmp_path)["status"] == "PASS"

    def down(*a, **k):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(httpx, "get", down)
    row = dr.model_row(tmp_path)
    assert row["status"] == "WARN" and "qwen3:8b" in row["detail"] and "Ollama" in row["remedy"]
    assert dr.model_row(tmp_path, probe=False)["status"] == "PASS", "no probe, no network"

    S.local_models = ()
    monkeypatch.setattr(pit_config, "load", lambda path: S())
    assert dr.model_row(tmp_path)["status"] == "WARN"

    def broken(path):
        raise ValueError("no config")
    monkeypatch.setattr(pit_config, "load", broken)
    assert dr.model_row(tmp_path)["status"] == "WARN"


@pytest.mark.skipif(not S6_APPLIED, reason="S6 patch (ASR/TTS/ACL rows) is not applied in scripts/bossman_doctor.py")
def test_a_weak_voice_tract_or_acl_is_a_warning_with_the_row_named_never_blocked(doctor, monkeypatch):
    monkeypatch.setattr(doctor, "_importable", lambda module: True)
    _voice_rows(monkeypatch, "WARN")
    check = doctor.check_telegram_calls()
    assert check.status == doctor.WARN and "Голос (Piper)" in check.detail and "bossman call doctor" in check.remedy
    assert [r["status"] for r in check.facts["voice_rows"]] == ["WARN"] * 4


@pytest.mark.skipif(not S6_APPLIED, reason="S6 patch (ASR/TTS/ACL rows) is not applied in scripts/bossman_doctor.py")
def test_a_crashing_voice_probe_cannot_crash_the_doctor(doctor, monkeypatch):
    from bcc.telegram_calls import doctor_rows
    monkeypatch.setattr(doctor, "_importable", lambda module: True)

    def boom(data_dir, **kw):
        raise RuntimeError("probe exploded")
    monkeypatch.setattr(doctor_rows, "jeff_call_rows", boom)
    check = doctor.check_telegram_calls()
    assert check.status == doctor.WARN and "RuntimeError" in check.detail
