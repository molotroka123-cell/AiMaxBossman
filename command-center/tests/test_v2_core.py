"""V2-core: таблицы, загрузчик фич, хуки engine (контракты §1, §8).

Фичи строятся на этих контрактах — они проверяются до фич.
"""
import sqlalchemy as sa

from bcc import db as dbm
from bcc.features import Feature, load_features
from bcc.permissions import agent_allowed, is_dangerous, needs_approval

from .conftest import FakeAdapter
from .helpers import make_stack


async def _run_once(env):
    """Прогнать очередь до пустоты (worker в тестах выключен)."""
    for _ in range(10):
        run_id = await env.svc.engine.claim()
        if run_id is None:
            return
        await env.svc.engine.execute(run_id)


async def test_v2_tables_created(env):
    async with env.svc.db.session() as s:
        for table in (dbm.missions, dbm.kpi_history, dbm.orchestras, dbm.skills,
                      dbm.skill_versions, dbm.benchmarks, dbm.checkpoints,
                      dbm.session_forks, dbm.resource_reservations,
                      dbm.interventions, dbm.recovery_attempts):
            await s.execute(sa.select(table).limit(1))    # таблицы существуют
        # новые колонки старых таблиц доступны и в metadata, и в БД
        await s.execute(sa.select(dbm.tasks.c.mission_id, dbm.tasks.c.kind).limit(1))
        await s.execute(sa.select(dbm.task_runs.c.route).limit(1))


def test_feature_loader_contract():
    features = load_features()
    assert isinstance(features, list)          # пустой пакет — валидное состояние
    for f in features:
        assert isinstance(f, Feature) and f.name


async def test_checkpoint_rows_written_per_step(env):
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter("ответ")
    stack = await make_stack(env.client)
    await _run_once(env)
    async with env.svc.db.session() as s:
        rows = (await s.execute(sa.select(dbm.checkpoints))).fetchall()
    assert len(rows) == 1
    assert rows[0]._mapping["step"] == 1
    assert rows[0]._mapping["messages"][-1]["role"] == "assistant"


async def test_pick_model_hook_overrides_choice(env):
    used: list[str] = []

    def factory(model, provider):
        return FakeAdapter(f"через {model['alias']}",
                           on_chat=_remember(used, model["alias"]))
    env.svc.registry.adapter_factory = factory
    stack = await make_stack(env.client)
    other = (await env.client.post("/api/models", json={
        "provider_id": stack["provider"]["id"], "name": "fast", "alias": "router-fast"})).json()

    async def pick(task, agent):
        return {"model_id": other["id"], "route": {"reason": "тест-маршрут"}}
    env.svc.engine.add_hook("pick_model", pick)
    await _run_once(env)
    assert used == ["router-fast"]            # выбор агента перекрыт хуком
    async with env.svc.db.session() as s:
        run = (await s.execute(sa.select(dbm.task_runs))).fetchall()[-1]._mapping
    assert run["route"] == {"reason": "тест-маршрут"}


def _remember(used, alias):
    async def on_chat(_call, _messages):
        used.append(alias)
    return on_chat


async def test_before_run_defer_requeues(env):
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter()
    calls = {"n": 0}

    async def gatekeeper(task, run):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"defer": 0.01, "reason": "мало RAM (тест)"}
        return None
    env.svc.engine.add_hook("before_run", gatekeeper)
    stack = await make_stack(env.client)
    await _run_once(env)                       # первый claim отложен
    import asyncio
    await asyncio.sleep(0.05)
    await _run_once(env)                       # второй проходит
    task = (await env.client.get(f"/api/tasks/{stack['task']['id']}")).json()["task"]
    assert task["status"] == "completed"
    assert calls["n"] == 2


async def test_gate_completion_fail_then_pass(env):
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter("код готов")
    # EH-05 (TZ-01 §2.5): FAIL гейта обязан явно сказать, возвращать ли run в очередь
    verdicts = iter([{"verdict": "fail", "feedback": "нет тестов", "requeue": True},
                     {"verdict": "pass"}])

    async def gate(task, run_id, answer):
        return next(verdicts)
    env.svc.engine.add_hook("gate_completion", gate)
    stack = await make_stack(env.client, max_steps=3)
    await _run_once(env)
    task = (await env.client.get(f"/api/tasks/{stack['task']['id']}")).json()
    assert task["task"]["status"] == "completed"
    # фидбек ревьюера дошёл до модели отдельным сообщением
    async with env.svc.db.session() as s:
        run = (await s.execute(sa.select(dbm.task_runs))).fetchall()[-1]._mapping
    contents = [m["content"] for m in run["checkpoint"]["messages"]]
    assert any("нет тестов" in c for c in contents)


def test_permissions_model():
    assert is_dangerous("email.send") and not is_dangerous("email.draft")
    agent = {"permissions": {"email.send": True}}
    assert agent_allowed(agent, "email.send")
    assert needs_approval({"permissions": {}}, "terminal.run")
    assert not needs_approval(agent, "email.send")
