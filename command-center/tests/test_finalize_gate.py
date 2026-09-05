"""EH-04 (TRUTH-003 §10): канонический finalize — объявленный эффект без свежего подтверждения
не даёт completed (владельцу — review_escalation); подтверждённый — completed с событием
task.finalized; человек-override идёт только через lifecycle.finalize_override."""
from __future__ import annotations

import sqlalchemy as sa

from bcc.db import approvals as approvals_t, tasks as tasks_t, tool_calls as tool_calls_t
from bcc.finalize import FinalizeDecision, finalize_task

from .conftest import FakeAdapter
from .helpers import make_stack


async def _run_once(env):
    for _ in range(6):
        run_id = await env.svc.engine.claim()
        if run_id is None:
            break
        await env.svc.engine.execute(run_id)


async def _set_meta(env, task_id, meta):
    async with env.svc.db.session() as s:
        await s.execute(sa.update(tasks_t).where(tasks_t.c.id == task_id).values(meta=meta))
        await s.commit()


async def _allow_root(env, root):
    """Файловые ожидания проверяются только внутри одобренных корней (terminal.roots)."""
    import json
    from bcc.db import settings_kv
    async with env.svc.db.session() as s:
        await s.execute(sa.insert(settings_kv).values(key="terminal.roots", value_enc=env.svc.vault.encrypt(json.dumps([str(root)]))))
        await s.commit()


def _finalized(events):
    out = []
    for e in events:
        if e.get("kind") == "task.finalized":
            payload = e.get("data") if isinstance(e.get("data"), dict) else e
            out.append(payload)
    return out


async def _status(env, task_id):
    return (await env.client.get(f"/api/tasks/{task_id}")).json()["task"]["status"]


async def test_declared_effect_absent_is_not_completed(env, tmp_path):
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter("готово, файл создан")
    stack = await make_stack(env.client)
    target = tmp_path / "proof.txt"
    await _allow_root(env, tmp_path)
    await _set_meta(env, stack["task"]["id"], {"required_effects": [{"kind": "file", "target": str(target), "expect": {"exists": True}}]})
    await _run_once(env)
    assert await _status(env, stack["task"]["id"]) == "waiting_approval"           # не completed по слову модели
    async with env.svc.db.session() as s:
        appr = [dict(r._mapping) for r in (await s.execute(sa.select(approvals_t))).fetchall()]
    assert any(a["kind"] == "review_escalation" and "required effects not verified" in a["preview"] for a in appr)
    events = await env.svc.bus.recent(100)
    assert not any(e.get("kind") == "task.finalized" for e in events)


async def test_declared_effect_present_is_finalized_with_event(env, tmp_path):
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter("готово")
    stack = await make_stack(env.client)
    target = tmp_path / "proof.txt"; target.write_text("bossman-proof-42", encoding="utf-8")
    await _allow_root(env, tmp_path)
    await _set_meta(env, stack["task"]["id"], {"required_effects": [{"kind": "file", "target": str(target),
                                                                     "expect": {"contains": "bossman-proof-42"}}]})
    await _run_once(env)
    assert await _status(env, stack["task"]["id"]) == "completed"
    fin = _finalized(await env.svc.bus.recent(100))
    assert fin and fin[0]["override"] is False and fin[0]["checks"]["verification"] == "VERIFIED"
    assert fin[0]["checks"]["expectations"] == 1


async def test_plain_task_still_completes_and_records_receipts(env):
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter("ответ")
    stack = await make_stack(env.client)
    await _run_once(env)
    assert await _status(env, stack["task"]["id"]) == "completed"
    fin = _finalized(await env.svc.bus.recent(100))
    assert fin and fin[0]["checks"]["verification"] == "NOT_REQUIRED"


async def test_finalize_refuses_fail_verdict_and_stale_engine_fence(env):
    stack = await make_stack(env.client)
    run_id = await env.svc.engine.claim()
    d = await finalize_task(env.svc.engine, run_id, stack["task"]["id"], answer="x", usage={},
                            verdicts=[{"verdict": "FAIL", "requeue": False}])
    assert isinstance(d, FinalizeDecision) and not d.ok and "FAIL" in d.reason
    assert await _status(env, stack["task"]["id"]) != "completed"
