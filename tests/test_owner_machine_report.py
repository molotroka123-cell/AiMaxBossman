"""Отчёт о машине владельца: каждая стадия отвечает за себя и не врёт.

Проверяется не «код исполняется», а свойства, ради которых отчёт написан:
неизмеримое не выдаётся за измеренное, счёт на процессоре не выдаётся за
ускорение, и никакая комбинация стадий не превращается в аппаратную приёмку.
К каждому вердикту здесь идёт ПАРА: законный случай проходит, и настоящий
плохой случай по-прежнему отвергается.
"""
from __future__ import annotations

import json
import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import owner_machine_report as report  # noqa: E402

PASS, WARN, NOT_MEASURED, BLOCKED = report.PASS, report.WARN, report.NOT_MEASURED, report.BLOCKED


def _loaded(*names: str) -> dict:
    return report._stage("model_loaded", PASS, "", facts={"models": list(names)})


def _not_loaded() -> dict:
    return report._stage("model_loaded", NOT_MEASURED, "", facts={})


# --- ускорение: главная пара ------------------------------------------------

def test_weights_in_video_memory_are_reported_as_working_acceleration(monkeypatch):
    monkeypatch.setattr(report, "_get_json", lambda url, timeout=5.0: {
        "models": [{"name": "qwen3:8b", "size": 8_000_000_000, "size_vram": 8_000_000_000}]})
    stage = report.stage_acceleration(_loaded("qwen3:8b"))
    assert stage["status"] == PASS
    assert stage["facts"]["qwen3:8b"]["share_on_gpu"] == 1.0


def test_a_model_computed_on_the_processor_is_never_called_accelerated(monkeypatch):
    """Негативный контроль. Мощный GPU в списке устройств — не ускорение.

    Ровно этот случай отчёт и обязан ловить: веса целиком в обычной памяти,
    видеопамять нулевая. Если он когда-нибудь вернёт здесь PASS, владелец
    прочитает «ускорение работает» на машине, которая считает процессором.
    """
    monkeypatch.setattr(report, "_get_json", lambda url, timeout=5.0: {
        "models": [{"name": "qwen3:8b", "size": 8_000_000_000, "size_vram": 0}]})
    stage = report.stage_acceleration(_loaded("qwen3:8b"))
    assert stage["status"] == WARN
    assert "процессор" in stage["detail"]
    assert stage["facts"]["qwen3:8b"]["share_on_gpu"] == 0.0


def test_partial_offload_is_measured_rather_than_rounded_to_yes_or_no(monkeypatch):
    monkeypatch.setattr(report, "_get_json", lambda url, timeout=5.0: {
        "models": [{"name": "big:70b", "size": 40_000_000_000, "size_vram": 10_000_000_000}]})
    stage = report.stage_acceleration(_loaded("big:70b"))
    assert stage["status"] == PASS
    assert stage["facts"]["big:70b"]["share_on_gpu"] == 0.25
    assert "25%" in stage["detail"]


def test_an_unreachable_server_does_not_become_a_verdict_about_acceleration(monkeypatch):
    def refuse(url, timeout=5.0):
        raise urllib.error.URLError("no server")

    monkeypatch.setattr(report, "_get_json", refuse)
    stage = report.stage_acceleration(_loaded("qwen3:8b"))
    assert stage["status"] == NOT_MEASURED
    assert stage["status"] != WARN, "молчание сервера — не доказательство счёта на процессоре"


def test_acceleration_is_not_guessed_when_no_model_is_loaded():
    stage = report.stage_acceleration(_not_loaded())
    assert stage["status"] == NOT_MEASURED
    assert stage["facts"]["depends_on"] == "model_loaded"


# --- сервер и загрузка модели -----------------------------------------------

def test_a_reachable_server_with_no_models_is_a_warning_not_a_pass(monkeypatch):
    monkeypatch.setattr(report, "_get_json", lambda url, timeout=5.0: {"models": []})
    server = report.stage_model_server()
    assert server["status"] == PASS, "сам сервер отвечает — это правда"
    loaded = report.stage_model_loaded(server)
    assert loaded["status"] == WARN, "но весов в нём нет, и это другая правда"
    assert loaded["facts"]["models"] == []


def test_a_silent_server_is_not_measured_rather_than_failed(monkeypatch):
    def refuse(url, timeout=5.0):
        raise OSError("connection refused")

    monkeypatch.setattr(report, "_get_json", refuse)
    stage = report.stage_model_server()
    assert stage["status"] == NOT_MEASURED
    assert "next_step" in stage["facts"]


# --- скорость: «не измерено» не равно нулю ----------------------------------

def test_speed_is_absent_rather_than_zero_when_there_is_nothing_to_ask():
    stage = report.stage_speed_memory(_not_loaded())
    assert stage["status"] == NOT_MEASURED
    assert "tokens_per_second" not in stage["facts"], "отсутствие замера не должно выглядеть нулём"


def test_measured_speed_comes_from_the_servers_own_counters(monkeypatch):
    class Reply:
        def read(self):
            return json.dumps({"eval_count": 16, "eval_duration": 800_000_000}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(report.urllib.request, "urlopen", lambda *a, **k: Reply())
    stage = report.stage_speed_memory(_loaded("qwen3:8b"))
    assert stage["status"] == PASS
    assert stage["facts"]["tokens_per_second"] == 20.0
    assert "не замер производительности" in stage["does_not_prove"]


# --- камера: устройство не открывается --------------------------------------

def test_the_camera_stage_only_reads_the_declared_readiness(monkeypatch):
    """Камера не открывается НИ РАЗУ: запрос уходит только на /readyz."""
    asked: list[str] = []

    def record(url, timeout=5.0):
        asked.append(url)
        return {"ready": True}

    monkeypatch.setitem(sys.modules, "ai_webcam_vision", object())
    monkeypatch.setattr(report, "_get_json", record)
    stage = report.stage_camera()
    assert stage["status"] == PASS
    assert asked and all(url.endswith("/readyz") for url in asked), asked
    assert "камера" in stage["does_not_prove"] and "НЕ открывается" in stage["does_not_prove"]


def test_a_running_service_does_not_prove_calibration(monkeypatch):
    monkeypatch.setitem(sys.modules, "ai_webcam_vision", object())
    monkeypatch.setattr(report, "_get_json", lambda url, timeout=5.0: {"ready": False})
    stage = report.stage_camera()
    assert stage["status"] == WARN


# --- отчёт целиком ----------------------------------------------------------

def test_no_combination_of_stages_can_claim_hardware_acceptance(monkeypatch):
    """Негативный контроль высшего уровня: даже когда ВСЁ зелёное."""
    monkeypatch.setattr(report, "stage_app", lambda: report._stage("app", PASS, "ok"))
    monkeypatch.setattr(report, "stage_hardware",
                        lambda: report._stage("hardware", PASS, "целевая", facts={"on_target": True}))
    monkeypatch.setattr(report, "stage_model_server",
                        lambda: report._stage("model_server", PASS, "ok", facts={"models": ["m"]}))
    monkeypatch.setattr(report, "stage_model_loaded", lambda s: _loaded("m"))
    monkeypatch.setattr(report, "stage_acceleration", lambda l: report._stage("acceleration", PASS, "ok"))
    monkeypatch.setattr(report, "stage_camera", lambda: report._stage("camera", PASS, "ok"))
    monkeypatch.setattr(report, "stage_speed_memory", lambda l: report._stage("speed_memory", PASS, "ok"))
    built = report.build_report()
    assert built["status"] == "ALL_STAGES_MEASURED"
    assert built["hardware_acceptance"] == "NOT_CLAIMED_BY_THIS_REPORT"
    assert "TARGET_HARDWARE_READY" not in json.dumps(built, ensure_ascii=False)


def test_an_unwritable_data_directory_blocks_and_says_so(monkeypatch, tmp_path):
    monkeypatch.setenv("BCC_DATA_DIR", str(tmp_path / "denied"))

    def deny(*args, **kwargs):
        raise OSError("read-only file system")

    monkeypatch.setattr(report.Path, "mkdir", deny)
    stage = report.stage_app()
    assert stage["status"] == BLOCKED


def test_the_exit_code_reports_that_a_report_was_produced_not_that_the_machine_is_fit(
        monkeypatch, capsys, tmp_path):
    """Семь «не измерено» — это законный отчёт, а не отказ инструмента."""
    monkeypatch.setattr(report, "stage_app", lambda: report._stage("app", PASS, "ok"))
    for name in ("stage_hardware", "stage_camera"):
        monkeypatch.setattr(report, name, lambda: report._stage(name, NOT_MEASURED, "нет данных"))
    monkeypatch.setattr(report, "stage_model_server",
                        lambda: report._stage("model_server", NOT_MEASURED, "молчит"))
    monkeypatch.setattr(report, "stage_model_loaded", lambda s: _not_loaded())
    monkeypatch.setattr(report, "stage_acceleration",
                        lambda l: report._stage("acceleration", NOT_MEASURED, "нечего"))
    monkeypatch.setattr(report, "stage_speed_memory",
                        lambda l: report._stage("speed_memory", NOT_MEASURED, "нечего"))
    out = tmp_path / "report.json"
    assert report.main(["--json", str(out)]) == 0
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["status"] == "OWNER_ACTION_REQUIRED"
    assert "BOSSMAN_OWNER_MACHINE=OWNER_ACTION_REQUIRED" in capsys.readouterr().out


def test_a_blocked_stage_makes_the_exit_code_non_zero(monkeypatch, tmp_path):
    monkeypatch.setattr(report, "stage_app",
                        lambda: report._stage("app", BLOCKED, "каталог недоступен"))
    for name, value in (("stage_hardware", None), ("stage_camera", None)):
        monkeypatch.setattr(report, name, lambda: report._stage(name, NOT_MEASURED, ""))
    monkeypatch.setattr(report, "stage_model_server",
                        lambda: report._stage("model_server", NOT_MEASURED, ""))
    monkeypatch.setattr(report, "stage_model_loaded", lambda s: _not_loaded())
    monkeypatch.setattr(report, "stage_acceleration",
                        lambda l: report._stage("acceleration", NOT_MEASURED, ""))
    monkeypatch.setattr(report, "stage_speed_memory",
                        lambda l: report._stage("speed_memory", NOT_MEASURED, ""))
    assert report.main(["--json", str(tmp_path / "r.json"), "--quiet"]) == 1


def test_every_stage_states_what_it_does_not_prove():
    """Стадия без этой строки — приглашение прочитать её шире, чем она есть."""
    built = report.build_report()
    assert len(built["stages"]) == 7
    for stage in built["stages"]:
        assert stage["does_not_prove"], stage["stage"]


@pytest.mark.parametrize("name", ["app", "hardware", "model_server", "model_loaded",
                                  "acceleration", "camera", "speed_memory"])
def test_the_seven_stages_are_never_collapsed_into_one_number(name):
    built = report.build_report()
    assert name in [s["stage"] for s in built["stages"]]


# --- достижимость в раскладке архива ---------------------------------------

def test_the_report_finds_its_neighbour_in_the_flat_archive_layout(tmp_path):
    """BL-036 и BL-050 — один и тот же класс: инструмент работает в клоне и не
    работает там, где его запускает адресат. Здесь это проверено буквально.

    В архиве все вспомогательные файлы лежат ПЛОСКО в `app-support`, launcher
    зовёт их по абсолютному пути, и текущий каталог у владельца — любой. Стадия
    железа опирается на соседний `target_hardware_acceptance.py`; если импорт
    там не сойдётся, она молча ответит «не измерено», и владелец не узнает про
    свою машину ничего.
    """
    import shutil
    import subprocess

    sys.path.insert(0, str(ROOT / "tools"))
    import build_windows_bundle as bundle  # noqa: E402

    support = tmp_path / "app-support"
    support.mkdir()
    for origin, name in bundle.SUPPORT_SCRIPTS:
        shutil.copyfile(origin, support / name)
    assert (support / "owner_machine_report.py").is_file()
    assert (support / "target_hardware_acceptance.py").is_file(), (
        "определитель железа не едет в архив — стадия железа станет слепой")

    # Изолированный режим и ЧУЖОЙ текущий каталог: -I убирает каталог скрипта
    # из пути поиска, а владелец запускает launcher откуда угодно.
    proc = subprocess.run([sys.executable, "-I", str(support / "owner_machine_report.py")],
                          cwd=str(tmp_path), capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "BOSSMAN_OWNER_MACHINE=" in proc.stdout
    assert "определитель железа недоступен" not in proc.stdout, (
        "в плоской раскладке архива отчёт не нашёл соседний определитель железа:\n"
        + proc.stdout[:1500])
    # Стадия железа обязана СКАЗАТЬ что-то о машине, а не промолчать.
    hardware = [line for line in proc.stdout.splitlines() if " hardware " in line]
    assert hardware and "NOT_MEASURED" not in hardware[0], hardware
