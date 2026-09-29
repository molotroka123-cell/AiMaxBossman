"""Free-only product runtime at the command-center registry (adapter_for).

An owner-registered direct cloud provider (api.openai.com, a priced OpenRouter
model, a 0/0 typed by hand) must not make paid calls; ':free' OpenRouter, the
connected free-tier presets and local models keep working."""
from __future__ import annotations

import pytest

from bcc.provider_governance import free_only_refusal
from bcc.providers import ProviderError


@pytest.fixture(autouse=True)
def _default_policy(monkeypatch):
    monkeypatch.delenv("BOSSMAN_ALLOW_PAID_CLOUD", raising=False)


def P(base, kind="openai_compat"):
    return {"kind": kind, "base_url": base, "name": "p"}


def M(name, pi, po, kind="cloud", caps=None):
    return {"kind": kind, "name": name, "alias": name, "price_in": pi, "price_out": po,
            "pricing_known": True, "caps": caps or {}}


@pytest.mark.parametrize("prov,model,ok", [
    (P("https://api.openai.com/v1"), M("gpt-x", 1.0, 2.0), False),          # paid direct
    (P("https://api.openai.com/v1"), M("gpt-x", 0, 0), False),              # hand-typed 0/0
    (P("https://openrouter.ai/api/v1"), M("z-ai/glm-5.3", 0.1, 0.2), False),  # priced OR
    (P("https://openrouter.ai/api/v1"), M("z-ai/glm-5.3", 0, 0), False),    # 0/0 without :free
    (P("https://openrouter.ai/api/v1"), M("qwen/qwen3:free", 0, 0), True),
    (P("https://integrate.api.nvidia.com/v1"), M("m", 0, 0, caps={"free_tier": "nvidia_nim"}), True),
    (P("https://api.groq.com/openai/v1"), M("m", 0, 0, caps={"free_tier": "groq"}), True),
    (P("https://evil.example/v1"), M("m", 0, 0, caps={"free_tier": "groq"}), False),  # forged tag, wrong host
    (P("http://127.0.0.1:11434/v1"), M("q", None, None, kind="local"), True),
    (P("http://192.168.1.5:8080/v1"), M("q", 0, 0, kind="cloud"), True),
])
def test_policy_table(prov, model, ok):
    assert (free_only_refusal(prov, model) == "") is ok


def test_opt_out_is_explicit_only(monkeypatch):
    monkeypatch.setenv("BOSSMAN_ALLOW_PAID_CLOUD", "1")
    assert free_only_refusal(P("https://api.openai.com/v1"), M("gpt-x", 1, 2)) == ""


async def test_adapter_for_refuses_chat_but_not_health(env):
    reg = env.svc.registry
    calls = []

    class Inner:
        async def chat(self, *a, **k):
            calls.append("chat")

        async def health(self, *a, **k):
            calls.append("health")
            return "ok"

    reg.adapter_factory = lambda m, p: Inner()
    prov = await reg.create_provider("oa", "openai_compat", "https://api.openai.com/v1", "sk-x")
    paid = await reg.create_model(provider_id=prov["id"], name="gpt-x", kind="cloud",
                                  price_in=1.0, price_out=2.0)
    adapter, _ = await reg.adapter_for(paid["id"])
    with pytest.raises(ProviderError, match="free-only"):
        await adapter.chat("gpt-x", [])
    assert await adapter.health() == "ok"
    assert calls == ["health"]                      # no request reached the provider


async def test_adapter_for_allows_free_openrouter(env):
    reg = env.svc.registry
    hit = []

    class Inner:
        async def chat(self, *a, **k):
            hit.append(1)
            return "r"

    reg.adapter_factory = lambda m, p: Inner()
    prov = await reg.create_provider("or", "openai_compat", "https://openrouter.ai/api/v1", "k")
    free = await reg.create_model(provider_id=prov["id"], name="qwen/qwen3:free", kind="cloud",
                                  price_in=0.0, price_out=0.0)
    adapter, _ = await reg.adapter_for(free["id"])
    assert await adapter.chat("qwen/qwen3:free", []) == "r" and hit == [1]
