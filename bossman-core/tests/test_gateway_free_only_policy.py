"""Owner rule: product runtime cloud = OpenRouter ':free' only, else local.

No hidden paid fallback: a stray OPENAI/ANTHROPIC key must not create a backend,
and a paid/direct cloud target must be refused before any network call."""
import httpx
import pytest

from bossman.gateway.backends import OpenAIBackend
from bossman.gateway.config import (AliasConfig, BackendConfig, GatewayConfig,
                                    ModelTarget, load_gateway_config)
from bossman.gateway.router import CloudPolicyDenied, ModelRouter


@pytest.fixture(autouse=True)
def _default_policy(monkeypatch):
    monkeypatch.delenv("BOSSMAN_ALLOW_PAID_CLOUD", raising=False)


def _router(targets):
    backends = {
        "ollama": BackendConfig("ollama", "http://local", cloud=False),
        "openrouter": BackendConfig("openrouter", "http://or", cloud=True),
        "openai": BackendConfig("openai", "http://oa", cloud=True),
    }
    cfg = GatewayConfig(backends=backends, aliases={"a": AliasConfig("a", targets)})
    t = httpx.MockTransport(lambda r: httpx.Response(200, json={}))
    return ModelRouter(cfg, {n: OpenAIBackend(c, t) for n, c in backends.items()})


def test_env_keys_do_not_create_paid_backends(monkeypatch, tmp_path):
    for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GROQ_API_KEY", "GOOGLE_API_KEY",
              "MISTRAL_API_KEY", "TOGETHER_API_KEY", "NVIDIA_API_KEY", "ZAI_API_KEY"):
        monkeypatch.setenv(k, "sk-test")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("BOSSMAN_ENV_FILE", str(tmp_path / "none.env"))
    cfg = load_gateway_config(tmp_path / "missing.yaml")
    assert "openrouter" in cfg.backends
    assert not ({"openai", "anthropic", "groq", "google", "mistral", "together",
                 "nvidia", "zai"} & set(cfg.backends))


def test_paid_openrouter_model_is_refused_not_fallen_back_to():
    r = _router([ModelTarget("openrouter", "anthropic/claude-opus-5", 10, set())])
    with pytest.raises(CloudPolicyDenied):
        r.resolve("a", cloud_allowed=True)


def test_direct_paid_provider_target_is_refused():
    r = _router([ModelTarget("openai", "gpt-4o", 10, set())])
    with pytest.raises(CloudPolicyDenied):
        r.resolve("a", cloud_allowed=True)


def test_free_and_local_targets_still_routed_paid_dropped_from_chain():
    r = _router([ModelTarget("ollama", "qwen", 10, set()),
                 ModelTarget("openrouter", "x/y:free", 20, set()),
                 ModelTarget("openrouter", "x/paid", 30, set()),
                 ModelTarget("openai", "gpt-4o", 40, set())])
    routes = r.resolve("a", cloud_allowed=True)
    assert [(x.backend_name, x.model) for x in routes] == [
        ("ollama", "qwen"), ("openrouter", "x/y:free")]


def test_direct_prefix_paid_openrouter_refused_free_allowed():
    r = _router([ModelTarget("ollama", "qwen", 10, set())])
    with pytest.raises(CloudPolicyDenied):
        r.resolve("openrouter/openai/gpt-5", cloud_allowed=True)
    assert r.resolve("openrouter/meta/llama:free", cloud_allowed=True)[0].model == "meta/llama:free"


def test_explicit_operator_opt_in_restores_paid(monkeypatch):
    monkeypatch.setenv("BOSSMAN_ALLOW_PAID_CLOUD", "1")
    r = _router([ModelTarget("openai", "gpt-4o", 10, set())])
    assert r.resolve("a", cloud_allowed=True)
