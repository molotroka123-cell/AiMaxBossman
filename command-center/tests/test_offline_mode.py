"""Офлайн-режим: доказываем, что отчёт честный и что при выключенном флаге сети нет.

Главный приём тестов — заведомо мёртвый прокси во ВСЕХ переменных окружения.
Клиент, который читает окружение, на таком прокси гарантированно спотыкается;
значит, зелёная петлевая проба доказывает, что запрос ушёл мимо прокси, а не
«просто повезло». Один и тот же приём проверяет и то, что наружу не ушло ничего.
"""
from __future__ import annotations

import asyncio
import time

import httpx
import pytest
import sqlalchemy as sa

from bcc.db import providers as providers_t
from bcc.features import offline_mode


def kill_network_env(monkeypatch) -> str:
    """Ставит мёртвый прокси во все переменные и чистит NO_PROXY. Возвращает его адрес."""
    dead = f"socks5://127.0.0.1:{offline_mode.free_port()}"
    for name in ("ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy",
                 "HTTPS_PROXY", "https_proxy"):
        monkeypatch.setenv(name, dead)
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    return dead


def spy_on_clients(monkeypatch) -> list[bool]:
    """Записывает trust_env каждого созданного httpx-клиента: наружу ходят только с True."""
    seen: list[bool] = []
    original = httpx.AsyncClient.__init__

    def recording(self, *args, **kwargs):
        seen.append(bool(kwargs.get("trust_env", True)))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", recording)
    return seen


async def serve_once():
    """Живая служба на 127.0.0.1: отвечает 204 на любой запрос."""
    async def handle(reader, writer):
        await reader.read(4096)
        writer.write(b"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, int(server.sockets[0].getsockname()[1])


async def test_report_without_flag_does_not_touch_network(env, monkeypatch):
    """Флаг выключен: отчёт приходит, петля жива, ни одного внешнего запроса не сделано."""
    monkeypatch.delenv(offline_mode.FLAG, raising=False)
    kill_network_env(monkeypatch)
    clients = spy_on_clients(monkeypatch)

    started = time.monotonic()
    resp = await env.client.get("/api/offline")
    elapsed = time.monotonic() - started

    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is False
    # петля опознана живой ВОПРЕКИ мёртвому прокси в окружении
    assert body["network"]["loopback"]["alive"] is True
    assert body["network"]["external"]["checked"] is False
    assert offline_mode.FLAG in body["network"]["external"]["reason"]
    assert body["network"]["external"]["results"] == []
    # прокси в окружении виден в отчёте по именам переменных, но без значений
    assert "ALL_PROXY" in body["network"]["proxy_env"]
    assert all("127.0.0.1" not in name for name in body["network"]["proxy_env"])
    # ни один клиент не читал окружение — то есть наружу не ушло ничего
    assert clients and not any(clients)
    assert elapsed < 5.0, "отчёт не должен висеть на таймауте"


async def test_closed_port_is_reported_dead(env, monkeypatch):
    """Проверка не отвечает «всё хорошо» всегда: закрытый порт опознан мёртвым."""
    kill_network_env(monkeypatch)
    dead_url = f"http://127.0.0.1:{offline_mode.free_port()}/"

    probe = await offline_mode.http_probe(dead_url, timeout=1.5, trust_env=False)
    assert probe["alive"] is False

    report = await offline_mode.probe_loopback()
    assert report["alive"] is True
    assert report["sanity"]["refused"] is True, "закрытый порт обязан считаться мёртвым"


async def test_matrix_keeps_all_three_states(env, monkeypatch):
    """В матрице есть все три состояния, и «неизвестно» не подменено «работает»."""
    monkeypatch.delenv(offline_mode.FLAG, raising=False)
    body = (await env.client.get("/api/offline")).json()
    rows = {row["capability"]: row for row in body["capabilities"]}

    assert body["summary"][offline_mode.OFFLINE_OK] > 0
    assert body["summary"][offline_mode.NEEDS_NETWORK] > 0
    assert body["summary"][offline_mode.UNKNOWN] > 0
    # подсистема, про которую честно неизвестно, осталась неизвестной
    assert rows["tools_mcp"]["status"] == offline_mode.UNKNOWN
    assert rows["plugins"]["status"] == offline_mode.UNKNOWN
    # ни одна неопределённая подсистема не выдана за работающую
    unsure = [r for r in rows.values() if r["requirement"] == offline_mode.UNSURE]
    assert unsure and all(r["status"] == offline_mode.UNKNOWN for r in unsure)
    # матрица собрана из реально смонтированных фич
    assert {f.name for f in env.svc.features} <= set(rows)
    assert all(row["reason"] for row in rows.values())


async def test_can_answers_before_action_is_started(env, monkeypatch):
    """Ручка can отвечает да/нет/неизвестно с причиной, ничего не запуская."""
    monkeypatch.delenv(offline_mode.FLAG, raising=False)
    kill_network_env(monkeypatch)

    local = (await env.client.get("/api/offline/can/agentmap")).json()
    assert local["answer"] == "yes" and local["will_work"] is True

    # внешняя подсистема при выключенном флаге: именно «неизвестно», а не «да»
    external = (await env.client.get("/api/offline/can/openrouter")).json()
    assert external["status"] == offline_mode.NEEDS_NETWORK
    assert external["answer"] == "unknown"
    assert external["will_work"] is None
    assert external["checked"]["external"] is False
    assert offline_mode.FLAG in external["reason"]


async def test_can_says_no_for_local_service_that_is_not_running(env, monkeypatch):
    """Явная деградация: «эта кнопка не сработает» — до нажатия и с причиной."""
    kill_network_env(monkeypatch)
    monkeypatch.setenv("OPENCODE_URL", f"http://127.0.0.1:{offline_mode.free_port()}")

    answer = (await env.client.get("/api/offline/can/tools_opencode")).json()
    assert answer["answer"] == "no" and answer["will_work"] is False
    assert "не слушает" in answer["reason"]

    server, port = await serve_once()
    try:
        monkeypatch.setenv("OPENCODE_URL", f"http://127.0.0.1:{port}")
        answer = (await env.client.get("/api/offline/can/tools_opencode")).json()
    finally:
        server.close()
        await server.wait_closed()
    assert answer["answer"] == "yes" and answer["will_work"] is True


async def test_unknown_capability_is_404_not_yes(env):
    """Вопрос про несуществующую возможность — 404, а не бодрое «сработает»."""
    resp = await env.client.get("/api/offline/can/no_such_button")
    assert resp.status_code == 404
    body = resp.json()["error"]
    assert "no_such_button" in body["message"]
    assert "agentmap" in body["known"], "в ответе — реальный список возможностей"


async def test_flag_off_keeps_behaviour_unchanged_for_external_probe(env, monkeypatch):
    """Выключенный флаг не даёт модулю сходить наружу даже за одной возможностью."""
    monkeypatch.delenv(offline_mode.FLAG, raising=False)
    kill_network_env(monkeypatch)
    clients = spy_on_clients(monkeypatch)

    for capability in ("openrouter", "browser", "core.providers", "tools_mcp"):
        body = (await env.client.get(f"/api/offline/can/{capability}")).json()
        assert body["checked"]["external"] is False
        assert body["will_work"] is not True or body["status"] == offline_mode.OFFLINE_OK
    assert not any(clients), "при выключенном флаге ни один запрос не читает окружение"


async def test_flag_on_checks_only_configured_address(env, monkeypatch):
    """С включённым флагом проверка идёт — и только по адресу, настроенному в приложении.

    Хост в зоне .invalid (RFC2606) не существует по определению, а в окружении стоит
    мёртвый прокси, который httpx для внешнего адреса обязан использовать: имя даже
    не разрешается, пакет наружу не уходит — но ветка с включённым флагом отработана.
    """
    kill_network_env(monkeypatch)
    monkeypatch.setenv(offline_mode.FLAG, "1")
    async with env.svc.db.session() as s:
        await s.execute(sa.insert(providers_t).values(
            name="doc-net", kind="openai_compat", base_url="http://models.invalid/v1"))
        await s.commit()

    body = (await env.client.get("/api/offline/can/core.providers")).json()
    assert body["enabled"] is True
    assert body["checked"]["external"] is True
    assert body["answer"] == "no" and body["will_work"] is False
    assert "models.invalid" in body["reason"]

    matrix = (await env.client.get("/api/offline/can/agentmap")).json()
    assert matrix["checked"]["external"] is False, "локальной подсистеме проверка не нужна"


@pytest.mark.parametrize("value", ["", "0", "no"])
def test_flag_is_off_by_default(monkeypatch, value):
    """Флаг выключен по умолчанию и включается только явным значением."""
    monkeypatch.setenv(offline_mode.FLAG, value)
    assert offline_mode.enabled() is False
    monkeypatch.setenv(offline_mode.FLAG, "1")
    assert offline_mode.enabled() is True
