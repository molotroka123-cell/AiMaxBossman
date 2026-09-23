"""Jev feature wiring: Bossman unchanged when disabled; shadow never alters execution."""
from __future__ import annotations

import asyncio

import pytest
import sqlalchemy as sa

from bcc.db import models as models_t, tasks as tasks_t

from .conftest import FakeAdapter, client_for, make_settings, start_app
from .helpers import make_stack
from .jev_mock import FAKE_KEY, MockJev


@pytest.fixture
def mock():
    server = MockJev()
    yield server
    server.close()


async def _app(tmp_path):
    app, svc = await start_app(make_settings(tmp_path), start_workers=False)
    return app, svc, client_for(app, svc)


def _hook_names(svc):
    return [getattr(fn, "__qualname__", "") for fn in svc.engine.hooks["pick_model"]]


async def test_starts_normally_disabled_and_without_key(tmp_path, monkeypatch):
    for name in ("BOSSMAN_JEV_ENABLED", "BOSSMAN_JEV_API_KEY", "TYPESAFE_API_KEY",
                 "BOSSMAN_JEV_BROWSER_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    app, svc, client = await _app(tmp_path)
    try:
        assert not any("jev" in n for n in _hook_names(svc))          # nothing registered
        status = (await client.get("/api/jev/status")).json()
        assert status["wired"] is False and status["authoritative"] is False
        assert status["decision"]["enabled"] is False and status["decision"]["key_present"] is False
        assert status["browser"]["enabled"] is False and status["browser"]["may_execute"] is False
        assert status["contract"] == "CONTRACT_UNVERIFIED"
        assert (await client.get("/api/jev/shadow")).json() == {"records": [], "wired": False}
    finally:
        await client.aclose()
        await svc.stop()


async def _routed_task(svc, client, *, meta=None):
    seen = []

    def factory(model, provider):
        async def on_chat(_c, _m):
            seen.append(model["alias"])
        return FakeAdapter(f"via {model['alias']}", on_chat=on_chat)
    svc.registry.adapter_factory = factory
    stack = await make_stack(client)
    await client.post("/api/models", json={"provider_id": stack["provider"]["id"], "name": "coder",
                                           "alias": "router-coder", "kind": "local", "caps": {"coding": True}})
    await client.patch("/api/router/rules", json={"role_scores": {"router-coder": {"coding": 0.9}}})
    async with svc.db.session() as s:
        await s.execute(sa.update(models_t).values(status="online"))
        await s.commit()
    task = (await client.post("/api/tasks", json={"title": "код", "prompt": "напиши функцию",
                                                  "agent_id": stack["agent"]["id"], "run_now": False})).json()["task"]
    values = {"kind": "coding"}
    if meta is not None:
        values["meta"] = meta
    async with svc.db.session() as s:
        await s.execute(sa.update(tasks_t).where(tasks_t.c.id == task["id"]).values(**values))
        await s.commit()
    await svc.engine.enqueue(task["id"])
    for _ in range(10):
        rid = await svc.engine.claim()
        if rid is None:
            break
        await svc.engine.execute(rid)
    if getattr(svc, "jev", None) is not None and svc.jev.jobs:
        await asyncio.wait_for(asyncio.gather(*list(svc.jev.jobs)), 15)
    async with svc.db.session() as s:
        status = (await s.execute(sa.select(tasks_t.c.status).where(tasks_t.c.id == task["id"]))).scalar()
    return task, seen, status


@pytest.fixture
def jev_on(monkeypatch, mock, tmp_path):
    monkeypatch.setenv("BOSSMAN_JEV_ENABLED", "1")
    monkeypatch.setenv("BOSSMAN_JEV_API_KEY", FAKE_KEY)
    monkeypatch.setenv("BOSSMAN_JEV_ENDPOINT", mock.url)
    monkeypatch.setenv("BOSSMAN_JEV_TIMEOUT_MS", "300")
    monkeypatch.setenv("BOSSMAN_JEV_KILL_FILE", str(tmp_path / "jev.disabled"))
    return mock


async def test_enabled_failing_provider_task_completes_route_unchanged(tmp_path, jev_on):
    jev_on.script = [("status", 500)]
    app, svc, client = await _app(tmp_path)
    try:
        assert any("jev" in n for n in _hook_names(svc))
        jev_fn = next(fn for fn in svc.engine.hooks["pick_model"] if "jev" in fn.__qualname__)
        assert svc.engine.hook_is_critical("pick_model", jev_fn) is False
        task, seen, status = await _routed_task(svc, client, meta={"cloud_allowed": True})
        assert status == "completed"
        assert "router-coder" in seen                               # the router stayed authoritative
        explain = (await client.get(f"/api/router/explain?task_id={task['id']}")).json()
        assert explain["route"]["alias"] == "router-coder"
        records = (await client.get("/api/jev/shadow")).json()["records"]
        mine = [r for r in records if r["task_id"] == task["id"]]
        assert mine and mine[-1]["fallback_reason"] == "http_5xx" and mine[-1]["authoritative"] is False
        assert mine[-1]["baseline"]["model_alias"] == "router-coder"
        assert mine[-1]["baseline"]["source"] == "router"
        assert jev_on.requests                                     # egress was allowed → request made
        assert FAKE_KEY not in str((await client.get("/api/jev/status")).json())
    finally:
        await client.aclose()
        await svc.stop()


async def test_enabled_success_shadow_records_but_does_not_change_model(tmp_path, jev_on):
    app, svc, client = await _app(tmp_path)
    try:
        task, seen, status = await _routed_task(svc, client, meta={"cloud_allowed": True})
        assert status == "completed" and "router-coder" in seen
        rec = [r for r in svc.jev.recorder.recent if r["task_id"] == task["id"]][-1]
        assert rec["jev"] is not None and rec["fallback_reason"] is None
        assert rec["agreement"]["locality"] is True                 # mock picks local_fast; router picked local
    finally:
        await client.aclose()
        await svc.stop()


async def test_egress_gate_no_cloud_permission_no_request(tmp_path, jev_on):
    """Cloud is fail-closed in the router; the Jev shadow obeys the same policy."""
    app, svc, client = await _app(tmp_path)
    try:
        task, seen, status = await _routed_task(svc, client)
        assert status == "completed"
        rec = [r for r in svc.jev.recorder.recent if r["task_id"] == task["id"]][-1]
        assert rec["fallback_reason"] == "egress_not_allowed"
        assert jev_on.requests == []
    finally:
        await client.aclose()
        await svc.stop()


async def test_egress_gate_private_task_no_request(tmp_path, jev_on):
    app, svc, client = await _app(tmp_path)
    try:
        task, seen, status = await _routed_task(svc, client, meta={"cloud_allowed": True, "privacy": "private"})
        rec = [r for r in svc.jev.recorder.recent if r["task_id"] == task["id"]][-1]
        assert rec["fallback_reason"] == "egress_not_allowed" and jev_on.requests == []
    finally:
        await client.aclose()
        await svc.stop()
