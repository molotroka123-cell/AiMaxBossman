"""Defensive regression tests; never invoke strategy or financial execution."""
import asyncio
import ast
from pathlib import Path

import pytest

from solana_volume_suite.core.liquidity_gate import check_liquidity, fetch_pool_reserves, split_order_if_needed


def reserves():
    return dict(model="CONSTANT_PRODUCT", input_asset="SOL", reserve_in=500 * 10**9,
                reserve_out=10**12, fee_bps=25)


def test_missing_adapter_is_unknown():
    assert fetch_pool_reserves("mint", "https://unused.invalid")["status"] == "UNKNOWN"
    assert check_liquidity(100, {}, {})["execution_allowed"] is False


def test_small_order_is_hypothetically_safe_but_never_authorized():
    result = check_liquidity(10**8, reserves(), {})
    assert result["liquidity_gate_status"] == "PASS"
    assert result["estimated_impact_bps"] <= 120
    assert result["execution_allowed"] is False
    assert result["pool_liquidity_usd"] is None


def test_large_order_cannot_bypass_total_impact_by_splitting():
    amount = 50 * 10**9
    assert check_liquidity(amount, reserves(), {})["liquidity_gate_status"] == "BLOCK"
    assert split_order_if_needed(amount, reserves(), 120) == []
    assert split_order_if_needed(10**8, reserves(), 120) == [10**8]


def test_hard_minimum_and_rounding():
    r = reserves()
    r["reserve_in"] -= 1
    assert check_liquidity(10**8, r, {})["liquidity_gate_status"] == "BLOCK"
    r = reserves()
    r["reserve_out"] = 1
    assert check_liquidity(1, r, {})["estimated_impact_bps"] == 10000


@pytest.mark.parametrize("amount", [0, -1, True, 0.1, float("nan"), None, "100"])
def test_invalid_amount_never_passes(amount):
    assert check_liquidity(amount, reserves(), {})["liquidity_gate_status"] == "UNKNOWN"


@pytest.mark.parametrize("config", [{"max_impact_bps": 121}, {"min_reserve_sol": 499},
                                     {"max_order_share_bps": 501}, {"max_impact_bps": True}])
def test_config_cannot_weaken_limits(config):
    assert check_liquidity(1, reserves(), config)["liquidity_gate_status"] == "UNKNOWN"


@pytest.mark.parametrize("value", [None, [], "invalid", 42])
def test_invalid_mapping_returns_unknown(value):
    assert check_liquidity(100, reserves(), value)["liquidity_gate_status"] == "UNKNOWN"
    assert check_liquidity(100, value, {})["liquidity_gate_status"] == "UNKNOWN"


def test_no_jito_network_or_serialization_even_with_live_environment(monkeypatch):
    # Execute the actual method independently of optional SDK installation.
    source = Path("solana_volume_suite/core/jito_client.py").read_text()
    tree = ast.parse(source)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "JitoBundleClient")
    method = next(n for n in cls.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "send_bundle")
    method.returns = None
    for arg in method.args.args:
        arg.annotation = None
    namespace = {"JitoBundleDropException": RuntimeError}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])),
                 "send_bundle", "exec"), namespace)
    monkeypatch.setenv("LIVE_EXECUTION_ENABLED", "YES")
    class Client:
        total_bundles_dropped = 0
    instance = Client()
    with pytest.raises(RuntimeError, match="LIVE_EXECUTION_DISABLED"):
        asyncio.run(namespace["send_bundle"](instance, [object()], object()))
    assert instance.total_bundles_dropped == 1


def test_control_plane_blocks_execution_and_preserves_unknown(monkeypatch, tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from solana_volume_suite.dashboard.safety_app import app
    import secrets
    from solana_volume_suite.dashboard import safety_app
    token = secrets.token_urlsafe(32)
    monkeypatch.setenv("DASHBOARD_API_TOKEN", token)
    for key, value in {"LIVE_EXECUTION_ENABLED": "false", "PAPER_TRADING": "true",
                       "GEMINI_REAL_MONEY_READY": "false", "SOLANA_RPC_URL": "mock://offline",
                       "SOLANA_WSS_URL": "mock://offline", "JITO_BLOCK_ENGINE_URL": "mock://offline"}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(safety_app, "SUITE_ROOT", tmp_path)
    app.middleware_stack = None
    with TestClient(app, headers={"Authorization": "Bearer " + token}) as client:
        for route in ("/api/bot/start", "/api/bot/sweep", "/api/vault/generate"):
            assert client.post(route, json={}).status_code == 403
        response = client.post("/api/trading/simulate")
        assert response.status_code == 409
        assert response.json()["verified_side_effect"] is False
        assert client.get("/api/liquidity/status").json()["liquidity_gate_status"] == "UNKNOWN"
        data = client.post("/api/liquidity/assess", json=dict(amount_lamports=10**8,
                           reserve_in=500*10**9, reserve_out=10**12)).json()
        assert data["liquidity_gate_status"] == "PASS"
        assert data["execution_allowed"] is False
        assert client.post("/api/trading/simulate").status_code == 409
        assert client.get("/api/trading/executions").json()["executions"] == []
        assert client.post("/api/trading/kill-switch").json()["status"] == "STOPPED"
        assert client.get("/api/trading/telemetry").json()["volume_5m_usd"] is None
        assert "NO LIVE EXECUTION ENABLED" in client.get("/").text


def test_legacy_entrypoint_uses_same_safe_app():
    pytest.importorskip("fastapi")
    from solana_volume_suite.dashboard.app import app
    from solana_volume_suite.dashboard.safety_app import app as safe_app
    assert app is safe_app
