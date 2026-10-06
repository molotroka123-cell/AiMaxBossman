"""NVIDIA NIM / Groq: ключ проверяется каталогом до записи, чат-модели встают по 0/0."""
from __future__ import annotations

import pytest

from bcc.providers import OpenAICompatAdapter, ProviderError

KEY = "nvapi-test-0123456789abcdef"
CATALOG = ["moonshotai/kimi-k3", "z-ai/glm-5.3", "nvidia/nemotron-3-embed-1b",
           "meta/llama-guard-4-12b", "nvidia/nemotron-3.5-content-safety"]


@pytest.fixture
def catalog(monkeypatch):
    seen = {}

    async def fake_list_models(self):
        seen["base_url"], seen["key"] = self.base_url, self.api_key
        if self.api_key != KEY:
            raise ProviderError("401 Unauthorized", kind="http")
        return list(CATALOG)

    monkeypatch.setattr(OpenAICompatAdapter, "list_models", fake_list_models)
    return seen


async def test_connect_registers_only_chat_models_at_zero_price(env, catalog):
    r = await env.client.post("/api/free-providers/nvidia_nim/connect", json={"api_key": KEY})
    assert r.status_code == 200, r.text
    body = r.json()
    assert KEY not in r.text and body["key"] and body["key"] != KEY
    assert sorted(body["models_added"]) == ["moonshotai/kimi-k3", "z-ai/glm-5.3"]
    assert catalog["base_url"].startswith("https://integrate.api.nvidia.com")
    models = [m for m in await env.svc.registry.list_models() if m["provider_id"] == body["provider_id"]]
    assert {m["name"] for m in models} == {"moonshotai/kimi-k3", "z-ai/glm-5.3"}
    for m in models:
        assert m["price_in"] == 0 and m["price_out"] == 0 and m["pricing_known"]
        assert m["kind"] == "cloud" and m["caps"]["free_tier"] == "nvidia_nim"
    listed = (await env.client.get("/api/free-providers")).json()["providers"]
    nim = next(p for p in listed if p["name"] == "nvidia_nim")
    assert nim["has_key"] and nim["models_registered"] == 2 and KEY not in str(listed)


async def test_bad_key_is_refused_and_nothing_is_stored(env, catalog):
    r = await env.client.post("/api/free-providers/groq/connect", json={"api_key": "gsk_wrong"})
    assert r.status_code == 400
    assert "gsk_wrong" not in r.text
    assert not [p for p in await env.svc.registry.list_providers() if "groq" in (p.get("base_url") or "")]


async def test_reconnect_reuses_provider_without_duplicate_models(env, catalog):
    first = (await env.client.post("/api/free-providers/nvidia_nim/connect", json={"api_key": KEY})).json()
    again = await env.client.post("/api/free-providers/nvidia_nim/connect", json={"api_key": KEY})
    assert again.status_code == 200
    assert again.json()["provider_id"] == first["provider_id"] and again.json()["created"] is False
    assert again.json()["models_added"] == []
    names = [m["name"] for m in await env.svc.registry.list_models()
             if m["provider_id"] == first["provider_id"]]
    assert len(names) == len(set(names)) == 2


async def test_unknown_preset_and_multiline_key_are_refused(env, catalog):
    assert (await env.client.post("/api/free-providers/nope/connect", json={"api_key": KEY})).status_code == 404
    bad = await env.client.post("/api/free-providers/groq/connect", json={"api_key": "a\nb"})
    assert bad.status_code == 422


def test_cli_vendors_route_to_the_free_provider_endpoint():
    from bcc.terminal_cli import keys

    class FakeClient:
        def __init__(self):
            self.calls = []

        def post(self, path, body):
            self.calls.append((path, body))
            return {"provider_id": 7, "created": True, "key": "…cdef", "models_added": ["a", "b"]}

    client = FakeClient()
    res = keys.store_key(client, keys.vendor_of("nim"), KEY)
    assert client.calls == [("/api/free-providers/nvidia_nim/connect", {"api_key": KEY})]
    assert res["mask"] == "…cdef" and res["models_registered"] == 2 and KEY not in str(res)
    assert keys.vendor_of("groq").free_preset == "groq"


def test_ai_studio_registers_only_free_tier_models():
    from bcc.features.free_providers import PRESETS, is_chat_model
    p = PRESETS["google_ai_studio"]
    free = ["models/gemini-2.5-flash", "models/gemini-3.8-flash", "models/gemini-flash-lite-latest",
            "models/gemini-3.5-flash-lite", "models/gemma-4-31b-it"]
    paid_or_not_chat = ["models/gemini-2.5-pro", "models/gemini-3.1-pro-preview", "models/gemini-3.8-flash-tts",
                        "models/gemini-2.5-flash-image", "models/veo-3.1-generate-preview",
                        "models/gemini-embedding-2", "models/gemini-3.8-live", "models/aqa"]
    assert all(is_chat_model(m, p) for m in free)
    assert not any(is_chat_model(m, p) for m in paid_or_not_chat)
