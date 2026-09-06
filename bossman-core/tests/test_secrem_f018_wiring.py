"""SECREM F-018 (core) — «мёртвый» защитный код либо подключён, либо честно помечен."""
from __future__ import annotations

import asyncio
import os

import pytest

from bossman.toolkit import REGISTRY
from bossman import db as db_mod


def test_fileintel_and_analysis_tools_are_registered():
    for name in ("analysis.run", "file.parse", "artifact.create"):
        assert name in REGISTRY, f"{name} не зарегистрирован — V2.6 интеграция"


def test_registered_fileintel_tools_are_confined_to_the_workdir(tmp_path):
    """S1: регистрации мало — подключённые в реестр file.parse/artifact.create
    обязаны держать containment рабочей папки. Вызываем именно ЗАРЕГИСТРИРОВАННЫЕ
    handler'ы (а не модульные функции), потому что агент дотягивается до них
    через REGISTRY, а artifact.create идёт с confirm_default=False."""
    from pathlib import Path
    from bossman.toolkit import ToolContext

    work = tmp_path / "coder"
    work.mkdir()
    sibling = tmp_path / "coder-secrets"      # общий префикс имени с workdir
    sibling.mkdir()
    (sibling / "secret.txt").write_text("TOP SECRET", encoding="utf-8")
    ctx = ToolContext(agent="coder", workdir=work)

    with pytest.raises(PermissionError):
        asyncio.run(REGISTRY["artifact.create"].handler(
            {"path": "../coder-secrets/pwned.md", "format": "md", "content": "OWNED"}, ctx))
    assert not (sibling / "pwned.md").exists()

    with pytest.raises(PermissionError):
        asyncio.run(REGISTRY["file.parse"].handler(
            {"path": "../coder-secrets/secret.txt"}, ctx))

    # Внутри рабочей папки инструмент по-прежнему работает.
    ok = asyncio.run(REGISTRY["artifact.create"].handler(
        {"path": "report.md", "format": "md", "content": "ok"}, ctx))
    assert not ok.error and Path(work / "report.md").exists()


def test_capabilities_and_secret_broker_marked_non_protective():
    import bossman.capabilities as caps
    import bossman.sandbox.secrets as sec
    assert "GATED_NON_PROTECTIVE" in (caps.__doc__ or "")
    assert "GATED_NON_PROTECTIVE" in (sec.__doc__ or "")


def test_gateway_logging_uses_redaction_filter():
    import logging
    from bossman.gateway.main import configure_gateway_logging
    from bossman.obs import RedactionFilter
    configure_gateway_logging()
    lg = logging.getLogger("bossman.gateway")
    has = any(isinstance(f, RedactionFilter) for f in lg.filters) or any(
        isinstance(f, RedactionFilter) for h in logging.getLogger().handlers for f in h.filters)
    assert has, "у процесса Gateway нет RedactionFilter"


# ------------------------------------------------------------ BUG-004: пул asyncpg и loop

def _pg_available() -> bool:
    return bool(os.environ.get("BOSSMAN_TEST_PG_DSN") or os.environ.get("BOSSMAN_DATABASE_URL"))


@pytest.mark.skipif(not _pg_available(), reason="NOT_TESTED_ON_THIS_HOST: нет Postgres DSN")
def test_bug004_pool_is_rebound_per_event_loop():
    """REPRO BUG-004: пул, созданный на loop A, использовался на loop B →
    «Future attached to a different loop». Теперь pool() видит чужой loop,
    terminate()'ит старый и создаёт новый на текущем."""
    async def use():
        p = await db_mod.pool()
        await db_mod.fetch("SELECT 1 AS x")
        return p, db_mod.bound_loop()

    p1, l1 = asyncio.run(use())
    p2, l2 = asyncio.run(use())          # НОВЫЙ loop
    assert l1 is not l2 and p1 is not p2
    assert l2 is not None
    asyncio.run(db_mod.close())          # close на чужом loop — не падает
