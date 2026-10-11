"""Long-run budget profile: opt-in, capped per provider per day, STOP-aware, loud on refusal.

The DEFAULT profile must stay exactly as it was: free-only gate, $2.00 / 400k
per-mission ceilings.  Every test uses its own autonomy root (tmp) so the
owner's real ledger is never touched.
"""
from __future__ import annotations

import json

import pytest

from bcc import long_run, mission_budget
from bcc.provider_governance import GovernedAdapter, free_only_refusal
from bcc.providers import ChatResult, ProviderError

OR = "https://openrouter.ai/api/v1"
MISTRAL = "https://api.mistral.ai/v1"


def prov(base):
    return {"kind": "openai_compat", "base_url": base, "name": "p"}


def model(name, pin=0.5, pout=2.0):          # registry units: USD per 1M tokens
    return {"kind": "cloud", "name": name, "alias": name, "pricing_known": True,
            "price_in": pin, "price_out": pout}


class Inner:
    def __init__(self, cost=None, tokens=(1000, 500)):
        self.calls, self.cost, self.tokens = 0, cost, tokens

    async def chat(self, *a, **kw):
        self.calls += 1
        meta = {"usage": {"cost": self.cost}} if self.cost is not None else {}
        return ChatResult(text="ok", tokens_in=self.tokens[0], tokens_out=self.tokens[1], provider_meta=meta)


@pytest.fixture(autouse=True)
def root(tmp_path, monkeypatch):
    monkeypatch.delenv("BOSSMAN_ALLOW_PAID_CLOUD", raising=False)     # product default: free only
    monkeypatch.delenv("BOSSMAN_LOCAL_ONLY", raising=False)
    r = tmp_path / "autonomy"
    monkeypatch.setenv("BOSSMAN_AUTONOMY_ROOT", str(r))
    return r


def enable(root, **caps):
    long_run.write_profile(enable=True, caps=caps or None, root=root)


# ----------------------------------------------------------------- default unchanged
def test_default_profile_is_unchanged(root):
    assert not long_run.enabled()
    for name, base in (("z-ai/glm-5.3-flash", OR), ("anthropic/claude-haiku-5.5", OR),
                       ("mistral-large-4", MISTRAL)):
        assert free_only_refusal(prov(base), model(name)), name           # still refused
    lim = mission_budget.Limits.for_task({"meta": {"budget_profile": "long_run"}})
    assert lim.max_cost_usd == mission_budget.DEFAULT_MAX_COST_USD == 2.0
    assert lim.max_tokens == mission_budget.DEFAULT_MAX_TOKENS
    assert mission_budget.Limits.for_task(None).max_cost_usd == 2.0


def test_disabled_or_broken_profile_file_changes_nothing(root):
    p = long_run.profile_path(root)
    p.parent.mkdir(parents=True)
    for body in ("{not json", json.dumps({"enabled": "yes"}), json.dumps({"enabled": False}), "[]"):
        p.write_text(body, encoding="utf-8")
        assert not long_run.enabled()
        assert free_only_refusal(prov(OR), model("z-ai/glm-5.3-flash"))


# ----------------------------------------------------------------- enabled: exactly three families
def test_enabled_admits_exactly_the_three_approved_families_on_their_hosts(root):
    enable(root)
    ok = [("z-ai/glm-5.3-flash", OR), ("anthropic/claude-haiku-5.5", OR), ("mistral-large-4", MISTRAL),
          ("mistral-large-4-0", MISTRAL)]
    for name, base in ok:
        assert free_only_refusal(prov(base), model(name)) == "", name
    no = [("z-ai/glm-5.3-flash", "https://evil.example/v1"),       # lookalike host
          ("mistral-large-4", OR),                                  # wrong host for the family
          ("openai/gpt-5", OR), ("anthropic/claude-opus-5", OR),   # other paid models stay refused
          ("mistral-medium-3.5", MISTRAL)]
    for name, base in no:
        assert free_only_refusal(prov(base), model(name)), (name, base)
    assert long_run.classify("z-ai/glm-5.3-flash:free", OR) is None  # free variant is not a paid call


def test_caps_are_clamped_to_the_hard_maximum(root):
    long_run.write_profile(enable=True, caps={"glm": 500, "haiku": 3.5, "mistral": -1}, root=root)
    caps = long_run.load_profile(root)["caps_eur_per_day"]
    assert caps == {"mistral": 10.0, "glm": 10.0, "haiku": 3.5}
    # a hand-edited file cannot exceed the maximum either
    long_run.profile_path(root).write_text(json.dumps(
        {"enabled": True, "caps_eur_per_day": {"glm": 99999, "haiku": "x", "mistral": True}}), encoding="utf-8")
    assert long_run.load_profile(root)["caps_eur_per_day"] == {"mistral": 10.0, "glm": 10.0, "haiku": 10.0}


# ----------------------------------------------------------------- per-call enforcement
async def test_paid_call_is_booked_and_report_shows_it(root):
    enable(root)
    inner = Inner(cost=0.02)
    g = GovernedAdapter(inner, prov(OR), model("z-ai/glm-5.3-flash"))
    await g.chat("z-ai/glm-5.3-flash", [{"role": "user", "content": "hi"}], max_tokens=100)
    assert inner.calls == 1
    rep = long_run.LongRunLedger(root).report()
    assert rep["profile_enabled"] and rep["providers"]["glm"]["spent_eur"] == pytest.approx(0.02)
    assert rep["providers"]["glm"]["calls"] == 1
    assert rep["providers"]["glm"]["remaining_eur"] == pytest.approx(9.98)
    assert rep["providers"]["haiku"]["spent_eur"] == 0 and rep["refusals_today"] == 0


async def test_cap_hit_is_loud_and_the_provider_is_not_called(root, caplog):
    enable(root, glm=0.001)
    inner = Inner(cost=0.0005)
    g = GovernedAdapter(inner, prov(OR), model("z-ai/glm-5.3-flash", pin=1.0, pout=10.0))
    with caplog.at_level("ERROR", logger="bcc.long_run"):
        with pytest.raises(ProviderError) as ei:
            await g.chat("z-ai/glm-5.3-flash", [{"role": "user", "content": "x" * 5000}], max_tokens=4000)
    assert ei.value.kind == "budget" and "LONG-RUN CAP HIT" in str(ei.value) and "NOT made" in str(ei.value)
    assert inner.calls == 0
    assert any("LONG-RUN CAP HIT" in r.message for r in caplog.records)
    rep = long_run.LongRunLedger(root).report()
    assert rep["refusals_today"] == 1 and "CAP HIT" in rep["last_refusal"]["why"]


async def test_cap_is_per_provider_and_per_day_total_not_per_call(root):
    enable(root, glm=0.05, haiku=5.0)
    glm = GovernedAdapter(Inner(cost=0.03), prov(OR), model("z-ai/glm-5.3-flash"))
    msgs = [{"role": "user", "content": "q"}]
    await glm.chat("m", msgs, max_tokens=10)
    with pytest.raises(ProviderError):                       # 0.03 booked, worst case + 0.03 > 0.05? make it so
        big = GovernedAdapter(Inner(), prov(OR), model("z-ai/glm-5.3-flash", pin=1.0, pout=1000.0))
        await big.chat("m", msgs, max_tokens=50)             # worst case 0.05 > 0.02 left
    haiku = GovernedAdapter(Inner(cost=0.01), prov(OR), model("anthropic/claude-haiku-5.5"))
    await haiku.chat("m", msgs, max_tokens=10)               # other provider unaffected
    rep = long_run.LongRunLedger(root).report()
    assert rep["providers"]["haiku"]["spent_eur"] == pytest.approx(0.01)


async def test_unknown_outcome_keeps_the_worst_case_booked(root):
    enable(root)

    class Boom:
        async def chat(self, *a, **kw):
            raise ProviderError("timeout", kind="network")

    g = GovernedAdapter(Boom(), prov(MISTRAL), model("mistral-large-4", pin=1.0, pout=10.0))
    with pytest.raises(ProviderError):
        await g.chat("mistral-large-4", [{"role": "user", "content": "hello"}], max_tokens=1000)
    spent = long_run.LongRunLedger(root).spent_today()["mistral"]
    assert spent == pytest.approx(5 * 1e-6 + 1000 * 1e-5, rel=0.3)   # provider may have billed: not released


async def test_stop_file_blocks_every_paid_call(root):
    enable(root)
    long_run.request_stop("owner pressed STOP", root=root)
    assert (root / "STOP").is_file()
    inner = Inner(cost=0.01)
    g = GovernedAdapter(inner, prov(OR), model("anthropic/claude-haiku-5.5"))
    with pytest.raises(ProviderError) as ei:
        await g.chat("m", [{"role": "user", "content": "x"}], max_tokens=10)
    assert "STOP" in str(ei.value) and inner.calls == 0
    assert long_run.LongRunLedger(root).report()["stop"]
    long_run.clear_stop(root)
    await g.chat("m", [{"role": "user", "content": "x"}], max_tokens=10)
    assert inner.calls == 1


async def test_free_models_and_local_never_touch_the_ledger(root):
    enable(root)
    inner = Inner()
    g = GovernedAdapter(inner, prov(OR), model("nvidia/nemotron-3-ultra-550b-a55b:free", 0, 0))
    await g.chat("m", [{"role": "user", "content": "x"}], max_tokens=10)
    loc = GovernedAdapter(Inner(), {"kind": "openai_compat", "base_url": "http://127.0.0.1:8081/v1", "name": "l"},
                          {"kind": "local", "name": "z-ai/glm-5.3-flash", "alias": "x", "pricing_known": False})
    await loc.chat("m", [{"role": "user", "content": "x"}], max_tokens=10)
    rep = long_run.LongRunLedger(root).report()
    assert all(p["calls"] == 0 for p in rep["providers"].values())


# ----------------------------------------------------------------- mission ceilings
def test_long_run_mission_limits_are_larger_but_finite(root):
    enable(root)
    lim = mission_budget.Limits.for_task({"meta": {"budget_profile": "long_run"}})
    assert lim.max_cost_usd == mission_budget.LONG_RUN_MAX_COST_USD > mission_budget.DEFAULT_MAX_COST_USD
    assert 0 < lim.max_tokens == mission_budget.LONG_RUN_MAX_TOKENS < 10_000_000
    plain = mission_budget.Limits.for_task({"meta": {}})
    assert plain.max_cost_usd == 2.0                       # a task that does not ask keeps the default
    explicit = mission_budget.Limits.for_task({"meta": {"budget_profile": "long_run", "max_cost_usd": 1.5}})
    assert explicit.max_cost_usd == 1.5                    # explicit meta still wins
    assert mission_budget.check_spend(lim, tokens_in=3_000_001, tokens_out=0, cost_usd=0.1) is not None
    assert mission_budget.check_spend(lim, tokens_in=0, tokens_out=0, cost_usd=12.5) is not None


def test_cli_roundtrip(root, capsys):
    assert long_run.main(["--root", str(root), "enable", "--glm", "7", "--haiku", "20", "--mistral", "1.5"]) == 0
    assert long_run.load_profile(root)["caps_eur_per_day"] == {"mistral": 1.5, "glm": 7.0, "haiku": 10.0}
    long_run.main(["--root", str(root), "stop", "--reason", "t"])
    assert (root / "STOP").is_file()
    long_run.main(["--root", str(root), "resume"])
    assert not (root / "STOP").exists()
    capsys.readouterr()
    long_run.main(["--root", str(root), "report"])
    data = json.loads(capsys.readouterr().out)
    assert data["providers"]["glm"]["cap_eur"] == 7.0
    long_run.main(["--root", str(root), "disable"])
    assert not long_run.enabled(root)


def test_price_units_registry_per_million_vs_orchestrator_per_token():
    reg = {"price_in": 0.075, "price_out": 0.25}                       # USD per 1M tokens
    orch = {"price_in": 0.075 / 1e6, "price_out": 0.25 / 1e6, "price_unit": "per_token"}
    msgs = [{"role": "user", "content": "x" * 1000}]
    a = long_run.estimate_worst_case_eur(reg, msgs, 1000)
    b = long_run.estimate_worst_case_eur(orch, msgs, 1000)
    assert a == pytest.approx(b) and 0 < a < 0.001
