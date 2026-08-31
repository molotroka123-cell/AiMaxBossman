"""V2.6 D3 — bcc: args в аудит-таблице tool_calls редактируются.

Сырой Bearer/api_key в аргументах модельного tool-call не должен осесть в
`tool_calls.args` (таблица отдаётся в UI/API). Anti-replay `args_hash` при
этом считается от СЫРЫХ аргументов и стабилен между попытками.
"""
from __future__ import annotations

import json

import pytest
import sqlalchemy as sa

from bcc.db import tool_calls as tool_calls_t
from bcc.tools import args_hash

SECRET = "sk-BCCREDACT-test-1234567890abcdef"


class _Call:
    id = "call-v26-1"
    name = "http.get"
    arguments = {"url": "https://x", "api_key": SECRET}


class _Spec:
    name = "http.get"
    source = "test"


@pytest.mark.asyncio
async def test_record_tool_call_redacts_args_keeps_raw_hash(env):
    from bcc.db import task_runs as task_runs_t
    from bcc.db import tasks as tasks_t
    eng = env.svc.engine
    async with env.svc.db.session() as s:
        task_id = (await s.execute(sa.insert(tasks_t).values(
            title="t", prompt="p", status="running"))).inserted_primary_key[0]
        run_id = (await s.execute(sa.insert(task_runs_t).values(
            task_id=task_id, status="running", attempt=0))).inserted_primary_key[0]
        await s.commit()
    await eng._record_tool_call(run_id, task_id, 0, _Call(), _Spec(),
                                effect="auto", status="executed")
    async with env.svc.db.session() as s:
        row = (await s.execute(sa.select(tool_calls_t).where(
            tool_calls_t.c.call_id == "call-v26-1"))).mappings().first()
    assert row is not None
    dumped = json.dumps(row["args"], ensure_ascii=False)
    assert SECRET not in dumped, "секрет не должен осесть в tool_calls.args"
    assert row["args"]["url"] == "https://x", "нечувствительные args не трогаем"
    assert row["args_hash"] == args_hash("http.get", _Call.arguments), \
        "hash считается от сырых args (стабильный anti-replay ключ)"
