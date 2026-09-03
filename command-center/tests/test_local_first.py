"""Local-first: локальная модель — первый исполнитель, облако — апелляция.

Тесты проверяют слой решения и его журнал (вызовов моделей здесь нет и не
должно быть): что вердикт всегда объяснён, что дорогая ошибка понижает планку,
что недоступность локального исполнителя не превращается в тихое поднятие и что
статистика считает ровно записанное.
"""
from __future__ import annotations

import pytest

from bcc.features import local_first as lf


async def _decide(env, **body) -> dict:
    r = await env.client.post("/api/local-first/decide", json=body)
    assert r.status_code == 200, r.text
    return r.json()["decision"]


async def test_low_uncertainty_local_high_uncertainty_cloud_with_reasons(env, monkeypatch):
    """Низкая неопределённость — local, высокая — cloud; в обоих случаях есть
    непустая причина и порог, по которому решение вынесено."""
    monkeypatch.setenv(lf.FLAG, "1")
    low = await _decide(env, kind="refactor", uncertainty=0.10, error_cost=0.1)
    high = await _decide(env, kind="refactor", uncertainty=0.90, error_cost=0.1)

    assert low["verdict"] == "local" and high["verdict"] == "cloud"
    for d in (low, high):
        assert d["reason"].strip()
        assert d["threshold"] == pytest.approx(lf.Thresholds().uncertainty)
    assert high["rule"] == lf.RULE_UNCERTAINTY


async def test_expensive_error_lowers_the_bar(env, monkeypatch):
    """Одно и то же число неопределённости даёт разный вердикт: при дорогой
    ошибке — cloud, при дешёвой — local. Это и доказывает, что правило работает."""
    monkeypatch.setenv(lf.FLAG, "1")
    same = 0.45                       # между high_stakes_uncertainty и uncertainty
    expensive = await _decide(env, kind="migration", uncertainty=same, error_cost=0.95)
    cheap = await _decide(env, kind="migration", uncertainty=same, error_cost=0.05)

    assert expensive["verdict"] == "cloud" and cheap["verdict"] == "local"
    assert expensive["rule"] == lf.RULE_HIGH_STAKES
    assert expensive["threshold"] < cheap["threshold"]
    assert "цена ошибки" in expensive["reason"]
    # то же правило проверяется напрямую: оно вынесено, а не спрятано в формуле
    th = lf.Thresholds()
    assert lf.effective_threshold(
        lf.DecisionRequest(kind="migration", uncertainty=same, error_cost=0.95), th) == (
        lf.RULE_HIGH_STAKES, th.high_stakes_uncertainty)
    assert lf.effective_threshold(
        lf.DecisionRequest(kind="migration", uncertainty=same, error_cost=0.05), th) == (
        lf.RULE_UNCERTAINTY, th.uncertainty)


async def test_missing_local_executor_is_never_a_silent_escalation(env, monkeypatch):
    """Флаг выключен — решение не выносится совсем (409, ни одной записи в
    журнале). Флаг включён, но причины для облака нет — отказ, а не поднятие."""
    monkeypatch.delenv(lf.FLAG, raising=False)
    r = await env.client.post("/api/local-first/decide",
                              json={"kind": "coding", "uncertainty": 0.1,
                                    "error_cost": 0.1, "local_available": False})
    assert r.status_code == 409
    state = (await env.client.get("/api/local-first")).json()
    assert state["enabled"] is False and state["stats"]["total"] == 0
    assert (await env.client.get("/api/local-first/decisions")).json()["decisions"] == []

    monkeypatch.setenv(lf.FLAG, "1")
    refused = await _decide(env, kind="coding", uncertainty=0.1, error_cost=0.1,
                            local_available=False)
    assert refused["verdict"] == "refuse" and refused["reason"].strip()
    # а при измеримой причине поднятие допустимо и при отсутствии локального
    escalated = await _decide(env, kind="coding", uncertainty=0.99, error_cost=0.1,
                              local_available=False)
    assert escalated["verdict"] == "cloud"


def test_decision_without_reason_cannot_be_created():
    """Вердикт без причины и с неизвестным вердиктом не создаётся конструктивно."""
    fields = dict(rule=lf.RULE_LOCAL_FIRST, threshold=0.6, kind="coding",
                  uncertainty=0.1, error_cost=0.0, local_available=True)
    with pytest.raises(ValueError):
        lf.Decision(verdict="local", reason="   ", **fields)
    with pytest.raises(ValueError):
        lf.Decision(verdict="maybe", reason="потому что", **fields)
    assert lf.Decision(verdict="local", reason="потому что", **fields).verdict == "local"
    with pytest.raises(ValueError):
        lf.DecisionRequest(kind="coding", uncertainty=1.5)


async def test_thresholds_are_data_and_change_requires_the_flag(env, monkeypatch):
    """При выключенном флаге изменение порогов отклоняется и ничего не меняет;
    при включённом — новый порог меняет вердикт для того же запроса."""
    monkeypatch.delenv(lf.FLAG, raising=False)
    before = (await env.client.get("/api/local-first")).json()["thresholds"]
    r = await env.client.post("/api/local-first/thresholds", json={"uncertainty": 0.2})
    assert r.status_code == 409
    assert (await env.client.get("/api/local-first")).json()["thresholds"] == before

    monkeypatch.setenv(lf.FLAG, "1")
    assert (await _decide(env, kind="docs", uncertainty=0.3, error_cost=0.0))["verdict"] == "local"
    ok = await env.client.post("/api/local-first/thresholds",
                               json={"uncertainty": 0.2, "high_stakes_uncertainty": 0.1})
    assert ok.status_code == 200 and ok.json()["thresholds"]["uncertainty"] == 0.2
    after = await _decide(env, kind="docs", uncertainty=0.3, error_cost=0.0)
    assert after["verdict"] == "cloud" and after["threshold"] == 0.2
    # порог «дорогой ошибки» обязан оставаться не выше обычного
    bad = await env.client.post("/api/local-first/thresholds",
                                json={"high_stakes_uncertainty": 0.9})
    assert bad.status_code == 400


async def test_stats_count_exactly_what_was_journalled(env, monkeypatch):
    """Статистика причин считает записанные решения: две обычные эскалации,
    одна по дорогой ошибке, одна локальная."""
    monkeypatch.setenv(lf.FLAG, "1")
    await _decide(env, kind="coding", uncertainty=0.95, error_cost=0.1)
    await _decide(env, kind="coding", uncertainty=0.80, error_cost=0.1)
    await _decide(env, kind="deploy", uncertainty=0.40, error_cost=0.9)
    await _decide(env, kind="docs", uncertainty=0.05, error_cost=0.0)

    stats = (await env.client.get("/api/local-first")).json()["stats"]
    assert stats == {"total": 4, "local": 1, "cloud": 3, "refuse": 0,
                     "escalation_reasons": [{"rule": lf.RULE_UNCERTAINTY, "count": 2},
                                            {"rule": lf.RULE_HIGH_STAKES, "count": 1}],
                     "escalation_kinds": [{"kind": "coding", "count": 2},
                                          {"kind": "deploy", "count": 1}]}

    journalled = (await env.client.get("/api/local-first/decisions?limit=2")).json()["decisions"]
    assert len(journalled) == 2 and journalled[0]["kind"] == "docs"
    assert all(d["reason"].strip() and d["ts"] for d in journalled)
    # то же самое видно и на шине — журнал не отдельное хранилище
    bus = [e for e in await env.svc.bus.recent(50) if e["kind"] == lf.EVENT_KIND]
    assert len(bus) == 4
