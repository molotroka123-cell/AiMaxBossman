"""Owner audit 2026-09-08, F3a: Web Designer exposed no model choice and the
backend used `models[0]` — the first registry row, whatever its health.

Now `/api/web-designer/models` lists the canonical registry with health and
marks the server default (health rank); `ai-edit` takes an explicit
`model_id`, refuses an unknown one, and otherwise picks by health, not by
insertion order. The selection must reach the adapter that is actually called.
"""
from __future__ import annotations

import sqlalchemy as sa

from bcc import model_health as mh
from bcc.db import models as models_t

from .conftest import FakeAdapter


class Recording(FakeAdapter):
    calls: list[str] = []

    async def chat(self, model, messages, **kw):
        Recording.calls.append(model)
        return await super().chat(model, messages, **kw)


async def _two_models(env):
    provider = (await env.client.post("/api/providers", json={
        "name": "p", "kind": "openai_compat", "base_url": "http://127.0.0.1:1/v1", "api_key": "sk-x"})).json()
    first = (await env.client.post("/api/models", json={"provider_id": provider["id"], "name": "first-model", "alias": "first"})).json()
    second = (await env.client.post("/api/models", json={"provider_id": provider["id"], "name": "glm-5.3", "alias": "glm"})).json()
    return first, second


async def _set_health(env, model_id, status):
    async with env.svc.db.session() as s:
        await s.execute(sa.update(models_t).where(models_t.c.id == model_id).values(
            health=mh.HealthRecord(status=status, detail="test").to_dict()))
        await s.commit()


async def _project(env):
    data = (await env.client.post("/api/web-designer/projects",
                                  json={"name": "t", "prompt": "", "template": "blank"})).json()
    pid = data["meta"]["id"]
    await env.client.put(f"/api/web-designer/projects/{pid}/code",
                         json={"html": "<!doctype html><html><body><h1>Hi</h1></body></html>", "note": "seed"})
    return pid


async def test_models_endpoint_lists_the_registry_with_health_and_a_default(env):
    first, second = await _two_models(env)
    await _set_health(env, first["id"], mh.PROVIDER_DOWN)
    await _set_health(env, second["id"], mh.HEALTHY)
    body = (await env.client.get("/api/web-designer/models")).json()
    by_id = {m["id"]: m for m in body["items"]}
    assert set(by_id) == {first["id"], second["id"]}
    assert by_id[second["id"]]["default"] is True and by_id[first["id"]]["default"] is False
    assert by_id[first["id"]]["health"]["status"] == mh.PROVIDER_DOWN
    assert body["default_model_id"] == second["id"] and body["chosen_by"] == "health_rank"


async def test_the_owners_choice_reaches_the_adapter(env):
    first, second = await _two_models(env)
    Recording.calls = []
    env.svc.registry.adapter_factory = lambda m, p: Recording("<h1>Chosen</h1>")
    pid = await _project(env)
    res = await env.client.post(f"/api/web-designer/projects/{pid}/ai-edit",
                                json={"prompt": "make it bold", "model_id": second["id"]})
    assert res.status_code == 200, res.text
    assert res.json()["model_id"] == second["id"] and res.json()["chosen_by"] == "owner"
    assert Recording.calls == ["glm-5.3"]


async def test_an_unknown_model_is_a_404_not_a_silent_fallback(env):
    await _two_models(env)
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter("<h1>x</h1>")
    pid = await _project(env)
    res = await env.client.post(f"/api/web-designer/projects/{pid}/ai-edit",
                                json={"prompt": "x", "model_id": 999})
    assert res.status_code == 404


async def test_without_a_choice_the_healthiest_model_is_used_not_the_first_row(env):
    first, second = await _two_models(env)
    await _set_health(env, first["id"], mh.PROVIDER_DOWN)
    await _set_health(env, second["id"], mh.HEALTHY)
    Recording.calls = []
    env.svc.registry.adapter_factory = lambda m, p: Recording("<h1>auto</h1>")
    pid = await _project(env)
    res = await env.client.post(f"/api/web-designer/projects/{pid}/ai-edit", json={"prompt": "x"})
    assert res.status_code == 200, res.text
    assert res.json()["model_id"] == second["id"] and res.json()["chosen_by"] == "health_rank"
    assert Recording.calls == ["glm-5.3"]


async def test_no_models_is_still_an_honest_409(env):
    pid = await _project(env)
    res = await env.client.post(f"/api/web-designer/projects/{pid}/ai-edit", json={"prompt": "x"})
    assert res.status_code == 409
    assert (await env.client.get("/api/web-designer/models")).json()["items"] == []
