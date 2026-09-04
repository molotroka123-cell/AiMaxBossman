"""Прокси из окружения: местное — мимо прокси, удалённое — честно и без ImportError.

Разбираемый отказ: при ALL_PROXY=socks5h://… и отсутствующем socksio httpx
падает ПРИ СОЗДАНИИ клиента. Ловить такое никто не ожидал, поэтому наружу оно
выходило пятисоткой — на системе, где всё в порядке, кроме одной ненайденной
зависимости.

Сети здесь нет: прокси заведомо никуда не ведёт, а удалённые адреса до сокета
не доходят — либо отказ на этапе создания клиента, либо MockTransport.
"""
from __future__ import annotations

import importlib.util

import httpx
import pytest

from bcc.providers import ProviderError, ProxyUnsupported, http_client, is_local_url
from bcc.v2.opencode_bridge import OpenCodeBridge

SOCKS = "socks5h://127.0.0.1:9"          # порт 9 (discard) — соединения не будет
SOCKS_INSTALLED = importlib.util.find_spec("socksio") is not None


@pytest.fixture
def socks_env(monkeypatch):
    for var in ("ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy",
                "HTTPS_PROXY", "https_proxy", "NO_PROXY", "no_proxy"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ALL_PROXY", SOCKS)


# --------------------------------------------------------------- местное мимо прокси

@pytest.mark.parametrize("url", [
    "http://127.0.0.1:11434/v1", "http://localhost:4096", "http://192.168.1.7:8080",
    "http://host.docker.internal:1234/v1", "http://[::1]:9999",
])
def test_local_endpoints_ignore_the_environment_proxy(socks_env, url):
    """Прокси стоит в окружении, но локальный адрес идёт напрямую.

    Проверяем не «не упало», а именно отсутствие маршрутов на прокси: клиент
    без прокси-маршрутов и есть прямое соединение.
    """
    client = http_client(url, timeout=1)
    assert client.trust_env is False
    assert not getattr(client, "_mounts", {}), "локальный адрес получил маршрут на прокси"


def test_opencode_health_stays_direct_and_honest(socks_env):
    """OpenCode слушает на этой же машине: ни прокси, ни ImportError."""
    bridge = OpenCodeBridge()
    assert is_local_url(bridge.base_url)
    client = bridge._client(1)
    assert client.trust_env is False
    assert not getattr(client, "_mounts", {})


async def test_opencode_health_returns_unavailable_not_an_exception(socks_env):
    """Сервер не поднят — честное «unavailable», а не исключение наружу."""
    health = await OpenCodeBridge(base_url="http://127.0.0.1:1").health(timeout=0.5)
    assert health["status"] == "unavailable"
    assert health["detail"] and health["hint"]


# --------------------------------------------------------------- удалённое

def test_remote_keeps_the_owner_proxy_or_refuses_readably(socks_env):
    """Удалённый адрес прокси не теряет; неподдержанный socks — внятный отказ.

    Оба исхода законны и зависят от того, установлен ли socksio. Незаконен
    ровно один: ImportError наружу.
    """
    try:
        client = http_client("https://api.anthropic.com", timeout=1)
        assert client.trust_env is True
        assert getattr(client, "_mounts", {}), "прокси владельца потерян для удалённого адреса"
        assert SOCKS_INSTALLED, "клиент собрался без socksio — значит socks не был задействован"
    except ProxyUnsupported as exc:
        assert not SOCKS_INSTALLED, "socksio установлен, а клиент всё равно отказал"
        assert isinstance(exc, ProviderError) and exc.kind == "network"
        assert "socks" in str(exc).lower() and "httpx[socks]" in (exc.hint or "")


def test_the_declared_dependency_matches_what_the_code_needs():
    """`httpx[socks]` объявлен в зависимостях — иначе починка держится на удаче."""
    from pathlib import Path
    text = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    assert "httpx[socks]" in text


async def test_no_import_error_leaks_through_a_model_endpoint(socks_env, env):
    """Ручка проверки модели отвечает статусом, а не пятисоткой.

    Здесь важен именно код ответа: ImportError из конструктора клиента ронял
    запрос целиком, и владелец видел «Ошибка сервера» вместо «оффлайн».
    """
    import sqlalchemy as sa
    from bcc.db import models as models_t, providers as providers_t

    async with env.svc.db.session() as s:
        pid = (await s.execute(sa.insert(providers_t).values(
            name="удалённый", kind="openai_compat",
            base_url="https://example.invalid/v1"))).inserted_primary_key[0]
        mid = (await s.execute(sa.insert(models_t).values(
            provider_id=pid, name="m", alias="удалённая-модель", kind="cloud",
            context_window=8192, caps={}, price_in=0.0, price_out=0.0,
            bench={}))).inserted_primary_key[0]
        await s.commit()

    res = await env.client.post(f"/api/models/{int(mid)}/check")
    assert res.status_code < 500, f"утёк ImportError: {res.status_code} {res.text[:300]}"
    body = res.json()
    assert body["status"] in ("offline", "error"), body
    assert body["detail"], "отказ обязан называть причину"
