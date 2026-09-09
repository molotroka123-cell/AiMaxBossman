"""Работающий код обязан называть свой SHA — и молчать об этом нельзя.

Дефект, ради которого этот файл существует: брейкер-сессия владельца гонялась по
одному чекауту, пока считалось, что запущен другой. Улика, которую не к чему
привязать, хуже отсутствующей: она выглядит доказательством.

Поэтому проверяется не «поле есть», а граница:

    доказанный источник      → PASS + 40-hex SHA
    недоказанный источник    → SOURCE_IDENTITY_UNKNOWN, и НИКОГДА не PASS
    /api/identity и /health  → ОДНА и та же личность, не две разные правды
"""
from __future__ import annotations

import re

import httpx
import pytest

from bcc import build_identity

from .conftest import client_for, make_settings, start_app

HEX40 = re.compile(r"^[0-9a-f]{40}$")


def anonymous_client(app) -> httpx.AsyncClient:
    """Без токена вообще: публичные ручки обязаны отвечать и так."""
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                             base_url="http://test")


@pytest.fixture(autouse=True)
def _fresh_identity():
    """Кэш TTL не должен переносить личность между тестами."""
    build_identity.reset_cache()
    yield
    build_identity.reset_cache()


# ------------------------------------------------------------------ резолвер

def test_a_proven_source_is_named_by_its_sha(monkeypatch):
    monkeypatch.setattr(build_identity, "_resolve", build_identity._resolve)
    from bcc import run_provenance
    monkeypatch.setattr(run_provenance, "repository_sha", lambda: "a" * 40)
    found = build_identity.source_identity(fresh=True)
    assert found["source_identity"] == "PASS"
    assert HEX40.match(found["build_sha"])
    assert found["build_sha_short"] == "a" * 12


def test_an_unproven_source_is_named_not_guessed(monkeypatch):
    """Негативный контроль. NOT_CAPTURED обязан стать состоянием, а не пустым
    полем: пустое поле в интерфейсе читается как «наверное, всё в порядке»."""
    from bcc import run_provenance
    monkeypatch.setattr(run_provenance, "repository_sha",
                        lambda: run_provenance.NOT_CAPTURED)
    found = build_identity.source_identity(fresh=True)
    assert found["source_identity"] == build_identity.UNKNOWN
    assert found["source_identity"] != "PASS"
    assert found["build_sha"] is None
    assert found["detail"], "недоказанный источник обязан назвать причину"


def test_a_broken_resolver_does_not_become_a_pass(monkeypatch):
    """Исключение внутри резолвера — это тоже «не доказано», а не отсутствие
    проблемы. Раньше такой путь мог бы уронить health целиком."""
    from bcc import run_provenance

    def boom():
        raise RuntimeError("git недоступен")

    monkeypatch.setattr(run_provenance, "repository_sha", boom)
    found = build_identity.source_identity(fresh=True)
    assert found["source_identity"] == build_identity.UNKNOWN


def test_a_short_or_malformed_sha_is_refused(monkeypatch):
    from bcc import run_provenance
    for bogus in ("abc", "", "z" * 40, "A" * 39):
        monkeypatch.setattr(run_provenance, "repository_sha", lambda v=bogus: v)
        found = build_identity.source_identity(fresh=True)
        assert found["source_identity"] == build_identity.UNKNOWN, bogus


def test_the_cache_expires_so_a_moved_head_becomes_visible(monkeypatch):
    from bcc import run_provenance
    monkeypatch.setattr(run_provenance, "repository_sha", lambda: "a" * 40)
    assert build_identity.source_identity(fresh=True)["build_sha"] == "a" * 40
    monkeypatch.setattr(run_provenance, "repository_sha", lambda: "b" * 40)
    # в пределах TTL — прежний ответ, это осознанная цена дешёвого /health
    assert build_identity.source_identity()["build_sha"] == "a" * 40
    # ...но TTL короткий, и сдвинутый HEAD обязан стать видимым
    monkeypatch.setattr(build_identity, "_TTL_SECONDS", 0.0)
    assert build_identity.source_identity()["build_sha"] == "b" * 40


# ------------------------------------------------------------------ эндпоинты

async def test_identity_and_health_report_the_same_source(tmp_path):
    """Две ручки, одна правда. Разойдись они — владелец получил бы выбор,
    какому SHA верить, а это ровно то, чего мы избегаем."""
    app, svc = await start_app(make_settings(tmp_path), start_workers=False)
    try:
        async with client_for(app, svc) as client:
            identity = (await client.get("/api/identity")).json()
            api_health = (await client.get("/api/health")).json()
        assert identity["app"] == "bossman-command-center"
        for key in ("build_sha", "source_identity", "source"):
            assert key in identity, key
            assert identity[key] == api_health[key], key
        assert identity["source_identity"] in ("PASS", build_identity.UNKNOWN)
        if identity["source_identity"] == "PASS":
            assert HEX40.match(identity["build_sha"])
    finally:
        await svc.stop()


async def test_public_health_carries_the_identity_without_authentication(tmp_path):
    """Владелец должен уметь спросить «что запущено» до входа: если UI не
    открылся, именно этот ответ говорит, тот ли код вообще поднялся."""
    app, svc = await start_app(make_settings(tmp_path), start_workers=False)
    try:
        async with anonymous_client(app) as client:
            for path in ("/health", "/healthz", "/health/live"):
                body = (await client.get(path)).json()
                assert "source_identity" in body, path
                assert body["source_identity"] in ("PASS", build_identity.UNKNOWN), path
                assert "build_sha" in body, path
    finally:
        await svc.stop()


async def test_identity_never_leaks_a_secret(tmp_path):
    """Личность сборки — это SHA, а не содержимое хранилища. Ни токена, ни
    ключа, ни пути к данным владельца здесь быть не может."""
    app, svc = await start_app(make_settings(tmp_path), start_workers=False)
    try:
        async with anonymous_client(app) as client:
            raw = (await client.get("/health")).text
        token = (svc.settings.data_dir / "token")
        assert "token" not in raw.lower()
        if token.exists():
            assert token.read_text(encoding="utf-8").strip() not in raw
        assert str(svc.settings.data_dir) not in raw
    finally:
        await svc.stop()
