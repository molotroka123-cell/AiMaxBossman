"""Провенанс показателей: паспорт обязателен, цифры сходятся с базой."""
from datetime import timedelta

import pytest
import sqlalchemy as sa

from bcc.db import approvals as approvals_t, task_runs as runs_t, tasks as tasks_t, utcnow
from bcc.features import provenance as P


async def _insert_task(svc, *, status: str) -> int:
    async with svc.db.session() as s:
        res = await s.execute(sa.insert(tasks_t).values(
            title="провенанс", prompt="посчитай 2+2", status=status))
        await s.commit()
    return int(res.inserted_primary_key[0])


# ---------- конструкция факта отказывает без паспорта ----------

def test_fact_without_source_rejected():
    """Без source факт не создаётся: цифра, о которой нельзя сказать «откуда»,
    не должна доходить до интерфейса даже как объект."""
    with pytest.raises(P.ProvenanceError) as exc:
        P.Fact(value=7, key="x", method="db_query", observed_at=utcnow())
    assert "source" in str(exc.value)


def test_fact_without_method_rejected():
    """Без method (и с методом вне закрытого списка) — отказ."""
    with pytest.raises(P.ProvenanceError):
        P.Fact(value=7, key="x", source="table:tasks", observed_at=utcnow())
    with pytest.raises(P.ProvenanceError):
        P.Fact(value=7, key="x", source="table:tasks", method="просто так",
               observed_at=utcnow())


def test_fact_without_observed_at_rejected():
    """Без времени наблюдения — отказ: свежую цифру не отличить от вчерашней."""
    with pytest.raises(P.ProvenanceError):
        P.Fact(value=7, key="x", source="table:tasks", method="db_query")
    with pytest.raises(P.ProvenanceError):
        P.Fact(value=7, key="x", source="table:tasks", method="db_query",
               observed_at="2026-09-03T00:00:00")     # строка — не наблюдение


def test_fact_with_full_passport_is_created():
    """Полный паспорт — факт создаётся и сериализуется целиком."""
    fact = P.Fact(value=7, key="x", source="table:tasks", method="db_query",
                  observed_at=utcnow(), confidence=0.5)
    assert fact.as_dict()["confidence"] == 0.5
    assert set(fact.as_dict()) == {"key", "value", "source", "method", "observed_at",
                                   "computed_from", "confidence"}


def test_computed_fact_requires_computed_from():
    """method=computed без computed_from — обрыв цепочки, отказ."""
    with pytest.raises(P.ProvenanceError):
        P.Fact(value=1, key="d", source="derived", method="computed", observed_at=utcnow())


def test_confidence_out_of_range_rejected():
    with pytest.raises(P.ProvenanceError):
        P.Fact(value=1, key="x", source="s", method="config", observed_at=utcnow(),
               confidence=1.5)


# ---------- реестр ----------

def test_register_replaces_same_key():
    """Повторная регистрация ключа заменяет производителя, а не плодит дубли."""
    async def first(svc):
        return P.Fact(value=1, key="t.probe", source="s", method="config",
                      observed_at=utcnow())

    async def second(svc):
        return P.Fact(value=2, key="t.probe", source="s", method="config",
                      observed_at=utcnow())

    try:
        P.register("t.probe", first, description="первый")
        P.register("t.probe", second, description="второй")
        assert P.known_keys().count("t.probe") == 1
        assert [c for c in P.catalog() if c["key"] == "t.probe"][0]["description"] == "второй"
    finally:
        P.unregister("t.probe")


async def test_catalog_does_not_compute_values(env):
    """GET /provenance перечисляет ключи, не вызывая производителей: описание
    показателя не должно стоить обхода базы."""
    async def explode(svc):
        raise AssertionError("производитель вызван на листинге")

    P.register("t.explode", explode, description="ловушка")
    try:
        r = await env.client.get("/api/provenance")
        assert r.status_code == 200
        body = r.json()
        assert "t.explode" in [k["key"] for k in body["keys"]]
        assert body["count"] == len(body["keys"])
    finally:
        P.unregister("t.explode")


# ---------- ручки ----------

async def test_known_key_returns_non_empty_passport(env):
    """Значение известного ключа приходит с непустым паспортом целиком."""
    r = await env.client.get("/api/provenance/tasks.total")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["value"], int)
    assert body["source"].startswith("table:tasks")
    assert body["method"] == "db_query"
    assert body["observed_at"] and len(body["observed_at"]) >= len("2026-01-01T00:00:00")
    assert 0.0 <= body["confidence"] <= 1.0


async def test_unknown_key_gives_404(env):
    r = await env.client.get("/api/provenance/нет.такого")
    assert r.status_code == 404
    detail = r.json()["error"]     # единый формат ошибок api.py
    assert "не зарегистрирован" in detail["message"]
    assert "tasks.total" in detail["known"]


async def test_flag_off_is_reported_honestly(env, monkeypatch):
    """Слой выключен по умолчанию и честно говорит об этом; включение видно там же."""
    monkeypatch.delenv(P.FLAG, raising=False)
    assert (await env.client.get("/api/provenance")).json()["enabled"] is False
    monkeypatch.setenv(P.FLAG, "1")
    assert (await env.client.get("/api/provenance")).json()["enabled"] is True


# ---------- производный показатель ----------

async def test_derived_fact_names_resolvable_sources(env):
    """Производный показатель называет ключи, из которых выведен, и каждый из
    них сам разрешим в реестре — цепочка происхождения глубже одного шага."""
    r = await env.client.get("/api/provenance/tasks.completion_ratio")
    assert r.status_code == 200
    body = r.json()
    assert body["method"] == "computed"
    assert set(body["computed_from"]) == {"tasks.completed", "tasks.total"}
    for key in body["computed_from"]:
        parent = await env.client.get(f"/api/provenance/{key}")
        assert parent.status_code == 200
        assert parent.json()["source"] and parent.json()["method"] == "db_query"


async def test_derived_value_matches_its_sources(env):
    """Доля считается ровно из тех фактов, что названы в computed_from."""
    await _insert_task(env.svc, status="completed")
    await _insert_task(env.svc, status="failed")
    total = (await env.client.get("/api/provenance/tasks.total")).json()["value"]
    done = (await env.client.get("/api/provenance/tasks.completed")).json()["value"]
    ratio = (await env.client.get("/api/provenance/tasks.completion_ratio")).json()["value"]
    assert total == 2 and done == 1
    assert ratio == pytest.approx(done / total)


# ---------- цифры совпадают с реальным состоянием базы ----------

async def test_counts_follow_real_db_state(env):
    """Показатели меняются вслед за строками, созданными через env.svc."""
    before = {k: (await env.client.get(f"/api/provenance/{k}")).json()["value"]
              for k in ("tasks.total", "tasks.completed", "runs.last_24h",
                        "approvals.pending")}

    task_id = await _insert_task(env.svc, status="completed")
    await _insert_task(env.svc, status="queued")
    async with env.svc.db.session() as s:
        await s.execute(sa.insert(runs_t).values(task_id=task_id, attempt=0,
                                                 status="completed", started_at=utcnow()))
        # старый запуск за пределами суточного окна не должен попасть в счёт
        await s.execute(sa.insert(runs_t).values(
            task_id=task_id, attempt=1, status="completed",
            started_at=utcnow() - timedelta(hours=48)))
        await s.execute(sa.insert(approvals_t).values(task_id=task_id, kind="review_escalation",
                                                      status="pending"))
        await s.commit()

    after = {k: (await env.client.get(f"/api/provenance/{k}")).json()["value"]
             for k in ("tasks.total", "tasks.completed", "runs.last_24h", "approvals.pending")}
    assert after["tasks.total"] - before["tasks.total"] == 2
    assert after["tasks.completed"] - before["tasks.completed"] == 1
    assert after["runs.last_24h"] - before["runs.last_24h"] == 1     # только свежий
    assert after["approvals.pending"] - before["approvals.pending"] == 1

    # и это ровно то, что лежит в таблицах
    async with env.svc.db.session() as s:
        rows = (await s.execute(sa.select(sa.func.count()).select_from(tasks_t))).scalar_one()
    assert after["tasks.total"] == rows


async def test_producer_returning_bare_number_is_refused(env):
    """Производитель обязан вернуть Fact: голое число реестр не пропускает."""
    async def bare(svc):
        return 42

    P.register("t.bare", bare)
    try:
        with pytest.raises(P.ProvenanceError):
            await P.resolve("t.bare", env.svc)
    finally:
        P.unregister("t.bare")
