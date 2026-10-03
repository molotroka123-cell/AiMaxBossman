"""cv-e port (RC19 "catalog presence"): a registry model that the live provider catalog no longer lists is
not «online».

`nex-agi/nex-n2.5-pro:free` was withdrawn by OpenRouter. The sync already dropped its price (so the pricing
gate refuses it) but the registry row kept status=online and the UI showed a working model. Now a sync marks
it `unavailable` (with a detail), a model that comes back goes to `unknown` until a probe proves it answers,
an empty catalog proves nothing, the sync result names what it marked, executor auto-selection keeps the
PRECISE catalog message, and the UI has a label and a tone for the new status. No network: httpx.MockTransport.
"""
from __future__ import annotations

import httpx
import pytest
import sqlalchemy as sa

from bcc.db import models as models_t
from bcc.task_admission import ExecutorUnavailable, select_executor
from bcc.v2 import openrouter_ext
from bcc.v2.openrouter_catalog_service import MISSING_DETAIL, MISSING_STATUS, OpenRouterCatalogService

OPENROUTER = "https://openrouter.ai/api/v1"


def card(remote_id: str, price: str = "0") -> dict:
    return {"id": remote_id, "name": remote_id, "context_length": 32000,
            "pricing": {"prompt": price, "completion": price},
            "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
            "supported_parameters": ["tools"]}


class Remote:
    def __init__(self, *ids: str):
        self.cards = [card(i) for i in ids]

    def set(self, *ids: str) -> None:
        self.cards = [card(i) for i in ids]

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/models")
        return httpx.Response(200, json={"data": self.cards})


@pytest.fixture
def remote(monkeypatch):
    fake = Remote("vendor/stays:free", "vendor/goes:free")
    orig = openrouter_ext.OpenRouterClient.__init__

    def new_init(self, api_key, base_url=openrouter_ext.DEFAULT_BASE, transport=None):
        orig(self, api_key, base_url="http://openrouter.test/api/v1", transport=httpx.MockTransport(fake.handler))

    monkeypatch.setattr(openrouter_ext.OpenRouterClient, "__init__", new_init)
    return fake


async def provider(env, name="or-presence", url=OPENROUTER) -> dict:
    return (await env.client.post("/api/providers", json={
        "name": name, "kind": "openai_compat", "base_url": url, "api_key": "sk-or-test"})).json()


async def model(env, prov, name, *, status="online", kind="cloud", price=0.0) -> dict:
    body = {"provider_id": prov["id"], "name": name, "alias": name, "kind": kind, "price_in": price,
            "price_out": price}
    row = (await env.client.post("/api/models", json=body)).json()
    async with env.svc.db.session() as s:
        await s.execute(sa.update(models_t).where(models_t.c.id == row["id"]).values(status=status))
        await s.commit()
    return row


async def row_of(env, model_id) -> dict:
    async with env.svc.db.session() as s:
        return dict((await s.execute(sa.select(models_t).where(models_t.c.id == model_id))).first()._mapping)


async def sync(env, prov, *, force=True) -> dict:
    return await OpenRouterCatalogService(env.svc.db, env.svc.vault).sync(prov["id"], force=force)


async def test_a_model_the_catalog_dropped_becomes_unavailable_and_the_sync_says_so(env, remote):
    prov = await provider(env)
    stays = await model(env, prov, "vendor/stays:free")
    goes = await model(env, prov, "vendor/goes:free")
    first = await sync(env, prov)
    assert first["unavailable"] == [] and (await row_of(env, goes["id"]))["status"] == "online"

    remote.set("vendor/stays:free")                                  # OpenRouter withdrew one model
    second = await sync(env, prov)
    assert second["unavailable"] == ["vendor/goes:free"]
    gone, kept = await row_of(env, goes["id"]), await row_of(env, stays["id"])
    assert gone["status"] == MISSING_STATUS == "unavailable" and gone["status_detail"] == MISSING_DETAIL
    assert "нет в живом каталоге" in gone["status_detail"] and gone["last_check"] is not None
    assert kept["status"] == "online"                                # the neighbour is untouched
    assert gone["id"] == goes["id"] and gone["alias"] == "vendor/goes:free"   # a pinned alias keeps its identity
    third = await sync(env, prov)
    assert third["unavailable"] == []                                # still missing: marked once, not reported again
    shown = {m["alias"]: m["status"] for m in (await env.client.get("/api/models")).json()}
    assert shown["vendor/goes:free"] == "unavailable"                # the registry API tells the truth too


async def test_a_model_that_comes_back_is_unknown_until_a_probe_proves_it(env, remote):
    prov = await provider(env)
    goes = await model(env, prov, "vendor/goes:free")
    remote.set("vendor/stays:free")
    await sync(env, prov)
    assert (await row_of(env, goes["id"]))["status"] == "unavailable"
    remote.set("vendor/stays:free", "vendor/goes:free")
    back = await sync(env, prov)
    row = await row_of(env, goes["id"])
    assert back["unavailable"] == [] and row["status"] == "unknown" and not row["status_detail"]


async def test_an_empty_catalog_proves_nothing_and_changes_no_status(env, remote):
    prov = await provider(env)
    goes = await model(env, prov, "vendor/goes:free")
    remote.set()                                                     # the provider answered with nothing
    result = await sync(env, prov)
    assert result["unavailable"] == []
    assert (await row_of(env, goes["id"]))["status"] == "online"


async def test_only_this_providers_models_are_judged_by_its_catalog(env, remote):
    prov = await provider(env)
    other = await provider(env, name="local-ollama", url="http://127.0.0.1:11434/v1")
    mine = await model(env, prov, "vendor/goes:free")
    theirs = await model(env, other, "vendor/goes:free-local", kind="local")
    remote.set("vendor/stays:free")
    result = await sync(env, prov)
    assert result["unavailable"] == ["vendor/goes:free"]
    assert (await row_of(env, mine["id"]))["status"] == "unavailable"
    assert (await row_of(env, theirs["id"]))["status"] == "online"


async def _agent(env, model_row) -> dict:
    return (await env.client.post("/api/agents", json={"name": f"agent-{model_row['id']}",
                                                        "model_id": model_row["id"], "max_steps": 1})).json()


async def test_auto_selection_keeps_the_precise_catalog_message_for_a_withdrawn_model(env, remote):
    prov = await provider(env)
    goes = await model(env, prov, "vendor/goes:free")
    await _agent(env, goes)
    remote.set("vendor/stays:free")
    await sync(env, prov)                                            # also drops the price: pricing_known False
    row = await row_of(env, goes["id"])
    assert row["status"] == "unavailable" and row["pricing_known"] is False
    with pytest.raises(ExecutorUnavailable) as exc:
        async with env.svc.db.session() as s:
            await select_executor(s, prompt="привет", agent_id=None)
    assert "модель удалена из каталога" in str(exc.value)            # the catalog branch wins, not a generic text
    assert "снята с живого каталога" not in str(exc.value)


async def test_auto_selection_skips_an_unavailable_model_even_when_its_price_is_known(env):
    """The dedicated `unavailable` branch (after the catalog-price one): a registry row marked unavailable whose
    price is still known is never auto-selected; an explicit choice of the agent stays the owner's."""
    prov = await provider(env, name="local-p", url="http://127.0.0.1:11434/v1")
    dead = await model(env, prov, "qwen-gone", kind="local", status="unavailable")
    live_prov = await provider(env, name="local-q", url="http://127.0.0.1:11435/v1")
    live = await model(env, live_prov, "qwen-live", kind="local", status="online")
    a_dead, a_live = await _agent(env, dead), await _agent(env, live)
    async with env.svc.db.session() as s:
        chosen = await select_executor(s, prompt="привет", agent_id=None)
    assert chosen["id"] == a_live["id"]                              # the live one, not the first in the list
    async with env.svc.db.session() as s:
        await s.execute(sa.update(models_t).where(models_t.c.id == live["id"]).values(status="unavailable"))
        await s.commit()
    with pytest.raises(ExecutorUnavailable) as exc:
        async with env.svc.db.session() as s:
            await select_executor(s, prompt="привет", agent_id=None)
    assert "снята с живого каталога" in str(exc.value)
    async with env.svc.db.session() as s:                            # explicit selection is the owner's choice
        assert (await select_executor(s, prompt="привет", agent_id=a_dead["id"]))["id"] == a_dead["id"]


def test_the_ui_has_a_label_and_a_tone_for_unavailable():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] / "ui" / "components.js").read_text(encoding="utf-8")
    tone = source[source.index("const STATUS_TONE = {"):source.index("export const STATUS_LABEL")]
    label = source[source.index("export const STATUS_LABEL = {"):source.index("export function statusTone")]
    assert "unavailable: 'warn'" in tone
    assert "unavailable: 'нет в каталоге'" in label
