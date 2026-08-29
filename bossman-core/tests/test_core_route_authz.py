"""Аудит-правки: два открытых пути, которые обходили границы безопасности.

1. Консеквентные маршруты ядра (решение подтверждения, cloud_policy агента,
   approve проекта, гейт обучающего набора) были доступны без аутентификации:
   кто дотянулся до порта — тот и решал. Проверка «только 127.0.0.1» тут не
   годится: за `tailscale serve` запрос ВСЕГДА приходит с loopback.
2. `toolkit.shell` при SANDBOX_MODE=local исполнял команду агента прямо на
   хосте. Это второй путь исполнения мимо песочницы Stage 8.

Тесты бьют по поведению (запрос/отказ), а не по внутренностям реализации.
"""
from __future__ import annotations

import asyncio
import types
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from bossman import authz, errors
from bossman.config import settings


# ---------- 1. гейт ключа ядра ----------

def _client() -> TestClient:
    app = FastAPI()
    errors.install_error_handlers(app)

    @app.post("/consequential", dependencies=[Depends(authz.require_core_key)])
    async def consequential():
        return {"decided": True}

    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def core_key(monkeypatch):
    monkeypatch.setattr(settings, "core_api_key", "k" * 40, raising=False)
    return "k" * 40


def test_no_key_configured_closes_the_route(monkeypatch):
    """Fail closed: ключ не настроен — решать нельзя вообще никому."""
    monkeypatch.setattr(settings, "core_api_key", "", raising=False)
    resp = _client().post("/consequential", headers={"Authorization": "Bearer whatever"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == errors.ErrorCode.AUTH_DENIED.value


def test_anonymous_request_is_denied(core_key):
    resp = _client().post("/consequential")
    assert resp.status_code == 401


def test_wrong_key_is_denied(core_key):
    resp = _client().post("/consequential", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401


def test_loopback_does_not_substitute_for_the_key(core_key):
    """TestClient ходит с 127.0.0.1 — и всё равно получает отказ: гейт решает
    ключ, а не адрес. Иначе `tailscale serve` открывал бы маршрут всему миру."""
    resp = _client().post("/consequential")
    assert resp.status_code == 401


def test_correct_key_passes_in_both_header_forms(core_key):
    assert _client().post("/consequential",
                          headers={"Authorization": f"Bearer {core_key}"}).status_code == 200
    assert _client().post("/consequential",
                          headers={"X-Bossman-Key": core_key}).status_code == 200


def test_denial_message_leaks_nothing_about_the_key(core_key):
    body = _client().post("/consequential", headers={"Authorization": "Bearer wrong"}).text
    assert core_key not in body
    assert str(len(core_key)) not in body      # даже длина — подсказка для подбора


def test_consequential_core_routes_declare_the_gate():
    """Список закрытых маршрутов — часть контракта: если кто-то добавит роут
    решения подтверждения без зависимости, тест это увидит."""
    from bossman import api

    guarded = {
        ("POST", "/approvals/{approval_id}"),
        ("PATCH", "/agents/{name}"),
        ("POST", "/projects/{slug}/approve"),
    }
    seen = set()
    for route in api.app.routes:
        methods = getattr(route, "methods", set()) or set()
        for method in methods:
            if (method, getattr(route, "path", "")) not in guarded:
                continue
            names = [getattr(d.call, "__name__", "") for d in route.dependant.dependencies]
            assert "require_core_key" in names, f"{method} {route.path} без гейта"
            seen.add((method, route.path))
    assert seen == guarded, f"маршруты пропали или переименованы: {guarded - seen}"


def test_ai_lab_training_gate_is_closed():
    """Одобрение траектории в обучающий набор — такое же консеквентное решение."""
    from bossman.ai_lab import routes as lab

    paths = {}
    for route in lab.router.routes:
        names = [getattr(d.call, "__name__", "") for d in route.dependant.dependencies]
        paths[getattr(route, "path", "")] = names
    assert "require_core_key" in paths["/api/lab/candidates/{candidate_id}/decide"]
    assert "require_core_key" in paths["/api/lab/exports/{candidate_id}/launch_training"]


# ---------- 2. второй путь исполнения ----------

def _ctx(tmp_path: Path):
    return types.SimpleNamespace(workdir=tmp_path)


def test_local_mode_refuses_without_explicit_optin(monkeypatch, tmp_path):
    from bossman.toolkit import shell

    monkeypatch.setattr(settings, "sandbox_mode", "local", raising=False)
    monkeypatch.setattr(settings, "allow_unsafe_local_exec", False, raising=False)
    with pytest.raises(errors.PolicyDenied):
        shell._build_command("id", _ctx(tmp_path))


def test_unknown_mode_fails_closed_not_into_host_shell(monkeypatch, tmp_path):
    """Опечатка в SANDBOX_MODE не должна означать «выполняй на хосте»."""
    from bossman.toolkit import shell

    monkeypatch.setattr(settings, "sandbox_mode", "loocal", raising=False)
    monkeypatch.setattr(settings, "allow_unsafe_local_exec", True, raising=False)
    with pytest.raises(errors.PolicyDenied):
        shell._build_command("id", _ctx(tmp_path))


def test_docker_mode_stays_isolated(monkeypatch, tmp_path):
    from bossman.toolkit import shell

    monkeypatch.setattr(settings, "sandbox_mode", "docker", raising=False)
    monkeypatch.setattr(settings, "sandbox_image", "bossman-sandbox:latest", raising=False)
    built = shell._build_command("pytest -q", _ctx(tmp_path))
    assert "docker run --rm --network none" in built
    assert str(tmp_path.resolve()) in built


def test_optin_restores_local_mode_for_development(monkeypatch, tmp_path):
    from bossman.toolkit import shell

    monkeypatch.setattr(settings, "sandbox_mode", "local", raising=False)
    monkeypatch.setattr(settings, "allow_unsafe_local_exec", True, raising=False)
    assert "cd " in shell._build_command("id", _ctx(tmp_path))


def test_refusal_reaches_the_tool_not_just_the_builder(monkeypatch, tmp_path):
    """Отказ должен ронять сам вызов инструмента, а не остаться в helper'е."""
    from bossman.toolkit import shell

    monkeypatch.setattr(settings, "sandbox_mode", "local", raising=False)
    monkeypatch.setattr(settings, "allow_unsafe_local_exec", False, raising=False)
    with pytest.raises(errors.PolicyDenied):
        asyncio.run(shell.run({"cmd": "id"}, _ctx(tmp_path)))
