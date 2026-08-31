"""V2.6 Phase 2 (bcc) — адаптивный роутер: classify_reasoning подключён к
pick_model, per-class портфельные метрики консервативны.

Гейт: rules["adaptive"] (OFF по умолчанию — поведение без изменений).
"""
from __future__ import annotations

import pytest
import sqlalchemy as sa

from bcc.db import models as models_t, task_runs as runs_t, tasks as tasks_t
from bcc.features.router import (CLASS_MIN_EPISODES, _candidates, _make_pick_hook,
                                 _save_rules, complexity_features)
from bcc.v2.model_intelligence import classify_reasoning


async def _seed_model(env, alias: str, **kw) -> int:
    from bcc.db import providers as providers_t
    values = dict(alias=alias, kind="local", status="online",
                  context_window=32768, caps={}, price_in=0.0, price_out=0.0,
                  bench={})
    values.update(kw)
    async with env.svc.db.session() as s:
        pid = (await s.execute(sa.insert(providers_t).values(
            name=f"prov-{alias}", kind="openai_compat", base_url="http://127.0.0.1:9"))
        ).inserted_primary_key[0]
        mid = (await s.execute(sa.insert(models_t).values(
            provider_id=pid, name=alias, **values))).inserted_primary_key[0]
        await s.commit()
    return mid


async def _seed_runs(env, alias: str, kind: str, ok: int, fail: int) -> None:
    async with env.svc.db.session() as s:
        for status, n in (("completed", ok), ("failed", fail)):
            for _ in range(n):
                tid = (await s.execute(sa.insert(tasks_t).values(
                    title="t", prompt="p", status="completed", kind=kind))).inserted_primary_key[0]
                await s.execute(sa.insert(runs_t).values(
                    task_id=tid, status=status, attempt=0, model_alias=alias))
        await s.commit()


def test_complexity_features_deterministic_and_classified():
    f1 = complexity_features("посчитай 2+2", {})
    assert classify_reasoning(f1)[0] in ("L0", "L1")
    f2 = complexity_features("удали секретный пароль и задеплой", {"review": True})
    level, reasons = classify_reasoning(f2)
    assert level in ("L3", "L4") and reasons


@pytest.mark.asyncio
async def test_adaptive_off_no_reasoning_in_route(env):
    await _seed_model(env, "local-a")
    hook = await _make_pick_hook(env.svc)
    out = await hook({"id": 999, "prompt": "почини код", "kind": "coding",
                      "meta": {"route": True}}, {})
    if out is not None:                     # маршрут выбран
        assert "reasoning" not in out["route"], \
            "без rules.adaptive поведение прежнее — reasoning не подмешивается"


@pytest.mark.asyncio
async def test_adaptive_on_reasoning_visible_in_route(env):
    await _seed_model(env, "local-b")
    await _save_rules(env.svc, {"adaptive": True, "prefer_local": True})
    hook = await _make_pick_hook(env.svc)
    out = await hook({"id": 998, "prompt": "почини функцию", "kind": "coding",
                      "meta": {"route": True}}, {})
    assert out is not None
    assert out["route"]["reasoning"]["level"] in ("L0", "L1", "L2", "L3", "L4")
    assert out["route"]["reasoning"]["reasons"]


@pytest.mark.asyncio
async def test_per_class_success_used_only_with_enough_episodes(env):
    """Модуль G: per-(alias, kind) метрика включается при n >= CLASS_MIN_EPISODES,
    иначе консервативный fallback на глобальную per-alias."""
    await _seed_model(env, "class-rich")
    await _seed_model(env, "class-poor")
    # class-rich: глобально слабый (кроме класса), в классе coding — идеален (n=6)
    await _seed_runs(env, "class-rich", "generic", ok=1, fail=9)
    await _seed_runs(env, "class-rich", "coding", ok=CLASS_MIN_EPISODES + 1, fail=0)
    # class-poor: в классе coding всего 2 эпизода (мало) — глобальная остаётся
    await _seed_runs(env, "class-poor", "generic", ok=8, fail=2)
    await _seed_runs(env, "class-poor", "coding", ok=0, fail=2)

    rules = {"adaptive": True}
    cands = {c.alias: c for c in await _candidates(env.svc, rules, kind="coding")}
    assert cands["class-rich"].success_rate == pytest.approx(1.0), \
        "достаточная выборка по классу -> класс-метрика"
    # 2 эпизода в классе < CLASS_MIN_EPISODES -> глобальная (8 ok + 2 fail из
    # generic + 2 fail класса = 8/12)
    assert cands["class-poor"].success_rate == pytest.approx(8 / 12), \
        "малая выборка по классу -> консервативный fallback на глобальную"


@pytest.mark.asyncio
async def test_no_adaptive_no_class_query(env):
    """adaptive выключен → success считается только глобально (как раньше)."""
    await _seed_model(env, "plain")
    await _seed_runs(env, "plain", "coding", ok=CLASS_MIN_EPISODES + 1, fail=0)
    await _seed_runs(env, "plain", "generic", ok=0, fail=10)
    cands = {c.alias: c for c in await _candidates(env.svc, {}, kind="coding")}
    # глобальная: 6 ok / 16 всего
    assert cands["plain"].success_rate == pytest.approx(6 / 16)
