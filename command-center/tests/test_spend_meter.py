"""Spend Meter: расход в трёх разрезах и жёсткий потолок.

Запись расхода в тестах создаётся так же, как её создаёт движок, — строкой
task_runs с ценой, моделью и задачей миссии. Своего журнала у модуля нет,
поэтому проверяем именно то, что видит система в бою.
"""
from __future__ import annotations

from datetime import timedelta

import sqlalchemy as sa

from bcc.db import missions as missions_t, task_runs as runs_t, tasks as tasks_t, utcnow
from bcc.features import spend_meter

from .conftest import FakeAdapter
from .helpers import make_stack


async def _mission(env, *, title: str = "миссия", budget: float = 0.0) -> int:
    async with env.svc.db.session() as s:
        res = await s.execute(sa.insert(missions_t).values(
            title=title, goal="", status="queued", cloud_budget_usd=budget,
            created_at=utcnow(), updated_at=utcnow()))
        mid = int(res.inserted_primary_key[0])
        await s.commit()
    return mid


async def _spend(env, *, mission_id: int | None, model: str, usd: float,
                 days_ago: int = 0) -> int:
    """Одна трата = один прогон движка: миссия, модель и сутки в одной строке."""
    ts = utcnow() - timedelta(days=days_ago)
    async with env.svc.db.session() as s:
        res = await s.execute(sa.insert(tasks_t).values(
            title="платный вызов", prompt="p", status="completed", mission_id=mission_id,
            created_at=ts, updated_at=ts))
        task_id = int(res.inserted_primary_key[0])
        await s.execute(sa.insert(runs_t).values(
            task_id=task_id, attempt=0, status="completed", model_alias=model,
            tokens_in=10, tokens_out=5, cost_usd=usd, started_at=ts, finished_at=ts))
        await s.commit()
    return task_id


async def _get(env, path: str = "/api/spend") -> dict:
    return (await env.client.get(path)).json()


async def _check(env, amount: float, mission_id: int | None = None) -> dict:
    body: dict = {"amount_usd": amount}
    if mission_id is not None:
        body["mission_id"] = mission_id
    return (await env.client.post("/api/spend/check", json=body)).json()


async def _drive(env, task_id: int, n: int = 6) -> str:
    for _ in range(n):
        rid = await env.svc.engine.claim()
        if rid is None:
            break
        await env.svc.engine.execute(rid)
    return (await env.client.get(f"/api/tasks/{task_id}")).json()["task"]["status"]


async def test_flag_off_no_accounting_no_admission_no_limit_change(env, monkeypatch):
    """Выключенный флаг: учёта нет, допуск не выдаётся, потолок не меняется,
    а обычный прогон идёт ровно как раньше."""
    monkeypatch.delenv(spend_meter.FLAG, raising=False)
    mid = await _mission(env)
    await _spend(env, mission_id=mid, model="local-7b", usd=0.5)

    report = await _get(env)
    assert report["enabled"] is False and report["spent"] is None and report["limits"] is None

    verdict = await _check(env, 0.01, mid)
    assert verdict["enabled"] is False and verdict["allowed"] is False

    resp = await env.client.post("/api/spend/limit", json={"scope": "daily", "limit_usd": 1.0})
    assert resp.status_code == 409
    assert not (env.settings.data_dir / spend_meter.STATE_FILE).exists()

    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter("готово")
    stack = await make_stack(env.client)
    assert await _drive(env, stack["task"]["id"]) == "completed"


async def test_one_spend_lands_in_all_three_cuts(env, monkeypatch):
    """Каждая трата попадает в миссию, модель и сутки одновременно: суммы всех
    трёх разрезов равны итогу, а записей ровно столько, сколько трат."""
    monkeypatch.setenv(spend_meter.FLAG, "1")
    a, b = await _mission(env, title="A"), await _mission(env, title="B")
    await _spend(env, mission_id=a, model="opus", usd=0.40)
    await _spend(env, mission_id=a, model="haiku", usd=0.10)
    await _spend(env, mission_id=b, model="opus", usd=0.25, days_ago=1)

    spent = (await _get(env))["spent"]
    assert spent["total_usd"] == 0.75 and spent["entries"] == 3

    by_mission = {i["mission_id"]: i["spent_usd"] for i in spent["by_mission"]}
    by_model = {i["model"]: i["spent_usd"] for i in spent["by_model"]}
    by_day = {i["day"]: i["spent_usd"] for i in spent["by_day"]}
    assert by_mission[a] == 0.50 and by_mission[b] == 0.25
    assert by_model["opus"] == 0.65 and by_model["haiku"] == 0.10
    assert by_day[spend_meter.today()] == 0.50 and len(by_day) == 2
    assert spent["today_usd"] == 0.50

    # три разреза — один и тот же расход, а не три разных журнала
    assert round(sum(by_mission.values()), 6) == spent["total_usd"]
    assert round(sum(by_model.values()), 6) == spent["total_usd"]
    assert round(sum(by_day.values()), 6) == spent["total_usd"]
    assert sum(i["entries"] for i in spent["by_mission"]) == 3
    assert sum(i["entries"] for i in spent["by_model"]) == 3
    assert sum(i["entries"] for i in spent["by_day"]) == 3


async def test_admission_flips_exactly_at_the_ceiling(env, monkeypatch):
    """Граница точная: последняя влезающая трата разрешена (остаток ровно 0),
    первая переваливающая — запрещена."""
    monkeypatch.setenv(spend_meter.FLAG, "1")
    mid = await _mission(env)
    assert (await env.client.post("/api/spend/limit",
                                  json={"scope": "mission", "mission_id": mid,
                                        "limit_usd": 1.0})).status_code == 200
    for _ in range(3):
        await _spend(env, mission_id=mid, model="opus", usd=0.30)

    ok = await _check(env, 0.10, mid)
    assert ok["allowed"] is True
    mission_view = next(v for v in ok["limits"] if v["scope"] == "mission")
    assert mission_view["remaining_usd"] == 0.10 and mission_view["remaining_after_usd"] == 0.0

    over = await _check(env, 0.11, mid)
    assert over["allowed"] is False and over["blocking"]["scope"] == "mission"

    # добираем ровно до потолка — дальше отказ на любую трату
    await _spend(env, mission_id=mid, model="opus", usd=0.10)
    exhausted = await _check(env, 0.000001, mid)
    assert exhausted["allowed"] is False
    assert exhausted["blocking"]["remaining_usd"] == 0.0
    assert (await _get(env))["spent"]["by_mission"][0]["spent_usd"] == 1.0


async def test_denial_names_the_limit_and_the_remainder(env, monkeypatch):
    """Отказ называет исчерпанный потолок и остаток, а не просто «нельзя»."""
    monkeypatch.setenv(spend_meter.FLAG, "1")
    mid = await _mission(env)
    await env.client.post("/api/spend/limit", json={"scope": "daily", "limit_usd": 2.0})
    await _spend(env, mission_id=mid, model="opus", usd=1.90)

    denied = await _check(env, 0.50, mid)
    assert denied["allowed"] is False
    assert denied["blocking"]["scope"] == "daily"
    assert denied["blocking"]["limit_usd"] == 2.0
    assert denied["blocking"]["spent_usd"] == 1.90
    assert denied["blocking"]["remaining_usd"] == 0.10
    assert denied["blocking"]["remaining_after_usd"] == -0.40
    assert "суточный потолок исчерпан" in denied["reason"]
    assert "0.100000" in denied["reason"] and "0.500000" in denied["reason"]

    # прогноз показывает край до того, как в него упрутся
    survives = await _check(env, 0.10, mid)
    assert survives["allowed"] is True
    assert next(v for v in survives["limits"]
                if v["scope"] == "daily")["remaining_after_usd"] == 0.0
    assert (await _get(env))["nearest_limit"]["scope"] == "daily"


async def test_check_spends_nothing(env, monkeypatch):
    """Ручка допуска только отвечает: счётчики после неё те же самые."""
    monkeypatch.setenv(spend_meter.FLAG, "1")
    mid = await _mission(env)
    await env.client.post("/api/spend/limit", json={"scope": "mission", "mission_id": mid,
                                                    "limit_usd": 1.0})
    await _spend(env, mission_id=mid, model="opus", usd=0.40)

    before = (await _get(env))["spent"]
    for amount in (0.10, 0.60, 5.0, 0.000001):
        await _check(env, amount, mid)
        await _check(env, amount)
    after = (await _get(env))["spent"]
    assert after == before
    assert after["total_usd"] == 0.40 and after["entries"] == 1


async def test_limit_change_requires_the_flag(env, monkeypatch):
    """Потолок меняет только владелец и только при включённом флаге."""
    monkeypatch.delenv(spend_meter.FLAG, raising=False)
    mid = await _mission(env)
    refused = await env.client.post("/api/spend/limit",
                                    json={"scope": "mission", "mission_id": mid,
                                          "limit_usd": 99.0})
    assert refused.status_code == 409

    monkeypatch.setenv(spend_meter.FLAG, "1")
    limits = (await _get(env))["limits"]
    assert limits["per_mission"] == {}       # отклонённая правка не просочилась
    assert limits["daily_usd"] == spend_meter.DEFAULT_DAILY_USD

    accepted = await env.client.post("/api/spend/limit",
                                     json={"scope": "mission", "mission_id": mid,
                                           "limit_usd": 99.0})
    assert accepted.status_code == 200
    assert (await _get(env))["limits"]["per_mission"] == {str(mid): 99.0}
    assert (await env.client.post("/api/spend/limit",
                                  json={"scope": "mission", "mission_id": mid + 1000,
                                        "limit_usd": 1.0})).status_code == 404


async def test_limit_lowered_below_spent_keeps_accounting_and_denies(env, monkeypatch):
    """Понижение потолка ниже потраченного не ломает учёт: расход остаётся как
    был, остаток честно отрицательный, следующая трата отклонена сразу."""
    monkeypatch.setenv(spend_meter.FLAG, "1")
    mid = await _mission(env)
    await env.client.post("/api/spend/limit", json={"scope": "mission", "mission_id": mid,
                                                    "limit_usd": 5.0})
    await _spend(env, mission_id=mid, model="opus", usd=0.80)
    await _spend(env, mission_id=mid, model="haiku", usd=0.40)
    assert (await _check(env, 1.0, mid))["allowed"] is True

    await env.client.post("/api/spend/limit", json={"scope": "mission", "mission_id": mid,
                                                    "limit_usd": 0.50})
    spent = (await _get(env))["spent"]
    assert spent["total_usd"] == 1.20 and spent["entries"] == 2
    mission_row = next(i for i in spent["by_mission"] if i["mission_id"] == mid)
    assert mission_row["spent_usd"] == 1.20 and mission_row["remaining_usd"] == -0.70

    denied = await _check(env, 0.01, mid)
    assert denied["allowed"] is False and denied["blocking"]["scope"] == "mission"
    assert denied["blocking"]["remaining_usd"] == -0.70


async def test_exhausted_ceiling_stops_the_run_itself(env, monkeypatch):
    """Потолок жёсткий: исчерпан — прогон не стартует, модель не вызывается.
    Система останавливается сама, а не спрашивает разрешения продолжить."""
    monkeypatch.setenv(spend_meter.FLAG, "1")
    await env.client.post("/api/spend/limit", json={"scope": "daily", "limit_usd": 0.0})
    adapter = FakeAdapter("готово")
    env.svc.registry.adapter_factory = lambda m, p: adapter
    stack = await make_stack(env.client)

    assert await _drive(env, stack["task"]["id"]) == "failed"
    assert adapter.calls == 0                # денег не потратили ни на один вызов
    run = (await env.client.get(f"/api/tasks/{stack['task']['id']}")).json()["runs"][-1]
    assert "суточный потолок" in (run["error"] or "")
