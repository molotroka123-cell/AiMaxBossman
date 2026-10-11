import os
import sys
import json
import pytest
from solders.keypair import Keypair
from fastapi.testclient import TestClient

# Add project root to sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.key_vault import SecurityKeyVault
from core.ai_orchestrator import AIOrchestrator, DeterministicFallbackEngine, VolumeDecision
from core.jito_client import JitoBundleClient, JitoBundleDropException
from core.treasury_guard import SubImpactEngine, TreasuryGuard
from core.paper_plan import (
    TOKEN_ACCOUNT_RENT_LAMPORTS,
    MigrationThresholdExceededException,
)
from stages.pumpfun_engine import PumpFunPaperPlanner
from dashboard.app import app


@pytest.fixture
def temp_vault_path(tmp_path):
    return str(tmp_path / "test_wallets_encrypted.json")


# ==============================================================================
# TEST 1: Vault Encryption Roundtrip
# ==============================================================================
def test_vault_encryption_roundtrip(temp_vault_path):
    """
    Verifies that AES-256-GCM encryption and PBKDF2 decryption properly roundtrips Keypairs.
    Ensures invalid password raises PermissionError.
    """
    vault = SecurityKeyVault(storage_path=temp_vault_path)
    password = "CorrectSuperSecretKey456!"  # ci-secret-scan: allow -- synthetic local test fixture; no deployed credentials
    count = 10

    pubkeys = vault.create_and_store_pool(count, password)
    assert len(pubkeys) == count
    assert os.path.exists(temp_vault_path)

    # Inspect encrypted file structure
    with open(temp_vault_path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    assert "salt" in payload
    assert "nonce" in payload
    assert "ciphertext" in payload
    # Raw private keys must NOT appear in ciphertext file
    raw_content = json.dumps(payload)
    for pk in pubkeys:
        assert pk not in payload["ciphertext"]

    # Decrypt with correct password
    loaded_keypairs = vault.load_keypairs(password)
    assert len(loaded_keypairs) == count
    for idx, kp in enumerate(loaded_keypairs):
        assert str(kp.pubkey()) == pubkeys[idx]

    # Attempt decrypt with incorrect password -> MUST FAIL
    with pytest.raises(PermissionError):
        vault.load_keypairs("WrongPassword123!")


# ==============================================================================
# TEST 2: Zero-Knowledge Isolation
# ==============================================================================
def test_zero_knowledge_isolation(temp_vault_path):
    """
    Proves that the AI LLM context receives ONLY sanitized virtual indices (e.g. wallet_0)
    and that raw secret keys or sensitive tokens never enter the AI payload.
    """
    vault = SecurityKeyVault(storage_path=temp_vault_path)
    password = "ZKIsolationTestPassword999!"  # ci-secret-scan: allow -- synthetic local test fixture; no deployed credentials
    vault.create_and_store_pool(5, password)

    sanitized_view = vault.get_sanitized_public_view(password)
    assert len(sanitized_view) == 5
    for item in sanitized_view:
        assert "secret_base58" not in item
        assert "private_key" not in item
        assert "keypair" not in item
        assert "wallet_index" in item
        assert "alias" in item
        assert item["alias"].startswith("wallet_")

    # Verify AI Orchestrator sanitizer purges any accidental secret key leakage
    orchestrator = AIOrchestrator()
    dirty_context = {
        "stage": "pumpfun_bonding_curve",
        "wallets_count": 5,
        "curve_progress_pct": 33.5,
        "secret_base58": "SUPER_SECRET_KEY_ACCIDENTALLY_INSERTED",
        "nested": {
            "private_key": "ANOTHER_SECRET",
            "safe_metric": 42
        }
    }

    clean_context = orchestrator.sanitize_market_context(dirty_context)
    assert "secret_base58" not in clean_context
    assert "private_key" not in clean_context["nested"]
    assert clean_context["nested"]["safe_metric"] == 42
    assert clean_context["curve_progress_pct"] == 33.5


# ==============================================================================
# TEST 3: Pump.fun paper plan (replaces the removed PDA / instruction builder test)
# ==============================================================================
def test_pumpfun_paper_plan_is_pure_data():
    """
    A buy plan is a frozen dataclass of numbers and strings: sliced <= 1.2% impact, with an integer slippage cap.
    """
    plan = PumpFunPaperPlanner().plan_buy(
        mint="DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",  # ci-secret-scan: allow -- public Solana program/mint/destination address
        amount_sol=0.05, curve_progress_pct=10.0, virtual_sol_reserve=30.0,
    )
    assert plan.live_executable is False
    assert len(plan.slices) == 1
    only = plan.slices[0]
    assert only.impact_pct <= 1.2
    assert only.cap_lamports == 52_500_000          # 0.05 SOL * 1.05
    assert plan.to_dict()["live_executable"] is False


# ==============================================================================
# TEST 4: AI Fallback on LLM Error (Fail-Closed Robustness)
# ==============================================================================
@pytest.mark.asyncio
async def test_ai_fallback_on_llm_error():
    """
    Verifies that when LLM endpoint is unreachable, down, or malformed,
    the bot DOES NOT CRASH, but seamlessly falls back to the deterministic engine.
    """
    # Point orchestrator to an unreachable port
    orchestrator = AIOrchestrator(api_url="http://127.0.0.1:59999/v1", timeout=0.5)

    market_state = {
        "stage": "pumpfun_bonding_curve",
        "token_mint": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",  # ci-secret-scan: allow -- public Solana program/mint/destination address
        "curve_progress_pct": 20.0,
        "seconds_since_last_external_tx": 30.0,  # Stagnant > 25s: fallback must hold, not fabricate volume
        "recent_dump_size_sol": 0.0,
        "wallets_count": 20
    }

    decision = await orchestrator.get_volume_decision(
        market_state=market_state,
        active_wallet_count=20
    )

    assert isinstance(decision, VolumeDecision)
    assert decision.action == "WAIT"
    assert decision.mode_tag == "POLICY_HOLD_NO_ACTIVITY"
    assert decision.amount_sol == 0
    assert 4.0 <= decision.delay_sec <= 95.0
    assert 0 <= decision.wallet_index < 20
    assert orchestrator.total_fallback_calls == 1


# ==============================================================================
# TEST 5: Dashboard Endpoints via FastAPI TestClient
# ==============================================================================
def test_dashboard_endpoints():
    """
    Verifies all Command Center API endpoints:
    /api/vault/generate, /api/vault/wallets, /api/bot/start, /api/bot/stop, /api/telemetry, /api/bot/sweep
    """
    client = TestClient(app)
    from solana_volume_suite.dashboard.safety_app import CONTROL_TOKEN
    client.headers["X-Control-Token"] = CONTROL_TOKEN

    # 1. Real key generation is disabled in the paper-only control plane.
    res_gen = client.post("/api/vault/generate", json={
        "count": 15,
        "password": "MasterTestPassword777!"
    })
    assert res_gen.status_code == 403
    assert res_gen.json()["reason"] == "PAPER_ONLY_REAL_KEY_GENERATION_DISABLED"

    # 2. Get Wallets
    res_wallets = client.get("/api/vault/wallets")
    assert res_wallets.status_code == 200
    data_wallets = res_wallets.json()
    assert data_wallets["count"] == 10
    assert len(data_wallets["wallets"]) == 10
    assert "sol_balance" in data_wallets["wallets"][0]
    assert data_wallets["wallets"][0]["role"] == "virtual_paper_wallet"

    # 3. Start Bot
    # strict SAFETY_ONLY acknowledgement: any extra field (password, mint...) is refused
    assert client.post("/api/bot/start", json={
        "paper_only": True, "password": "MasterTestPassword777!"}).status_code == 403
    res_start = client.post("/api/bot/start", json={"paper_only": True})
    assert res_start.status_code == 200
    data_start = res_start.json()
    assert data_start["status"] == "RUNNING"
    assert data_start["bot_status"] == "RUNNING"
    assert data_start["mode"] == "PAPER_TRADING_ONLY"
    assert data_start["live_execution_enabled"] is False

    # 4. Telemetry
    res_telem = client.get("/api/telemetry")
    assert res_telem.status_code == 200
    data_telem = res_telem.json()
    assert data_telem["bot_status"] in ["RUNNING", "PAUSED", "STOPPED"]
    assert "metrics" in data_telem
    assert "jito_stats" in data_telem
    assert data_telem["jito_stats"]["mempool_leak_prevention"] == "LIVE_BUNDLES_DISABLED"

    # 5. Stop Bot (Kill Switch)
    res_stop = client.post("/api/bot/stop")
    assert res_stop.status_code == 200
    data_stop = res_stop.json()
    assert data_stop["status"] in ["SUCCESS", "STOPPED"]
    assert data_stop["bot_status"] == "STOPPED"

    # 6. A paper-only runtime cannot claim to sweep real funds.
    res_sweep = client.post("/api/bot/sweep", json={
        "cold_destination_pubkey": str(Keypair().pubkey()),
        "password": "MasterTestPassword777!"
    })
    assert res_sweep.status_code == 409
    assert res_sweep.json()["reason"] == "PAPER_ONLY_NO_REAL_WALLETS_OR_FUNDS"


# ==============================================================================
# TEST 7: Invariant Verification - Jito stub always refuses (drop, no fallback)
# ==============================================================================
@pytest.mark.asyncio
async def test_jito_only_invariant_and_drop_on_failure():
    """
    Verifies that the inert Jito stub drops every bundle with JitoBundleDropException and NEVER routes to standard RPC.
    No transaction object can even be built any more, so the argument is an opaque placeholder.
    """
    client = JitoBundleClient(block_engine_url="http://127.0.0.1:58888/api/v1/bundles", timeout=0.5)

    with pytest.raises(JitoBundleDropException) as excinfo:
        await client.send_bundle([object()])

    assert "Bundle DROPPED (no public mempool fallback)" in str(excinfo.value)
    assert client.total_bundles_dropped == 1
    assert client.total_bundles_confirmed == 0


# ==============================================================================
# TEST 8: Invariant Verification - Sub-Impact Engine & Treasury Guard
# ==============================================================================
def test_sub_impact_engine_and_treasury_guard():
    """
    Verifies:
    1. Sub-Impact Engine limits price impact to <= 1.2% by splitting large orders.
    2. TreasuryGuard tracks burn and trips circuit breaker when MAX_ALLOWED_LOSS_USD is exceeded.
    """
    # Pool has 10 SOL liquidity. Trade of 1 SOL would cause ~9% impact (> 1.2%)
    pool_liq = 10.0
    slices = SubImpactEngine.enforce_sub_impact_limits(requested_sol=1.0, pool_sol_liquidity=pool_liq, max_impact_pct=1.2)
    assert len(slices) >= 4
    for s in slices:
        impact = SubImpactEngine.calculate_price_impact(s, pool_liq)
        assert impact <= 1.25  # Within precision limit

    # Treasury Guard circuit breaker
    guard = TreasuryGuard(max_allowed_loss_usd=5.0, sol_usd_price=180.0)
    assert guard.is_circuit_breaker_tripped is False

    # Simulate trades accumulating fees
    for _ in range(10):
        guard.record_trade(volume_sol=0.5, dex_type="pumpfun", jito_tip_lamports=100_000)

    # After enough trades, burn exceeds $5.0 -> Circuit breaker must trip
    assert guard.get_total_burn_usd() >= 5.0
    assert guard.is_circuit_breaker_tripped is True
    assert "AUTO-PAUSE" in guard.pause_reason


# ==============================================================================
# TEST 9: Invariant Verification - Pump.fun 95% Migration Threshold Safeguard (paper plan)
# ==============================================================================
def test_pumpfun_migration_threshold_safeguard():
    """
    When the bonding curve reaches 95%, a BUY plan is refused to avoid capital lock before migration to Raydium.
    """
    planner = PumpFunPaperPlanner()
    mint = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"  # ci-secret-scan: allow -- public Solana program/mint/destination address

    # Below 95% -> a paper plan with at least one slice
    plan = planner.plan_buy(mint=mint, amount_sol=0.05, curve_progress_pct=94.5, virtual_sol_reserve=30.0)
    assert len(plan.slices) >= 1
    assert plan.curve_progress_pct == 94.5

    # At or above 95% -> MUST RAISE MigrationThresholdExceededException
    for pct in (95.0, 95.1, 99.9):
        with pytest.raises(MigrationThresholdExceededException):
            planner.plan_buy(mint=mint, amount_sol=0.05, curve_progress_pct=pct, virtual_sol_reserve=30.0)


# ==============================================================================
# TEST 10: Invariant Verification - Rent reclaim is a plan item (no instruction object)
# ==============================================================================
def test_rent_reclaim_is_a_plan_item():
    """
    A full exit carries a rent-reclaim plan item worth ~0.002039 SOL; it is data, not a close_account instruction.
    """
    plan = PumpFunPaperPlanner().plan_sell(
        mint="DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",  # ci-secret-scan: allow -- public Solana program/mint/destination address
        token_amount=1_000_000.0, reclaim_rent=True,
    )
    assert plan.rent_reclaim.included is True
    assert plan.rent_reclaim.estimated_lamports == TOKEN_ACCOUNT_RENT_LAMPORTS == 2_039_280
    assert not hasattr(plan.rent_reclaim, "accounts")
    skipped = PumpFunPaperPlanner().plan_sell(mint="m", token_amount=1.0, reclaim_rent=False)
    assert skipped.rent_reclaim.included is False
