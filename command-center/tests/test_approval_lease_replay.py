"""Authenticated HTTP + persistent SQLite regressions, without model calls.

This is process integration, not Windows/installed-artifact certification.
Only generated fixture tasks are used; no external action is dispatched.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa

from bcc.api import create_app
from bcc.config import Settings
from bcc import approval_scope as scope
from bcc.db import (agents, tasks, task_runs, tool_calls, approvals,
                    approval_leases, Database)


@pytest_asyncio.fixture
async def lease_env(tmp_path):
    app = create_app(Settings(data_dir=tmp_path / "data"),
                     start_workers=False, announce_token=False)
    async with app.router.lifespan_context(app):
        svc = app.state.svc
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
                headers={"X-BCC-Token": svc.auth.token}) as client:
            yield SimpleNamespace(svc=svc, client=client, app=app)


async def _park(env):
    async with env.svc.db.session() as s:
        res = await s.execute(sa.insert(agents).values(name="lease-replay-fixture"))
        agent_id = int(res.inserted_primary_key[0])
        res = await s.execute(sa.insert(tasks).values(prompt="fixture only",
            agent_id=agent_id, status="waiting_approval"))
        task_id = int(res.inserted_primary_key[0])
        res = await s.execute(sa.insert(task_runs).values(task_id=task_id, status="queued"))
        run_id = int(res.inserted_primary_key[0])
        await s.commit()
    approval = await env.svc.approvals.create("tool", "fixture: list sandbox",
                                            task_id=task_id, run_id=run_id)
    args = {"command": "ls", "mode": "sandbox"}
    async with env.svc.db.session() as s:
        res = await s.execute(sa.insert(tool_calls).values(task_id=task_id, run_id=run_id,
            call_id=f"lease-probe-{approval['id']}", tool="terminal.run", args=args,
            effect="ask", status="pending_approval", approval_id=approval["id"]))
        call_id = int(res.inserted_primary_key[0])
        await s.commit()
    sc = scope.scope_for("terminal.run", args, agent={"id": agent_id}, task={"id": task_id})
    return approval, sc, call_id


def _body(uses=2):
    return {"approve": True, "lease": {"max_uses": uses, "ttl_seconds": 60}}


async def _leases(env, aid):
    async with env.svc.db.session() as s:
        return [dict(r._mapping) for r in (await s.execute(sa.select(approval_leases).where(
            approval_leases.c.approval_id == aid))).all()]


async def _status(env, aid):
    async with env.svc.db.session() as s:
        return (await s.execute(sa.select(approvals.c.status).where(approvals.c.id == aid))).scalar()


@pytest.mark.asyncio
async def test_duplicate_http_decision_cannot_mint_more_capacity(lease_env):
    env = lease_env
    approval, sc, _ = await _park(env)
    path = f"/api/approvals/{approval['id']}"
    first = await env.client.post(path, json=_body())
    assert first.status_code == 200, first.text
    assert first.json()["lease"]["max_uses"] == 2
    await env.client.post(path, json=_body(200))
    rows = await _leases(env, approval["id"])
    assert len(rows) == 1
    assert rows[0]["max_uses"] == 2
    assert await scope.consume(env.svc, sc) is not None
    assert await scope.consume(env.svc, sc) is not None
    assert await scope.consume(env.svc, sc) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("prior", ["rejected", "revoked", "expired", "consumed"])
async def test_replayed_approval_cannot_override_nonpending_decision(lease_env, prior):
    env = lease_env
    approval, sc, _ = await _park(env)
    aid = approval["id"]
    if prior == "rejected":
        response = await env.client.post(f"/api/approvals/{aid}", json={"approve": False})
        assert response.status_code == 200
    else:
        async with env.svc.db.session() as s:
            await s.execute(sa.update(approvals).where(approvals.c.id == aid).values(status=prior))
            await s.commit()
    await env.client.post(f"/api/approvals/{aid}", json=_body())
    assert await _leases(env, aid) == []
    assert await _status(env, aid) == prior
    assert await scope.consume(env.svc, sc) is None


@pytest.mark.asyncio
async def test_parallel_deliveries_grant_one_lease_only(lease_env):
    env = lease_env
    approval, _, _ = await _park(env)
    responses = await asyncio.gather(*(env.client.post(
        f"/api/approvals/{approval['id']}", json=_body()) for _ in range(8)))
    assert any(r.status_code == 200 for r in responses)
    assert all(r.status_code in (200, 409) for r in responses)
    assert len(await _leases(env, approval["id"])) == 1


@pytest.mark.asyncio
async def test_invalid_lease_request_does_not_commit_an_approval(lease_env):
    env = lease_env
    approval = await env.svc.approvals.create("tool", "no parked call")
    response = await env.client.post(f"/api/approvals/{approval['id']}", json=_body())
    assert response.status_code == 409
    assert await _status(env, approval["id"]) == "pending"
    assert await _leases(env, approval["id"]) == []


@pytest.mark.asyncio
async def test_cancelled_task_cannot_receive_new_authority(lease_env):
    env = lease_env
    approval, _, _ = await _park(env)
    async with env.svc.db.session() as s:
        await s.execute(sa.update(tasks).where(tasks.c.id == approval["task_id"]).values(status="stopped"))
        await s.commit()
    response = await env.client.post(f"/api/approvals/{approval['id']}", json=_body())
    assert response.status_code == 409
    assert await _status(env, approval["id"]) == "pending"
    assert await _leases(env, approval["id"]) == []


@pytest.mark.asyncio
async def test_unrelated_parked_task_cannot_borrow_approval(lease_env):
    env = lease_env
    approval, _, call_id = await _park(env)
    other, _, _ = await _park(env)
    async with env.svc.db.session() as s:
        await s.execute(sa.update(tool_calls).where(tool_calls.c.id == call_id).values(
            task_id=other["task_id"], run_id=other["run_id"]))
        await s.commit()
    response = await env.client.post(f"/api/approvals/{approval['id']}", json=_body())
    assert response.status_code == 409
    assert await _leases(env, approval["id"]) == []


@pytest.mark.asyncio
async def test_wrong_token_never_decides_or_grants(lease_env):
    env = lease_env
    approval, _, _ = await _park(env)
    response = await env.client.post(f"/api/approvals/{approval['id']}", json=_body(),
                                    headers={"X-BCC-Token": "wrong-fixture-token"})
    assert response.status_code == 401
    assert await _status(env, approval["id"]) == "pending"
    assert await _leases(env, approval["id"]) == []


@pytest.mark.asyncio
async def test_replay_after_database_reopen_cannot_reset_spent_capacity(lease_env):
    env = lease_env
    approval, sc, _ = await _park(env)
    path = f"/api/approvals/{approval['id']}"
    assert (await env.client.post(path, json=_body(1))).status_code == 200
    assert await scope.consume(env.svc, sc) is not None
    # New database engine, same durable file: no process-local replay cache.
    db = Database(env.svc.db.url)
    reopened = SimpleNamespace(db=db, bus=env.svc.bus)
    try:
        assert await scope.consume(reopened, sc) is None
        await env.client.post(path, json=_body(200))
        assert await scope.consume(reopened, sc) is None
        assert len(await _leases(env, approval["id"])) == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_failed_lease_preparation_rolls_back_decision_and_insert(lease_env, monkeypatch):
    env = lease_env
    approval, _, _ = await _park(env)
    real_grant = scope.grant
    async def failed_grant(*args, **kwargs):
        await real_grant(*args, **kwargs)
        raise RuntimeError("isolated simulated failure after INSERT")
    monkeypatch.setattr(scope, "grant", failed_grant)
    with pytest.raises(RuntimeError, match="isolated simulated failure"):
        await env.client.post(f"/api/approvals/{approval['id']}", json=_body())
    assert await _status(env, approval["id"]) == "pending"
    assert await _leases(env, approval["id"]) == []


@pytest.mark.asyncio
async def test_lease_is_durable_before_worker_receives_decision(lease_env, monkeypatch):
    env = lease_env
    approval, _, _ = await _park(env)
    original = env.svc.bus.emit
    observed = []
    async def observe(kind, **data):
        if kind == "approval.decided" and data.get("id") == approval["id"]:
            # A separate session must see both rows before the wakeup is sent.
            observed.append((await _status(env, approval["id"]), len(await _leases(env, approval["id"]))))
        return await original(kind, **data)
    monkeypatch.setattr(env.svc.bus, "emit", observe)
    response = await env.client.post(f"/api/approvals/{approval['id']}", json=_body())
    assert response.status_code == 200
    assert observed == [("approved", 1)]


@pytest.mark.asyncio
async def test_plain_decisions_keep_their_original_idempotence(lease_env):
    env = lease_env
    approval, _, _ = await _park(env)
    path = f"/api/approvals/{approval['id']}"
    for decision in (False, True, True):
        response = await env.client.post(path, json={"approve": decision})
        assert response.status_code == 200
        assert response.json()["status"] == "rejected"
        assert response.json()["lease"] is None
    assert await _leases(env, approval["id"]) == []


@pytest.mark.asyncio
async def test_fresh_process_cannot_replay_spent_approval(lease_env):
    import os
    import subprocess
    import sys
    env = lease_env
    approval, sc, _ = await _park(env)
    path = f"/api/approvals/{approval['id']}"
    assert (await env.client.post(path, json=_body(1))).status_code == 200
    assert await scope.consume(env.svc, sc) is not None
    script = """
import asyncio, sys
from pathlib import Path
import httpx
import sqlalchemy as sa
from bcc.api import create_app
from bcc.config import Settings
from bcc.db import approval_leases
async def probe():
    app = create_app(Settings(data_dir=Path(sys.argv[1])), start_workers=False, announce_token=False)
    async with app.router.lifespan_context(app):
        svc = app.state.svc
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver',
                                    headers={'X-BCC-Token': svc.auth.token}) as client:
            result = await client.post('/api/approvals/' + sys.argv[2],
                  json={'approve': True, 'lease': {'max_uses': 200, 'ttl_seconds': 60}})
            assert result.status_code == 409, result.text
        async with svc.db.session() as s:
            rows = (await s.execute(sa.select(approval_leases).where(
                     approval_leases.c.approval_id == int(sys.argv[2])))).all()
            assert len(rows) == 1
            row = rows[0]._mapping
            assert row['used'] == row['max_uses'] == 1 and row['status'] == 'exhausted'
        print('LEASE_RESTART_PROBE=PASS')
asyncio.run(probe())
"""
    result = subprocess.run([sys.executable, "-c", script, str(env.svc.settings.data_dir), str(approval["id"])],
                            capture_output=True, text=True, timeout=30, env=os.environ.copy())
    assert result.returncode == 0, result.stderr
    assert "LEASE_RESTART_PROBE=PASS" in result.stdout
