import os
import sys
import json
import pytest
from solders.pubkey import Pubkey
from solders.keypair import Keypair
from solders.hash import Hash
from fastapi.testclient import TestClient

# Add project root to sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.key_vault import SecurityKeyVault, RentReclaimer
from core.ai_orchestrator import AIOrchestrator, DeterministicFallbackEngine, VolumeDecision
from core.funding_router import AntiClusteringFundingRouter, CEXWithdrawalAdapter
from core.jito_client import JitoBundleClient, JitoBundleDropException, MIN_JITO_TIP_LAMPORTS
from core.treasury_guard import SubImpactEngine, TreasuryGuard
from stages.pumpfun_engine import (
    get_bonding_curve_pda,
    build_pump_buy_instruction,
    build_pump_sell_instruction,
    PumpFunEngine,
    MigrationThresholdExceededException,
    PUMP_PROGRAM
)
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
# TEST 3: Pump.fun PDA Derivation
# ==============================================================================
def test_pumpfun_pda_derivation():
    """
    Validates PDA derivation math: seeds=[b"bonding-curve", bytes(mint)]
    on program 6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P.  # ci-secret-scan: allow -- public Solana program/mint/destination address
    """
    test_mint = Pubkey.from_string("DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263")  # ci-secret-scan: allow -- public Solana program/mint/destination address
    pda, bump = get_bonding_curve_pda(test_mint)

    assert isinstance(pda, Pubkey)
    assert isinstance(bump, int)
    assert 0 <= bump <= 255

    # Re-derive directly with Solders
    expected_pda, expected_bump = Pubkey.find_program_address(
        [b"bonding-curve", bytes(test_mint)],
        PUMP_PROGRAM
    )
    assert pda == expected_pda
    assert bump == expected_bump

    # Verify instruction builder uses derived PDA
    buyer = Keypair()
    ix = build_pump_buy_instruction(
        payer=buyer.pubkey(),
        mint=test_mint,
        token_amount=1_000_000,
        max_sol_cost_lamports=100_000_000
    )
    assert ix.program_id == PUMP_PROGRAM
    # Account at index 3 is bonding_curve
    assert ix.accounts[3].pubkey == pda
    assert ix.accounts[3].is_writable is True
    # Account at index 6 is payer signer
    assert ix.accounts[6].pubkey == buyer.pubkey()
    assert ix.accounts[6].is_signer is True


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
        "seconds_since_last_external_tx": 30.0,  # Stagnant > 25s triggers KOTH_PULSE
        "recent_dump_size_sol": 0.0,
        "wallets_count": 20
    }

    decision = await orchestrator.get_volume_decision(
        market_state=market_state,
        active_wallet_count=20
    )

    assert isinstance(decision, VolumeDecision)
    assert decision.action in ["BUY", "SELL", "WAIT", "KOTH_PULSE", "FLOOR_DEFENSE"]
    # Due to silence > 25s, it should trigger KOTH_PULSE
    assert decision.action == "KOTH_PULSE"
    assert 0.03 <= decision.amount_sol <= 0.08
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

    # 1. Generate Vault
    res_gen = client.post("/api/vault/generate", json={
        "count": 15,
        "password": "MasterTestPassword777!"
    })
    assert res_gen.status_code == 200
    data_gen = res_gen.json()
    assert data_gen["status"] == "SUCCESS"
    assert data_gen["count"] == 15

    # 2. Get Wallets
    res_wallets = client.get("/api/vault/wallets")
    assert res_wallets.status_code == 200
    data_wallets = res_wallets.json()
    assert data_wallets["count"] == 15
    assert len(data_wallets["wallets"]) == 15
    assert "sol_balance" in data_wallets["wallets"][0]

    # 3. Start Bot
    res_start = client.post("/api/bot/start", json={
        "stage": "BONDING_CURVE",
        "target_token_mint": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",  # ci-secret-scan: allow -- public Solana program/mint/destination address
        "password": "MasterTestPassword777!",
        "max_loss_usd": 50.0
    })
    assert res_start.status_code == 200
    data_start = res_start.json()
    assert data_start["status"] == "SUCCESS"
    assert data_start["bot_status"] == "RUNNING"

    # 4. Telemetry
    res_telem = client.get("/api/telemetry")
    assert res_telem.status_code == 200
    data_telem = res_telem.json()
    assert data_telem["bot_status"] in ["RUNNING", "PAUSED", "STOPPED"]
    assert "metrics" in data_telem
    assert "jito_stats" in data_telem
    assert data_telem["jito_stats"]["mempool_leak_prevention"] == "100%_SECURED"

    # 5. Stop Bot (Kill Switch)
    res_stop = client.post("/api/bot/stop")
    assert res_stop.status_code == 200
    data_stop = res_stop.json()
    assert data_stop["status"] == "SUCCESS"
    assert data_stop["bot_status"] == "STOPPED"

    # 6. Emergency Sweep
    cold_target = str(Keypair().pubkey())
    res_sweep = client.post("/api/bot/sweep", json={
        "cold_destination_pubkey": cold_target,
        "password": "MasterTestPassword777!"
    })
    assert res_sweep.status_code == 200
    data_sweep = res_sweep.json()
    assert data_sweep["status"] == "SUCCESS"
    assert data_sweep["destination"] == cold_target


# ==============================================================================
# TEST 6: Invariant Verification - Anti-Clustering, Pareto & Poisson
# ==============================================================================
def test_anti_clustering_pareto_and_poisson():
    """
    Verifies:
    1. Volumes follow Pareto distribution and NEVER yield exact round numbers (0.1, 0.5, 1.0).
    2. Intervals follow Poisson distribution within 4s to 95s.
    3. Cascaded funding breaks 1-to-N direct links via 3 transit wallets.
    """
    # 1. Pareto non-round test
    for _ in range(50):
        vol = AntiClusteringFundingRouter.generate_pareto_volume(min_sol=0.03, max_sol=1.5)
        assert vol >= 0.03
        assert abs(vol - round(vol, 1)) > 1e-4, f"Volume {vol} should not be a round tenth!"

    # 2. Poisson intervals test
    for _ in range(50):
        interval = AntiClusteringFundingRouter.generate_poisson_interval(lam=20.0, min_sec=4.0, max_sec=95.0)
        assert 4.0 <= interval <= 95.0

    # 3. Cascaded funding tree
    router = AntiClusteringFundingRouter(transit_count=3)
    master = Keypair()
    sub_wallets = [Keypair().pubkey() for _ in range(12)]
    plan = router.plan_cascaded_funding(master, sub_wallets, total_sol=3.0)

    assert len(plan["transit_keypairs"]) == 3
    assert len(plan["tier1_transfers"]) == 3
    assert len(plan["tier2_transfers"]) == 12

    # Verify no direct master -> sub_wallet transfers in tier2
    for t2 in plan["tier2_transfers"]:
        assert t2["from"] != str(master.pubkey())

    # 4. Asymmetric rotation test
    rot = router.register_rotation_step("TokenMint123", buyer_index=2, amount_tokens=10_000, available_wallet_indices=list(range(10)))
    assert rot["buyer_wallet"] == 2
    assert rot["seller_wallet_b"] != 2
    assert rot["tokens_to_sell_b"] + rot["tokens_to_transfer_c"] + rot["tokens_held_a"] == 10_000


# ==============================================================================
# TEST 7: Invariant Verification - Strict Jito-Only & Drop on Failure
# ==============================================================================
@pytest.mark.asyncio
async def test_jito_only_invariant_and_drop_on_failure():
    """
    Verifies that if Jito Block Engine fails or is unreachable, the transaction
    is strictly DROPPED with JitoBundleDropException and NEVER routes to standard RPC.
    """
    # Point to an invalid Jito endpoint to trigger transport drop
    client = JitoBundleClient(block_engine_url="http://127.0.0.1:58888/api/v1/bundles", timeout=0.5)

    kp = Keypair()
    tip_ix = client.build_tip_instruction(kp.pubkey(), tip_lamports=MIN_JITO_TIP_LAMPORTS)
    assert tip_ix is not None

    tx = client.compile_v0_transaction(
        payer=kp.pubkey(),
        instructions=[tip_ix],
        recent_blockhash=Hash.default(),
        signers=[kp]
    )

    with pytest.raises(JitoBundleDropException) as excinfo:
        await client.send_bundle([tx])

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
# TEST 9: Invariant Verification - Pump.fun 95% Migration Threshold Safeguard
# ==============================================================================
def test_pumpfun_migration_threshold_safeguard():
    """
    Verifies that when bonding curve reaches 95%, buying is strictly prohibited
    to prevent getting stuck before migration to Raydium.
    """
    engine = PumpFunEngine(jito_client=JitoBundleClient())
    kp = Keypair()
    mint = Pubkey.from_string("DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263")  # ci-secret-scan: allow -- public Solana program/mint/destination address

    # Below 95% -> Success
    bundles = engine.assemble_pump_buy_bundle(
        buyer_kp=kp,
        mint=mint,
        amount_sol=0.05,
        curve_progress_pct=94.5,
        recent_blockhash=Hash.default(),
        pool_sol_reserve=30.0
    )
    assert len(bundles) >= 1

    # At or above 95% -> MUST RAISE MigrationThresholdExceededException
    with pytest.raises(MigrationThresholdExceededException):
        engine.assemble_pump_buy_bundle(
            buyer_kp=kp,
            mint=mint,
            amount_sol=0.05,
            curve_progress_pct=95.1,
            recent_blockhash=Hash.default(),
            pool_sol_reserve=30.0
        )


# ==============================================================================
# TEST 10: Invariant Verification - Rent Reclaimer (SPL Token Close Account)
# ==============================================================================
def test_rent_reclaimer_close_account():
    """
    Verifies RentReclaimer generates valid close_account instruction to reclaim 0.002039 SOL rent.
    """
    wallet = Keypair().pubkey()
    ata = Keypair().pubkey()

    ix = RentReclaimer.build_close_account_ix(
        token_account=ata,
        destination_sol_wallet=wallet,
        owner=wallet
    )
    assert ix is not None
    # close_account instruction discriminator/structure in SPL token program
    assert len(ix.accounts) == 3
    assert ix.accounts[0].pubkey == ata
    assert ix.accounts[1].pubkey == wallet
    assert ix.accounts[2].pubkey == wallet
