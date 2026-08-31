"""V2.6 Phase 2 — production-wiring `_select_compute` на живом Postgres.

Флаг включён: тривиальная задача получает C0 (retrieval пропускается —
demand-driven активация), провалы прошлых прогонов поднимают uncertainty и
уровень. Без BOSSMAN_TEST_PG_DSN — честный SKIP_HOST.
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
    monkeypatch.setenv("BOSSMAN_DATABASE_URL", DSN)
    from bossman import db
    from bossman.config import settings
    monkeypatch.setattr(settings, "database_url", DSN, raising=False)
    monkeypatch.setattr(settings, "adaptive_compute", True, raising=False)
    await db.close()
    yield db
    await db.close()


async def _task(pg, text: str) -> int:
    row = await pg.fetchrow(
        "INSERT INTO tasks (agent, source, text) VALUES ('coder','ui',$1) RETURNING id", text)
    return row["id"]


async def test_trivial_task_gets_c0_fast(pg):
    from bossman.compute_budget import ComputeLevel
    from bossman.runner import _select_compute
    tid = await _task(pg, "посчитай 2+2")
    level, reasons = await _select_compute({"id": tid, "text": "посчитай 2+2", "agent": "coder"})
    assert level is ComputeLevel.C0_FAST
    assert reasons


async def test_failed_runs_raise_level_above_c0(pg):
    from bossman.compute_budget import ComputeLevel
    from bossman.runner import _select_compute
    tid = await _task(pg, "посчитай 2+2")
    for _ in range(3):
        await pg.execute(
            "INSERT INTO runs (task_id, agent, status) VALUES ($1,'coder','failed')", tid)
    level, _ = await _select_compute({"id": tid, "text": "посчитай 2+2", "agent": "coder"})
    assert level is not ComputeLevel.C0_FAST, \
        "3 провала — задача больше не «тривиальная» (история учтена)"


async def test_risky_task_gets_c4(pg):
    from bossman.compute_budget import ComputeLevel
    from bossman.runner import _select_compute
    text = "оплати счёт и отправь пароль клиенту"
    tid = await _task(pg, text)
    level, _ = await _select_compute({"id": tid, "text": text, "agent": "coder"})
    assert level is ComputeLevel.C4_MAX_VERIFICATION
