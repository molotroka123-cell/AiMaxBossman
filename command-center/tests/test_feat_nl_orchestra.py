"""Feature 11 — NL Orchestration: разбор, валидация, ничего-до-confirm."""
import sqlalchemy as sa

from bcc.db import orchestras as orch_t
from bcc.v2.orchestration_schema import OrchestraDraft

from .helpers import make_stack


def test_draft_validation():
    d = OrchestraDraft(name="t", manager_agent="x", max_workers=50)
    errs = d.validate({"x"})
    assert any("max_workers" in e for e in errs)


async def _models(env):
    prov = (await env.client.post("/api/providers", json={
        "name": "p", "kind": "openai_compat", "base_url": "http://x/v1"})).json()
    for alias in ("local-coder", "vision-worker", "cloud-reviewer"):
        await env.client.post("/api/models", json={
            "provider_id": prov["id"], "name": alias, "alias": alias})


async def test_parse_recognizes_roles_and_limits(env):
    await _models(env)
    text = ("Создай команду: local-coder главный, vision-worker для UI, "
            "cloud-reviewer fallback. Максимум 2 workers, 6 часов, "
            "cloud budget $2, dangerous actions требуют approval.")
    r = (await env.client.post("/api/orchestras/parse", json={"text": text})).json()
    assert r["valid"] is True
    cfg = r["orchestra"]["config"]
    assert cfg["max_workers"] == 2 and cfg["duration_hours"] == 6
    assert cfg["cloud_budget_usd"] == 2.0 and cfg["approval_policy"] == "required"
    roles = {m["role"] for m in r["members"]}
    assert {"manager", "reviewer", "worker"} <= roles


async def test_invalid_model_blocks(env):
    await _models(env)
    r = (await env.client.post("/api/orchestras/parse",
                               json={"text": "ghost-model главный"})).json()
    assert r["valid"] is False and r["warnings"]


async def test_nothing_created_before_confirm(env):
    await _models(env)
    async with env.svc.db.session() as s:
        before = (await s.execute(sa.select(sa.func.count()).select_from(orch_t))).scalar_one()
    await env.client.post("/api/orchestras/parse", json={"text": "local-coder главный"})
    async with env.svc.db.session() as s:
        after = (await s.execute(sa.select(sa.func.count()).select_from(orch_t))).scalar_one()
    assert before == after       # parse ничего не создаёт


async def test_confirm_creates_orchestra(env):
    await _models(env)
    preview = (await env.client.post("/api/orchestras/parse", json={
        "text": "local-coder главный, vision-worker для UI, cloud-reviewer fallback"})).json()
    assert preview["valid"]
    created = (await env.client.post("/api/orchestras/confirm", json=preview)).json()
    assert created["orchestra_id"] and created["members"] >= 3
    # невалидный preview → 422
    bad = await env.client.post("/api/orchestras/confirm",
                                json={"valid": False, "warnings": ["x"], "orchestra": {}})
    assert bad.status_code == 422
