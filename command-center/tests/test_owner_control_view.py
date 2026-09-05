"""Минимальный вид владельца (TRUTH-003 §20): КТО / ГДЕ / КАКАЯ МОДЕЛЬ / ЧТО /
СОСТОЯНИЕ ДЕЙСТВИЯ / ПОЧЕМУ ЗАБЛОКИРОВАНО / ЦЕНА / ВНИМАНИЕ.

Главное правило: зелёный COMPLETE не появляется раньше канонического финализатора.
"""
from __future__ import annotations

import sqlalchemy as sa

from bcc.db import agents as agents_t, approvals as approvals_t, events as events_t, \
    task_runs as runs_t, tasks as tasks_t, tool_calls as tool_calls_t, utcnow
from bcc.features.control_plane import ACTION_STATES, _action_state, owner_rows


async def test_old_run_evidence_and_finalizer_cannot_verify_new_run(env):
    tid, old = await _task(env.svc, title="повтор", status="completed")
    await _call(env.svc, tid, old, verified=True, observed=True)
    async with env.svc.db.session() as s:
        await s.execute(sa.insert(events_t).values(kind="task.finalized", ts=utcnow(),
                                                   data={"task_id": tid, "run_id": old}))
        await s.execute(sa.insert(runs_t).values(task_id=tid, status="completed"))
        await s.commit()
    row = next(r for r in await owner_rows(env.svc) if r["task_id"] == tid)
    assert row["action_state"] == "UNVERIFIED"
    assert not row["finalized"] and row["attention"]
    assert row["effects"]["verified"] == 0


async def test_blocked_without_run_shows_executor_reason(env):
    async with env.svc.db.session() as s:
        tid = int((await s.execute(sa.insert(tasks_t).values(title="нет агента", prompt="p",
            status="blocked", meta={"blocked_reason": "Выберите исполнителя"}))).inserted_primary_key[0])
        await s.commit()
    row = next(r for r in await owner_rows(env.svc) if r["task_id"] == tid)
    assert row["action_state"] == "BLOCKED" and row["attention"]
    assert row["why_blocked"] == "Выберите исполнителя"
    assert row["cost_usd"] is None


def test_completed_without_any_observation_is_unverified():
    state, reason = _action_state({"status": "completed"}, {"status": "completed"}, {}, False)
    assert state == "UNVERIFIED" and reason


async def _task(svc, *, title, status, agent="кодер", model="glm-local", cost=0.0):
    async with svc.db.session() as s:
        aid = int((await s.execute(sa.insert(agents_t).values(name=agent))).inserted_primary_key[0])
        tid = int((await s.execute(sa.insert(tasks_t).values(
            title=title, prompt="p", agent_id=aid, status=status,
            created_at=utcnow(), updated_at=utcnow()))).inserted_primary_key[0])
        rid = int((await s.execute(sa.insert(runs_t).values(
            task_id=tid, status="completed" if status == "completed" else "running",
            model_alias=model, cost_usd=cost))).inserted_primary_key[0])
        await s.commit()
    return tid, rid


async def _call(svc, tid, rid, *, status="executed", verified=None, observed=False):
    async with svc.db.session() as s:
        await s.execute(sa.insert(tool_calls_t).values(
            run_id=rid, task_id=tid, tool="fs.write", status=status, verified=verified,
            observed_at=utcnow() if observed else None))
        await s.commit()


async def test_row_carries_every_owner_field(env):
    tid, rid = await _task(env.svc, title="создать отчёт", status="running", cost=0.25)
    await _call(env.svc, tid, rid)
    row = next(r for r in await owner_rows(env.svc) if r["task_id"] == tid)
    assert row["who"] == "кодер" and row["where"] and row["model"] == "glm-local"
    assert row["what"] == "создать отчёт" and row["cost_usd"] == 0.25
    assert row["action_state"] in ACTION_STATES
    assert set(row) >= {"who", "where", "model", "what", "action_state", "why_blocked",
                        "cost_usd", "attention"}


async def test_tool_call_alone_is_not_verified_for_the_owner():
    """INV: TOOL_CALLED ≠ SIDE_EFFECT_VERIFIED — владелец видит EXECUTED, не VERIFIED."""
    state, why = _action_state({"status": "running"}, {"status": "running"},
                               {"executed": 3, "observed": 0, "verified": 0}, False)
    assert state == "EXECUTED" and "не доказательство" in why
    seen, _ = _action_state({"status": "running"}, {"status": "running"},
                            {"executed": 3, "observed": 3, "verified": 0}, False)
    assert seen == "OBSERVED"
    ok, _ = _action_state({"status": "running"}, {"status": "running"},
                          {"executed": 3, "observed": 3, "verified": 3}, False)
    assert ok == "VERIFIED"


async def test_completed_without_finalizer_is_not_shown_as_complete(env):
    """Строка `completed` без события task.finalized НЕ даёт зелёного COMPLETE."""
    tid, rid = await _task(env.svc, title="без финализатора", status="completed")
    await _call(env.svc, tid, rid, verified=True, observed=True)
    row = next(r for r in await owner_rows(env.svc) if r["task_id"] == tid)
    assert row["action_state"] == "VERIFIED" and not row["finalized"]
    assert "финализатора" in row["why_blocked"]
    # тот же ряд после следа канонического финализатора
    async with env.svc.db.session() as s:
        await s.execute(sa.insert(events_t).values(kind="task.finalized", ts=utcnow(),
                                                   data={"task_id": tid, "run_id": rid}))
        await s.commit()
    row = next(r for r in await owner_rows(env.svc) if r["task_id"] == tid)
    assert row["action_state"] == "COMPLETE" and row["finalized"] and row["why_blocked"] == ""


async def test_blocked_row_says_why_and_asks_for_attention(env):
    tid, _ = await _task(env.svc, title="ждёт решения", status="waiting_approval")
    async with env.svc.db.session() as s:
        await s.execute(sa.insert(approvals_t).values(
            task_id=tid, kind="terminal", preview="rm -rf /данные", status="pending",
            created_at=utcnow()))
        await s.commit()
    row = next(r for r in await owner_rows(env.svc) if r["task_id"] == tid)
    assert row["action_state"] == "BLOCKED" and row["attention"] is True
    assert "rm -rf" in row["why_blocked"]


async def test_failed_row_shows_the_error_not_silence(env):
    tid, rid = await _task(env.svc, title="упала", status="failed")
    async with env.svc.db.session() as s:
        await s.execute(sa.update(runs_t).where(runs_t.c.id == rid).values(error="провайдер недоступен"))
        await s.commit()
    row = next(r for r in await owner_rows(env.svc) if r["task_id"] == tid)
    assert row["action_state"] == "FAILED" and "провайдер" in row["why_blocked"] and row["attention"]


async def test_owner_view_is_served_by_the_control_plane_without_prompts(env):
    tid, rid = await _task(env.svc, title="видно владельцу", status="running")
    await _call(env.svc, tid, rid)
    body = (await env.client.get("/api/control-plane")).json()
    view = body["owner_view"]
    assert view["states"] == list(ACTION_STATES) and "finalize_task" in view["rule"]
    row = next(r for r in view["rows"] if r["task_id"] == tid)
    assert row["what"] == "видно владельцу"
    # ни промптов, ни секретов в срезе владельца
    assert '"prompt"' not in str(view) and "api_key" not in str(view).lower()
