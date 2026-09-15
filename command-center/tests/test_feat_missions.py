"""Feature 01+13 — Missions + KPI: план, лимит воркеров, прогресс, KPI, защита."""
import sqlalchemy as sa

from bcc.db import missions as missions_t, tasks as tasks_t

from .conftest import FakeAdapter, wait_for
from .helpers import make_stack


async def _agent(env):
    stack = await make_stack(env.client)
    return stack["agent"]["id"]


async def test_mission_creates_planned_tasks(env):
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter("ок")
    agent_id = await _agent(env)
    m = (await env.client.post("/api/missions", json={
        "title": "Создать 3 research-задачи", "goal": "Создать 3 тестовых research задачи",
        "duration_minutes": 30, "max_workers": 2})).json()
    assert m["status"] == "queued"
    full = (await env.client.get(f"/api/missions/{m['id']}")).json()
    assert len(full["tasks"]) == 3
    # назначим агента задачам, чтобы они исполнялись
    async with env.svc.db.session() as s:
        await s.execute(sa.update(tasks_t).where(tasks_t.c.mission_id == m["id"]).values(
            agent_id=agent_id))
        await s.commit()


async def test_worker_limit_and_progress_and_completion(env):
    import asyncio

    class Slow(FakeAdapter):
        def __init__(self):
            super().__init__("готово")
        async def chat(self, model, messages, **kw):
            await asyncio.sleep(0.3)
            return await super().chat(model, messages, **kw)

    env.svc.registry.adapter_factory = lambda m, p: Slow()
    env.svc.engine.workers = 5           # движок не мешает; лимит держит миссия
    env.svc.engine.poll_interval = 0.02
    agent_id = await _agent(env)
    m = (await env.client.post("/api/missions", json={
        "title": "5 задач", "goal": "Создать 5 research задач", "max_workers": 2})).json()
    async with env.svc.db.session() as s:
        await s.execute(sa.update(tasks_t).where(tasks_t.c.mission_id == m["id"]).values(
            agent_id=agent_id))
        await s.commit()
    await env.client.post(f"/api/missions/{m['id']}/start")

    loop = asyncio.create_task(env.svc.engine.worker_loop())
    tick = asyncio.create_task(_mission_ticker(env))
    max_active = 0
    try:
        async def check():
            nonlocal max_active
            tasks = (await env.client.get(f"/api/missions/{m['id']}")).json()["tasks"]
            active = sum(1 for t in tasks if t["status"] in ("queued", "running"))
            max_active = max(max_active, active)
            mission = (await env.client.get(f"/api/missions/{m['id']}")).json()
            return mission["status"] == "completed"
        await wait_for(check, timeout=15)
    finally:
        # Отменить мало: задача-воркер держит соединение с БД и, не будучи
        # дождавшейся своего CancelledError, доходит до commit уже на
        # закрываемом пуле — закрытие цикла виснет (так в остальных восьми).
        loop.cancel()
        tick.cancel()
        await asyncio.gather(loop, tick, return_exceptions=True)
    assert max_active <= 2, f"миссия запустила {max_active} задач сразу при лимите 2"
    mission = (await env.client.get(f"/api/missions/{m['id']}")).json()
    assert mission["status"] == "completed" and mission["progress"] == 1.0


async def _mission_ticker(env):
    import asyncio
    from bcc.features.missions import _tick
    while True:
        await _tick(env.svc)
        await asyncio.sleep(0.1)


async def test_kpi_apply_history_and_progress(env):
    m = (await env.client.post("/api/missions", json={
        "title": "Sales", "goal": "Test Sales Pipeline",
        "kpi_targets": {"analyzed": 10, "qualified": 3, "offers": 1}})).json()
    await env.client.post(f"/api/missions/{m['id']}/kpi", json={"key": "analyzed", "delta": 4})
    await env.client.post(f"/api/missions/{m['id']}/kpi", json={"key": "qualified", "delta": 1})
    r = (await env.client.post(f"/api/missions/{m['id']}/kpi", json={"key": "offers", "delta": 1})).json()
    kpi = (await env.client.get(f"/api/missions/{m['id']}/kpi")).json()
    assert kpi["current"] == {"analyzed": 4.0, "qualified": 1.0, "offers": 1.0}
    # progress = среднее (4/10 + 1/3 + 1/1)/3
    assert abs(kpi["progress"] - round((0.4 + 1/3 + 1.0) / 3, 4)) < 1e-3
    hist = (await env.client.get(f"/api/missions/{m['id']}/kpi/history?key=analyzed")).json()
    assert len(hist) == 1 and hist[0]["delta"] == 4


async def test_kpi_rejects_foreign_task(env):
    m1 = (await env.client.post("/api/missions", json={
        "title": "M1", "goal": "g", "kpi_targets": {"x": 5}})).json()
    m2 = (await env.client.post("/api/missions", json={"title": "M2", "goal": "g2"})).json()
    foreign = (await env.client.get(f"/api/missions/{m2['id']}")).json()["tasks"][0]
    r = await env.client.post(f"/api/missions/{m1['id']}/kpi",
                              json={"key": "x", "delta": 1, "source_task_id": foreign["id"]})
    assert r.status_code == 409


async def test_kpi_missing_mission_404(env):
    r = await env.client.post("/api/missions/99999/kpi", json={"key": "x", "delta": 1})
    assert r.status_code == 404


async def test_a_fully_blocked_mission_stops_instead_of_claiming_to_run(env):
    """Заблокированная миссия обязана прийти к исходу, а не «выполняться» вечно.

    `blocked` не попадал ни в active, ни в done, ни в failed, поэтому миссия, все
    задачи которой упёрлись в недоступного исполнителя, оставалась `running`:
    интерфейс показывал «выполняется» и тикающий таймер, хотя не стартовал ни
    один прогон. Владелец видел работу там, где её не было.
    """
    from bcc.features.missions import _tick

    m = (await env.client.post("/api/missions", json={
        "title": "Миссия без исполнителя", "goal": "Создать 3 тестовых research задачи",
        "duration_minutes": 30, "max_workers": 2})).json()
    async with env.svc.db.session() as s:
        await s.execute(sa.update(missions_t).where(missions_t.c.id == m["id"]).values(
            status="running"))
        await s.execute(sa.update(tasks_t).where(tasks_t.c.mission_id == m["id"]).values(
            status="blocked",
            meta={"reason_code": "BLOCKED_CAPABILITY_UNAVAILABLE",
                  "blocked_reason": "Исполнитель не выбран или недоступен."}))
        await s.commit()

    await _tick(env.svc)

    async with env.svc.db.session() as s:
        after = (await s.execute(sa.select(missions_t)
                                 .where(missions_t.c.id == m["id"]))).first()._mapping
    assert after["status"] != "running", "миссия всё ещё выдаёт себя за выполняющуюся"
    assert after["status"] == "failed"
    # Причина названа И СОХРАНЕНА: она должна пережить перезагрузку страницы,
    # а не жить только в шине событий, которую владелец мог не слушать.
    assert "заблокированы" in str((after["meta"] or {}).get("finish_reason", ""))
    assert "Исполнитель" in str((after["meta"] or {}).get("finish_reason", ""))


async def test_a_mission_with_work_left_is_not_stopped_by_the_blocked_rule(env):
    """Негативный контроль: пока есть что двигать, миссия не останавливается.

    Правило обязано ловить тупик, а не любое присутствие заблокированной задачи:
    иначе одна упавшая в блок задача убивала бы миссию, у которой остальные
    прекрасно идут.
    """
    from bcc.features.missions import _tick

    env.svc.registry.adapter_factory = lambda mo, p: FakeAdapter("ок")
    m = (await env.client.post("/api/missions", json={
        "title": "Смешанная миссия", "goal": "Создать 3 тестовых research задачи",
        "duration_minutes": 30, "max_workers": 2})).json()
    async with env.svc.db.session() as s:
        rows = (await s.execute(sa.select(tasks_t.c.id)
                                .where(tasks_t.c.mission_id == m["id"])
                                .order_by(tasks_t.c.id))).fetchall()
        ids = [int(r[0]) for r in rows]
        await s.execute(sa.update(missions_t).where(missions_t.c.id == m["id"]).values(
            status="running"))
        await s.execute(sa.update(tasks_t).where(tasks_t.c.id == ids[0]).values(
            status="blocked", meta={"blocked_reason": "нет исполнителя"}))
        await s.execute(sa.update(tasks_t).where(tasks_t.c.id == ids[1]).values(
            status="running"))
        await s.commit()

    await _tick(env.svc)

    async with env.svc.db.session() as s:
        after = (await s.execute(sa.select(missions_t)
                                 .where(missions_t.c.id == m["id"]))).first()._mapping
    assert after["status"] == "running", "миссия остановлена, хотя работа ещё шла"


async def test_malformed_kpi_event_does_not_kill_subscription(env):
    mission = (await env.client.post("/api/missions", json={
        "title": "KPI event isolation", "goal": "one task", "kpi_targets": {"done": 2}})).json()
    task = (await env.client.get(f"/api/missions/{mission['id']}")).json()["tasks"][0]
    watcher = next(task for task in env.svc._tasks if task.get_name() == "bcc-mission-kpi")
    async with env.svc.db.session() as session:
        await session.execute(sa.update(tasks_t).where(tasks_t.c.id == task["id"]).values(
            meta={"kpi_key": "done", "kpi_delta": "malformed"}))
        await session.commit()
    await env.svc.bus.emit("task.completed", task_id=task["id"])
    async def rejected():
        return watcher.done() or any(event["kind"] == "worker.error" for event in await env.svc.bus.recent())
    await wait_for(rejected)
    assert not watcher.done(), "one malformed delta terminated every later KPI update"
    async with env.svc.db.session() as session:
        await session.execute(sa.update(tasks_t).where(tasks_t.c.id == task["id"]).values(
            meta={"kpi_key": "done", "kpi_delta": 1}))
        await session.commit()
    await env.svc.bus.emit("task.completed", task_id=task["id"])
    async def advanced():
        current = (await env.client.get(f"/api/missions/{mission['id']}/kpi")).json()["current"]
        return current == {"done": 1}
    await wait_for(advanced)
