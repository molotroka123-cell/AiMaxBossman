"""S6: the repository doctor (scripts/bossman_doctor.py) has a Telegram-calls row that can only be PASS or WARN."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

DOCTOR = Path(__file__).resolve().parents[3] / "scripts" / "bossman_doctor.py"


@pytest.fixture()
def doctor():
    spec = importlib.util.spec_from_file_location("bossman_doctor_under_test", DOCTOR)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_row_is_registered(doctor):
    assert doctor.check_telegram_calls in doctor.CHECKS


def test_all_packages_present_is_a_pass(doctor, monkeypatch):
    monkeypatch.setattr(doctor, "_importable", lambda module: True)
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
