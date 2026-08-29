"""Регрессы на два аудитных бага:
- P0: apply_compaction при пустой сводке стирал историю → амнезия агента.
- P1: run_project не защищал проект от двойного параллельного запуска
  (двойная оплата облака + гонка за state.json).
"""
from __future__ import annotations

import pytest

from bossman.context import ContextBudget, ContextBuilder, SUMMARY_MAX_TOKENS


def _builder() -> ContextBuilder:
    b = ContextBuilder(ContextBudget(window=8000), system="sys", refs="", key_constraint="")
    b.add_assistant("делаю шаг раз")
    b.add_tool_result("shell", "вывод команды" * 20, "shell: ок")
    b.add_assistant("делаю шаг два")
    return b


# ---------- P0: без амнезии ----------

def test_empty_summary_keeps_history():
    b = _builder()
    before = list(b.history)
    b.apply_compaction("")            # резервный LLM вернул пусто
    assert b.history == before        # история НЕ стёрта
    assert b.summary is None          # пустая сводка не записана


def test_whitespace_summary_keeps_history():
    b = _builder()
    n = len(b.history)
    b.apply_compaction("   \n\t  ")
    assert len(b.history) == n
    assert b.summary is None


def test_nonempty_summary_compacts():
    b = _builder()
    b.apply_compaction("Сводка: сделано X, дальше Y")
    assert b.history == []
    assert b.summary and "сделано X" in b.summary


def test_repeated_compaction_merges_not_forgets():
    b = _builder()
    b.apply_compaction("Сводка-1: важный якорь commit abc123")
    # новые элементы после первого уплотнения
    b.add_assistant("ещё шаг")
    b.apply_compaction("Сводка-2: результат готов")
    # обе сводки живы — прежнее уплотнение не забыто
    assert "commit abc123" in b.summary
    assert "результат готов" in b.summary
    assert b.history == []


def test_merged_summary_bounded_keeps_newest():
    b = _builder()
    b.apply_compaction("A" * (SUMMARY_MAX_TOKENS * 3))    # заполнить бюджет старой сводкой
    b.add_assistant("шаг")
    b.apply_compaction("СВЕЖЕЕ-ХВОСТ-МАРКЕР")
    assert len(b.summary) <= SUMMARY_MAX_TOKENS * 3
    assert "СВЕЖЕЕ-ХВОСТ-МАРКЕР" in b.summary   # самое свежее выжило (обрезка с хвоста)


# ---------- P1: один писатель на проект ----------

def test_lock_key_deterministic_and_int4_range():
    from bossman.projects.runner import _project_lock_key
    k1 = _project_lock_key("my-film")
    k2 = _project_lock_key("my-film")
    assert k1 == k2
    assert -(2**31) <= k1 < 2**31
    assert _project_lock_key("other") != k1  # разные slug → разные ключи (практически)


class _FakeConn:
    def __init__(self, lock_granted: bool):
        self._granted = lock_granted
        self.unlocked = False

    async def fetchval(self, sql, *args):
        assert "pg_try_advisory_lock" in sql
        return self._granted

    async def execute(self, sql, *args):
        if "pg_advisory_unlock" in sql:
            self.unlocked = True
        return "OK"


class _FakePool:
    def __init__(self, conn):
        self._conn = conn

    async def acquire(self):
        return self._conn

    async def release(self, conn):
        return None


@pytest.mark.asyncio
async def test_run_project_skips_when_already_locked(monkeypatch):
    import bossman.projects.runner as R

    conn = _FakeConn(lock_granted=False)
    monkeypatch.setattr(R.db, "pool", lambda: _async(_FakePool(conn)))

    called = {"body": False}

    async def _body(slug):
        called["body"] = True

    monkeypatch.setattr(R, "_run_project_locked", _body)
    monkeypatch.setattr(R, "journal_append", lambda *a, **k: None)
    monkeypatch.setattr(R.events, "emit", lambda *a, **k: None)

    await R.run_project("busy-slug")
    assert called["body"] is False      # тело НЕ запущено, второй запуск отбит


@pytest.mark.asyncio
async def test_run_project_runs_and_unlocks_when_free(monkeypatch):
    import bossman.projects.runner as R

    conn = _FakeConn(lock_granted=True)
    monkeypatch.setattr(R.db, "pool", lambda: _async(_FakePool(conn)))

    called = {"body": False}

    async def _body(slug):
        called["body"] = True

    monkeypatch.setattr(R, "_run_project_locked", _body)
    monkeypatch.setattr(R, "journal_append", lambda *a, **k: None)
    monkeypatch.setattr(R.events, "emit", lambda *a, **k: None)

    await R.run_project("free-slug")
    assert called["body"] is True       # тело выполнено
    assert conn.unlocked is True        # лок снят в finally


async def _async(value):
    return value
