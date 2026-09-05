import asyncio
import json
import os
import signal

import httpx
import pytest
from starlette.websockets import WebSocketDisconnect
from solana_volume_suite.core.security import generate_password, validate_password, require_virtual_mode
from solana_volume_suite.orchestrator_loop import VolumeOrchestratorLoop


@pytest.mark.parametrize("path", ["/api/status", "/api/vault/wallets", "/api/github/results"])
def test_authentication_get(client, path):
    assert client.get(path, headers={"Authorization": ""}).status_code == 401
    assert client.get(path, headers={"Authorization": "Bearer wrong"}).status_code == 401


@pytest.mark.parametrize("path", ["/api/orchestrator/start", "/api/bot/start", "/api/vault/generate", "/api/github/search",
                                 "/api/trading/kill-switch", "/api/sweep"])
def test_authentication_mutations(client, path):
    assert client.post(path, json={}, headers={"Authorization": ""}).status_code == 401


def test_start_rate_limit_includes_aliases_and_stop_remains_available(client, caplog):
    for i in range(5):
        if i % 2:
            response = client.post("/api/bot/start", json={"mode": "simulation"}, headers={"X-Forwarded-For": str(i)})
        else:
            response = client.post("/api/orchestrator/start")
        assert response.status_code == 200
    assert client.post("/api/orchestrator/start").status_code == 429
    assert "security.rate_limit_exceeded" in caplog.text
    assert client.post("/api/trading/kill-switch").status_code == 200


def test_vault_rate_limit_and_no_real_file(client):
    for _ in range(5):
        assert client.post("/api/vault/generate", json={"count": 5}).status_code == 200
    assert client.post("/api/vault/generate", json={"count": 5}).status_code == 429


@pytest.mark.parametrize("count", [True, 0, -1, 101, 1000000000, "10"])
def test_vault_bounds(client, count):
    assert client.post("/api/vault/generate", json={"count": count}).status_code == 422


def test_body_cap_and_credentials_rejected(client):
    assert client.post("/api/vault/generate", json={"password": "mock", "count": 1}).status_code == 403
    assert client.post("/api/orchestrator/start", json={"mainnet": True}).status_code == 403
    assert client.post("/api/vault/generate", content=b"x" * 8193).status_code == 413


@pytest.mark.parametrize("key,value", [("LIVE_EXECUTION_ENABLED", "true"), ("LIVE_EXECUTION_ENABLED", "YES"),
    ("PAPER_TRADING", "false"), ("GEMINI_REAL_MONEY_READY", "true"), ("SOLANA_RPC_URL", "https://mainnet.invalid")])
def test_live_flags_blocked(monkeypatch, caplog, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(PermissionError):
        require_virtual_mode()
    assert "SECURITY_VIOLATION" in caplog.text


def test_runtime_flag_change_blocks_api(client, monkeypatch):
    monkeypatch.setenv("LIVE_EXECUTION_ENABLED", "true")
    assert client.post("/api/orchestrator/start").status_code == 403
    assert client.post("/api/trading/kill-switch").status_code == 200


@pytest.mark.parametrize("password", ["short", "a" * 64, "SuperSecretPassphraseChangeMe123!", "REPLACE_WITH_GENERATED_MOCK_PASSWORD"])
def test_weak_passwords(password):
    with pytest.raises(ValueError):
        validate_password(password)
    validate_password(generate_password())


@pytest.mark.asyncio
@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM])
async def test_signal_stops_and_persists(tmp_path, sig):
    runner = VolumeOrchestratorLoop(test_mode=False, state_path=tmp_path / "state.json")
    prior = runner.install_signal_handlers()
    try:
        task = asyncio.create_task(runner.run())
        await asyncio.sleep(0)
        signal.getsignal(sig)(sig, None)
        await asyncio.wait_for(task, 0.5)
    finally:
        for number, handler in prior.items():
            signal.signal(number, handler)
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["running"] is False
    assert any(event["type"] == "RUNNER_STOP" for event in state["events"])
    assert runner.master_password not in json.dumps(state)


def test_websocket_auth(client):
    with client.websocket_connect("ws://127.0.0.1/ws/telemetry", headers={"Authorization": "Bearer invalid"}) as ws:
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    with client.websocket_connect("ws://127.0.0.1/ws/telemetry") as ws:
        assert ws.receive_json()["live_execution_enabled"] is False


@pytest.mark.asyncio
async def test_rpc_timeout_fail_closed(monkeypatch, caplog):
    from solana_volume_suite.core.liquidity_gate import LiquidityGate
    actual = httpx.AsyncClient
    def handle(request):
        assert request.extensions["timeout"]["read"] == 10
        raise httpx.ReadTimeout("offline", request=request)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: actual(transport=httpx.MockTransport(handle), **kw))
    gate = LiquidityGate()
    assert (await gate.fetch_dexscreener_reserves("mock"))["status"] == "UNKNOWN"
    assert "warning.rpc_timeout" in caplog.text
    assert gate.validate_and_slice_order(0.1)["execution_allowed"] is False


@pytest.mark.asyncio
async def test_no_swap_network_or_key_use(monkeypatch):
    from solana_volume_suite.core.jupiter_engine import JupiterSwapEngine
    with pytest.raises(PermissionError):
        await JupiterSwapEngine().execute_swap("mock", "mock", 1, object())
