"""V2.6 Phase 1 — Flight Recorder: сборка трейса + редакция секретов.

Без `BOSSMAN_TEST_PG_DSN` — честный SKIP_HOST (как весь PG-гейт памяти).
Проверяем на живом Postgres теми же формами запросов, что и production:
- трейс собирает intent/агента/модели/инструменты/approvals/ресурсы;
- секрет, ОСЕВШИЙ в строках (legacy до D3-фикса), не выходит из explain —
  read-side защита в глубину;
- write-side: `_call_tool` больше не пишет сырые args/preview (D3).
"""
from __future__ import annotations

import os

import pytest

DSN = os.getenv("BOSSMAN_TEST_PG_DSN")
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not DSN, reason="SKIP_HOST: no BOSSMAN_TEST_PG_DSN (real PostgreSQL) available"),
]

SECRET = "sk-FLIGHTREC-test-1234567890abcdef"  # ci-secret-scan: allow (synthetic test canary)


@pytest.fixture()
async def pg(monkeypatch):
    monkeypatch.setenv("BOSSMAN_DATABASE_URL", DSN)
    from bossman import db
    from bossman.config import settings
    monkeypatch.setattr(settings, "database_url", DSN, raising=False)
    await db.close()
    yield db
    await db.close()


async def _mk_task(pg, text="тестовая задача flight recorder") -> tuple[int, int]:
    task = await pg.fetchrow(
        "INSERT INTO tasks (agent, source, text, status, started_at, finished_at) "
        "VALUES ('coder','ui',$1,'done', now() - interval '5 seconds', now()) RETURNING id", text)
    run = await pg.fetchrow(
        "INSERT INTO runs (task_id, agent, status, steps, prompt_tokens, started_at, finished_at) "
        "VALUES ($1,'coder','done',2,100, now() - interval '5 seconds', now()) RETURNING id",
        task["id"])
    return task["id"], run["id"]


async def test_explain_assembles_full_trace(pg):
    from bossman import flight_recorder
    task_id, run_id = await _mk_task(pg)
    await pg.execute(
        "INSERT INTO model_calls (run_id, agent, alias, is_cloud, prompt_tokens, completion_tokens, "
        "window_fill, prefix_cache_hit) VALUES ($1,'coder','bossman-coder',false,80,20,0.1,true)", run_id)
    await pg.execute(
        "INSERT INTO tool_calls (run_id, agent, tool, args, result_preview, status) "
        "VALUES ($1,'coder','fs.read','{\"path\":\"a.py\"}','ok','ok')", run_id)
    await pg.execute(
        "INSERT INTO approvals (task_id, run_id, kind, tool, preview, status, decided_by) "
        "VALUES ($1,$2,'action','run','предпросмотр','approved','owner')", task_id, run_id)

    trace = await flight_recorder.explain_task(task_id)
    assert trace is not None
    assert trace["intent"].startswith("тестовая задача")
    assert trace["status"] == "done"
    assert trace["runs"][0]["steps"] == 2
    assert trace["runs"][0]["duration_s"] is not None and trace["runs"][0]["duration_s"] >= 4
    assert trace["models"]["aliases"] == ["bossman-coder"]
    assert trace["models"]["prefix_cache_hits"] == 1
    assert trace["tools"][0]["tool"] == "fs.read"
    assert trace["approvals"][0]["decided_by"] == "owner"
    assert trace["resources"]["total_tokens"] == 100
    assert trace["retries"] == 0
    assert "agent_selection" in trace and trace["agent_selection"].get("reason")


async def test_explain_redacts_legacy_raw_secrets_on_read(pg):
    """Строки, записанные ДО D3-фикса (сырой токен в args/preview), не должны
    выходить наружу через explain."""
    import json

    from bossman import flight_recorder
    task_id, run_id = await _mk_task(pg, text="задача с легаси-секретом")
    await pg.execute(
        "INSERT INTO tool_calls (run_id, agent, tool, args, result_preview, status) "
        "VALUES ($1,'coder','http',$2,$3,'ok')", run_id,
        {"headers": {"Authorization": f"Bearer {SECRET}"}},
        f"ответ с токеном {SECRET}")
    await pg.execute(
        "INSERT INTO approvals (task_id, run_id, kind, preview, status) "
        "VALUES ($1,$2,'cloud',$3,'approved')", task_id, run_id,
        f"уйдёт наружу: api_key: {SECRET}")

    trace = await flight_recorder.explain_task(task_id)
    dumped = json.dumps(trace, ensure_ascii=False, default=str)
    assert SECRET not in dumped, "секрет из legacy-строк не должен покидать explain"


async def test_explain_missing_task_returns_none(pg):
    from bossman import flight_recorder
    assert await flight_recorder.explain_task(999_999_999) is None


async def test_call_tool_writes_redacted_args_and_preview(pg, monkeypatch):
    """Write-side D3: `_call_tool` пишет в tool_calls/approvals только
    редактированные args/preview; сам инструмент получает СЫРОЙ секрет."""
    from bossman import runner
    from bossman.toolkit import REGISTRY, ToolContext, ToolDef, ToolResult

    seen_raw: dict = {}

    async def handler(args, ctx):
        seen_raw.update(args)
        return ToolResult(content=f"использован ключ {args['api_key']}", one_line="ok")

    tool = ToolDef(name="v26.test_secret_tool", description="тестовый",
                   rights="read", handler=handler,
                   params={"api_key": {"type": "string"}}, required=["api_key"])
    monkeypatch.setitem(REGISTRY, tool.name, tool)

    class Grant:
        confirm = False

    class FakeAgent:
        name = "coder"
        title = "Кодер"

        def grant(self, name):
            return Grant() if name == "v26.test_secret_tool" else None

    task_id, run_id = await _mk_task(pg, text="write-side redaction")
    ctx = ToolContext(agent="coder", run_id=run_id)
    text, one = await runner._call_tool(
        FakeAgent(), run_id, task_id, "v26_test_secret_tool", {"api_key": SECRET}, ctx)

    assert seen_raw["api_key"] == SECRET, "инструмент должен получить сырой аргумент"
    row = await pg.fetchrow(
        "SELECT args::text AS a, result_preview FROM tool_calls "
        "WHERE run_id=$1 AND tool='v26.test_secret_tool' ORDER BY id DESC LIMIT 1", run_id)
    assert row is not None
    assert SECRET not in row["a"], "args в аудит-таблице должны быть редактированы"
    assert SECRET not in (row["result_preview"] or ""), "preview результата — тоже"
