"""Feature 12 Resource Brain + 05 Replay/Fork + 14 Self-Healing."""
import sqlalchemy as sa

from bcc.db import models as models_t
from bcc.v2.resource_brain import Reservation, ResourceSnapshot, plan_memory

from .conftest import FakeAdapter
from .helpers import make_stack


# ---------- Resource Brain ----------

def test_plan_starts_when_budget_available():
    snap = ResourceSnapshot(total_memory_mb=100000, used_system_mb=10000, reserve_floor_mb=16000)
    plan = plan_memory(snap, 30000, policy="balanced")
    assert plan.allowed and plan.action == "start"


def test_balanced_queues_when_insufficient():
    snap = ResourceSnapshot(total_memory_mb=100000, used_system_mb=10000, reserve_floor_mb=16000,
                            reservations=[Reservation("A", 55000)])
    plan = plan_memory(snap, 65000, policy="balanced")
    assert not plan.allowed and plan.action == "queue_or_ask"


def test_performance_unloads_idle():
    snap = ResourceSnapshot(total_memory_mb=100000, used_system_mb=10000, reserve_floor_mb=16000,
                            reservations=[Reservation("idleA", 55000, idle=True)])
    plan = plan_memory(snap, 65000, policy="performance")
    assert plan.allowed and "idleA" in plan.unload


def test_low_power_avoids_replacement():
    snap = ResourceSnapshot(total_memory_mb=100000, used_system_mb=10000, reserve_floor_mb=16000,
                            reservations=[Reservation("idleA", 55000, idle=True)])
    plan = plan_memory(snap, 65000, policy="low_power")
    assert not plan.allowed and plan.action == "queue"


async def test_reserve_and_release_around_run(env):
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter("ок")
    # включаем управление памятью (enforce) + большой total, чтобы задача прошла и создала резерв
    await env.client.post("/api/resources/policy",
                          json={"total_override_mb": 200000, "enforce": True})
    stack = await make_stack(env.client)
    # у модели bench с ram_mb, чтобы оценка была ненулевой
    async with env.svc.db.session() as s:
        await s.execute(sa.update(models_t).where(models_t.c.id == stack["model"]["id"]).values(
            bench={"ram_mb": 4000}))
        await s.commit()
    for _ in range(6):
        rid = await env.svc.engine.claim()
        if rid is None:
            break
        await env.svc.engine.execute(rid)
    res = (await env.client.get("/api/resources")).json()
    # после завершения задачи её резерв освобождён (held пуст)
    assert all(r["holder_kind"] != "task" for r in res["reservations"])


async def test_resource_manual_reserve_release(env):
    r = (await env.client.post("/api/resources/reserve",
                               json={"owner_kind": "model", "owner_id": 1, "memory_mb": 5000})).json()
    res = (await env.client.get("/api/resources")).json()
    assert res["reserved_mb"] >= 5000
    await env.client.post("/api/resources/release", json={"reservation_id": r["reservation_id"]})
    res2 = (await env.client.get("/api/resources")).json()
    assert res2["reserved_mb"] < res["reserved_mb"]


# ---------- Replay / Fork ----------

async def test_fork_creates_independent_run_with_lineage(env):
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter("исходный ответ")
    stack = await make_stack(env.client)   # run_now=True уже поставил run в очередь
    for _ in range(4):
        rid = await env.svc.engine.claim()
        if rid is None:
            break
        await env.svc.engine.execute(rid)
    data = (await env.client.get(f"/api/tasks/{stack['task']['id']}")).json()
    run_id = data["runs"][-1]["id"]
    cps = (await env.client.get(f"/api/runs/{run_id}/checkpoints")).json()
    assert cps, "чекпоинты не записались"

    fork = (await env.client.post(f"/api/runs/{run_id}/fork",
                                  json={"checkpoint_id": cps[-1]["id"],
                                        "instruction": "теперь сделай иначе"})).json()
    # выполнить форк
    for _ in range(4):
        rid = await env.svc.engine.claim()
        if rid is None:
            break
        await env.svc.engine.execute(rid)
    # оригинал не изменился
    orig = (await env.client.get(f"/api/tasks/{stack['task']['id']}")).json()["task"]
    assert orig["status"] == "completed"
    # инструкция форка попала в сообщения нового прогона
    async with env.svc.db.session() as s:
        from bcc.db import task_runs as runs_t
        new_run = (await s.execute(sa.select(runs_t.c.checkpoint).where(
            runs_t.c.id == fork["new_run_id"]))).scalar_one()
    contents = [m["content"] for m in new_run["messages"]]
    assert any("теперь сделай иначе" in c for c in contents)
    # lineage: оригинал → форк
    lineage = (await env.client.get(f"/api/forks?task_id={stack['task']['id']}")).json()
    assert lineage["forks"] and lineage["forks"][0]["id"] == fork["new_task_id"]


# ---------- Self-Healing ----------

async def test_healing_report_escalates_after_limit(env):
    await env.client.patch("/api/healing/rules", json={"attempt_limit": 3, "window_seconds": 300})
    statuses = []
    for _ in range(4):
        r = (await env.client.post("/api/healing/report",
                                   json={"target_kind": "browser", "failure": "crash"})).json()
        statuses.append(r["status"])
    assert statuses[-1] == "escalated"
    approvals = (await env.client.get("/api/approvals?status=pending")).json()
    assert any(a["kind"] == "healing_escalation" for a in approvals)
    attempts = (await env.client.get("/api/healing/attempts")).json()
    assert any(a["status"] == "escalated" for a in attempts)
