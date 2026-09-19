"""Дефекты, вскрытые живым прогоном оператора на машине владельца 2026-09-06.

Каждый из них стоил владельцу времени и ни один не был виден из кода: система
запускалась, «работала» и молча не делала того, что обещала.
Источник: docs/testing/acceptance-run-20260906/OPEN_FINDINGS.json.
"""
from __future__ import annotations

import os
import tomllib
from pathlib import Path

import pytest

from bossman.gateway.client import DEFAULT_BASE_URL, GatewayClient, normalize_base_url

REPO = Path(__file__).resolve().parents[2]


# ------------------------------------------------------- GATEWAY-URL-V1

@pytest.mark.parametrize("configured,expected", [
    ("http://127.0.0.1:8765", "http://127.0.0.1:8765/v1"),   # ровно случай владельца
    ("http://127.0.0.1:8765/", "http://127.0.0.1:8765/v1"),
    ("http://127.0.0.1:8877", "http://127.0.0.1:8877/v1"),
    ("", DEFAULT_BASE_URL),
    ("   ", DEFAULT_BASE_URL),
])
def test_a_gateway_address_without_a_version_is_completed(configured, expected):
    """Адрес без `/v1` превращал КАЖДЫЙ ход планировщика в 404: 21 перепланирование
    подряд и «planner replan budget» без намёка на причину."""
    assert normalize_base_url(configured) == expected


@pytest.mark.parametrize("configured", [
    "http://127.0.0.1:8765/v1",       # уже полный — не трогаем
    "http://127.0.0.1:8765/v2",       # явная другая версия — решение владельца
    "https://gw.example/openai/v1",   # версия не в конце хоста
])
def test_an_explicit_version_is_never_rewritten(configured):
    assert normalize_base_url(configured) == configured


def test_the_client_uses_the_completed_address_for_its_requests(monkeypatch):
    monkeypatch.setenv("BOSSMAN_GATEWAY_URL", "http://127.0.0.1:8877")
    assert GatewayClient().base_url == "http://127.0.0.1:8877/v1"
    monkeypatch.delenv("BOSSMAN_GATEWAY_URL")
    assert GatewayClient().base_url == DEFAULT_BASE_URL
    assert GatewayClient("http://h:1/").base_url == "http://h:1/v1"


# ---------------------------------------------------- OBSERVER-DEPS-001

def test_the_windows_operator_declares_the_packages_it_cannot_see_without():
    """На чистой машине наблюдение возвращало ModuleNotFoundError, планировщик
    выжигал бюджет и задача падала: оператор был слеп, а выглядело как дефект
    логики. Пакеты обязаны быть объявлены, иначе установить их неоткуда."""
    data = tomllib.loads((REPO / "bossman-core" / "pyproject.toml").read_text(encoding="utf-8"))
    extras = data["project"]["optional-dependencies"]
    assert "windows" in extras, "нет extra `windows`: ставить нечего"
    declared = " ".join(extras["windows"]).lower()
    for package in ("pywinauto", "pywin32", "pyautogui", "pillow"):
        assert package in declared, f"{package} не объявлен"
    # Извлекать их на Linux/macOS незачем и они там не собираются.
    for requirement in extras["windows"]:
        assert "sys_platform == 'win32'" in requirement, requirement


def test_the_doctor_reports_the_operator_and_the_gateway_address():
    """Диагноз обязан стоить секунды до прогона, а не час посреди него."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "bossman_doctor", REPO / "scripts" / "bossman_doctor.py")
    doctor = importlib.util.module_from_spec(spec)
    # Регистрируем ДО exec_module: dataclasses резолвят аннотации через
    # sys.modules[cls.__module__], и без записи Check не собирается.
    import sys as _sys
    _sys.modules["bossman_doctor"] = doctor
    try:
        spec.loader.exec_module(doctor)
    finally:
        _sys.modules.pop("bossman_doctor", None)
    assert doctor.check_computer_operator_deps in doctor.CHECKS
    assert doctor.check_gateway_url in doctor.CHECKS

    operator = doctor.check_computer_operator_deps()
    if os.name == "nt":
        assert operator.status in (doctor.PASS, doctor.BLOCKED)
        if operator.status is doctor.BLOCKED:
            assert "bossman-core[windows]" in operator.remedy
    else:
        assert operator.status == doctor.PASS      # адаптер сюда не идёт

    gateway = doctor.check_gateway_url()
    assert gateway.facts["effective"].rsplit("/", 1)[-1].startswith("v")
