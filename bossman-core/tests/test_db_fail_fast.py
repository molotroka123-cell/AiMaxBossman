"""Регресс на аудитный пункт: Core без Postgres должен отказывать понятно.

Раньше `asyncpg.create_pool` бросал сырой трейс в stderr на каждый запрос, и по
нему нельзя было понять, что именно поднимать. Теперь — DEPENDENCY_UNAVAILABLE
(503) с подсказкой и БЕЗ пароля из DSN.
"""
from __future__ import annotations

import pytest

from bossman import db, errors


@pytest.mark.asyncio
async def test_unavailable_postgres_gives_actionable_error(monkeypatch):
    monkeypatch.setattr(db, "_pool", None, raising=False)
    monkeypatch.setattr(db.settings, "database_url",
                        "postgresql://bossman:SUPER-SECRET-PW@127.0.0.1:59999/bossman")
    with pytest.raises(errors.DependencyUnavailable) as ei:
        await db.pool()
    err = ei.value
    assert err.http == 503
    assert err.code is errors.ErrorCode.DEPENDENCY_UNAVAILABLE
    # подсказка есть...
    assert "BOSSMAN_DATABASE_URL" in err.detail or "docker compose" in err.detail
    # ...а пароля из DSN нет ни в сообщении, ни в extra
    assert "SUPER-SECRET-PW" not in err.detail
    assert "SUPER-SECRET-PW" not in str(err.extra)
    monkeypatch.setattr(db, "_pool", None, raising=False)


def test_dsn_hint_strips_credentials(monkeypatch):
    monkeypatch.setattr(db.settings, "database_url",
                        "postgresql://user:pw123@db.internal:5432/bossman")
    hint = db._dsn_hint()
    assert "pw123" not in hint and "user" not in hint
    assert "db.internal:5432/bossman" == hint


def test_schema_is_read_as_utf8():
    """Схема читается явно в utf-8: на Windows дефолт cp1252 ломает кириллицу
    в комментариях (тот же класс бага, что и в fs-инструментах)."""
    import inspect
    src = inspect.getsource(db.pool)
    assert 'encoding="utf-8"' in src
