"""End-to-End integration tests for the Solana AI Volume Suite interactive prototype."""
import os
import sys
import pytest
from fastapi.testclient import TestClient

# Ensure suite is on sys.path
TEST_DIR = os.path.dirname(os.path.abspath(__file__))
SUITE_ROOT = os.path.dirname(TEST_DIR)
if SUITE_ROOT not in sys.path:
    sys.path.insert(0, SUITE_ROOT)
WORKSPACE_ROOT = os.path.dirname(SUITE_ROOT)
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from orchestrator_loop import VolumeOrchestratorLoop
from core.liquidity_gate import LiquidityGate
from dashboard.safety_app import app, orchestrator


@pytest.mark.asyncio
async def test_orchestrator_loop_three_iterations(tmp_path):
    """
    Runs 3 iterations of VolumeOrchestratorLoop in test_mode=True.
    Verifies event journal records trades (TRADE_EXECUTED or TRADE_HELD) and loop halts.
    """
    vault_file = str(tmp_path / "temp_vault.json")
    loop = VolumeOrchestratorLoop(
        vault_path=vault_file,
        master_password="SuperSecretTestPass123!",
        test_mode=True
    )
    loop.initialize_vault_pool(count=10)
    assert len(loop.cached_keypairs) == 10
    assert len(loop.wallet_balances) == 10

    # Run exactly 3 iterations
    await loop.run(max_iterations=3)

    assert loop.iteration_count == 3
    assert loop.is_running is False
    assert len(loop.event_journal) > 0

    journal_types = [entry["type"] for entry in loop.event_journal]
    assert any(t in ("TRADE_EXECUTED", "TRADE_HELD") for t in journal_types)


def test_liquidity_gate_evaluates_and_blocks_or_slices_orders():
    """
    Verifies LiquidityGate evaluates impact and blocks or slices orders exceeding 1.2% (120 bps).
    """
    gate = LiquidityGate(max_impact_bps=120)
    reserves_standard = {
        "model": "CONSTANT_PRODUCT",
        "input_asset": "SOL",
        "reserve_in": 650 * 10**9,
        "reserve_out": 10**15,
        "fee_bps": 25,
        "liquidity_usd": 120_000.0
    }

    # 1. Normal order within bounds (0.1 SOL on 650 SOL pool => ~1.5 bps)
    small_eval = gate.validate_and_slice_order(0.1, pool_reserves=reserves_standard)
    assert small_eval["status"] == "PASS"
    assert small_eval["execution_allowed"] is True
    assert small_eval["estimated_impact_bps"] <= 120
    assert small_eval["slices_sol"] == [0.1]

    # 2. Huge order exceeding 1.2% impact cap (50.0 SOL on 650 SOL pool => ~714 bps)
    large_eval = gate.validate_and_slice_order(50.0, pool_reserves=reserves_standard)
    assert large_eval["status"] in ("SLICED_PASS", "BLOCK")
    if large_eval["status"] == "SLICED_PASS":
        # Sliced into micro-orders <= 1.2%
        assert len(large_eval["slices_sol"]) > 1
        for s in large_eval["slices_sol"]:
            assert s < 50.0
    else:
        assert large_eval["execution_allowed"] is False

    # 3. Order on unsafe pool that fails minimum liquidity reserve (only 100 SOL < 500 SOL min)
    unsafe_pool = {
        "model": "CONSTANT_PRODUCT",
        "input_asset": "SOL",
        "reserve_in": 100 * 10**9,
        "reserve_out": 10**15,
        "fee_bps": 25,
        "liquidity_usd": 15_000.0
    }
    blocked_eval = gate.validate_and_slice_order(5.0, pool_reserves=unsafe_pool)
    assert blocked_eval["status"] == "BLOCK"
    assert blocked_eval["execution_allowed"] is False
    assert gate.last_status == "BLOCK"


def test_kill_switch_immediately_halts():
    """
    Verifies Kill Switch immediately sets is_running=False and writes audit record.
    """
    loop = VolumeOrchestratorLoop(test_mode=True)
    loop.is_running = True
    loop.stop()

    assert loop.is_running is False
    assert len(loop.event_journal) > 0
    assert loop.event_journal[0]["type"] == "KILL_SWITCH"


def test_dashboard_interactive_endpoints():
    """
    Tests dashboard prototype endpoints:
    /api/status, /api/orchestrator/start, /api/orchestrator/stop, /api/sweep, /api/vault/generate.
    """
    with TestClient(app) as client:
        # 1. GET /api/status
        status_resp = client.get("/api/status")
        assert status_resp.status_code == 200
        status_data = status_resp.json()
        assert status_data["mode"] == "PAPER_TRADING_ONLY"
        assert "bot_status" in status_data
        assert "wallets" in status_data
        assert "metrics" in status_data
        assert "liquidity_gate_status" in status_data
        assert "events" in status_data

        # 2. POST /api/orchestrator/start
        start_resp = client.post("/api/orchestrator/start")
        assert start_resp.status_code == 200
        assert start_resp.json()["status"] == "RUNNING"
        assert orchestrator.is_running is True

        # 3. POST /api/orchestrator/stop
        stop_resp = client.post("/api/orchestrator/stop")
        assert stop_resp.status_code == 200
        assert stop_resp.json()["status"] == "STOPPED"
        assert orchestrator.is_running is False

        # 4. POST /api/sweep
        sweep_resp = client.post("/api/sweep", json={"destination": "ColdDestTestAddress111111111111111111111111"})
        assert sweep_resp.status_code == 200
        sweep_data = sweep_resp.json()
        assert sweep_data["status"] == "SUCCESS"
        assert sweep_data["mode"] == "PAPER_TRADING_SIMULATED"
        assert "total_sol_swept" in sweep_data

        # 5. POST /api/vault/generate
        # Empty body -> 403 BLOCKED
        blocked_resp = client.post("/api/vault/generate", json={})
        assert blocked_resp.status_code == 403

        # Valid body -> 200 SUCCESS
        gen_resp = client.post("/api/vault/generate", json={
            "count": 5,
            "password": "ValidMasterPassword123!"
        })
        assert gen_resp.status_code == 200
        assert gen_resp.json()["status"] == "SUCCESS"
        assert gen_resp.json()["count"] == 5
        assert len(orchestrator.cached_keypairs) == 5
