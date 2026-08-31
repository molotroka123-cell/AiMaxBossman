"""Deterministic coverage of the asyncpg-contract repairs in decision/failure memory.

These modules were fake-green: они звали SQLite-API (executescript/commit,
execute()+async-for, result.status) на asyncpg-пуле. Здесь — детерминированные
тесты починенного контракта через подмену модульных db-хелперов (без живого PG).
Полная проверка против реального Postgres — SKIP_HOST (нет сервера в этой среде).
"""
from __future__ import annotations

from pathlib import Path

import pytest

import bossman.decision_memory as dm
import bossman.failure_memory as fm

pytestmark = pytest.mark.asyncio


def _fail_row(fid="fail-1", resolved=False):
    return {
        "failure_id": fid, "task_id": "t1", "symptom": "s", "error_class": "E",
        "root_cause": "rc", "attempted_fix": "fix", "result": "r",
        "files": "[]", "tests": "[]", "environment": "{}",
        "resolved": resolved, "created_at": "2026-08-31T00:00:00Z", "resolved_at": None,
    }


# ---------------- failure_memory ----------------

async def test_init_failures_verifies_canonical_table_no_embedded_ddl(monkeypatch):
    """init больше НЕ создаёт вторую схему: он проверяет каноничную таблицу."""
    seen = {}
    async def fake_fetchval(sql, *a):
        seen["sql"] = sql
        return True
    monkeypatch.setattr(fm, "fetchval", fake_fetchval)
    await fm.init_failures_table()
    assert "to_regclass" in seen["sql"] and "failures" in seen["sql"]
    assert "CREATE TABLE" not in Path(fm.__file__).read_text(encoding="utf-8")


async def test_init_failures_raises_when_canonical_table_missing(monkeypatch):
    async def fake_fetchval(sql, *a):
        return False
    monkeypatch.setattr(fm, "fetchval", fake_fetchval)
    with pytest.raises(Exception):
        await fm.init_failures_table()


async def test_get_unresolved_failures_fetches_list(monkeypatch):
    async def fake_fetch(sql, *a):
        assert "resolved = FALSE" in sql
        return [_fail_row("a"), _fail_row("b")]
    monkeypatch.setattr(fm, "fetch", fake_fetch)
    recs = await fm.get_unresolved_failures("t1")
    assert [r.failure_id for r in recs] == ["a", "b"]


async def test_resolve_failure_status_string(monkeypatch):
    async def fake_execute_ok(sql, *a):
        return "UPDATE 1"
    monkeypatch.setattr(fm, "execute", fake_execute_ok)
    assert await fm.resolve_failure("fail-1") is True

    async def fake_execute_none(sql, *a):
        return "UPDATE 0"
    monkeypatch.setattr(fm, "execute", fake_execute_none)
    assert await fm.resolve_failure("missing") is False


async def test_query_failures_single_fetch(monkeypatch):
    calls = {"n": 0}
    async def fake_fetch(sql, *a):
        calls["n"] += 1
        assert "LIMIT" in sql
        return [_fail_row("x", resolved=True)]
    monkeypatch.setattr(fm, "fetch", fake_fetch)
    recs = await fm.query_failures(task_id="t1", resolved=True, error_class="E", limit=10)
    assert calls["n"] == 1 and recs[0].failure_id == "x" and recs[0].resolved is True


# ---------------- decision_memory ----------------

async def test_init_decisions_verifies_canonical_table_no_embedded_ddl(monkeypatch):
    seen = {}
    async def fake_fetchval(sql, *a):
        seen["sql"] = sql
        return True
    monkeypatch.setattr(dm, "fetchval", fake_fetchval)
    await dm.init_decisions_table()
    assert "to_regclass" in seen["sql"] and "decisions" in seen["sql"]
    assert "CREATE TABLE" not in Path(dm.__file__).read_text(encoding="utf-8")


async def test_no_pool_as_context_manager_anywhere(monkeypatch):
    """`async with (await pool())` ЗАКРЫВАЕТ глобальный пул asyncpg — запрещено."""
    for mod in (dm, fm):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        assert "async with (await pool()) as" not in src, f"{mod.__name__}: pool-closing pattern"


async def test_supersede_decision_no_commit_path(monkeypatch):
    """supersede: fail-before-write if old missing; else create new + UPDATE via execute()."""
    class _Old:
        id = 7
        scope = "route"; subject = "db"; decision = "postgres"; reason = "durable"
        alternatives_rejected: list = []; evidence: list = []
        source_kind = "agent"; source_run_id = None; source_note = None; confidence = 1.0
    class _New:
        id = 8

    async def fake_get(did):
        return _Old() if did == "d-old" else None
    async def fake_create(**kw):
        return _New()
    updates = []
    async def fake_execute(sql, *a):
        updates.append((sql, a))
        return "UPDATE 1"
    monkeypatch.setattr(dm, "get_decision", fake_get)
    monkeypatch.setattr(dm, "create_decision", fake_create)
    monkeypatch.setattr(dm, "execute", fake_execute)

    res = await dm.supersede_decision("d-old", "d-new")
    assert res["supersedes"] == 8
    assert any("UPDATE decisions SET supersedes" in sql for sql, _ in updates)

    # missing old → NotFound, no write
    updates.clear()
    with pytest.raises(Exception):
        await dm.supersede_decision("nope", "d-new")
    assert updates == []
