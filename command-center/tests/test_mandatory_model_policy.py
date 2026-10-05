"""Mandatory model policy (autonomy freeze, line C, tasks 1-2).

1. Liquid/LFM models are rejected on every active route and stay rejected after a settings reload.
2. Main cloud planning models need >=10B parameters AND a fresh live 0/0 price; unknown size or price fails
   closed; <10B is a flagged fallback that may never approve architecture/security/memory/release work.

Fakes only: no network, no real model, no participant data.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import re
from pathlib import Path

import pytest

from bcc.pit import config as pit_config
from bcc.pit import model_policy as mp
from bcc.pit.model_route import route_verdict

from .test_pit_runtime import FakeAdapter, make_runtime, make_settings

FREE = {"prompt": 0.0, "completion": 0.0}
LFM_IDS = ("liquid/lfm-2.5-2.6b:free", "LiquidAI/LFM2-1.2B", "lfm2:1.2b", "liquid/lfm-7b")
REPO = Path(__file__).resolve().parents[2]


# -- task 1: Liquid / LFM ---------------------------------------------------------------------------
@pytest.mark.parametrize("model", LFM_IDS)
def test_lfm_and_liquid_ids_are_banned(model):
    assert mp.is_banned_model(model)
    assert route_verdict(model, True, FREE) == mp.BANNED_MODEL


@pytest.mark.parametrize("model", ("nvidia/nemotron-3-ultra-550b-a55b:free", "qwen2.5:7b", "x/liquidity-70b"))
def test_ordinary_ids_are_not_banned(model):
    assert not mp.is_banned_model(model)


def test_defaults_carry_no_banned_model():
    assert not [m for m in pit_config.DEFAULT_FREE_CHAT_MODELS if mp.is_banned_model(m)]
    from bcc.jev import config as jev_config
    assert not mp.is_banned_model(jev_config.DEFAULT_MODEL)


_SCAN_ROOTS = ("command-center/bcc", "bossman-core/bossman", "bossman-core/config", "bossman_shared", "config")
_SCAN_SUFFIXES = {".py", ".json", ".toml", ".yaml", ".yml", ".js", ".ps1", ".env", ".example"}
# The files that DEFINE the ban necessarily name it.
_BAN_DEFINITIONS = {"command-center/bcc/pit/model_policy.py", "bossman-core/bossman/gateway/router.py"}
_MODEL_LITERAL = re.compile(r"[\"'][^\"'\s]*(?:lfm|liquid/)[^\"'\s]*[\"']", re.I)


def test_no_active_route_or_default_names_a_liquid_model():
    hits = []
    for root in _SCAN_ROOTS:
        base = REPO / root
        if not base.exists():
            continue
        for path in base.rglob("*"):
            rel = path.relative_to(REPO).as_posix()
            if not path.is_file() or path.suffix not in _SCAN_SUFFIXES or rel in _BAN_DEFINITIONS:
                continue
            if "/tests/" in f"/{rel}" or "node_modules" in rel:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            # regex sources (detectors) are not model ids: ids never contain a backslash or "|"
            hits += [f"{rel}: {m.group(0)}" for m in _MODEL_LITERAL.finditer(text)
                     if "\\" not in m.group(0) and "|" not in m.group(0)]
    assert hits == []


def test_settings_refuse_a_banned_model_directly(tmp_path):
    with pytest.raises(ValueError, match="banned"):
        dataclasses.replace(make_settings(tmp_path), chat_models=("liquid/lfm-2.5-2.6b:free", "a/b:free"))
    with pytest.raises(ValueError, match="banned"):
        dataclasses.replace(make_settings(tmp_path), local_url="http://127.0.0.1:11434/v1",
                            local_models=("lfm2:1.2b",))


def _write_config(home: Path, chat_models, local_models=()):
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.json").write_text(json.dumps({
        "people": [{"user_id": 101, "chat_id": 101, "role": "owner"}],
        "chat_models": list(chat_models), "provider_base_url": "https://openrouter.ai/api/v1",
        "core_url": "http://127.0.0.1:8800", "local_url": "http://127.0.0.1:11434/v1" if local_models else "",
        "local_models": list(local_models)}), encoding="utf-8")
    return home / "config.json"


@pytest.fixture
def secrets_env(monkeypatch):
    monkeypatch.setenv("BOSSMAN_PIT_BOT_TOKEN", "fake-bot-token")
    monkeypatch.setenv("BOSSMAN_PIT_PROVIDER_KEY", "fake-provider-key")
    monkeypatch.delenv("BOSSMAN_PIT_LOCAL_MODELS", raising=False)
    monkeypatch.delenv("BOSSMAN_PIT_LOCAL_URL", raising=False)
    monkeypatch.setattr(pit_config, "_read_credentials", lambda home: {"identity_salt": "cd" * 32})


def test_banned_models_stay_rejected_across_reload_and_restart(tmp_path, monkeypatch, secrets_env):
    path = _write_config(tmp_path / "pit-v1.7", ["liquid/lfm-2.5-2.6b:free", "good/big-70b:free"],
                         local_models=["lfm2:1.2b", "qwen3:14b"])
    monkeypatch.setenv("BOSSMAN_PIT_LOCAL_MODELS", "lfm2:1.2b,qwen3:14b")
    for _restart in range(2):                         # load -> "restart" -> load again
        settings = pit_config.load(path)
        assert settings.chat_models == ("good/big-70b:free",)
        assert settings.local_models == ("qwen3:14b",)
        assert set(settings.rejected_models) == {"liquid/lfm-2.5-2.6b:free", "lfm2:1.2b"}


def test_only_banned_chat_models_fall_back_to_the_safe_defaults(tmp_path, secrets_env):
    path = _write_config(tmp_path / "pit-v1.7", ["liquid/lfm-2.5-2.6b:free"])
    settings = pit_config.load(path)
    assert settings.chat_models == pit_config.DEFAULT_FREE_CHAT_MODELS
    assert settings.rejected_models == ("liquid/lfm-2.5-2.6b:free",)


def test_runtime_catalog_never_admits_a_banned_model_even_if_it_slips_in(tmp_path):
    pricing = {"liquid/lfm-2.5-2.6b:free": FREE, "free/model:free": FREE}
    runtime = make_runtime(tmp_path, adapter=FakeAdapter(pricing=pricing))
    # simulate an old in-memory settings object that predates the ban
    object.__setattr__(runtime.settings, "chat_models", tuple(pricing))
    try:
        catalog = asyncio.run(runtime.refresh_catalog())
        assert set(catalog) == {"free/model:free"}
        assert runtime.model_route_status()["rejected"]["liquid/lfm-2.5-2.6b:free"] == mp.BANNED_MODEL
    finally:
        asyncio.run(runtime.close())


def test_governance_refuses_banned_models_local_and_cloud():
    from bcc.provider_governance import free_only_refusal
    local = {"kind": "ollama", "base_url": "http://127.0.0.1:11434"}
    cloud = {"kind": "openai_compat", "base_url": "https://openrouter.ai/api/v1"}
    assert "banned" in free_only_refusal(local, {"name": "lfm2:1.2b", "kind": "local"})
    assert "banned" in free_only_refusal(cloud, {"name": "liquid/lfm-2.5-2.6b:free", "price_in": 0.0,
                                                 "price_out": 0.0})
    assert free_only_refusal(local, {"name": "qwen3:14b", "kind": "local"}) == ""


def test_governed_adapter_refuses_banned_model_before_the_provider():
    from bcc.provider_governance import GovernedAdapter
    from bcc.providers import ProviderError
    calls = []

    class Inner:
        async def chat(self, *a, **k):
            calls.append(1)

    adapter = GovernedAdapter(Inner(), {"kind": "ollama", "base_url": "http://127.0.0.1:11434"},
                              {"name": "lfm2:1.2b", "kind": "local"})
    with pytest.raises(ProviderError, match="banned"):
        asyncio.run(adapter.chat("lfm2:1.2b", [{"role": "user", "content": "hi"}]))
    assert calls == []


def test_core_gateway_drops_banned_targets():
    import httpx
    from bossman.gateway.backends import OpenAIBackend
    from bossman.gateway.config import AliasConfig, BackendConfig, GatewayConfig, ModelTarget
    from bossman.gateway.router import CloudPolicyDenied, ModelRouter
    backends = {"ollama": BackendConfig("ollama", "http://local", cloud=False),
                "openrouter": BackendConfig("openrouter", "http://or", cloud=True)}
    cfg = GatewayConfig(backends=backends, aliases={"a": AliasConfig("a", [
        ModelTarget("ollama", "lfm2:1.2b", 5, set()), ModelTarget("ollama", "qwen", 10, set()),
        ModelTarget("openrouter", "liquid/lfm-2.5-2.6b:free", 20, set())])})
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={}))
    router = ModelRouter(cfg, {n: OpenAIBackend(c, transport) for n, c in backends.items()})
    assert [(r.backend_name, r.model) for r in router.resolve("a")] == [("ollama", "qwen")]
    with pytest.raises(CloudPolicyDenied):
        router.resolve("openrouter/liquid/lfm-2.5-2.6b:free")


def test_jev_model_override_cannot_select_a_banned_model(monkeypatch):
    from bcc.jev import config as jev_config
    monkeypatch.setenv(jev_config.MODEL_ENV, "liquid/lfm-7b")
    assert jev_config.load().model == jev_config.DEFAULT_MODEL


# -- task 2: >=10B + live 0/0 for main planning ----------------------------------------------------
NOW = 1_000_000.0


def test_curated_nemotron_size_is_recorded_with_source_and_time():
    info = mp.resolve_params("nvidia/nemotron-3-ultra-550b-a55b:free", now=NOW)
    assert info.total_b == 550 and info.active_b == 55
    assert info.source == "curated" and info.resolved_at


@pytest.mark.parametrize("row,expected,source", [
    ({"id": "x/foo-70b-instruct:free"}, 70.0, "catalog:id"),
    ({"id": "x/foo:free", "hugging_face_id": "org/Foo-32B-Instruct"}, 32.0, "catalog:hugging_face_id"),
    ({"id": "x/foo:free", "name": "Foo 8x7B"}, 56.0, "catalog:name"),
    ({"id": "local-gguf", "meta": {"n_params": 7_241_732_096}}, 7.24, "catalog:meta.n_params"),
])
def test_catalog_fields_resolve_parameter_counts(row, expected, source):
    info = mp.resolve_params(row["id"], row, now=NOW)
    assert info.total_b == pytest.approx(expected, rel=0.01) and info.source == source


def test_unknown_size_fails_closed():
    info = mp.resolve_params("nex-agi/nex-n2.5-pro:free", {"id": "nex-agi/nex-n2.5-pro:free"}, now=NOW)
    assert info.total_b is None and info.source == "unknown"
    decision = mp.planning_decision("nex-agi/nex-n2.5-pro:free", listed=True, prices=FREE,
                                    row={"id": "nex-agi/nex-n2.5-pro:free"}, price_checked_at=NOW, now=NOW)
    assert not decision.allowed and decision.reason == mp.PARAMS_UNKNOWN


def _decide(model, prices=FREE, checked=NOW, **kw):
    return mp.planning_decision(model, listed=True, prices=prices, row={"id": model},
                                price_checked_at=checked, now=NOW, **kw)


def test_big_free_fresh_model_is_a_main_planner_that_may_approve():
    decision = _decide("nvidia/nemotron-3-ultra-550b-a55b:free")
    assert decision.tier == "main" and decision.allowed
    assert all(decision.may_approve[scope] for scope in mp.APPROVAL_SCOPES)
    record = decision.to_dict()
    assert record["params"]["source"] == "curated" and record["price_verified_at"]
    assert record["schema"] == mp.MODEL_POLICY_SCHEMA


@pytest.mark.parametrize("prices,checked,reason", [
    (None, NOW, "price_unknown"),
    ({"prompt": 0.0, "completion": None}, NOW, "price_unknown"),
    ({"prompt": 1e-7, "completion": 0.0}, NOW, "price_positive"),
    (FREE, None, mp.PRICE_STALE),
    (FREE, NOW - 10_000, mp.PRICE_STALE),
])
def test_price_must_be_live_zero_zero(prices, checked, reason):
    decision = _decide("x/big-70b:free", prices=prices, checked=checked)
    assert not decision.allowed and decision.reason == reason


def test_small_model_is_rejected_unless_explicitly_a_flagged_fallback():
    assert _decide("x/tiny-8b:free").reason == mp.PARAMS_BELOW_MIN
    fallback = _decide("x/tiny-8b:free", allow_small_fallback=True)
    assert fallback.tier == "fallback" and fallback.allowed
    assert fallback.may_approve == {scope: False for scope in mp.APPROVAL_SCOPES}
    assert not mp.may_approve(fallback, "security")


def test_banned_model_is_rejected_whatever_its_size_or_price():
    assert _decide("liquid/lfm-70b:free").reason == mp.BANNED_MODEL


def test_selection_uses_fallback_only_when_no_main_planner_exists():
    rows = {m: {"id": m} for m in ("x/big-70b:free", "x/tiny-8b:free", "x/huge-405b:free", "x/mystery:free")}
    pricing = {m: FREE for m in rows}
    chosen = mp.select_planning_models(list(rows), rows=rows, pricing=pricing, price_checked_at=NOW, now=NOW)
    assert [d.model for d in chosen] == ["x/huge-405b:free", "x/big-70b:free"]
    only_small = mp.select_planning_models(["x/tiny-8b:free", "x/mystery:free"], rows=rows, pricing=pricing,
                                           price_checked_at=NOW, now=NOW)
    assert [(d.model, d.tier) for d in only_small] == [("x/tiny-8b:free", "fallback")]
    assert not only_small[0].may_approve["release"]


def test_live_evaluation_reads_the_catalog_once_and_records_it():
    adapter = FakeAdapter(pricing={"nvidia/nemotron-3-ultra-550b-a55b:free": FREE,
                                   "x/tiny-8b:free": FREE})
    chosen = asyncio.run(mp.evaluate_live(adapter, ["nvidia/nemotron-3-ultra-550b-a55b:free", "x/tiny-8b:free",
                                                     "x/absent-70b:free"], now=NOW))
    assert [d.model for d in chosen if d.allowed] == ["nvidia/nemotron-3-ultra-550b-a55b:free"]
    by_model = {d.model: d for d in chosen}
    assert by_model["x/absent-70b:free"].reason == "not_listed"


def test_jeff_route_status_reports_parameter_policy(tmp_path):
    model = "nvidia/nemotron-3-ultra-550b-a55b:free"
    settings = dataclasses.replace(make_settings(tmp_path), chat_models=(model,))
    runtime = make_runtime(tmp_path, adapter=FakeAdapter(pricing={model: FREE}), settings=settings)
    try:
        asyncio.run(runtime.refresh_catalog())
        policy = runtime.model_route_status()["policy"][model]
        assert policy["tier"] == "main" and policy["params"]["total_b"] == 550
    finally:
        asyncio.run(runtime.close())
