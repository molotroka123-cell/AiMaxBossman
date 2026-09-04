"""Жёсткий потолок платной работы Fable: 3.00 USD на всё, без исключений.

Ни один тест здесь не ходит в сеть и не делает платных вызовов: внутренний
адаптер подменён счётчиком, который сеть не трогает вовсе. Именно этот счётчик
и есть главная проверка — «отказали ДО вызова» значит, что счётчик остался
нулём, а не что в ответе было написано слово «отказ».
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import sqlalchemy as sa

from bcc import fable_cap
from bcc.db import models as models_t, providers as providers_t
from bcc.providers import ChatResult, Health, ProviderError

pytestmark = pytest.mark.skipif(not fable_cap.LEDGER_AVAILABLE,
                                reason=f"общий журнал потолка недоступен: {fable_cap.LEDGER_PROBLEM}")

PAID_MODEL = "claude-sonnet-4-5"
CLOUD = "https://api.anthropic.com"


class CountingAdapter:
    """Считает вызовы и НЕ ходит в сеть. Ноль здесь = денег не потратили."""

    def __init__(self, *, result: ChatResult | None = None, raises: BaseException | None = None):
        self.calls = 0
        self.result = result or ChatResult(text="ответ", tokens_in=1000, tokens_out=500,
                                           model=PAID_MODEL)
        self.raises = raises
        self.hold: asyncio.Event | None = None      # держит вызов в полёте

    async def chat(self, model: str, messages: list[dict], **kw) -> ChatResult:
        self.calls += 1
        if self.hold is not None:
            await self.hold.wait()
        if self.raises is not None:
            raise self.raises
        return self.result

    async def health(self) -> Health:
        return Health(status="ok")

    async def list_models(self) -> list[str]:
        return [PAID_MODEL]


async def seed_paid_model(env, *, name: str = PAID_MODEL, base_url: str = CLOUD,
                          kind: str = "anthropic") -> int:
    async with env.svc.db.session() as s:
        pid = (await s.execute(sa.insert(providers_t).values(
            name=f"prov-{name}-{base_url}", kind=kind, base_url=base_url))).inserted_primary_key[0]
        mid = (await s.execute(sa.insert(models_t).values(
            provider_id=pid, name=name, alias=f"alias-{name}-{int(pid)}", kind="cloud",
            status="online", context_window=200000, caps={}, price_in=0.0, price_out=0.0,
            bench={}))).inserted_primary_key[0]
        await s.commit()
    return int(mid)


def ledger():
    from bossman_shared.fable_budget import canonical_budget
    return canonical_budget()


def burn(amount: float) -> None:
    """Занять часть потолка так, как его занимает настоящая работа."""
    b = ledger()
    rid = b.reserve(amount, purpose="уже потрачено")
    b.commit(rid, amount, request_id="req-прошлый")


# ---------------------------------------------------------------- отказ до вызова

async def test_a_request_that_does_not_fit_never_reaches_the_adapter(env):
    """2.95 уже потрачено, худший случай не влезает в оставшиеся 0.05 —
    адаптер не вызывается ВООБЩЕ. Проверяется счётчик, а не текст ошибки."""
    burn(2.95)
    inner = CountingAdapter()
    env.svc.registry.adapter_factory = lambda m, p: inner
    model_id = await seed_paid_model(env)
    adapter, model = await env.svc.registry.adapter_for(model_id)

    big = [{"role": "user", "content": "я" * 200_000}]     # худший случай заведомо > 0.05
    with pytest.raises(fable_cap.BudgetRefused):
        await adapter.chat(model["name"], big, max_tokens=4096)

    assert inner.calls == 0, "запрос ушёл в адаптер, хотя потолок его не пускал"
    assert ledger().remaining() == pytest.approx(0.05, abs=1e-6)


async def test_unknown_model_price_is_refused_before_the_adapter(env):
    """Цены нет — оценить консервативно нечем, значит тратить нельзя."""
    inner = CountingAdapter()
    env.svc.registry.adapter_factory = lambda m, p: inner
    model_id = await seed_paid_model(env, name="claude-выдуманная-9")
    adapter, model = await env.svc.registry.adapter_for(model_id)

    with pytest.raises(fable_cap.BudgetRefused) as exc:
        await adapter.chat(model["name"], [{"role": "user", "content": "привет"}])
    assert "unknown model" in str(exc.value)
    assert inner.calls == 0, "модель без цены дошла до адаптера"


async def test_a_local_anthropic_endpoint_is_not_capped(env):
    """Местный endpoint денег не стоит: резервировать под него нечего, и
    потолок он не занимает."""
    inner = CountingAdapter()
    env.svc.registry.adapter_factory = lambda m, p: inner
    model_id = await seed_paid_model(env, base_url="http://127.0.0.1:8080")
    adapter, model = await env.svc.registry.adapter_for(model_id)

    await adapter.chat(model["name"], [{"role": "user", "content": "привет"}])
    assert inner.calls == 1
    assert ledger().remaining() == pytest.approx(3.0)


# ---------------------------------------------------------------- сверка исхода

async def test_a_successful_call_settles_at_the_reported_price(env):
    """Резерв под худший случай заменяется отчётом провайдера — но по прайсу
    из кода, а не по цене, записанной рядом с моделью в базе."""
    inner = CountingAdapter(result=ChatResult(text="ок", tokens_in=1_000_000,
                                              tokens_out=100_000, model=PAID_MODEL))
    env.svc.registry.adapter_factory = lambda m, p: inner
    model_id = await seed_paid_model(env)
    adapter, model = await env.svc.registry.adapter_for(model_id)

    await adapter.chat(model["name"], [{"role": "user", "content": "привет"}], max_tokens=100_000)
    assert inner.calls == 1
    # 1M входных по 3.0 + 100k выходных по 15.0 = 3.0 + 1.5 = 4.5 — больше резерва,
    # поэтому списывается ровно зарезервированное: списать больше, чем удержано,
    # нельзя, иначе потолок пробивается «отчётом».
    b = ledger()
    assert b.remaining() < 3.0
    assert round(b._committed_total() + b._hold_total(), 6) <= 3.0


async def test_a_broken_call_keeps_its_money_held(env):
    """Обрыв связи, отмена и таймаут оставляют резерв висеть: провайдер мог
    списать деньги, и «наверное, не списал» — не основание их вернуть."""
    for boom in (ProviderError("сеть недоступна", kind="network"),
                 asyncio.CancelledError(),
                 asyncio.TimeoutError()):
        before = ledger().remaining()
        inner = CountingAdapter(raises=boom)
        env.svc.registry.adapter_factory = lambda m, p: inner
        model_id = await seed_paid_model(env, base_url=f"{CLOUD}/{type(boom).__name__}")
        adapter, model = await env.svc.registry.adapter_for(model_id)
        with pytest.raises(type(boom)):
            await adapter.chat(model["name"], [{"role": "user", "content": "привет"}])
        assert ledger().remaining() < before, f"{type(boom).__name__}: резерв молча вернули"

    held = [r for r in ledger()._records if r["status"] == "RECONCILING"]
    assert len(held) == 3, "неопределённые исходы обязаны остаться в RECONCILING"


async def test_an_answer_without_usage_is_held_not_forgiven(env):
    """Ответ без счётчиков токенов — это отсутствие свидетельства, а не ноль."""
    inner = CountingAdapter(result=ChatResult(text="ок", tokens_in=0, tokens_out=0,
                                              model=PAID_MODEL))
    env.svc.registry.adapter_factory = lambda m, p: inner
    model_id = await seed_paid_model(env)
    adapter, model = await env.svc.registry.adapter_for(model_id)

    await adapter.chat(model["name"], [{"role": "user", "content": "привет"}])
    assert [r["status"] for r in ledger()._records] == ["RECONCILING"]


async def test_a_held_reservation_survives_a_restart(env, tmp_path):
    """Перезапуск не освобождает неопределённый резерв: журнал durable, и
    после перечитывания деньги всё ещё заняты."""
    inner = CountingAdapter(raises=ProviderError("обрыв", kind="network"))
    env.svc.registry.adapter_factory = lambda m, p: inner
    model_id = await seed_paid_model(env)
    adapter, model = await env.svc.registry.adapter_for(model_id)
    with pytest.raises(ProviderError):
        await adapter.chat(model["name"], [{"role": "user", "content": "п" * 30_000}],
                           max_tokens=4096)
    after = ledger().remaining()
    assert after < 3.0
    # «перезапуск» — новый объект журнала поверх того же файла
    assert ledger().remaining() == pytest.approx(after)


# ---------------------------------------------------------------- потолок не поднять

async def test_two_workers_cannot_reserve_more_than_the_cap_together(env):
    """Одновременные вызовы делят один потолок, а не получают по своему."""
    inner = CountingAdapter(result=ChatResult(text="ок", tokens_in=10, tokens_out=10,
                                              model=PAID_MODEL))
    env.svc.registry.adapter_factory = lambda m, p: inner
    model_id = await seed_paid_model(env)
    adapter, model = await env.svc.registry.adapter_for(model_id)

    # Держим все вызовы В ПОЛЁТЕ одновременно: иначе первый успевает списаться
    # раньше, чем второй резервирует, и тест проверял бы очередь, а не потолок.
    gate = asyncio.Event()
    inner.hold = gate

    # каждый вызов резервирует ~1.37 USD худшего случая: под потолок влезают два
    big = [{"role": "user", "content": "я" * 250_000}]
    calls = [asyncio.create_task(adapter.chat(model["name"], big, max_tokens=8192))
             for _ in range(4)]
    for _ in range(200):                       # даём всем дойти до резерва
        await asyncio.sleep(0.005)
        if sum(t.done() for t in calls) >= 2:
            break
    gate.set()
    results = await asyncio.gather(*calls, return_exceptions=True)

    refused = [r for r in results if isinstance(r, fable_cap.BudgetRefused)]
    assert len(refused) == 2, (
        f"под потолком 3.00 USD уместилось {4 - len(refused)} вызовов по 1.37 — "
        f"потолок не держит")
    assert inner.calls == 2, "отказанный вызов всё же дошёл до адаптера"
    b = ledger()
    assert round(b._committed_total() + b._hold_total(), 6) <= 3.0


async def test_the_owner_api_cannot_raise_the_fable_cap(env, monkeypatch):
    """Spend Meter — не потолок Fable. Сколько бы владелец ни выставил его
    ручкой, платный вызов сверх 3.00 USD всё равно не состоится."""
    monkeypatch.setenv("BOSSMAN_SPEND_METER_ENABLED", "1")
    huge = await env.client.post("/api/spend/limit",
                                 json={"scope": "daily", "limit_usd": 1_000_000.0})
    assert huge.status_code == 200 and huge.json()["limit_usd"] == 1_000_000.0

    burn(2.99)
    inner = CountingAdapter()
    env.svc.registry.adapter_factory = lambda m, p: inner
    model_id = await seed_paid_model(env)
    adapter, model = await env.svc.registry.adapter_for(model_id)
    with pytest.raises(fable_cap.BudgetRefused):
        await adapter.chat(model["name"], [{"role": "user", "content": "я" * 100_000}],
                           max_tokens=4096)
    assert inner.calls == 0, "потолок Spend Meter поднял потолок Fable"


async def test_the_stored_cap_is_a_ratchet(env):
    """Журнал помнит свою величину и не даёт открыть себя с большей: иначе
    «жёсткий потолок» снимался бы одной строкой в чужом коде."""
    from bossman_shared.fable_budget import DirectApiBudget, canonical_budget
    burn(1.0)
    greedy = DirectApiBudget(canonical_budget().path, total_usd=1000.0,
                             mission_id="fable-hard-cap")
    assert greedy.total == 3.0
    assert greedy.remaining() == pytest.approx(2.0)


async def test_both_paths_share_one_ledger(env):
    """Прямой транспорт Fable и Command Center считают одни и те же деньги:
    двух потолков по три доллара не существует."""
    from bossman_shared.fable_budget import canonical_budget as shared_cap

    # Command Center intentionally does not depend on the whole bossman-core
    # distribution at runtime.  Verify the cross-package re-export in a clean
    # child interpreter instead of leaking bossman-core onto this pytest
    # process' sys.path (which would make the documented standalone install
    # pass locally but fail in Command Center CI).
    root = Path(__file__).resolve().parents[2]
    probe = subprocess.run(
        [sys.executable, "-c", (
            "from bossman.apprentice import fable_direct as direct; "
            "from bossman_shared import fable_budget as shared; "
            "assert direct.canonical_budget is shared.canonical_budget; "
            "assert direct.DirectApiBudget is shared.DirectApiBudget; "
            "assert direct.FABLE_HARD_CAP_USD == shared.FABLE_HARD_CAP_USD == 3.0"
        )],
        cwd=root,
        env={**os.environ, "PYTHONPATH": os.pathsep.join(
            (str(root / "bossman-core"), str(root)))},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert probe.returncode == 0, probe.stderr
    assert shared_cap().total == 3.0

    burn(2.5)
    inner = CountingAdapter()
    env.svc.registry.adapter_factory = lambda m, p: inner
    model_id = await seed_paid_model(env)
    adapter, model = await env.svc.registry.adapter_for(model_id)
    with pytest.raises(fable_cap.BudgetRefused):
        await adapter.chat(model["name"], [{"role": "user", "content": "я" * 200_000}],
                           max_tokens=8192)
    # трата прямого пути видна Command Center'у и наоборот
    assert shared_cap().remaining() == pytest.approx(0.5, abs=1e-6)
    assert inner.calls == 0


async def test_health_and_list_models_do_not_eat_the_cap(env):
    """Проверка доступности бьёт в /v1/models — токенов там нет, и съедать
    ими потолок было бы платой ни за что."""
    inner = CountingAdapter()
    env.svc.registry.adapter_factory = lambda m, p: inner
    model_id = await seed_paid_model(env)
    adapter, _ = await env.svc.registry.adapter_for(model_id)
    assert (await adapter.health()).status == "ok"
    assert await adapter.list_models() == [PAID_MODEL]
    assert ledger().remaining() == pytest.approx(3.0)
