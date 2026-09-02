"""SECREM F-016 — роутер: облако fail-closed, force_model_id через ту же политику,
«местная» метка выводится из провайдера И модели (не из одной строки в БД).

Сценарии находки FABLE51-016:
- без меты cloud_allowed было True (бюджет-производное «разрешено»);
- meta.force_model_id возвращал любую модель без проверки облака/цены/способностей;
- модель с kind=local у облачного провайдера проходила фильтр «cloud disabled».
"""
from __future__ import annotations

import pytest
import sqlalchemy as sa

from bcc.db import models as models_t, providers as providers_t
from bcc.features.forks import _force_model_hook
from bcc.features.router import _candidates, _make_pick_hook, _save_rules, check_forced_model
from bcc.v2.model_router import derive_local

from .conftest import FakeAdapter
from .helpers import make_stack

CLOUD_URL = "https://openrouter.ai/api/v1"
LOCAL_URL = "http://127.0.0.1:11434/v1"


async def _seed(env, alias: str, *, kind: str = "local", base_url: str = LOCAL_URL,
                provider_kind: str = "openai_compat", price_out: float = 0.0,
                caps: dict | None = None) -> int:
    async with env.svc.db.session() as s:
        pid = (await s.execute(sa.insert(providers_t).values(
            name=f"prov-{alias}", kind=provider_kind, base_url=base_url))
        ).inserted_primary_key[0]
        mid = (await s.execute(sa.insert(models_t).values(
            provider_id=pid, name=alias, alias=alias, kind=kind, status="online",
            context_window=32768, caps=caps if caps is not None else {"coding": True},
            price_in=0.0, price_out=price_out, bench={}))).inserted_primary_key[0]
        await s.commit()
    return int(mid)


def _payload(event: dict) -> dict:
    """Полезная нагрузка события из истории шины (столбец data) или плоская."""
    data = event.get("data")
    return data if isinstance(data, dict) else event


def _task(meta: dict | None = None, kind: str = "coding") -> dict:
    return {"id": 4242, "prompt": "почини функцию", "kind": kind,
            "meta": {"route": True, **(meta or {})}}


# ------------------------------------------------------------ cloud_allowed fail-closed

async def test_no_meta_cloud_model_excluded(env):
    """Нет явного разрешения → облачная модель не кандидат (даже при бюджете > 0)."""
    await _seed(env, "cloud-x", kind="cloud", base_url=CLOUD_URL)
    hook = await _make_pick_hook(env.svc)

    for meta in ({}, {"cloud_budget_usd": 5.0}, {"cloud_budget_usd": None}):
        out = await hook(_task(meta), {})
        assert out is None, f"облако выбрано без разрешения при meta={meta}: {out}"


async def test_no_meta_local_wins_and_cloud_is_rejected_honestly(env):
    await _seed(env, "cloud-y", kind="cloud", base_url=CLOUD_URL)
    await _seed(env, "local-y")
    hook = await _make_pick_hook(env.svc)
    out = await hook(_task({"cloud_budget_usd": 100.0}), {})
    assert out is not None and out["route"]["alias"] == "local-y"
    assert "cloud disabled" in out["route"]["rejected"]["cloud-y"]


async def test_meta_cloud_allowed_true_includes_cloud(env):
    await _seed(env, "cloud-z", kind="cloud", base_url=CLOUD_URL)
    hook = await _make_pick_hook(env.svc)
    out = await hook(_task({"cloud_allowed": True}), {})
    assert out is not None and out["route"]["alias"] == "cloud-z"
    # только строгий True: строка/число «правдой» не считаются
    for bad in ("true", 1, "yes"):
        assert await hook(_task({"cloud_allowed": bad}), {}) is None, bad


async def test_agent_permission_and_rules_default_allow_cloud(env):
    await _seed(env, "cloud-a", kind="cloud", base_url=CLOUD_URL)
    hook = await _make_pick_hook(env.svc)
    # политика агента: permissions.cloud_allowed=true
    out = await hook(_task(), {"permissions": {"cloud_allowed": True}})
    assert out is not None and out["route"]["alias"] == "cloud-a"
    assert await hook(_task(), {"permissions": {"cloud_allowed": False}}) is None
    # глобальное правило роутера cloud_default_allow
    await _save_rules(env.svc, {"cloud_default_allow": True})
    out = await hook(_task(), {})
    assert out is not None and out["route"]["alias"] == "cloud-a"


async def test_zero_budget_denies_cloud_even_when_allowed(env):
    """Явный нулевой бюджет — это лимит: облако закрыто даже при разрешении."""
    await _seed(env, "cloud-b", kind="cloud", base_url=CLOUD_URL)
    hook = await _make_pick_hook(env.svc)
    assert await hook(_task({"cloud_allowed": True, "cloud_budget_usd": 0}), {}) is None


# ------------------------------------------------------------ force_model_id через политику

async def test_force_model_id_cloud_denied_refused(env):
    cloud_id = await _seed(env, "cloud-f", kind="cloud", base_url=CLOUD_URL)
    hook = await _force_model_hook(env.svc)
    out = await hook({"id": 1, "kind": "coding", "meta": {"force_model_id": cloud_id}}, {})
    assert out is None, "принудительная облачная модель прошла без разрешения"
    events = [e for e in await env.svc.bus.recent(50) if e["kind"] == "router.force_refused"]
    # bus.recent отдаёт строки таблицы events: полезная нагрузка лежит в столбце data
    assert events and "cloud disabled" in _payload(events[-1])["reason"]

    # с явным разрешением — та же модель проходит
    out = await hook({"id": 1, "kind": "coding",
                      "meta": {"force_model_id": cloud_id, "cloud_allowed": True}}, {})
    assert out == {"model_id": cloud_id}


async def test_force_model_id_enforces_price_and_capabilities(env):
    pricey = await _seed(env, "cloud-pricey", kind="cloud", base_url=CLOUD_URL, price_out=5.0)
    reasons = await check_forced_model(
        env.svc, pricey, meta={"cloud_allowed": True, "max_price_out": 0.1},
        agent={}, kind="coding")
    assert any("output price" in r for r in reasons), reasons

    no_caps = await _seed(env, "local-nocaps", caps={})
    reasons = await check_forced_model(env.svc, no_caps, meta={}, agent={}, kind="coding")
    assert any("missing capabilities" in r for r in reasons), reasons

    ok = await _seed(env, "local-ok")
    assert await check_forced_model(env.svc, ok, meta={}, agent={}, kind="coding") == []
    # несуществующая модель — честный отказ, а не «взяли что было»
    assert await check_forced_model(env.svc, 999999, meta={}, agent={}, kind="generic")


async def test_fork_api_refuses_denied_cloud_model(env):
    """POST /runs/{id}/fork с model_id облачной модели без разрешения → 403,
    задача-форк не создаётся."""
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter("исходный ответ")
    stack = await make_stack(env.client)
    for _ in range(4):
        rid = await env.svc.engine.claim()
        if rid is None:
            break
        await env.svc.engine.execute(rid)
    data = (await env.client.get(f"/api/tasks/{stack['task']['id']}")).json()
    run_id = data["runs"][-1]["id"]
    cloud_id = await _seed(env, "cloud-fork", kind="cloud", base_url=CLOUD_URL)

    before = (await env.client.get(f"/api/forks?task_id={stack['task']['id']}")).json()
    r = await env.client.post(f"/api/runs/{run_id}/fork", json={"model_id": cloud_id})
    assert r.status_code == 403, r.text
    assert "cloud disabled" in r.text
    after = (await env.client.get(f"/api/forks?task_id={stack['task']['id']}")).json()
    assert after["forks"] == before["forks"]


# ------------------------------------------------------------ local-mislabel

def test_derive_local_requires_both_provider_and_model_local():
    assert derive_local("local", "openai_compat", "http://127.0.0.1:8080/v1") == (True, None)
    assert derive_local("local", "openai_compat", "http://192.168.1.5:8080/v1") == (True, None)
    local, why = derive_local("local", "openai_compat", "https://api.example.com/v1")
    assert local is False and why
    local, why = derive_local("local", "anthropic", "")
    assert local is False and why
    assert derive_local("cloud", "openai_compat", "http://127.0.0.1:8080/v1")[0] is False


async def test_mislabeled_local_model_is_treated_as_cloud(env):
    """kind=local в БД у модели облачного провайдера → кандидат НЕ местный."""
    await _seed(env, "liar-local", kind="local", base_url="https://api.example.com/v1")
    await _seed(env, "liar-anthropic", kind="local", provider_kind="anthropic", base_url="")
    await _seed(env, "honest-local")
    cands = {c.alias: c for c in await _candidates(env.svc, {})}
    assert cands["liar-local"].local is False
    assert cands["liar-anthropic"].local is False
    assert cands["honest-local"].local is True

    hook = await _make_pick_hook(env.svc)
    out = await hook(_task(), {})
    assert out is not None and out["route"]["alias"] == "honest-local"
    assert "cloud disabled" in out["route"]["rejected"]["liar-local"]
    assert "cloud disabled" in out["route"]["rejected"]["liar-anthropic"]


@pytest.mark.parametrize("url", ["http://localhost:1234/v1", "http://[::1]:8080/v1",
                                 "http://host.docker.internal:8080/v1"])
def test_derive_local_accepts_loopback_forms(url):
    assert derive_local("local", "openai_compat", url) == (True, None)
