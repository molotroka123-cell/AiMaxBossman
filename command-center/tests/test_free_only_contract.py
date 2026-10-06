"""FREE_ONLY product mode, the parts the registry table does not pin: unknown price, the opt-out switch, the Fable exception."""
from __future__ import annotations

import pytest

from bcc.provider_governance import ALLOW_PAID_CLOUD_ENV, free_only_policy_active, free_only_refusal


def P(base, kind="openai_compat"):
    return {"kind": kind, "base_url": base, "name": "p"}


def M(name, pi, po, caps=None):
    return {"kind": "cloud", "name": name, "alias": name, "price_in": pi, "price_out": po, "caps": caps or {}}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv(ALLOW_PAID_CLOUD_ENV, raising=False)


def test_the_mode_is_on_when_nothing_is_configured():
    assert free_only_policy_active() is True


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "paid", "TRUE ", " yes"])
def test_only_an_explicit_truthy_opt_out_switches_it_off(monkeypatch, value):
    monkeypatch.setenv(ALLOW_PAID_CLOUD_ENV, value)
    assert free_only_policy_active() is (value.strip().lower() not in {"1", "true", "yes"})


@pytest.mark.parametrize("prov,model", [
    (P("https://api.openai.com/v1"), M("gpt-x", None, None)),                       # unknown price, direct cloud
    (P("https://openrouter.ai/api/v1"), M("vendor/model", None, None)),             # unknown price, no ':free'
    (P("https://openrouter.ai/api/v1"), M("vendor/model", "n/a", "n/a")),           # non-numeric price is not proof
    (P("https://openrouter.ai/api/v1"), M("vendor/model:free-ish", 0, 0)),          # suffix must be exactly ':free'
])
def test_an_unknown_or_unproven_price_is_refused_not_guessed(prov, model):
    assert free_only_refusal(prov, model)


def test_a_priced_free_named_model_is_still_refused():
    assert free_only_refusal(P("https://openrouter.ai/api/v1"), M("vendor/model:free", 0.5, 0.5))


def test_the_exception_is_exactly_the_owner_capped_anthropic_route():
    """Not part of the claim «no paid path»: an Anthropic-kind cloud provider is judged by bcc.fable_cap (hard USD ceiling),
    not by this rule. Pinned so that widening it to other providers fails here."""
    assert free_only_refusal(P("https://api.anthropic.com", kind="anthropic"), M("claude-x", 3.0, 15.0)) == ""
    assert free_only_refusal(P("https://api.openai.com/v1", kind="openai_compat"), M("gpt-x", 3.0, 15.0))
