"""Home "attention" reads health.models/providers: "error" there is shown as «Сбой».

UX attack 2026-09-22: main was healthy (checked an hour ago, now stale) and fast had
never been probed, yet the home page said «Сбой: Модели — моделей: 2». Nothing had
failed; the summary fell through to "error" because the mix matched no earlier rule.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import sqlalchemy as sa

from bcc import db as dbm
from bcc.health import model_components

OLD = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
NOW = datetime.now(timezone.utc).isoformat()


async def _models(env, *healths):
    provider = (await env.client.post("/api/providers", json={
        "name": "local", "kind": "openai_compat", "base_url": "http://127.0.0.1:9/v1"})).json()
    for i, health in enumerate(healths):
        model = (await env.client.post("/api/models", json={
            "provider_id": provider["id"], "name": f"m{i}", "alias": f"m{i}"})).json()
        if health is not None:
            async with env.svc.db.session() as s:
                await s.execute(sa.update(dbm.models).where(dbm.models.c.id == model["id"])
                                .values(health=health))
                await s.commit()


def _h(status, at):
    return {"status": status, "checked_at": at, "last_success_at": at if status == "healthy" else None,
            "consecutive_failures": 0 if status == "healthy" else 1, "successes": 1, "samples": 1}


async def test_stale_healthy_plus_unprobed_is_not_a_failure(env):
    await _models(env, _h("healthy", OLD), None)
    health = await model_components(env.svc)
    assert health["models"]["status"] == "stale"
    assert health["providers"]["status"] == "stale"


async def test_a_measured_failure_is_still_an_error(env):
    await _models(env, _h("healthy", OLD), _h("timeout", NOW))
    assert (await model_components(env.svc))["models"]["status"] == "error"


async def test_nothing_probed_yet_is_unknown_and_all_fresh_is_ok(env):
    await _models(env, None, None)
    assert (await model_components(env.svc))["models"]["status"] == "unknown"


async def test_one_fresh_healthy_among_unprobed_is_degraded(env):
    await _models(env, _h("healthy", NOW), None)
    assert (await model_components(env.svc))["models"]["status"] == "degraded"
