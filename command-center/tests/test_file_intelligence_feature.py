"""§30/§31 — фича в составе Command Center: выключена, дёшева и безопасна.

Проверяется не «работает ли File Intelligence», а то, что её присутствие ничего
не стоит и её отсутствие ничего не ломает. Способность двигать файлы обязана
быть выключаемой до нуля, иначе «по умолчанию выключено» — просто надпись.
"""
from __future__ import annotations

import pytest

from bcc import file_intelligence as fi
from bcc.features import load_features

from .conftest import client_for, make_settings, start_app


@pytest.fixture
def off(monkeypatch):
    monkeypatch.delenv(fi.FLAG_ENV, raising=False)
    return None


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv(fi.FLAG_ENV, "1")
    return None


# ------------------------------------------------------------------ §31 флаг

def test_the_flag_is_off_unless_the_owner_turns_it_on(off, monkeypatch):
    assert fi.enabled() is False
    for value in ("0", "false", "no", "", "off", "maybe"):
        monkeypatch.setenv(fi.FLAG_ENV, value)
        assert fi.enabled() is False, value
    for value in ("1", "true", "yes", "on", "TRUE"):
        monkeypatch.setenv(fi.FLAG_ENV, value)
        assert fi.enabled() is True, value


def test_the_feature_is_registered_but_declares_no_background_loop():
    """§30 — неиспользуемая фича не опрашивает ничего.

    tick_seconds=0 означает, что петля не создаётся вовсе. Петля «раз в минуту
    проверить, не появился ли сайдкар» стоила бы столько же при выключенной
    фиче, сколько при включённой.
    """
    feature = next(f for f in load_features() if f.name == "file_intelligence")
    assert feature.tick_seconds == 0.0
    assert feature.tick is None


async def test_command_center_starts_normally_with_the_feature_off(off, tmp_path):
    """Отсутствие сайдкара — не повод не запуститься."""
    settings = make_settings(tmp_path)
    app, svc = await start_app(settings, start_workers=False)
    try:
        async with client_for(app, svc) as client:
            assert (await client.get("/api/system")).status_code == 200
            # ничего не создано: ни каталога состояния, ни процесса
            assert not (settings.data_dir / "file-intelligence").exists()
            assert getattr(svc, "_file_intelligence", None) is None
    finally:
        await svc.stop()


async def test_command_center_starts_when_the_sidecar_is_absent(on, tmp_path,
                                                                monkeypatch):
    """§31 — фича включена, бинаря нет. Bossman всё равно полностью работает."""
    from bcc.file_intelligence import discovery
    monkeypatch.setenv(discovery.EXECUTABLE_ENV, str(tmp_path / "nowhere"))
    monkeypatch.setattr(discovery.shutil, "which", lambda _n: None)
    settings = make_settings(tmp_path)
    app, svc = await start_app(settings, start_workers=False)
    try:
        async with client_for(app, svc) as client:
            assert (await client.get("/api/system")).status_code == 200
            status = (await client.get("/api/file-intelligence/status")).json()
            assert status["binary"]["status"] == "NOT_INSTALLED"
            assert status["enabled"] is True
    finally:
        await svc.stop()


async def test_setup_failure_cannot_take_down_startup(on, tmp_path, monkeypatch):
    """§31 — падение File Intelligence не имеет права уронить Command Center."""
    from bcc.features import file_intelligence as feature_module

    def explode(_svc):
        raise RuntimeError("file intelligence is broken")

    monkeypatch.setattr(feature_module, "_service", explode)
    settings = make_settings(tmp_path)
    app, svc = await start_app(settings, start_workers=False)
    try:
        async with client_for(app, svc) as client:
            assert (await client.get("/api/system")).status_code == 200
    finally:
        await svc.stop()


# ------------------------------------------------------------------ эндпоинты

async def test_status_is_readable_even_while_the_feature_is_off(off, tmp_path):
    """Доктор работает при выключенной фиче: владелец должен видеть, ЧТО именно
    не готово, прежде чем включать."""
    settings = make_settings(tmp_path)
    app, svc = await start_app(settings, start_workers=False)
    try:
        async with client_for(app, svc) as client:
            body = (await client.get("/api/file-intelligence/status")).json()
            assert body["enabled"] is False
            assert body["flag"] == "file_intelligence_v1"
            assert body["undo_headless"] == "UNAVAILABLE"
            assert body["pinned_upstream_sha"] == \
                "4dc374df69b5e63d5354e121097d92e25bbd32da"
            assert len(body["task_classes"]) == 8
    finally:
        await svc.stop()


async def test_analyze_is_refused_while_the_feature_is_off(off, tmp_path):
    """Выключенный флаг — это отказ, а не «работает, но тихо»."""
    settings = make_settings(tmp_path)
    app, svc = await start_app(settings, start_workers=False)
    try:
        async with client_for(app, svc) as client:
            body = (await client.post("/api/file-intelligence/analyze", json={
                "task_class": "file.categorize",
                "targets": [str(tmp_path)]})).json()
            assert body["ok"] is False
            assert body["refused"] == "FEATURE_DISABLED"
    finally:
        await svc.stop()


async def test_no_authorized_roots_means_analyze_denies(on, tmp_path, monkeypatch):
    """Включённая фича без разрешённых корней не может никуда — и говорит об этом."""
    from bcc.file_intelligence import discovery
    monkeypatch.setenv(discovery.EXECUTABLE_ENV, str(tmp_path / "nowhere"))
    monkeypatch.setattr(discovery.shutil, "which", lambda _n: None)
    settings = make_settings(tmp_path)
    app, svc = await start_app(settings, start_workers=False)
    try:
        async with client_for(app, svc) as client:
            body = (await client.post("/api/file-intelligence/analyze", json={
                "task_class": "file.categorize",
                "targets": [str(tmp_path / "Downloads")]})).json()
            assert body["ok"] is False
            # Отказ области — это ЗАПИСАННАЯ работа в состоянии DENIED с
            # названной причиной, а не голая ошибка: владелец должен видеть в
            # истории, что запрос был и почему он не прошёл.
            job = body["job"]
            assert job["state"] == "DENIED"
            assert job["refusal"] in ("BINARY_NOT_INSTALLED",
                                      "PATH_OUTSIDE_AUTHORIZED_ROOTS"), job
    finally:
        await svc.stop()


async def test_an_unknown_task_class_is_refused_by_the_endpoint(on, tmp_path):
    settings = make_settings(tmp_path)
    app, svc = await start_app(settings, start_workers=False)
    try:
        async with client_for(app, svc) as client:
            body = (await client.post("/api/file-intelligence/analyze", json={
                "task_class": "shell.run", "targets": ["/tmp"]})).json()
            assert body["ok"] is False
            assert body["refused"] == "UNSUPPORTED_ARGUMENT"
    finally:
        await svc.stop()


def test_the_tool_surface_exposes_no_raw_argument_channel():
    """§21 — модель называет класс задачи и цели, а не флаги.

    Поле, принимающее произвольные аргументы CLI, и есть shell: проверяется, что
    во входных схемах такого поля нет.
    """
    from bcc.features.file_intelligence import AnalyzeRequest, ApplyRequest
    for model in (AnalyzeRequest, ApplyRequest):
        fields = set(model.model_fields)
        assert not (fields & {"args", "argv", "flags", "command", "extra",
                              "options", "raw", "cli"}), (model, fields)


# ------------------------------------------------- утечка маршрутов (регресс)

async def test_the_module_router_does_not_grow_across_app_starts(off, tmp_path):
    """Регресс на утечку, которая утроила время набора Command Center.

    Предыдущая версия довешивала маршруты в `setup()` замыканием на svc, и каждый
    старт приложения добавлял к ОБЩЕМУ модульному роутеру ещё шесть. В
    продакшене старт один и утечка невидима; в тестовом процессе стартов сотни,
    `include_router` копировал всё накопленное, запросы перебирали всё
    скопированное, и набор перестал укладываться в 30-минутный предел CI.

    Проверяется НАБЛЮДЕНИЕМ: число маршрутов до серии стартов равно числу после.
    """
    from bcc.features import file_intelligence as feature_module

    before = len(feature_module.router.routes)
    assert before == feature_module.ROUTE_COUNT
    for index in range(4):
        settings = make_settings(tmp_path / f"app{index}")
        app, svc = await start_app(settings, start_workers=False)
        try:
            assert len(feature_module.router.routes) == before, (
                f"после старта #{index + 1} маршрутов стало "
                f"{len(feature_module.router.routes)}, было {before}")
        finally:
            await svc.stop()
    assert len(feature_module.router.routes) == before


def test_every_endpoint_is_reachable_in_a_fresh_process():
    """Маршруты объявлены на импорте, а не в setup(): первое приложение ПЕРВОГО
    процесса обязано видеть все семь. Проверяется в дочернем интерпретаторе,
    потому что в родительском роутер уже мог быть затронут другими тестами —
    и тест прошёл бы по неправильной причине.
    """
    import json
    import subprocess
    import sys

    script = r'''
import asyncio, json, sys, tempfile
sys.path.insert(0, "tests")
from pathlib import Path
from tests.conftest import client_for, make_settings, start_app

async def main():
    with tempfile.TemporaryDirectory() as td:
        app, svc = await start_app(make_settings(Path(td)), start_workers=False)
        try:
            async with client_for(app, svc) as c:
                codes = {}
                codes["status"] = (await c.get("/api/file-intelligence/status")).status_code
                codes["jobs"] = (await c.get("/api/file-intelligence/jobs")).status_code
                codes["job"] = (await c.get("/api/file-intelligence/jobs/x")).status_code
                codes["analyze"] = (await c.post("/api/file-intelligence/analyze",
                    json={"task_class": "file.categorize", "targets": []})).status_code
                codes["apply"] = (await c.post("/api/file-intelligence/apply",
                    json={"job_id": "x", "selected": []})).status_code
                codes["cancel"] = (await c.post("/api/file-intelligence/jobs/x/cancel")).status_code
                codes["reconcile"] = (await c.post("/api/file-intelligence/jobs/x/reconcile")).status_code
                print(json.dumps(codes))
        finally:
            await svc.stop()
asyncio.run(main())
'''
    result = subprocess.run([sys.executable, "-c", script], capture_output=True,
                            text=True, timeout=120,
                            cwd=str(__import__("pathlib").Path(__file__).resolve().parents[1]))
    assert result.returncode == 0, result.stderr[-1500:]
    codes = json.loads(result.stdout.strip().splitlines()[-1])
    assert len(codes) == 7
    # 404 означало бы «маршрута нет»; всё остальное — маршрут существует и
    # ответил (в т.ч. названным отказом FEATURE_DISABLED при выключенном флаге).
    assert all(code != 404 for code in codes.values()), codes
