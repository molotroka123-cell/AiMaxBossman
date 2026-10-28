"""SpendFirewall free gate (audit 28.10): «free» must be proven from the parsed host, the price and the provider, not from text.

Found by probing the gate: any provider whose URL merely CONTAINED «127.0.0.1» or «localhost» (host label, path, user@) was
treated as free even with a real price, any model name ending in :free was free on every provider, and a free flag beat a real
price. Every case below is a way the old gate said «free» for a paid call.
"""
from __future__ import annotations

import pytest

from bcc.security.spend_firewall import PaidCallBlockedError, SpendFirewall, TokenInvalidOrConsumedError

PAID = "https://api.paid-vendor.example/v1"


@pytest.fixture
def fw() -> SpendFirewall:
    return SpendFirewall()


@pytest.mark.parametrize("provider,model", [
    ({"kind": "ollama", "base_url": "http://127.0.0.1:11434"}, "qwen3:8b"),
    ({"kind": "openai", "base_url": "http://localhost:1234/v1"}, "any-local-model"),
    ({"kind": "openai", "base_url": "http://[::1]:8080/v1"}, "any-local-model"),
    ({"kind": "llama_cpp"}, "gguf-model"),                                         # no URL: the kind label is all there is
])
def test_real_local_providers_are_free(fw, provider, model):
    assert fw.is_free_model(provider, model) is True


@pytest.mark.parametrize("url", [
    "https://127.0.0.1.attacker.example/v1",           # a host label that starts like loopback
    "https://localhost.attacker.example/v1",
    "https://api.paid-vendor.example/proxy/127.0.0.1/v1",   # in the path
    "http://127.0.0.1@api.paid-vendor.example/v1",          # before the @ (the host is the part after it)
    "https://api.paid-vendor.example/v1?upstream=localhost",
])
def test_a_loopback_text_inside_a_remote_url_does_not_make_the_call_free(fw, url):
    assert fw.is_free_model({"kind": "openai", "base_url": url}, "gpt-big") is False
    assert fw.is_free_model({"kind": "openai", "base_url": url}, "gpt-big", {"prompt": 0.00003, "completion": 0.00006}) is False


def test_free_suffix_counts_only_on_openrouter(fw):
    openrouter = {"kind": "openai_compat", "base_url": "https://openrouter.ai/api/v1"}
    assert fw.is_free_model(openrouter, "nvidia/nemotron:free") is True
    assert fw.is_free_model({"kind": "openai", "base_url": PAID}, "gpt-5-pro:free") is False
    assert fw.is_free_model(openrouter, "anthropic/claude-haiku") is False                  # no :free, no zero price


def test_an_explicit_price_beats_flags_and_names(fw):
    paid = {"kind": "openai", "base_url": PAID}
    assert fw.is_free_model(paid, {"name": "x", "free": True}, {"price_in": 3.0}) is False
    openrouter = {"kind": "openai_compat", "base_url": "https://openrouter.ai/api/v1"}
    assert fw.is_free_model(openrouter, "vendor/model:free", {"prompt": 0.000001}) is False   # contradictory: treat as paid


def test_zero_price_is_free_but_a_missing_price_is_not(fw):
    paid = {"kind": "openai", "base_url": PAID}
    assert fw.is_free_model(paid, "m", {"prompt": 0, "completion": 0}) is True
    assert fw.is_free_model(paid, "m", {}) is False
    assert fw.is_free_model(paid, "m", None) is False


def test_cloud_models_served_through_a_local_ollama_are_not_free(fw):
    assert fw.is_free_model({"kind": "ollama", "base_url": "http://127.0.0.1:11434"}, "gpt-oss:120b-cloud") is False


def test_verify_blocks_the_attacker_url_and_the_token_is_single_use(fw):
    attacker = {"kind": "openai", "base_url": "https://127.0.0.1.attacker.example/v1", "name": "evil"}
    with pytest.raises(PaidCallBlockedError):
        fw.verify_pre_call_free_or_authorized(attacker, "gpt-5-pro", {"prompt": 0.00003})
    token = fw.issue_one_time_owner_token(purpose="test", max_usd=1.0)
    assert fw.verify_pre_call_free_or_authorized(attacker, "gpt-5-pro", {"prompt": 0.00003}, owner_token=token)["status"] == "AUTHORIZED_PAID"
    with pytest.raises(TokenInvalidOrConsumedError):
        fw.verify_pre_call_free_or_authorized(attacker, "gpt-5-pro", {"prompt": 0.00003}, owner_token=token)
