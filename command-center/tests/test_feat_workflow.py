"""Feature 16 — Workflow Builder: граф/таймлайн/метрики строятся из реальных строк БД."""
import asyncio

import sqlalchemy as sa

from bcc.db import approvals as approvals_t, tasks as tasks_t, utcnow

from .conftest import FakeAdapter, wait_for
from .helpers import make_stack


async def _mission_with_agent(env, goal="Создать 2 research задачи", **kw):
    stack = await make_stack(env.client)
    m = (await env.client.post("/api/missions", json={
        "title": kw.pop("title", "рабочий процесс"), "goal": goal, **kw})).json()
    async with env.svc.db.session() as s:
        await s.execute(sa.update(tasks_t).where(tasks_t.c.mission_id == m["id"]).values(
            agent_id=stack["agent"]["id"]))
        await s.commit()
    return m, stack


async def test_graph_before_run_is_honest(env):
    env.svc.registry.adapter_factory = lambda mo, p: FakeAdapter("ок")
    m, _ = await _mission_with_agent(env)

    wf = (await env.client.get(f"/api/workflow/missions/{m['id']}")).json()
    ids = [n["id"] for n in wf["graph"]["nodes"]]
    assert "trigger" in ids and "planner" in ids and "report" in ids
    assert sum(1 for i in ids if i.startswith("task:")) == 2
    # раннов не было — ни метрик, ни таймлайна не выдумываем
    assert wf["metrics"]["runs"] == 0
    assert wf["metrics"]["tokens_total"] == 0
    assert wf["timeline"]["rows"] == []
    assert wf["queue"] == []
    # у каждого узла есть посчитанная раскладка
    assert all("x" in n and "y" in n and n["w"] > 0 for n in wf["graph"]["nodes"])
    # ревью-узла нет, пока approval'ов нет
    assert "gate" not in ids


async def test_graph_reflects_real_runs_and_metrics(env):
    env.svc.registry.adapter_factory = lambda mo, p: FakeAdapter("готово", tokens=(7, 3))
    env.svc.engine.poll_interval = 0.02
    m, stack = await _mission_with_agent(env)
    await env.client.post(f"/api/missions/{m['id']}/start")

    worker = asyncio.create_task(env.svc.engine.worker_loop())
    try:
        async def done():
            wf = (await env.client.get(f"/api/workflow/missions/{m['id']}")).json()
            return wf if wf["run"]["tasks_done"] == wf["run"]["tasks_total"] else None
        wf = await wait_for(done, timeout=8.0)
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)

    nodes = {n["id"]: n for n in wf["graph"]["nodes"]}
    # узел модели появился из реального model_alias в task_runs
    assert "model:local-7b" in nodes
    assert nodes["model:local-7b"]["meta"]["runs"] == 2
    assert all(nodes[f"task:{t}"]["status"] == "success"
               for t in [n["meta"]["task_id"] for n in wf["graph"]["nodes"] if n["kind"] == "task"])

    # метрики — суммы реальных раннов (2 задачи × 10 токенов у FakeAdapter)
    assert wf["metrics"]["runs"] == 2
    assert wf["metrics"]["tokens_in"] == 14 and wf["metrics"]["tokens_out"] == 6
    assert wf["metrics"]["tokens_total"] == 20
    assert wf["metrics"]["by_model"][0]["label"] == "local-7b"
    assert abs(wf["metrics"]["by_model"][0]["share"] - 1.0) < 1e-6

    # таймлайн: строка на задачу, сегмент на ранн, внутри общего окна
    assert len(wf["timeline"]["rows"]) == 2
    for row in wf["timeline"]["rows"]:
        for seg in row["segments"]:
            assert 0 <= seg["start_ms"] <= seg["end_ms"] <= wf["timeline"]["span_ms"]
    assert len(wf["queue"]) == 2
    assert wf["run"]["progress"] == 1.0

    # рёбра: от модели к задачам, поток к отчёту
    edges = {(e["source"], e["target"]) for e in wf["graph"]["edges"]}
    assert ("router", "model:local-7b") in edges
    assert any(src == "model:local-7b" and dst.startswith("task:") for src, dst in edges)
    assert ("memory", "report") in edges


async def test_gate_node_appears_only_with_real_approval(env):
    env.svc.registry.adapter_factory = lambda mo, p: FakeAdapter("ок")
    m, _ = await _mission_with_agent(env)
    tasks = (await env.client.get(f"/api/missions/{m['id']}")).json()["tasks"]

    before = (await env.client.get(f"/api/workflow/missions/{m['id']}")).json()
    assert not before["approvals"]

    async with env.svc.db.session() as s:
        await s.execute(sa.insert(approvals_t).values(
            task_id=tasks[0]["id"], kind="review", preview="дифф на проверку",
            status="pending", created_at=utcnow()))
        await s.commit()

    wf = (await env.client.get(f"/api/workflow/missions/{m['id']}")).json()
    gate = next(n for n in wf["graph"]["nodes"] if n["id"] == "gate")
    assert gate["status"] == "waiting" and gate["meta"]["pending"] == 1
    assert len(wf["approvals"]) == 1
    assert wf["approvals"][0]["preview"] == "дифф на проверку"
    assert any(e["source"] == "gate" and e["kind"] == "approved" for e in wf["graph"]["edges"])


async def test_log_endpoint_is_incremental(env):
    env.svc.registry.adapter_factory = lambda mo, p: FakeAdapter("готово")
    env.svc.engine.poll_interval = 0.02
    m, _ = await _mission_with_agent(env, goal="Создать 1 research задачу")
    await env.client.post(f"/api/missions/{m['id']}/start")

    worker = asyncio.create_task(env.svc.engine.worker_loop())
    try:
        async def has_log():
            rows = (await env.client.get(f"/api/workflow/missions/{m['id']}/log")).json()
            return rows if len(rows) >= 2 else None
        rows = await wait_for(has_log, timeout=8.0)
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)

    tail = (await env.client.get(
        f"/api/workflow/missions/{m['id']}/log?after={rows[0]['id']}")).json()
    # Проверяем СВОЙСТВО `after`, а не совпадение длин двух разных снимков:
    # между двумя запросами журнал может дописаться (движок доводит запущенный
    # ран после отмены воркера), и сравнение `len(tail) == len(rows) - 1`
    # падало под полной нагрузкой набора, хотя эндпоинт работал верно.
    assert all(r["id"] > rows[0]["id"] for r in tail)
    seen_after_first = [r["id"] for r in rows[1:]]
    assert [r["id"] for r in tail][:len(seen_after_first)] == seen_after_first


async def test_unknown_mission_is_404(env):
    assert (await env.client.get("/api/workflow/missions/9999")).status_code == 404
