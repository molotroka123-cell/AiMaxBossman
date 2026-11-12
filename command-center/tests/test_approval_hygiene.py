"""Architecture P0 step 3: synthetic/bench pending approvals expire after the TTL; real tasks' approvals never do."""
from __future__ import annotations

import datetime as dt

import sqlalchemy as sa

from bcc.db import approvals as approvals_t, tasks as tasks_t, utcnow
from bcc.features import approval_hygiene as hy


async def _approval(svc, title, age_h):
    async with svc.db.session() as s:
        tid = (await s.execute(sa.insert(tasks_t).values(title=title, prompt="p", status="waiting_approval")
                               )).inserted_primary_key[0]
        aid = (await s.execute(sa.insert(approvals_t).values(
            kind="skill_permissions", preview="x", task_id=tid, status="pending",
            created_at=utcnow() - dt.timedelta(hours=age_h)))).inserted_primary_key[0]
        await s.commit()
    return aid


async def _status(svc, aid):
    async with svc.db.session() as s:
        return (await s.execute(sa.select(approvals_t.c.status, approvals_t.c.decided_by)
                                .where(approvals_t.c.id == aid))).first()


async def test_only_old_synthetic_pending_expire(env):
    svc = env.svc
    old_synth = await _approval(svc, "synthetic operator probe", 30)
    old_bench = await _approval(svc, "pcbench v5-base H07", 25)
    fresh_synth = await _approval(svc, "pcbench v5-base H08", 2)
    old_real = await _approval(svc, "Сделай отчёт по маркетингу", 300)       # negative control: real task, very old
    done = await hy.expire_synthetic(svc)
    assert sorted(done) == sorted([old_synth, old_bench])
    assert (await _status(svc, old_synth)) == ("expired", hy.SYSTEM_ACTOR)
    assert (await _status(svc, fresh_synth))[0] == "pending"
    assert (await _status(svc, old_real))[0] == "pending"
    assert await hy.expire_synthetic(svc) == []                              # idempotent


async def test_ttl_and_markers_are_config(env, monkeypatch):
    svc = env.svc
    a = await _approval(svc, "pcbench x", 3)
    monkeypatch.setenv("BCC_SYNTH_APPROVAL_TTL_HOURS", "0")
    assert await hy.expire_synthetic(svc) == []                              # 0 = off
    monkeypatch.setenv("BCC_SYNTH_APPROVAL_TTL_HOURS", "1")
    assert await hy.expire_synthetic(svc) == [a]
    b = await _approval(svc, "dataset-cloud trajectory", 5)
    assert await hy.expire_synthetic(svc) == []                              # not a default marker
    monkeypatch.setenv("BCC_SYNTH_APPROVAL_MARKERS", "dataset-cloud")
    assert await hy.expire_synthetic(svc) == [b]


async def test_expired_cannot_be_approved(env):
    a = await _approval(env.svc, "synthetic probe", 40)
    await hy.expire_synthetic(env.svc)
    row = await env.svc.approvals.decide(a, True)
    assert row["status"] == "expired"
