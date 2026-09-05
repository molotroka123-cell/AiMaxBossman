import secrets
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
from solana_volume_suite.dashboard import safety_app


@pytest.mark.asyncio
async def test_orchestrator_loop_three_iterations(tmp_path):
    """
    Runs 3 iterations of VolumeOrchestratorLoop in test_mode=True.
    Verifies event journal records trades (TRADE_EXECUTED or TRADE_HELD) and loop halts.
    """
    vault_file = str(tmp_path / "temp_vault.json")
    loop = VolumeOrchestratorLoop(
        vault_path=vault_file,
        master_password=secrets.token_urlsafe(32),
        test_mode=True
    )
    loop.initialize_vault_pool(count=10)
    assert loop.cached_keypairs == []
    assert len(loop.sub_wallet_addresses) == 10
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
    assert small_eval["execution_allowed"] is False
    assert small_eval["simulation_allowed"] is True
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


def test_dashboard_interactive_endpoints(client):
    assert client.get("/api/status").json()["mode"] == "PAPER_TRADING_ONLY"
    assert client.post("/api/orchestrator/start").json()["status"] == "RUNNING"
    assert safety_app.orchestrator.is_running is True
    assert client.post("/api/orchestrator/stop").json()["status"] == "STOPPED"
    assert safety_app.orchestrator.is_running is False
    sweep = client.post("/api/sweep", json={"destination": "mock:cold"})
    assert sweep.json()["mode"] == "PAPER_TRADING_SIMULATED"
    assert sweep.json()["tx_signature"] is None
    assert client.post("/api/vault/generate", json={}).status_code == 403
    generated = client.post("/api/vault/generate", json={"count": 5})
    assert generated.json()["count"] == 5
    assert safety_app.orchestrator.cached_keypairs == []
    assert len(safety_app.orchestrator.wallet_balances) == 5
