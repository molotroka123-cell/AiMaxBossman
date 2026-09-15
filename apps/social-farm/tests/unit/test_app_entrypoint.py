"""Приложение обязано подниматься, и объявленное обязано существовать.

`pyproject.toml` объявлял `social-farm = "social_farm.main:main"`, а модуля не
было. Через Bossman это выглядело так: кнопка «Запустить» порождает процесс,
который умирает с `ModuleNotFoundError` за доли секунды, и владелец видит
приложение, которое «не открывается», без единой причины.

Здесь проверяется, что объявление стало фактом — и что честность ответов не
принесена в жертву тому, чтобы «просто заработало».
"""
from __future__ import annotations

import importlib.util

import pytest

from social_farm.api import NOT_IMPLEMENTED, build_app, capability_catalogue

fastapi = pytest.importorskip("fastapi", reason="HTTP-поверхность ставится как [api]")
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture
def client():
    return TestClient(build_app())


def test_the_declared_console_entrypoint_exists():
    """То самое объявление, которое было намерением, а не фактом."""
    assert importlib.util.find_spec("social_farm.main") is not None


def test_the_manifest_http_entrypoint_exists():
    """`entrypoints.http: social_farm.api:build_app` из app.manifest.yaml."""
    module = importlib.import_module("social_farm.api")
    assert callable(getattr(module, "build_app", None))


def test_serve_is_a_real_command_and_help_is_not_a_crash():
    from social_farm.main import main
    with pytest.raises(SystemExit) as exited:
        main(["--help"])
    assert exited.value.code == 0
    assert main([]) == 2, "без команды — подсказка и ненулевой код"


def test_health_says_what_this_build_does_and_does_not_do(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert "capabilities" in body["implemented"]
    assert set(body["not_implemented"]) == {op for op, _, _ in NOT_IMPLEMENTED}


def test_the_capability_catalogue_is_real_data_not_a_placeholder(client):
    body = client.get("/api/capabilities").json()
    names = {row["capability"] for row in body["capabilities"]}
    assert "media.publish.image" in names
    assert "captcha.bypass" not in names, "запрет — не возможность"
    by_name = {row["capability"]: row for row in body["capabilities"]}
    assert by_name["account.password.change"]["default_decision"] == "DENY"
    assert by_name["media.publish.image"]["default_decision"] == "ASK"


def test_metrics_distinguishes_no_accounts_from_no_storage(client):
    """Ноль означал бы «ни одного аккаунта». Правда — «хранилища здесь нет»."""
    body = client.get("/api/metrics").json()
    assert body["accounts"] is None and body["pending_approvals"] is None
    assert body["measured"] is False
    assert body["reason"]


@pytest.mark.parametrize("operation,method,path", [
    (op, m, p) for op, m, p in NOT_IMPLEMENTED if "{" not in p])
def test_a_declared_operation_answers_501_not_404(client, operation, method, path):
    """404 сказал бы «такого адреса нет» — неправда: адрес объявлен контрактом.

    А пустой успех показал бы работающую операцию там, где её нет, и владелец
    узнал бы об этом в момент, когда она понадобилась.
    """
    response = client.request(method, path)
    assert response.status_code == 501, f"{operation}: {response.status_code}"
    body = response.json()
    assert body["code"] == "NOT_IMPLEMENTED"
    assert body["operation"] == operation


def test_no_declared_operation_answers_422(client):
    """Обработчик с параметрами FastAPI принимает за обязательные поля запроса
    и отвечает «вы неправильно позвали» вместо «операции здесь нет».

    Оба неверных варианта выглядели правильно в коде и оба давали 422.
    """
    for operation, method, path in NOT_IMPLEMENTED:
        if "{" in path:
            continue
        assert client.request(method, path).status_code != 422, operation


def test_the_app_declares_no_operation_it_cannot_answer(client):
    """Всё, что объявлено контрактом, либо работает, либо честно отказывает —
    третьего состояния (404) быть не должно."""
    declared = {p for _, _, p in NOT_IMPLEMENTED if "{" not in p}
    routed = {r.path for r in build_app().routes if hasattr(r, "path")}
    assert declared <= routed
