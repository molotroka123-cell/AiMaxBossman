"""REAL PostgreSQL gate для каноничной памяти (WorkingMemory/Decision/Failure).

Единственная авторитетность памяти: Postgres через `bossman.db` (схема —
`db/schema.sql`, jsonb-кодек в пуле). Здесь доказывается ПРОДАКШН-путь, а не мок:
create/update, оптимистическая конкурентность, checkpoint/restore, версии,
decision create/query/supersede, failure record/query/resolve и восстановление
после рестарта процесса (свежий пул).

Без `BOSSMAN_TEST_PG_DSN` — честный SKIP_HOST (никакого fake-green).
Поднять локально:
    initdb -D /tmp/pgdata -A trust -U postgres && pg_ctl -D /tmp/pgdata -o '-p 5433' start
    export BOSSMAN_TEST_PG_DSN=postgresql://bossman:bossman@127.0.0.1:5433/bossman
"""
from __future__ import annotations

import os

import pytest

DSN = os.getenv("BOSSMAN_TEST_PG_DSN")
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not DSN, reason="SKIP_HOST: no BOSSMAN_TEST_PG_DSN (real PostgreSQL) available"),
]


@pytest.fixture()
async def pg(monkeypatch):
    """Свежий пул на реальном Postgres; схему применяет сам db.pool()."""
    monkeypatch.setenv("BOSSMAN_DATABASE_URL", DSN)
    from bossman import db
    from bossman.config import settings
    monkeypatch.setattr(settings, "database_url", DSN, raising=False)
    await db.close()
    yield db
    await db.close()


async def test_working_memory_full_cycle(pg):
    from bossman.working_memory import OptimisticConcurrencyConflict, WorkingMemory
    wm = WorkingMemory()
    tid = "pytest-wm-1"
    await pg.execute("DELETE FROM working_memory WHERE task_id=$1", tid)

    st = await wm.create_task_state(tid, "objective-a", constraints=[{"c": "no shell"}])
    assert st["version"] == 1
    # JSONB возвращается нативным объектом (кодек), не строкой — без двойного кодирования
    assert st["constraints"] == [{"c": "no shell"}]

    up = await wm.update_task_state(tid, {"current_step": "s2"}, expected_version=1)
    assert up["version"] == 2 and up["current_step"] == "s2"

    with pytest.raises(OptimisticConcurrencyConflict):
        await wm.update_task_state(tid, {"current_step": "stale"}, expected_version=1)
    assert (await wm.get_task_state(tid))["current_step"] == "s2"   # конфликт не записал

    cp = await wm.checkpoint(tid)
    await wm.update_task_state(tid, {"current_step": "s9"})
    assert (await wm.restore_checkpoint(cp))["current_step"] == "s2"
    assert len(await wm.list_versions(tid)) >= 3


async def test_decision_memory_supersede_preserves_history(pg):
    import bossman.decision_memory as dm
    await pg.execute("DELETE FROM decisions WHERE decision_id IN ('pt-d1','pt-d2')")
    d = await dm.create_decision("pt-d1", "route", "db", "postgres", "durable",
                                 evidence=[{"e": "db.py"}])
    assert d.evidence == [{"e": "db.py"}]
    res = await dm.supersede_decision("pt-d1", "pt-d2")
    assert res["supersedes"] is not None
    assert await dm.get_decision("pt-d1") is not None          # история сохранена
    cur = await dm.query_decisions(scope="route", current_only=True)
    assert any(x.decision_id == "pt-d2" for x in cur)


async def test_failure_memory_roundtrip_and_jsonb_queryable(pg):
    import bossman.failure_memory as fm
    tid = "pytest-fm-1"
    await pg.execute("DELETE FROM failures WHERE task_id=$1", tid)
    f = await fm.record_failure(tid, "boom", "E", "rc", "fix", "failed",
                                files=["a.py"], environment={"os": "linux"})
    assert f.files == ["a.py"] and f.environment["os"] == "linux"
    assert len(await fm.get_unresolved_failures(tid)) == 1
    assert await fm.resolve_failure(f.failure_id) is True
    assert (await fm.get_failure(f.failure_id)).resolved is True
    # containment работает ⇒ в колонке настоящий JSON, а не двойно закодированная строка
    n = await pg.fetchval("SELECT count(*) FROM failures WHERE files @> '[\"a.py\"]'::jsonb")
    assert n >= 1


async def test_schema_init_is_noop_not_second_authority(pg):
    """init_*_table больше не создаёт вторую схему — только проверяет каноничную."""
    import bossman.decision_memory as dm
    import bossman.failure_memory as fm
    await dm.init_decisions_table()
    await fm.init_failures_table()
    src = (
        open("bossman/decision_memory.py", encoding="utf-8").read()
        + open("bossman/failure_memory.py", encoding="utf-8").read()
    )
    assert "CREATE TABLE" not in src, "embedded DDL вернулся — это второй источник правды"


async def test_restart_restores_durable_state(pg):
    """Рестарт процесса эмулируется закрытием пула: состояние переживает."""
    from bossman.working_memory import WorkingMemory
    wm = WorkingMemory()
    tid = "pytest-restart-1"
    await pg.execute("DELETE FROM working_memory WHERE task_id=$1", tid)
    await wm.create_task_state(tid, "survive")
    await wm.update_task_state(tid, {"current_step": "before-restart"})
    await pg.close()                       # ← процесс "перезапущен"
    again = await WorkingMemory().get_task_state(tid)
    assert again is not None and again["current_step"] == "before-restart"
