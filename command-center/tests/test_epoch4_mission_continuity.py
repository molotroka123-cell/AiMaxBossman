"""Real canonical queue/SQLite/finalizer integration, no cloud model calls."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from bcc.db import missions, tasks, task_runs, utcnow
from bcc.engine import TaskEngine
from bcc.features import missions as feature
from bcc.mission_continuity import bindings
from .test_finalize_gate import _allow_root


def plan(tmp_path, *, dependency=True):
    return {"tasks": [
        {"node_id": "a", "prompt": "alpha", "kind": "research", "meta": {
            "allowed_tools": [], "required_effects": [{"kind": "file", "target": str(tmp_path / "a.txt"),
                                                       "expect": {"contains": "alpha"}}]}},
        {"node_id": "b", "prompt": "beta", "kind": "research", "depends_on": ["a"] if dependency else [],
         "meta": {"allowed_tools": [], "required_effects": [{"kind": "file", "target": str(tmp_path / "b.txt"),
                                                             "expect": {"contains": "beta"}}]}},
    ]}


async def create(env, tmp_path, *, dependency=True, workers=2, write=True):
    async def executor(task, run, engine):
        if write:
            target = tmp_path / ("a.txt" if task["prompt"] == "alpha" else "b.txt")
            if task["prompt"] == "beta" and dependency:
                assert (tmp_path / "a.txt").read_text() == "alpha"
            target.write_text(task["prompt"], encoding="utf-8")
        return "done"  # same prose with/without real effect
    env.svc.engine.register_executor("research", executor)
    await _allow_root(env, tmp_path)
    response = await env.client.post("/api/missions", json={"title": "Bound mission", "max_workers": workers,
                                                             "plan": plan(tmp_path, dependency=dependency)})
    assert response.status_code == 200, response.text
    return response.json()


async def children(env, mid):
    return (await env.client.get(f"/api/missions/{mid}")).json()["tasks"]


async def test_real_dependency_chain_reloads_from_durable_binding(env, tmp_path):
    m = await create(env, tmp_path)
    assert (await env.client.post(f"/api/missions/{m['id']}/start")).status_code == 200
    rows = await children(env, m["id"])
    assert [t["status"] for t in rows] == ["queued", "draft"]
    # Even a direct task Run cannot escape the mission graph.
    attempt = await env.client.post(f"/api/tasks/{rows[1]['id']}/run")
    assert attempt.json()["ok"] is False
    assert attempt.json()["reason"] == "DEPENDENCIES_PENDING"
    run = await env.svc.engine.claim()
    await env.svc.engine.execute(run)
    assert (tmp_path / "a.txt").read_text() == "alpha"
    assert not (tmp_path / "b.txt").exists()
    # A fresh engine and no in-memory graph: the next dispatch comes from DB.
    engine = TaskEngine(env.svc.db, env.svc.bus, env.svc.registry)
    engine.executors.update(env.svc.engine.executors)
    engine.services = env.svc
    original = env.svc.engine
    env.svc.engine = engine
    try:
        await feature._setup(env.svc)
        await feature._tick(env.svc)
        rows = await children(env, m["id"])
        assert [t["status"] for t in rows] == ["completed", "queued"]
        await engine.execute(await engine.claim())
        await feature._tick(env.svc)
    finally:
        env.svc.engine = original
    full = (await env.client.get(f"/api/missions/{m['id']}")).json()
    assert full["status"] == "completed"
    assert full["progress"] == 1
    assert full["continuity"]["binding_state"] == "BOUND"
    assert full["continuity"]["is_effect_evidence"] is False
    assert (tmp_path / "b.txt").read_text() == "beta"


async def test_false_success_does_not_release_dependent(env, tmp_path):
    m = await create(env, tmp_path, write=False)
    await env.client.post(f"/api/missions/{m['id']}/start")
    await env.svc.engine.execute(await env.svc.engine.claim())
    await feature._tick(env.svc)
    rows = await children(env, m["id"])
    assert rows[0]["status"] != "completed"
    assert rows[1]["status"] == "draft"
    assert not (tmp_path / "a.txt").exists()


async def test_repeated_materialization_does_not_duplicate_children(env, tmp_path):
    m = await create(env, tmp_path)
    before = [t["id"] for t in await children(env, m["id"])]
    await feature._create_tasks(env.svc, m["id"], m["plan"])
    await feature._create_tasks(env.svc, m["id"], m["plan"])
    assert [t["id"] for t in await children(env, m["id"])] == before


async def test_two_engines_share_atomic_mission_worker_cap(env, tmp_path):
    m = await create(env, tmp_path, dependency=False, workers=1)
    async with env.svc.db.session() as s:
        await s.execute(sa.update(missions).where(missions.c.id == m["id"]).values(status="running"))
        await s.commit()
    rows = await children(env, m["id"])
    other = TaskEngine(env.svc.db, env.svc.bus, env.svc.registry)
    other.executors.update(env.svc.engine.executors)
    results = await asyncio.gather(env.svc.engine.enqueue(rows[0]["id"]), other.enqueue(rows[1]["id"]))
    assert sum(r is not None for r in results) == 1
    assert sum(t["status"] == "queued" for t in await children(env, m["id"])) == 1
    async with env.svc.db.session() as s:
        assert (await s.execute(sa.select(sa.func.count()).select_from(task_runs))).scalar() == 1


async def test_concurrent_duplicate_enqueue_is_one_run(env, tmp_path):
    m = await create(env, tmp_path)
    async with env.svc.db.session() as s:
        await s.execute(sa.update(missions).where(missions.c.id == m["id"]).values(status="running"))
        await s.commit()
    tid = (await children(env, m["id"]))[0]["id"]
    results = await asyncio.gather(*(env.svc.engine.enqueue(tid, only_if_draft=True) for _ in range(8)))
    assert len(set(results)) == 1
    async with env.svc.db.session() as s:
        assert (await s.execute(sa.select(sa.func.count()).select_from(task_runs))).scalar() == 1


@pytest.mark.parametrize("operation", ["start", "resume", "pause"])
async def test_cancelled_mission_never_resurrects(env, tmp_path, operation):
    m = await create(env, tmp_path)
    await env.client.post(f"/api/missions/{m['id']}/stop")
    assert (await env.client.post(f"/api/missions/{m['id']}/{operation}")).status_code == 409
    assert all(t["status"] == "stopped" for t in await children(env, m["id"]))
    assert not (tmp_path / "a.txt").exists()


async def test_start_is_idempotent_and_pause_resume_preserves_deadline(env, tmp_path):
    m = await create(env, tmp_path)
    first = (await env.client.post(f"/api/missions/{m['id']}/start")).json()
    second = (await env.client.post(f"/api/missions/{m['id']}/start")).json()
    assert second["started_at"] == first["started_at"]
    await env.client.post(f"/api/missions/{m['id']}/pause")
    await env.client.post(f"/api/missions/{m['id']}/resume")
    current = (await env.client.get(f"/api/missions/{m['id']}")).json()
    assert current["started_at"] == first["started_at"]


async def test_late_tick_cannot_overwrite_owner_stop(env, tmp_path):
    m = await create(env, tmp_path)
    await env.client.post(f"/api/missions/{m['id']}/stop")
    await feature._finish_mission(env.svc, m["id"], "completed", "stale ticker")
    assert (await env.client.get(f"/api/missions/{m['id']}")).json()["status"] == "cancelled"


async def test_pause_does_not_consume_unstarted_run(env, tmp_path):
    m = await create(env, tmp_path)
    await env.client.post(f"/api/missions/{m['id']}/start")
    run = await env.svc.engine.claim()
    await env.client.post(f"/api/missions/{m['id']}/pause")
    await env.svc.engine.execute(run)
    assert not (tmp_path / "a.txt").exists()
    assert (await children(env, m["id"]))[0]["status"] == "queued"


@pytest.mark.parametrize("change", ["prompt", "obligation", "binding", "foreign_child"])
async def test_changed_child_contract_fails_closed(env, tmp_path, change):
    m = await create(env, tmp_path)
    row = (await children(env, m["id"]))[0]
    async with env.svc.db.session() as s:
        values = {}
        if change == "prompt":
            values["prompt"] = "substituted goal"
        elif change == "foreign_child":
            values["mission_id"] = None
        else:
            meta = dict(row["meta"])
            if change == "obligation":
                meta.pop("required_effects")
            else:
                meta.pop("continuity")
            values["meta"] = meta
        await s.execute(sa.update(tasks).where(tasks.c.id == row["id"]).values(**values))
        await s.commit()
    result = (await env.client.post(f"/api/missions/{m['id']}/start")).json()
    assert result["status"] == "failed"
    assert not (tmp_path / "a.txt").exists()


async def test_creation_failure_rolls_back_parent_and_all_children(env, tmp_path, monkeypatch):
    original = feature._insert_plan
    async def crash(session, mid, compiled):
        await original(session, mid, compiled)
        raise RuntimeError("simulated death before commit")
    monkeypatch.setattr(feature, "_insert_plan", crash)
    with pytest.raises(RuntimeError, match="before commit"):
        await env.client.post("/api/missions", json={"title": "Atomic", "plan": plan(tmp_path)})
    async with env.svc.db.session() as s:
        for table in (missions, tasks):
            assert (await s.execute(sa.select(sa.func.count()).select_from(table))).scalar() == 0


@pytest.mark.parametrize("raw", [{"tasks": []}, {"tasks": ["bad"]}, {"tasks": [
    {"node_id": "a", "prompt": "a", "depends_on": ["ghost"]}]}])
async def test_invalid_plan_has_no_persisted_parent(env, raw):
    response = await env.client.post("/api/missions", json={"title": "Invalid", "plan": raw})
    assert response.status_code == 400
    assert (await env.client.get("/api/missions")).json() == []


@pytest.mark.parametrize("field,value", [("max_workers", True), ("max_workers", 0),
    ("duration_minutes", -1), ("cloud_budget_usd", "NaN")])
async def test_invalid_resource_limits_rejected_before_writes(env, field, value):
    response = await env.client.post("/api/missions", json={"title": "Invalid", field: value})
    assert response.status_code == 422
    assert (await env.client.get("/api/missions")).json() == []


async def test_missing_mission_stop_is_404(env):
    assert (await env.client.post("/api/missions/999999/stop")).status_code == 404


def test_legacy_unbound_dag_is_not_guessed_from_row_order():
    m = {"id": 1, "status": "running", "plan": {"tasks": [
        {"node_id": "b", "prompt": "b", "depends_on": ["a"]}]}}
    assert bindings(m, [])[1] == "LEGACY_DAG_UNBOUND"


async def test_parent_stop_is_enforced_at_tool_boundary_with_stale_task_snapshot(env, tmp_path):
    from bcc.tools import ToolContext, ToolSpec, ToolResult, execute_tool
    m = await create(env, tmp_path)
    await env.client.post(f"/api/missions/{m['id']}/start")
    old = (await children(env, m["id"]))[0]
    await env.client.post(f"/api/missions/{m['id']}/stop")
    calls = []
    async def handler(args, ctx):
        calls.append(1)
        (tmp_path / "forbidden.txt").write_text("effect")
        return ToolResult(content="done")
    spec = ToolSpec("fixture.effect", "Fixture", handler)
    result = await execute_tool(spec, {}, ToolContext(env.svc, old, 1, {}))
    assert result.error
    assert result.data["reason_code"] in {"CHILD_NOT_RUNNING", "MISSION_CANCELLED"}
    assert calls == [] and not (tmp_path / "forbidden.txt").exists()


async def test_pause_preserves_an_already_running_child_but_blocks_new_children(env, tmp_path):
    from bcc.tools import ToolContext, ToolSpec, ToolResult, execute_tool
    m = await create(env, tmp_path)
    await env.client.post(f"/api/missions/{m['id']}/start")
    row = (await children(env, m["id"]))[0]
    async with env.svc.db.session() as s:
        await s.execute(sa.update(tasks).where(tasks.c.id == row["id"]).values(status="running"))
        await s.commit()
    await env.client.post(f"/api/missions/{m['id']}/pause")
    async def handler(args, ctx):
        (tmp_path / "inflight.txt").write_text("already running child")
        return ToolResult(content="done")
    result = await execute_tool(ToolSpec("fixture.effect", "Fixture", handler), {},
                                ToolContext(env.svc, row, 1, {}))
    assert not result.error
    assert (tmp_path / "inflight.txt").exists()
    await feature._tick(env.svc)
    assert (await children(env, m["id"]))[1]["status"] == "draft"


async def test_empty_generated_plan_does_not_leave_a_running_zombie(env):
    response = await env.client.post("/api/missions", json={"title": "0 tasks", "goal": "0 tasks"})
    assert response.status_code == 400
    assert (await env.client.get("/api/missions")).json() == []


async def test_declared_native_kind_cannot_bypass_app_admission(env):
    response = await env.client.post("/api/missions", json={"title": "Unsafe", "plan": {
        "tasks": [{"prompt": "Do it", "kind": "video_render"}]}})
    assert response.status_code == 400
    assert (await env.client.get("/api/missions")).json() == []
