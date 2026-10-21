"""«Строго по методичке» with a free-cloud fallback (owner 21.10: «Если локальная модель в Джефф недоступна, пусть
отвечает облачная, но тоже по методичке»).

Fakes only: a fake local LLM, fake cloud adapters behind Bossman's REAL registry (bcc.db rows, governed adapters,
health records) or a fake route in Jeff's runtime, a synthetic manual. No Ollama, no GPU, no network.
"""
from __future__ import annotations

import asyncio
import base64
import json
import sqlite3

import httpx
import pytest

from bcc.pit import jeff_kb as kb
from bcc.pit import jeff_kb_cloud as kc
from bcc.pit.config import pit_home
from bcc.pit.ollama_native import EmptyAnswer
from bcc.providers import ChatResult, ProviderError

from .test_jeff_manual_kb import GUEST, MANUAL_MD, MARK, OWNER, FakeLLM, _all_text_under, _ask, _runtime, _upload
from .test_jeff_settings_overlay import pit_setup  # noqa: F401  (fixture)

OPENROUTER = "https://openrouter.ai/api/v1"
NIM = "https://integrate.api.nvidia.com/v1"
FREE = "vendor/model-x:free"
CITED = "Разминка длится двенадцать минут (§1)."
QUESTION = "Сколько длится разминка перед игрой?"
OTHER_SECTION = "пять фишек"           # text of §2: must never reach the cloud for a §1 question


@pytest.fixture(autouse=True)
def _no_host_switches(monkeypatch):
    """The owner's machine may carry BOSSMAN_LOCAL_ONLY / an autonomy root: tests decide those themselves."""
    monkeypatch.delenv("BOSSMAN_LOCAL_ONLY", raising=False)
    monkeypatch.delenv("BOSSMAN_AUTONOMY_ROOT", raising=False)


class CloudFake:
    """A cloud provider stand-in behind Bossman's registry (GovernedAdapter wraps it)."""

    def __init__(self, text=CITED, *, error: BaseException | None = None, cost: float | None = None):
        self.text, self.error, self.cost = text, error, cost
        self.calls: list[tuple[str, list[dict]]] = []

    async def chat(self, model, messages, **kw):
        self.calls.append((model, [dict(m) for m in messages]))
        if self.error is not None:
            raise self.error
        meta = {"usage": {"cost": self.cost}} if self.cost is not None else {}
        return ChatResult(text=self.text, tokens_in=40, tokens_out=12, model=model, provider_meta=meta)


class LocalFake:
    """The owner check's local model: records the prompt, then answers, raises or hangs."""

    def __init__(self, *, text="Двенадцать минут (§1).", error: BaseException | None = None, hang: bool = False):
        self.text, self.error, self.hang = text, error, hang
        self.prompts: list[list[dict]] = []

    def factory(self, url, model):
        async def chat(messages):
            self.prompts.append([dict(m) for m in messages])
            if self.hang:
                await asyncio.sleep(30)
            if self.error is not None:
                raise self.error
            return self.text
        return chat


async def _free_model(env, *, name=FREE, base=OPENROUTER, alias=None, caps=None, price=(0.0, 0.0)):
    reg = env.svc.registry
    provider = next((p for p in await reg.list_providers() if p["base_url"] == base), None)
    if provider is None:
        provider = await reg.create_provider(name="OpenRouter" if "openrouter" in base else "NVIDIA NIM",
                                             kind="openai_compat", base_url=base, api_key="sk-test-not-real")
    row = await reg.create_model(provider_id=provider["id"], name=name, alias=alias or name, kind="cloud",
                                 caps=caps or {}, price_in=price[0], price_out=price[1])
    return row["id"]


async def _strict_manual(env):
    data = base64.b64encode(MANUAL_MD.encode("utf-8")).decode()
    assert (await env.client.put("/api/jeff-kb/manual", json={"name": "rules.md", "data_b64": data})).status_code == 200
    assert (await env.client.put("/api/jeff-kb/mode", json={"strict": True})).json()["strict"] is True


def _logs(pit_setup):
    return _all_text_under(pit_home(pit_setup) / "logs")


# ------------------------------------------------------------------------------------- what counts as «unavailable»
def test_only_availability_failures_trigger_the_fallback():
    import httpx as _httpx
    unavailable = {
        asyncio.TimeoutError(): "timeout",
        ProviderError("127.0.0.1 не ответил за 60 с", kind="network", timed_out=True): "timeout",
        ProviderError("нет связи с 127.0.0.1: connection refused", kind="network"): "connection",
        ConnectionRefusedError(): "connection",
        _httpx.ConnectError("refused"): "connection",
        ProviderError("провайдер временно перегружен", status_code=503): "http_5xx",
        ProviderError("internal error", status_code=500): "http_5xx",
        ProviderError('model "gemma4-31b-uncensored:latest" not found, try pulling it first', status_code=404):
            "model_missing",
        ProviderError("llama runner: failed to load model", status_code=500): "model_missing",
    }
    for exc, reason in unavailable.items():
        assert kc.availability_reason(exc) == reason, exc
    for exc in (EmptyAnswer("Ollama returned no visible answer"), ProviderError("bad request", status_code=400),
                ProviderError("Ollama returned invalid JSON", kind="protocol"), ValueError("x"), KeyError("y")):
        assert kc.availability_reason(exc) == "", exc


# ------------------------------------------------------------------------------------- which cloud models
def test_only_provably_free_cloud_chat_models_are_eligible():
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    providers = {1: {"id": 1, "name": "OpenRouter", "kind": "openai_compat", "base_url": OPENROUTER},
                 2: {"id": 2, "name": "NVIDIA NIM", "kind": "openai_compat", "base_url": NIM},
                 3: {"id": 3, "name": "Ollama", "kind": "openai_compat", "base_url": "http://127.0.0.1:11434/v1"},
                 4: {"id": 4, "name": "Other", "kind": "openai_compat", "base_url": "https://api.example.com/v1"}}
    free = {"price_in": 0.0, "price_out": 0.0, "pricing_known": True, "caps": {}, "status": "online"}
    cooldown = {"status": "throttled", "cooldown_until": (now + timedelta(minutes=5)).isoformat(),
                "checked_at": now.isoformat()}
    healthy = {"status": "healthy", "checked_at": now.isoformat()}
    rows = [
        {"id": 1, "provider_id": 1, "name": "a/unmeasured:free", **free},
        {"id": 2, "provider_id": 1, "name": "b/healthy:free", **free, "health": healthy},
        {"id": 3, "provider_id": 1, "name": "c/paid", **free, "price_in": 0.5, "price_out": 1.0},
        {"id": 4, "provider_id": 1, "name": "d/zero-but-not-free-id", **free},
        {"id": 5, "provider_id": 2, "name": "meta/llama-3.3-70b-instruct", **free, "caps": {"free_tier": True}},
        {"id": 6, "provider_id": 4, "name": "x/claims-free-tier", **free, "caps": {"free_tier": True}},
        {"id": 7, "provider_id": 3, "name": "gemma:latest", **free},
        {"id": 8, "provider_id": 3, "name": "minimax-m3.1-flash:cloud", **free},
        {"id": 9, "provider_id": 1, "name": "liquid/lfm-7b:free", **free},
        {"id": 10, "provider_id": 1, "name": "e/unknown-price:free", **free, "price_in": None, "price_out": None,
         "pricing_known": False},
        {"id": 11, "provider_id": 1, "name": "f/cooling:free", **free, "health": cooldown},
        {"id": 12, "provider_id": 2, "name": "nvidia/nv-embed-v1", **free, "caps": {"free_tier": True}},
        {"id": 13, "provider_id": 1, "name": "g/offline:free", **free, "status": "offline"},
    ]
    picked = kc.eligible(rows, providers, now=now)
    assert [c.id for c in picked] == [2, 1, 5], "healthy first, then unmeasured; nothing paid, local or unproven"
    assert picked[0].provider == "OpenRouter" and picked[0].host == "openrouter.ai"


# ------------------------------------------------------------------------------------- owner check over HTTP
async def test_check_local_timeout_answers_from_the_free_cloud_with_the_same_sections(env, pit_setup, monkeypatch):
    from bcc.features import jeff_kb as feature
    local, cloud = LocalFake(hang=True), CloudFake()
    monkeypatch.setattr(feature, "chat_factory", local.factory)
    monkeypatch.setattr(kc, "local_timeout_s", lambda state: 0.05)
    mid = await _free_model(env)
    env.svc.registry.adapter_factory = lambda m, p: cloud
    await _strict_manual(env)
    assert (await env.client.get("/api/jeff-kb")).json()["cloud_fallback"] is True, "owner 21.10: default ON"
    r = await env.client.post("/api/jeff-kb/check", json={"question": QUESTION})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["engine"] == "cloud" and out["model"] == FREE and out["provider"] == "OpenRouter"
    assert out["fallback_reason"] == "timeout" and out["local_model"] == "bossman-uncensored-chat:latest"
    assert out["answer"] == CITED and out["cited"] == [{"n": 1, "title": "Разминка"}] and out["retrieved"] == [1]
    # the cloud got exactly the local request: strict rules + the retrieved section + the question, nothing else
    (model, sent), = cloud.calls
    assert model == FREE and sent == local.prompts[0]
    assert kb.STRICT_RULES_RU in sent[0]["content"] and sent[-1] == {"role": "user", "content": QUESTION}
    blob = json.dumps(sent, ensure_ascii=False)
    assert MARK in blob and OTHER_SECTION not in blob, "only the retrieved section, never the whole manual"
    assert len(sent) == 3
    health = await env.svc.registry.model_health(mid)
    assert health.status == "healthy"
    logs = _logs(pit_setup)
    assert MARK not in logs and "разминка" not in logs.casefold() and "двенадцать" not in logs.casefold()
    row = [json.loads(x) for x in logs.splitlines() if '"cloud_answer"' in x][-1]
    assert row["provider"] == "OpenRouter" and row["model"] == FREE and row["local_reason"] == "timeout"
    assert row["sent_chars"] > 0 and len(row["sha256"]) == 16
    assert kc.usage(pit_setup)["calls"] == 1


async def test_check_local_5xx_goes_to_the_cloud(env, pit_setup, monkeypatch):
    from bcc.features import jeff_kb as feature
    local, cloud = LocalFake(error=ProviderError("Ollama: model runner crashed", status_code=503)), CloudFake()
    monkeypatch.setattr(feature, "chat_factory", local.factory)
    await _free_model(env)
    env.svc.registry.adapter_factory = lambda m, p: cloud
    await _strict_manual(env)
    out = (await env.client.post("/api/jeff-kb/check", json={"question": QUESTION})).json()
    assert out["engine"] == "cloud" and out["fallback_reason"] == "http_5xx" and "(§1)" in out["answer"]
    assert len(cloud.calls) == 1


async def test_check_local_answer_or_no_answer_never_calls_the_cloud(env, pit_setup, monkeypatch):
    from bcc.features import jeff_kb as feature
    cloud = CloudFake()
    await _free_model(env)
    env.svc.registry.adapter_factory = lambda m, p: cloud
    await _strict_manual(env)
    local = LocalFake()
    monkeypatch.setattr(feature, "chat_factory", local.factory)
    ok = (await env.client.post("/api/jeff-kb/check", json={"question": QUESTION})).json()
    assert ok["engine"] == "local" and ok["provider"] == "ollama" and ok["model"] == "bossman-uncensored-chat:latest"
    assert ok["answer"] == "Двенадцать минут (§1)."
    nothing = LocalFake(text="В методичке этого нет.")
    monkeypatch.setattr(feature, "chat_factory", nothing.factory)
    no = (await env.client.post("/api/jeff-kb/check", json={"question": QUESTION})).json()
    assert no["answer"] == kb.NO_ANSWER_RU and no["engine"] == "local" and nothing.prompts
    unmatched = (await env.client.post("/api/jeff-kb/check", json={"question": "Столица Франции?"})).json()
    assert unmatched["no_match"] is True and unmatched["engine"] == "local"
    broken = LocalFake(error=EmptyAnswer("Ollama returned no visible answer"))
    monkeypatch.setattr(feature, "chat_factory", broken.factory)
    r = await env.client.post("/api/jeff-kb/check", json={"question": QUESTION})
    assert r.status_code == 502 and r.json()["error"]["code"] == "JEFF_MODEL_DOWN", "not an availability failure"
    assert cloud.calls == [] and kc.usage(pit_setup)["calls"] == 0


async def test_check_cloud_also_down_is_an_honest_error_not_an_answer(env, pit_setup, monkeypatch):
    from bcc.features import jeff_kb as feature
    local = LocalFake(error=ProviderError("нет связи с 127.0.0.1", kind="network"))
    cloud = CloudFake(error=ProviderError("нет связи с openrouter.ai", kind="network"))
    monkeypatch.setattr(feature, "chat_factory", local.factory)
    first = await _free_model(env)
    await _free_model(env, name="vendor/model-y:free")
    env.svc.registry.adapter_factory = lambda m, p: cloud
    await _strict_manual(env)
    r = await env.client.post("/api/jeff-kb/check", json={"question": QUESTION})
    assert r.status_code == 503, r.text
    detail = r.json()["error"]
    assert detail["code"] == "JEFF_NO_MODELS" and detail["message"].startswith("Нет связи с моделями")
    assert detail["local_reason"] == "connection" and detail["cloud_reason"] == "failed"
    assert "answer" not in r.json() and "§" not in detail["message"]
    assert len(cloud.calls) == 2, "every eligible free model tried once, no more"
    assert (await env.svc.registry.model_health(first)).status == "provider_down"
    stored = json.dumps((await env.svc.registry.model_health(first)).to_dict(), ensure_ascii=False)
    assert "openrouter.ai" not in stored, "provider error text is not stored, only its class"


async def test_check_toggle_off_means_no_cloud(env, pit_setup, monkeypatch):
    from bcc.features import jeff_kb as feature
    local, cloud = LocalFake(error=asyncio.TimeoutError()), CloudFake()
    monkeypatch.setattr(feature, "chat_factory", local.factory)
    await _free_model(env)
    env.svc.registry.adapter_factory = lambda m, p: cloud
    await _strict_manual(env)
    r = await env.client.put("/api/jeff-kb/mode", json={"cloud_fallback": False})
    assert r.json()["cloud_fallback"] is False and r.json()["strict"] is True
    r = await env.client.post("/api/jeff-kb/check", json={"question": QUESTION})
    assert r.status_code == 502 and r.json()["error"]["code"] == "JEFF_MODEL_DOWN"
    assert cloud.calls == []
    assert (await env.client.put("/api/jeff-kb/mode", json={"cloud_fallback": True})).json()["cloud_fallback"] is True


async def test_check_spend_cap_stop_and_budget_refusal_mean_no_cloud(env, pit_setup, monkeypatch):
    from bcc.features import jeff_kb as feature
    local, cloud = LocalFake(error=asyncio.TimeoutError()), CloudFake()
    monkeypatch.setattr(feature, "chat_factory", local.factory)
    await _free_model(env)
    await _free_model(env, name="vendor/model-y:free")
    env.svc.registry.adapter_factory = lambda m, p: cloud
    await _strict_manual(env)
    # daily cap 0: nothing is sent
    assert (await env.client.put("/api/jeff-kb/mode", json={"cloud_daily_max": 0})).json()["cloud_daily_max"] == 0
    r = await env.client.post("/api/jeff-kb/check", json={"question": QUESTION})
    assert r.status_code == 503 and r.json()["error"]["cloud_reason"] == "daily_cap" and cloud.calls == []
    # cap 1: one answer, then the cap is reached
    await env.client.put("/api/jeff-kb/mode", json={"cloud_daily_max": 1})
    assert (await env.client.post("/api/jeff-kb/check", json={"question": QUESTION})).json()["engine"] == "cloud"
    r = await env.client.post("/api/jeff-kb/check", json={"question": QUESTION})
    assert r.json()["error"]["cloud_reason"] == "daily_cap" and len(cloud.calls) == 1
    # owner STOP: nothing is sent
    await env.client.put("/api/jeff-kb/mode", json={"cloud_daily_max": 100})
    stop = pit_setup / "computer" / "STOP"
    stop.parent.mkdir(parents=True, exist_ok=True)
    stop.write_text("owner\n", encoding="utf-8")
    r = await env.client.post("/api/jeff-kb/check", json={"question": QUESTION})
    assert r.json()["error"]["cloud_reason"] == "stop" and len(cloud.calls) == 1
    stop.unlink()
    # a budget refusal of the governed adapter (long_run cap / free-only gate) ends the fallback: no 2nd model tried
    cloud.error = ProviderError("LONG-RUN CAP HIT: the call was NOT made", kind="budget")
    r = await env.client.post("/api/jeff-kb/check", json={"question": QUESTION})
    assert r.json()["error"]["cloud_reason"] == "refused" and len(cloud.calls) == 2


async def test_check_never_sends_the_manual_to_a_paid_or_unproven_model(env, pit_setup, monkeypatch):
    from bcc.features import jeff_kb as feature
    local, cloud = LocalFake(error=asyncio.TimeoutError()), CloudFake()
    monkeypatch.setattr(feature, "chat_factory", local.factory)
    await _free_model(env, name="vendor/paid-model", price=(0.5, 1.5))          # BOSSMAN_ALLOW_PAID_CLOUD=1 in tests
    await _free_model(env, name="vendor/zero-price-no-free-suffix")
    await _free_model(env, name="vendor/claims-free", base="https://api.example.com/v1", caps={"free_tier": True})
    env.svc.registry.adapter_factory = lambda m, p: cloud
    await _strict_manual(env)
    r = await env.client.post("/api/jeff-kb/check", json={"question": QUESTION})
    assert r.status_code == 503 and r.json()["error"]["cloud_reason"] == "no_candidates"
    assert cloud.calls == []


async def test_a_reply_that_reports_a_cost_is_dropped_and_closes_the_fallback_for_the_day(env, pit_setup,
                                                                                          monkeypatch):
    from bcc.features import jeff_kb as feature
    local, cloud = LocalFake(error=asyncio.TimeoutError()), CloudFake(cost=0.0042)
    monkeypatch.setattr(feature, "chat_factory", local.factory)
    await _free_model(env)
    env.svc.registry.adapter_factory = lambda m, p: cloud
    await _strict_manual(env)
    r = await env.client.post("/api/jeff-kb/check", json={"question": QUESTION})
    assert r.status_code == 503 and r.json()["error"]["cloud_reason"] == "cost_violation"
    cloud.cost = None
    r = await env.client.post("/api/jeff-kb/check", json={"question": QUESTION})
    assert r.json()["error"]["cloud_reason"] == "cost_violation" and len(cloud.calls) == 1


async def test_mode_and_check_bodies_are_validated_by_the_real_models(env, pit_setup):
    await _strict_manual(env)
    for body in ({"local_timeout_s": 5}, {"local_timeout_s": 301}, {"cloud_daily_max": -1},
                 {"cloud_daily_max": 1001}, {"cloud_fallback": "maybe"}):
        assert (await env.client.put("/api/jeff-kb/mode", json=body)).status_code == 422, body
    ok = (await env.client.put("/api/jeff-kb/mode", json={"local_timeout_s": 45})).json()
    assert ok["local_timeout_s"] == 45 and ok["cloud_fallback"] is True and ok["cloud_calls_today"] == 0
    assert (await env.client.post("/api/jeff-kb/check", json={"question": ""})).status_code == 422
    assert (await env.client.post("/api/jeff-kb/check", json={"question": "q" * 2001})).status_code == 422


# ------------------------------------------------------------------------------------- Jeff chat (runtime)
class RouteFake:
    """Stands in for Bossman's registry inside Jeff's runtime (``BccDbRoute``)."""

    def __init__(self, text=CITED, *, error: BaseException | None = None, cands=1):
        self.text, self.error = text, error
        self.cands = [kc.Candidate(id=i + 1, name=FREE if i == 0 else f"vendor/model-{i}:free", provider="OpenRouter",
                                   host="openrouter.ai") for i in range(cands)]
        self.calls: list[tuple[str, list[dict]]] = []

    async def candidates(self):
        return list(self.cands)

    async def chat(self, cand, messages, *, max_tokens, timeout):
        self.calls.append((cand.name, [dict(m) for m in messages]))
        if self.error is not None:
            raise self.error
        return ChatResult(text=self.text, tokens_in=30, tokens_out=9, model=cand.name)

    async def note(self, cand, exc, latency_ms, text=""):
        return None


class DownLLM(FakeLLM):
    def __init__(self, error):
        super().__init__()
        self.error = error

    async def chat(self, model, messages, **kw):
        self.calls.append((model, [dict(m) for m in messages]))
        raise self.error


def _strict_runtime(tmp_path, llm, route, **mode):
    runtime = _runtime(tmp_path, llm)
    runtime.kb_cloud_route = route
    _upload(runtime.vault.data_dir)
    kb.set_mode(runtime.vault.data_dir, strict=True, **mode)
    return runtime


def test_chat_local_timeout_strict_turn_is_answered_by_the_cloud_and_marked_for_the_owner(tmp_path):
    llm, route = DownLLM(ProviderError("127.0.0.1 не ответил за 30 с", kind="network", timed_out=True)), RouteFake()
    runtime = _strict_runtime(tmp_path, llm, route)
    try:
        answer = _ask(runtime, OWNER, QUESTION)
        assert answer.startswith(CITED) and answer.endswith("☁️ облако · " + FREE)
        assert llm.calls, "the local model was asked first"
        (_model, sent), = route.calls
        assert kb.STRICT_RULES_RU in sent[0]["content"] and sent[1]["content"].startswith("Разделы методички")
        assert sent[-1] == {"role": "user", "content": QUESTION} and len(sent) == 5
        blob = json.dumps(sent, ensure_ascii=False)
        assert MARK in blob and OTHER_SECTION not in blob, "only the retrieved section"
        assert runtime.adapter.calls == [], "Jeff's own chat routes never carry the manual"
        logs = _all_text_under(pit_home(runtime.vault.data_dir) / "logs")
        assert MARK not in logs and "разминка" not in logs.casefold()
        assert '"engine": "cloud"' in logs and '"local_reason": "timeout"' in logs
        # the provenance line is not kept in memory: a later local prompt never sees a model name
        history = json.dumps(runtime.store.history(next(p for p in runtime.settings.people if p.user_id == OWNER).key),
                             ensure_ascii=False)
        assert "☁️" not in history and FREE not in history
    finally:
        asyncio.run(runtime.close())


def test_chat_participant_in_scope_gets_the_cloud_answer_without_any_provenance_line(tmp_path):
    llm, route = DownLLM(ProviderError("Ollama 503", status_code=503)), RouteFake()
    runtime = _strict_runtime(tmp_path, llm, route, scope_all=True)
    try:
        answer = _ask(runtime, GUEST, QUESTION)
        assert answer == CITED, "a participant never sees a provider or model name"
        assert len(route.calls) == 1
    finally:
        asyncio.run(runtime.close())


def test_chat_local_answer_or_no_answer_never_calls_the_cloud(tmp_path):
    route = RouteFake()
    runtime = _strict_runtime(tmp_path, FakeLLM(), route)
    try:
        assert "(§1)" in _ask(runtime, OWNER, QUESTION, 1) and "облако" not in _ask(runtime, OWNER, QUESTION, 2)
        runtime.local_adapter.text = "В методичке этого нет."
        assert _ask(runtime, OWNER, QUESTION, 3) == kb.NO_ANSWER_RU
        runtime.local_adapter.text = "Полчаса, так все делают."          # uncited -> no answer, still not the cloud
        assert _ask(runtime, OWNER, QUESTION, 4) == kb.NO_ANSWER_RU
        assert _ask(runtime, OWNER, "Какая столица Франции?", 5) == kb.NO_ANSWER_RU
        runtime.local_adapter = DownLLM(EmptyAnswer("Ollama returned no visible answer"))
        assert _ask(runtime, OWNER, QUESTION, 6) != CITED
        assert route.calls == []
    finally:
        asyncio.run(runtime.close())


def test_chat_cloud_down_too_is_an_honest_no_link(tmp_path):
    llm = DownLLM(ProviderError("нет связи с 127.0.0.1", kind="network"))
    route = RouteFake(error=ProviderError("нет связи с openrouter.ai", kind="network"), cands=2)
    runtime = _strict_runtime(tmp_path, llm, route)
    try:
        assert _ask(runtime, OWNER, QUESTION) == kc.NO_LINK_RU
        assert len(route.calls) == 2
        assert runtime.store.get("provider_last_error") == "kb_cloud_failed"
    finally:
        asyncio.run(runtime.close())


def test_chat_toggle_off_keeps_the_local_only_behaviour(tmp_path):
    from bcc.pit.runtime import NO_MODEL_RU, PROVIDER_DOWN_RU
    llm, route = DownLLM(asyncio.TimeoutError()), RouteFake()
    runtime = _strict_runtime(tmp_path, llm, route, cloud_fallback=False)
    try:
        assert _ask(runtime, OWNER, QUESTION, 1) == PROVIDER_DOWN_RU
        runtime.local_adapter = FakeLLM()
        runtime.local_adapter.models = []                       # not loaded: no local route at all
        assert _ask(runtime, OWNER, QUESTION, 2) == NO_MODEL_RU
        assert route.calls == []
    finally:
        asyncio.run(runtime.close())


def test_chat_no_local_route_goes_straight_to_the_cloud(tmp_path):
    llm, route = FakeLLM(), RouteFake()
    llm.models = []                                             # Ollama lists nothing: model not loaded / busy
    runtime = _strict_runtime(tmp_path, llm, route)
    try:
        answer = _ask(runtime, OWNER, QUESTION)
        assert answer.startswith(CITED) and "☁️ облако" in answer
        assert llm.calls == [] and len(route.calls) == 1
    finally:
        asyncio.run(runtime.close())


class HangLLM(FakeLLM):
    async def chat(self, model, messages, **kw):
        self.calls.append((model, [dict(m) for m in messages]))
        await asyncio.sleep(30)
        raise AssertionError("the short local timeout must have fired")


def test_chat_short_local_timeout_decides_unavailable_then_the_cloud_answers(tmp_path, monkeypatch):
    import time as _time
    monkeypatch.setattr(kc, "local_timeout_s", lambda state: 1.5)
    llm, route = HangLLM(), RouteFake()
    runtime = _strict_runtime(tmp_path, llm, route)
    try:
        started = _time.monotonic()
        answer = _ask(runtime, OWNER, QUESTION)
        assert _time.monotonic() - started < 10, "the owner's short timeout, not the 30 s turn deadline"
        assert answer.startswith(CITED) and llm.calls and len(route.calls) == 1
        logs = _all_text_under(pit_home(runtime.vault.data_dir) / "logs")
        assert '"local_reason": "timeout"' in logs
    finally:
        asyncio.run(runtime.close())


def test_chat_uncited_cloud_answer_is_replaced_by_no_answer(tmp_path):
    llm, route = DownLLM(asyncio.TimeoutError()), RouteFake(text="Полчаса, так принято.")
    runtime = _strict_runtime(tmp_path, llm, route)
    try:
        answer = _ask(runtime, OWNER, QUESTION)
        assert answer.startswith(kb.NO_ANSWER_RU) and "Полчаса" not in answer
    finally:
        asyncio.run(runtime.close())


def test_chat_spend_cap_and_stop_mean_no_cloud(tmp_path, monkeypatch):
    llm, route = DownLLM(asyncio.TimeoutError()), RouteFake()
    runtime = _strict_runtime(tmp_path, llm, route, cloud_daily_max=0)
    try:
        assert _ask(runtime, OWNER, QUESTION, 1) == kc.NO_LINK_RU
        kb.set_mode(runtime.vault.data_dir, cloud_daily_max=100)
        stop = runtime.vault.data_dir / "autonomy" / "STOP"
        stop.parent.mkdir(parents=True, exist_ok=True)
        stop.write_text("{}", encoding="utf-8")
        assert _ask(runtime, OWNER, QUESTION, 2) == kc.NO_LINK_RU
        stop.unlink()
        monkeypatch.setenv("BOSSMAN_LOCAL_ONLY", "1")
        assert _ask(runtime, OWNER, QUESTION, 3) == kc.NO_LINK_RU
        assert route.calls == []
    finally:
        asyncio.run(runtime.close())


def test_chat_participant_without_cloud_consent_is_never_sent_to_the_cloud(tmp_path):
    from bcc.pit.models import ConsentState
    llm, route = DownLLM(asyncio.TimeoutError()), RouteFake()
    runtime = _strict_runtime(tmp_path, llm, route, scope_all=True)
    try:
        guest = next(p for p in runtime.settings.people if p.user_id == GUEST)
        key = runtime.vault.key_for_telegram(GUEST)
        runtime.vault.set_consent(key, ConsentState(memory_enabled=True, remote_processing_enabled=False))
        answer = asyncio.run(runtime.handle(guest, {"_user_id": GUEST, "_chat_id": GUEST, "_message_id": 1,
                                                    "text": QUESTION, "_photo": "", "_document": None,
                                                    "_voice": False}))
        assert answer == kc.NO_LINK_RU and route.calls == []
    finally:
        asyncio.run(runtime.close())


# ------------------------------------------------------------------------------------- Jeff's read-only registry view
def _bcc_db(tmp_path, rows):
    from bcc.secrets import Vault
    db = tmp_path / "bcc.db"
    con = sqlite3.connect(db)
    con.execute("create table providers (id integer primary key, name text, kind text, base_url text, "
                "api_key_enc text)")
    con.execute("create table models (id integer primary key, provider_id integer, name text, alias text, kind text, "
                "caps text, price_in real, price_out real, pricing_known integer, status text, health text)")
    con.execute("insert into providers values (1, 'OpenRouter', 'openai_compat', ?, ?)",
                (OPENROUTER, Vault(tmp_path).encrypt("sk-or-test-not-real")))
    for row in rows:
        con.execute("insert into models values (?, 1, ?, ?, 'cloud', '{}', ?, ?, 1, 'online', null)", row)
    con.commit()
    con.close()


def test_jeff_reads_bossmans_registry_read_only_and_calls_through_the_governed_adapter(tmp_path):
    _bcc_db(tmp_path, [(1, FREE, "x", 0.0, 0.0), (2, "vendor/paid", "y", 0.5, 1.0)])
    built, cloud = [], CloudFake()

    def builder(kind, base, key):
        built.append((kind, base, key))
        return cloud
    route = kc.BccDbRoute(tmp_path, adapter_builder=builder)
    cands = asyncio.run(route.candidates())
    assert [c.name for c in cands] == [FREE]
    result = asyncio.run(route.chat(cands[0], [{"role": "user", "content": "q"}], max_tokens=50, timeout=5))
    assert result.text == CITED and built == [("openai_compat", OPENROUTER, "sk-or-test-not-real")]
    from bcc.provider_governance import GovernedAdapter
    assert isinstance(kc.governed_adapter({"kind": "openai_compat", "base_url": OPENROUTER}, {"name": FREE}, cloud),
                      GovernedAdapter)
    asyncio.run(route.note(cands[0], ProviderError("rate limited", kind="rate_limit", status_code=429), 10))
    assert asyncio.run(route.candidates()) == [], "a failing model cools down in this process"
    assert kc.BccDbRoute(tmp_path / "nowhere").__class__ and asyncio.run(
        kc.BccDbRoute(tmp_path / "nowhere").candidates()) == []
