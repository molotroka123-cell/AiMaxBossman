"""Lane G/H — сертификация пути OpenRouter + роутер по ПРОВЕРЕННЫМ способностям.

Сети нет: всё через httpx.MockTransport. Проверяем ровно то, что раньше не было
доказано: tool_calls переживают провайдер-адаптер, пробы отделяют advertised от
verified, роутер отвергает модель, которая ЗАЯВИЛА tools, но пробу провалила,
и shortlist кандидатов ограничен по длине.
"""
from __future__ import annotations

import json

import httpx
import sqlalchemy as sa

from bcc.db import models as models_t
from bcc.engine import _assistant_tool_message, _tool_message
from bcc.providers import build_adapter
from bcc.v2 import openrouter_ext
from bcc.v2.capability_probe import probe_model, probe_streaming, probe_structured_output
from bcc.v2.model_router import (MAX_CANDIDATES, MAX_REJECTED, ModelCandidate,
                                 RouteRequest, route, shortlist)
from bcc.v2.openrouter_ext import OpenRouterClient
from bcc.v2.tables import provider_catalog_models as catalog_t

# --------------------------------------------------------------------------- fake

CARD_TOOLS = {
    "id": "vendor/tools-ok", "name": "Tools OK", "context_length": 128000,
    "pricing": {"prompt": "0.0000005", "completion": "0.0000015"},
    "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
    "supported_parameters": ["tools", "tool_choice", "response_format"],
}
CARD_LIAR = {
    "id": "vendor/tools-liar", "name": "Tools Liar", "context_length": 32000,
    "pricing": {"prompt": "0.0000001", "completion": "0.0000002"},
    "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
    "supported_parameters": ["tools", "tool_choice"],
}
CARD_VISION = {
    "id": "vendor/vision", "name": "Vision", "context_length": 64000,
    "pricing": {"prompt": "0.000001", "completion": "0.000002"},
    "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["text"]},
    "supported_parameters": ["response_format"],
}
CARD_PLAIN = {
    "id": "vendor/plain", "name": "Plain", "context_length": 8000,
    "pricing": {"prompt": "0", "completion": "0"},
    "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
    "supported_parameters": [],
}


class FakeOpenRouter:
    """Детерминированный OpenRouter поверх MockTransport (в т.ч. SSE-стрим)."""

    def __init__(self, cards=None):
        self.cards = list(cards if cards is not None else
                          [CARD_TOOLS, CARD_LIAR, CARD_VISION, CARD_PLAIN])
        self.chat_payloads: list[dict] = []

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": self.cards})
        payload = json.loads(request.content or b"{}")
        self.chat_payloads.append(payload)
        model = payload.get("model", "")
        messages = payload.get("messages") or []
        last = messages[-1].get("content") if messages else ""
        has_image = isinstance(last, list) and any(
            p.get("type") == "image_url" for p in last)

        if payload.get("stream"):
            body = ("data: " + json.dumps({"choices": [{"delta": {"content": "1 "}}]}) + "\n\n"
                    "data: " + json.dumps({"choices": [{"delta": {"content": "2 3"}}]}) + "\n\n"
                    "data: [DONE]\n\n")
            return httpx.Response(200, content=body.encode(),
                                  headers={"content-type": "text/event-stream"})
        if has_image:
            return self._done(model, {"role": "assistant", "content": "red"})
        if payload.get("tools"):
            if model.endswith("liar"):
                # ЗАЯВИЛА tools, но зовёт словами — ровно случай advertised ≠ verified
                return self._done(model, {"role": "assistant",
                                          "content": "I would call bossman_probe(7)."})
            return self._done(model, {
                "role": "assistant", "content": None,
                "tool_calls": [{"id": "call_probe_1", "type": "function",
                                "function": {"name": "bossman_probe",
                                             "arguments": '{"value": 7}'}}],
            }, finish="tool_calls")
        if payload.get("response_format"):
            return self._done(model, {"role": "assistant", "content": '{"ok": true}'})
        return self._done(model, {"role": "assistant", "content": "OK"})

    @staticmethod
    def _done(model: str, message: dict, finish: str = "stop") -> httpx.Response:
        return httpx.Response(200, json={
            "id": "gen-1", "provider": "FakeProvider", "model": model,
            "choices": [{"index": 0, "message": message, "finish_reason": finish}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 4},
        })


def _patch_client(monkeypatch, fake: FakeOpenRouter) -> None:
    """OpenRouterClient (в т.ч. созданный внутри фич) ходит только в fake."""
    orig = openrouter_ext.OpenRouterClient.__init__

    def new_init(self, api_key, base_url=openrouter_ext.DEFAULT_BASE, transport=None):
        orig(self, api_key, base_url="http://openrouter.test/api/v1",
             transport=fake.transport)
    monkeypatch.setattr(openrouter_ext.OpenRouterClient, "__init__", new_init)


async def _provider(env) -> dict:
    return (await env.client.post("/api/providers", json={
        "name": "openrouter-lane-gh", "kind": "openai_compat",
        "base_url": "http://openrouter.test/api/v1", "api_key": "sk-or-test"})).json()


async def _online(env) -> None:
    async with env.svc.db.session() as s:
        await s.execute(sa.update(models_t).values(status="online"))
        await s.commit()


# --------------------------------------------------------------- 1. каталог / метаданные

async def test_catalog_sync_persists_remote_metadata(env, monkeypatch):
    fake = FakeOpenRouter()
    _patch_client(monkeypatch, fake)
    prov = await _provider(env)

    synced = (await env.client.post(f"/api/openrouter/{prov['id']}/sync")).json()
    assert synced["synced"] == 4 and synced["stale"] == 0

    catalog = (await env.client.get(f"/api/openrouter/{prov['id']}/catalog")).json()
    by_id = {c["remote_id"]: c for c in catalog}
    assert set(by_id) == {"vendor/tools-ok", "vendor/tools-liar",
                          "vendor/vision", "vendor/plain"}
    tools = by_id["vendor/tools-ok"]
    assert tools["context_window"] == 128000
    assert tools["price_in"] == 0.5 and tools["price_out"] == 1.5    # USD / 1M
    assert tools["supported_parameters"] == ["tools", "tool_choice", "response_format"]
    assert tools["advertised_caps"]["tools"] is True
    assert tools["advertised_caps"]["vision"] is False
    vision = by_id["vendor/vision"]
    assert vision["input_modalities"] == ["text", "image"]
    assert vision["advertised_caps"]["vision"] is True
    assert vision["raw_metadata"]["id"] == "vendor/vision"           # сырьё сохранено


async def test_alias_and_probe_history_survive_refresh(env, monkeypatch):
    fake = FakeOpenRouter()
    _patch_client(monkeypatch, fake)
    prov = await _provider(env)
    await env.client.post(f"/api/openrouter/{prov['id']}/sync")

    pinned = (await env.client.post(f"/api/openrouter/{prov['id']}/pin", json={
        "remote_id": "vendor/tools-ok", "alias": "gh-tools"})).json()
    await env.client.post(f"/api/openrouter/models/{pinned['model_id']}/probe")
    before = (await env.client.get(
        f"/api/openrouter/models/{pinned['model_id']}/capabilities")).json()
    assert before

    # модель исчезла из remote — каталог обязан пометить stale, но НЕ снести pin
    # (force: TTL-кэш без force вернул бы сохранённый каталог без похода в remote)
    fake.cards = [c for c in fake.cards if c["id"] != "vendor/plain"]
    again = (await env.client.post(f"/api/openrouter/{prov['id']}/sync?force=true")).json()
    assert again["synced"] == 3 and again["stale"] == 1 and again["cached"] is False

    async with env.svc.db.session() as s:
        row = (await s.execute(sa.select(catalog_t).where(
            catalog_t.c.remote_id == "vendor/plain"))).first()
        model = (await s.execute(sa.select(models_t).where(
            models_t.c.alias == "gh-tools"))).first()
    assert row._mapping["stale"] is True             # строка жива, помечена stale
    assert model is not None and model._mapping["id"] == pinned["model_id"]

    after = (await env.client.get(
        f"/api/openrouter/models/{pinned['model_id']}/capabilities")).json()
    assert {c["capability"] for c in after} == {c["capability"] for c in before}
    # каталог по умолчанию скрывает stale, с include_stale — показывает
    fresh = (await env.client.get(f"/api/openrouter/{prov['id']}/catalog")).json()
    assert "vendor/plain" not in {c["remote_id"] for c in fresh}
    withstale = (await env.client.get(
        f"/api/openrouter/{prov['id']}/catalog?include_stale=true")).json()
    assert "vendor/plain" in {c["remote_id"] for c in withstale}


# ------------------------------------------------------- 2. tool_calls через адаптер

TOOL_SCHEMA = {
    "type": "function",
    "function": {"name": "terminal__run", "description": "run a shell command",
                 "parameters": {"type": "object",
                                "properties": {"command": {"type": "string"},
                                               "timeout": {"type": "integer"}},
                                "required": ["command"]}},
}


async def test_tool_calls_survive_provider_adapter_round_trip():
    """OpenRouter-ответ с message.tool_calls доходит до движка как
    ChatResult.tool_calls (id/name/arguments), а следующий запрос несёт обратно
    assistant-сообщение с tool_calls и role=tool с тем же tool_call_id."""
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        sent.append(payload)
        if len(sent) == 1:
            return httpx.Response(200, json={
                "id": "gen-tc", "provider": "FakeProvider", "model": "vendor/tools-ok",
                "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                    "role": "assistant", "content": None,
                    "tool_calls": [{"id": "call_abc", "type": "function", "function": {
                        "name": "terminal__run",
                        "arguments": '{"command": "ls -1", "timeout": 30}'}}]}}],
                "usage": {"prompt_tokens": 21, "completion_tokens": 9},
            })
        return httpx.Response(200, json={
            "id": "gen-final", "model": "vendor/tools-ok",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {
                "role": "assistant", "content": "В каталоге два файла."}}],
            "usage": {"prompt_tokens": 40, "completion_tokens": 6},
        })

    adapter = build_adapter("openai_compat", base_url="https://openrouter.ai/api/v1",
                            api_key="sk-or-test", transport=httpx.MockTransport(handler))
    messages = [{"role": "user", "content": "перечисли файлы"}]
    first = await adapter.chat("vendor/tools-ok", messages,
                               tools=[TOOL_SCHEMA], tool_choice="auto")

    assert first.has_tool_calls and len(first.tool_calls) == 1
    call = first.tool_calls[0]
    assert call.id == "call_abc"
    assert call.name == "terminal__run"
    assert call.arguments == {"command": "ls -1", "timeout": 30}
    assert json.loads(call.raw_arguments) == call.arguments
    assert first.finish == "tool_calls"
    assert first.raw_message["tool_calls"][0]["id"] == "call_abc"
    assert first.provider_meta["provider"] == "FakeProvider"
    assert sent[0]["tools"][0]["function"]["name"] == "terminal__run"
    assert sent[0]["tool_choice"] == "auto"

    # второй виток: как его собирает движок (bcc/engine helpers, не самодельно)
    followup = messages + [_assistant_tool_message(first),
                           _tool_message(call, "a.py\nb.py")]
    second = await adapter.chat("vendor/tools-ok", followup, tools=[TOOL_SCHEMA])
    assert second.text == "В каталоге два файла." and not second.has_tool_calls

    out = sent[1]["messages"]
    assistant, tool_msg = out[-2], out[-1]
    assert assistant["role"] == "assistant"
    assert assistant["tool_calls"][0]["id"] == "call_abc"
    assert assistant["tool_calls"][0]["type"] == "function"
    assert json.loads(assistant["tool_calls"][0]["function"]["arguments"]) == call.arguments
    assert tool_msg == {"role": "tool", "tool_call_id": "call_abc",
                        "name": "terminal__run", "content": "a.py\nb.py"}


# ------------------------------------------------------------------- 3. пробы

async def test_structured_output_probe_verified():
    fake = FakeOpenRouter()
    client = OpenRouterClient("sk", base_url="http://openrouter.test/api/v1",
                              transport=fake.transport)
    res = await probe_structured_output(client, "vendor/tools-ok")
    assert res.ok is True and res.verified is True
    assert fake.chat_payloads[-1]["response_format"] == {"type": "json_object"}


async def test_streaming_probe_collects_sse_chunks():
    fake = FakeOpenRouter()
    client = OpenRouterClient("sk", base_url="http://openrouter.test/api/v1",
                              transport=fake.transport)
    res = await probe_streaming(client, "vendor/tools-ok")
    assert res.ok is True and "2 chunks" in res.detail
    assert fake.chat_payloads[-1]["stream"] is True


async def test_vision_probe_skipped_when_not_advertised():
    fake = FakeOpenRouter()
    client = OpenRouterClient("sk", base_url="http://openrouter.test/api/v1",
                              transport=fake.transport)
    plain = {r.capability: r for r in await probe_model(
        client, "vendor/plain", {"vision": False, "tools": False,
                                 "structured_output": False, "streaming": False})}
    assert plain["vision"].skipped is True
    assert plain["vision"].verified is None          # «не знаем», а не «не умеет»
    assert not any(isinstance(m.get("content"), list)
                   for p in fake.chat_payloads for m in p.get("messages", []))

    seen = {r.capability: r for r in await probe_model(
        client, "vendor/vision", {"vision": True})}
    assert seen["vision"].skipped is False and seen["vision"].verified is True
    assert any(isinstance(m.get("content"), list)
               for p in fake.chat_payloads for m in p.get("messages", []))


async def test_advertised_and_verified_stored_separately(env, monkeypatch):
    fake = FakeOpenRouter()
    _patch_client(monkeypatch, fake)
    prov = await _provider(env)
    await env.client.post(f"/api/openrouter/{prov['id']}/sync")
    liar = (await env.client.post(f"/api/openrouter/{prov['id']}/pin", json={
        "remote_id": "vendor/tools-liar", "alias": "gh-liar"})).json()

    probed = (await env.client.post(
        f"/api/openrouter/models/{liar['model_id']}/probe")).json()
    by_cap = {p["capability"]: p for p in probed["probes"]}
    assert by_cap["chat"]["verified"] is True
    # tools ЗАЯВЛЕНЫ каталогом, но модель их не вызвала
    assert by_cap["tools"]["advertised"] is True
    assert by_cap["tools"]["verified"] is False
    # vision не заявлен → пробу не гоняли, verified неизвестен
    assert by_cap["vision"]["skipped"] is True and by_cap["vision"]["verified"] is None

    stored = {c["capability"]: c for c in (await env.client.get(
        f"/api/openrouter/models/{liar['model_id']}/capabilities")).json()}
    assert stored["tools"]["advertised"] is True and stored["tools"]["verified"] is False
    assert stored["vision"]["advertised"] is False and stored["vision"]["verified"] is None


# ------------------------------------------------------- 4. ГЛАВНОЕ ПРАВИЛО роутера

def _cand(alias, **kw):
    return ModelCandidate(id=alias, alias=alias, local=False, **kw)


def test_router_rejects_advertised_but_unverified_tools_model():
    """advertised_tools=true + verified_tools=false → модель ОТВЕРГНУТА."""
    liar = _cand("gh-liar", capabilities={"tools"},
                 unsupported_capabilities={"tools"}, context_window=32000)
    d = route(RouteRequest(task_type="coding", requires={"tools"}), [liar])
    assert d.model is None
    assert any("verified NOT supported" in r for r in d.rejected["gh-liar"])
    # без требования tools та же модель прекрасно проходит
    ok = route(RouteRequest(task_type="chat"), [liar])
    assert ok.model is liar


def test_router_picks_verified_model_over_advertised_one():
    liar = _cand("gh-liar", capabilities={"tools"}, unsupported_capabilities={"tools"},
                 price_in=0.1, price_out=0.2, context_window=32000)
    good = _cand("gh-tools", capabilities={"tools"}, verified_capabilities={"tools"},
                 price_in=0.5, price_out=1.5, context_window=128000)
    d = route(RouteRequest(task_type="coding", requires={"tools"}), [liar, good])
    assert d.model.alias == "gh-tools"           # дороже, но проверена
    assert any("verified: tools" in r for r in d.reasons)
    assert "gh-liar" in d.rejected


def test_router_unknown_capability_falls_back_to_advertised():
    """Пробы не было → идём по advertised (иначе роутер парализован)."""
    fresh = _cand("gh-fresh", capabilities={"tools"})
    d = route(RouteRequest(task_type="coding", requires={"tools"}), [fresh])
    assert d.model is fresh
    strict = route(RouteRequest(task_type="coding", requires={"tools"},
                                require_verified=True), [fresh])
    assert strict.model is None
    assert any("not verified" in r for r in strict.rejected["gh-fresh"])


async def test_router_uses_probe_results_from_db(env, monkeypatch):
    """Сквозная проверка: пробы в БД → кандидаты роутера → выбор."""
    fake = FakeOpenRouter()
    _patch_client(monkeypatch, fake)
    prov = await _provider(env)
    await env.client.post(f"/api/openrouter/{prov['id']}/sync")
    good = (await env.client.post(f"/api/openrouter/{prov['id']}/pin", json={
        "remote_id": "vendor/tools-ok", "alias": "gh-tools"})).json()
    liar = (await env.client.post(f"/api/openrouter/{prov['id']}/pin", json={
        "remote_id": "vendor/tools-liar", "alias": "gh-liar"})).json()
    for mid in (good["model_id"], liar["model_id"]):
        await env.client.post(f"/api/openrouter/models/{mid}/probe")
    await _online(env)

    # task_type generic → требования берём явно из тела запроса
    preview = (await env.client.post("/api/router/preview", json={
        "task_type": "generic", "requires": ["tools"]})).json()
    assert preview["selected"] == "gh-tools"
    assert any("verified NOT supported" in r for r in preview["rejected"]["gh-liar"])


# --------------------------------------------------------- 5. ограниченный shortlist

def test_shortlist_is_bounded():
    many = [_cand(f"m{i}", capabilities={"tools"}, verified_capabilities={"tools"},
                  price_out=float(i % 30), success_rate=(i % 10) / 10)
            for i in range(400)]
    req = RouteRequest(task_type="coding", requires={"tools"})
    picked, rejected = shortlist(req, many)
    assert len(picked) == MAX_CANDIDATES <= 24
    assert len(rejected) <= MAX_REJECTED           # объяснения тоже не безразмерны

    d = route(req, many)
    assert d.total == 400
    assert d.considered == MAX_CANDIDATES
    assert d.model is not None
    assert len(d.rejected) <= MAX_REJECTED

    tight = RouteRequest(task_type="coding", requires={"tools"}, max_candidates=3)
    assert len(shortlist(tight, many)[0]) == 3


async def test_router_candidates_endpoint_is_bounded(env):
    prov = (await env.client.post("/api/providers", json={
        "name": "local-many", "kind": "openai_compat",
        "base_url": "http://local.test/v1"})).json()
    for i in range(12):
        await env.client.post("/api/models", json={
            "provider_id": prov["id"], "name": f"m{i}", "alias": f"many-{i}",
            "kind": "local", "caps": {"tools": True}})
    await _online(env)

    body = (await env.client.post("/api/router/candidates", json={
        "task_type": "generic", "requires": ["tools"], "max_candidates": 4})).json()
    assert body["total"] == 12 and body["limit"] == 4
    assert len(body["candidates"]) == 4
    digest = body["candidates"][0]
    # компактный digest: ни сырых метаданных каталога, ни промпта
    assert set(digest) == {"alias", "local", "context_window", "advertised", "verified",
                           "unsupported", "price_in", "price_out", "latency_ms",
                           "gen_tps", "success_rate"}
