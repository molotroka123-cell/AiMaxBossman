"""Phase-2 Jev is a bounded preference over Smart Router's authorized set."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from bcc.features.router import _jev_bounded_choice, _jev_route_hint
from bcc.jev import config as jev_config
from bcc.v2.model_router import ModelCandidate, RouteRequest, route


def _models():
    return [
        ModelCandidate(1, "local-good", local=True, online=True, price_in=0, price_out=0),
        ModelCandidate(2, "cloud-free", local=False, online=True, price_in=0, price_out=0),
        ModelCandidate(3, "cloud-unknown", local=False, online=True, price_in=None, price_out=None),
        ModelCandidate(4, "cloud-offline", local=False, online=False, price_in=0, price_out=0),
    ]


def test_jev_bucket_never_bypasses_cloud_price_health_or_policy():
    models = _models()
    req = RouteRequest("generic", cloud_allowed=False)
    assert _jev_bounded_choice(req, models, "cloud_reasoner") is None
    req.cloud_allowed = True
    assert _jev_bounded_choice(req, models, "cloud_reasoner").model.alias == "cloud-free"
    assert _jev_bounded_choice(req, models, "local_fast").model.alias == "local-good"
    assert _jev_bounded_choice(req, models, "other") is None


def test_no_key_or_unknown_jev_price_uses_original_router(monkeypatch, tmp_path):
    monkeypatch.setenv("BOSSMAN_JEV_ENABLED", "1")
    monkeypatch.setenv("BOSSMAN_JEV_SHADOW", "0")
    monkeypatch.setenv("BOSSMAN_JEV_KILL_FILE", str(tmp_path / "jev.disabled"))
    monkeypatch.delenv("BOSSMAN_JEV_ZERO_COST_CONFIRMED", raising=False)
    monkeypatch.delenv("BOSSMAN_JEV_API_KEY", raising=False)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    rows = []
    state = SimpleNamespace(cfg=jev_config.load(), recorder=SimpleNamespace(write=rows.append),
                            provider=SimpleNamespace(decide=lambda _ctx: (_ for _ in ()).throw(
                                AssertionError("Jev must not be called"))))
    req = RouteRequest("generic", cloud_allowed=True)
    baseline = route(req, _models())
    chosen, reason = asyncio.run(_jev_route_hint(SimpleNamespace(jev=state),
        {"id": 8, "kind": "generic", "prompt": "public"}, {},
        {"privacy": "public", "jev_egress_allowed": True}, req, _models(), baseline))
    assert chosen.model.id == baseline.model.id
    assert reason == "price_not_confirmed_free" and rows[-1]["authoritative"] is False


def test_valid_jev_hint_selects_only_authorized_model_and_private_falls_back(monkeypatch, tmp_path):
    for name, value in {
        "BOSSMAN_JEV_ENABLED": "1", "BOSSMAN_JEV_SHADOW": "0",
        "BOSSMAN_JEV_API_KEY": "jev-TEST-CANARY-NOT-REAL",  # ci-secret-scan: allow
        "BOSSMAN_JEV_ZERO_COST_CONFIRMED": "1",
        "BOSSMAN_JEV_PRICE_PER_CALL_USD": "0",
        "BOSSMAN_JEV_PRICE_PER_1K_INPUT_USD": "0",
        "BOSSMAN_JEV_PRICE_PER_1K_OUTPUT_USD": "0",
        "BOSSMAN_JEV_KILL_FILE": str(tmp_path / "jev.disabled"),
    }.items():
        monkeypatch.setenv(name, value)
    called = []
    rows = []
    def decide(_ctx):
        called.append(True)
        return SimpleNamespace(model_route="cloud_reasoner", low_confidence=False,
                               tool_route="terminal", needs_owner_approval=False)
    state = SimpleNamespace(cfg=jev_config.load(), recorder=SimpleNamespace(write=rows.append),
                            provider=SimpleNamespace(decide=decide))
    req = RouteRequest("generic", cloud_allowed=True)
    baseline = route(req, _models())
    task = {"id": 9, "kind": "generic", "prompt": "public information"}
    svc = SimpleNamespace(jev=state)
    chosen, reason = asyncio.run(_jev_route_hint(svc, task, {},
        {"privacy": "public", "jev_egress_allowed": True}, req, _models(), baseline))
    assert chosen.model.alias == "cloud-free" and reason is None and len(called) == 1
    assert rows[-1]["selected_alias"] == "cloud-free"
    chosen, reason = asyncio.run(_jev_route_hint(svc, task, {},
        {"privacy": "private", "jev_egress_allowed": True}, req, _models(), baseline))
    assert chosen.model.id == baseline.model.id and reason == "egress_not_allowed"
    assert len(called) == 1
