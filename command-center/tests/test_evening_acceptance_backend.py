"""Регрессии вечерней приёмки, которые проверяются без браузера.

Здесь две правды, которые UI обязан получить от сервера, иначе он начнёт врать:

  * приложение без точки входа нельзя запустить — и сервер обязан СКАЗАТЬ это
    в /apps/{id}/process, а не только отказать 409 после нажатия кнопки;
  * флот не сертифицирован для распределённого production — признак едет вместе
    со сводкой control-plane, иначе пилюле «ЭКСПЕРИМЕНТ» в UI неоткуда взяться.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from bcc.features import apps_control
from bcc.features import control_plane


def _app_without_entrypoint() -> str:
    """Первое установленное приложение, у которого нет модуля запуска.

    Имя приложения не зашито: набор apps/ меняется, и тест, привязанный к
    solana-volume-suite, начал бы падать по причине, к делу не относящейся.
    """
    for app_id in apps_control.known_app_dirs():
        if apps_control.command_for(app_id)["problem"]:
            return app_id
    pytest.skip("все установленные приложения имеют точку запуска — нечего проверять")


def test_process_info_forwards_the_reason_start_would_refuse(tmp_path: Path) -> None:
    """`problem` доезжает до UI: иначе кнопка «Запустить» гарантированно врёт.

    Раньше process_info возвращал manual_command="" и молчал о причине, а карточка
    печатала плейсхолдер `python -m <модуль приложения>` и оставляла кнопку живой.
    """
    app_id = _app_without_entrypoint()
    reason = apps_control.command_for(app_id)["problem"]
    info = apps_control.process_info(app_id, tmp_path)

    assert info["problem"] == reason, info
    assert info["problem"], "причина отказа не должна быть пустой строкой"
    # И это ровно та причина, которой ответит POST /apps/{id}/start (409).
    assert info["manual_command"] == "", info
    assert info["command"] == [], info


def test_process_info_of_a_runnable_app_reports_no_problem(tmp_path: Path) -> None:
    """Обратная сторона: у нормального приложения поле пустое, кнопка живая."""
    runnable = [a for a in apps_control.known_app_dirs()
                if not apps_control.command_for(a)["problem"]]
    if not runnable:
        pytest.skip("нет ни одного приложения с точкой запуска")
    info = apps_control.process_info(runnable[0], tmp_path)
    assert info["problem"] == "", info
    assert info["manual_command"], info


def test_fleet_summary_is_marked_experimental_when_off() -> None:
    """Выключенный флот тоже помечен: UI не должен гадать по другим полям."""
    fleet = asyncio.run(control_plane._fleet(None))
    assert fleet["enabled"] is False
    assert fleet["experimental"] is True, fleet
    assert fleet["experimental_reason"], fleet


def test_fleet_summary_is_marked_experimental_when_enabled() -> None:
    """Включённый флот — тем более: именно тогда работа уезжает на чужой узел.

    Удалённый транспорт и аутентификация узлов не доведены (об этом говорит сама
    сводка), поэтому «узлов 3» без пометки читается как готовая функция.
    """
    class _Fleet:
        pass

    class _Org:
        fleet = _Fleet()

        def fleet_summary(self) -> dict:
            return {"enabled": True, "nodes": [{"node_id": "n1"}], "queue_depth": 0,
                    "remote_transport_production_ready": False,
                    "node_auth_production_ready": False}

    fleet = asyncio.run(control_plane._fleet(_Org()))
    assert fleet["enabled"] is True
    assert fleet["experimental"] is True, fleet
    assert fleet["experimental_reason"], fleet
    # Пометка добавлена, а не подменила состояние: сводка осталась прежней.
    assert fleet["nodes"] == [{"node_id": "n1"}]
    assert fleet["remote_transport_production_ready"] is False
