"""Safety invariants for paper-only Solana tooling."""
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

SUITE_ROOT = Path(__file__).resolve().parents[1]
if str(SUITE_ROOT) not in sys.path:
    sys.path.insert(0, str(SUITE_ROOT))

from core.ai_orchestrator import DeterministicFallbackEngine, VolumeDecision
from dashboard.safety_app import app, orchestrator as dashboard_orchestrator
from orchestrator_loop import VolumeOrchestratorLoop
from setup_mainnet import DISABLED_REASON, run_setup_wizard
from start_prototype import main as launch_main


def test_paper_wallet_initialization_never_touches_vault(tmp_path):
    vault_file = tmp_path / "existing-vault.json"
    vault_file.write_text("preserve this file", encoding="utf-8")
    loop = VolumeOrchestratorLoop(vault_path=str(vault_file), test_mode=True)

    wallets = loop.initialize_vault_pool(count=3)

    assert wallets == ["SIM-WALLET-000", "SIM-WALLET-001", "SIM-WALLET-002"]
    assert loop.cached_keypairs == []
    assert len(loop.wallet_balances) == 3
    assert vault_file.read_text(encoding="utf-8") == "preserve this file"


def test_fallback_does_not_invent_order_when_model_is_unavailable_or_market_dumps():
    unavailable = DeterministicFallbackEngine.generate_fallback_decision({}, 10)
    dump = DeterministicFallbackEngine.generate_fallback_decision(
        {"recent_dump_size_sol": 3.0},
        10,
    )

    assert unavailable.action == "WAIT"
    assert unavailable.amount_sol == 0
    assert unavailable.mode_tag == "POLICY_HOLD_MODEL_UNAVAILABLE"
    assert dump.action == "WAIT"
    assert dump.amount_sol == 0
    assert dump.mode_tag == "POLICY_HOLD_PRICE_MOVEMENT"


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["KOTH_PULSE", "FLOOR_DEFENSE"])
async def test_paper_loop_blocks_volume_push_and_price_support(action):
    loop = VolumeOrchestratorLoop(test_mode=True)
    loop.initialize_vault_pool()
    loop.ai_orchestrator.get_volume_decision = AsyncMock(return_value=VolumeDecision(
        action=action,
        wallet_index=0,
        amount_sol=0.05,
        delay_sec=4,
        mode_tag="test",
        reason="synthetic test decision",
        confirmed_onchain=True,
        tx_signature="fabricated-model-signature",
    ))

    result = await loop.step()

    assert result["decision"]["confirmed_onchain"] is False
    assert result["decision"]["tx_signature"] is None
    assert loop.event_journal[0]["type"] == "POLICY_BLOCKED"
    assert loop.treasury_guard.records == []


@pytest.mark.asyncio
async def test_paper_trade_is_never_reported_as_onchain():
    loop = VolumeOrchestratorLoop(test_mode=True)
    loop.initialize_vault_pool()
    loop.ai_orchestrator.get_volume_decision = AsyncMock(return_value=VolumeDecision(
        action="BUY",
        wallet_index=0,
        amount_sol=0.05,
        delay_sec=4,
        mode_tag="test",
        reason="synthetic test decision",
        confirmed_onchain=True,
        tx_signature="fabricated-model-signature",
    ))

    result = await loop.step()

    assert result["decision"]["confirmed_onchain"] is False
    assert result["decision"]["tx_signature"] is None
    assert loop.event_journal[0]["type"] == "PAPER_TRADE_RECORDED"
    assert loop.event_journal[0]["meta"]["execution"] == "SIMULATED_ONLY"
    assert "sig" not in loop.event_journal[0]["meta"]


def test_mainnet_setup_is_disabled_without_side_effects(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(RuntimeError, match="Mainnet setup is disabled") as exc:
        run_setup_wizard(password="must not be written", rpc_url="https://invalid.example")  # ci-secret-scan: allow -- test placeholder, not a credential
    assert str(exc.value) == DISABLED_REASON
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("flag", ["--mainnet", "--setup"])
def test_launcher_rejects_live_modes_before_startup_imports(flag):
    with pytest.raises(SystemExit) as exc:
        launch_main([flag])
    assert exc.value.code == 2


def test_dashboard_blocks_key_generation_and_sweep_without_mutating_state(tmp_path):
    original_vault_path = dashboard_orchestrator.vault_path
    protected_vault = tmp_path / "protected-vault.json"
    protected_vault.write_bytes(b"keep existing vault unchanged")
    dashboard_orchestrator.vault_path = str(protected_vault)
    initial_balances = dict(dashboard_orchestrator.wallet_balances)
    try:
        with TestClient(app) as client:
            generate = client.post("/api/vault/generate", json={
                "count": 1,
                "password": "example-password",
            })
            sweep = client.post("/api/sweep", json={"destination": "example"})

        assert generate.status_code == 403
        assert generate.json()["reason"] == "PAPER_ONLY_REAL_KEY_GENERATION_DISABLED"
        assert sweep.status_code == 409
        assert sweep.json()["reason"] == "PAPER_ONLY_NO_REAL_WALLETS_OR_FUNDS"
        assert protected_vault.read_bytes() == b"keep existing vault unchanged"
        assert dashboard_orchestrator.wallet_balances == initial_balances
        assert dashboard_orchestrator.cached_keypairs == []
    finally:
        dashboard_orchestrator.vault_path = original_vault_path


# ---- legacy engines must never sign or reach swap APIs (night 10.10-11 hardening) -------------------------------
def test_compile_v0_transaction_never_signs_with_keypair():
    from solders.hash import Hash
    from solders.keypair import Keypair
    from solders.signature import Signature
    from core.jito_client import JitoBundleClient

    kp = Keypair()
    client = JitoBundleClient()
    tx = client.compile_v0_transaction(
        payer=kp.pubkey(), instructions=[client.build_tip_instruction(payer=kp.pubkey(), tip_lamports=1000)],
        recent_blockhash=Hash.default(), signers=[kp],
    )
    assert all(sig == Signature.default() for sig in tx.signatures)
    # negative control: a really signed transaction of the same message differs, so the assertion above is meaningful
    from solders.transaction import VersionedTransaction
    really_signed = VersionedTransaction(tx.message, [kp])
    assert any(sig != Signature.default() for sig in really_signed.signatures)


def test_pumpfun_bundles_are_unsigned_paper_plans():
    from solders.hash import Hash
    from solders.keypair import Keypair
    from solders.pubkey import Pubkey
    from solders.signature import Signature
    from core.jito_client import JitoBundleClient
    from stages.pumpfun_engine import PumpFunEngine

    kp = Keypair()
    engine = PumpFunEngine(jito_client=JitoBundleClient())
    mint = Pubkey.new_unique()
    buys = engine.assemble_pump_buy_bundle(kp, mint, 0.05, 10.0, Hash.default())
    sell = engine.assemble_pump_sell_bundle(kp, mint, 1000, 1, Hash.default())
    for tx in [*buys, sell]:
        assert all(sig == Signature.default() for sig in tx.signatures)


@pytest.mark.asyncio
async def test_jupiter_engines_refuse_before_network_or_signing():
    from solders.keypair import Keypair
    from core.paper_guard import LiveSigningDisabled
    from core.jupiter_engine import JupiterSwapEngine as CoreJupiter
    from stages.raydium_jupiter_engine import JupiterSwapEngine as StageJupiter

    called = {"net": 0}

    class Boom:
        def __getattr__(self, name):
            called["net"] += 1
            raise AssertionError("network client must not be touched")

    kp = Keypair()
    with pytest.raises(LiveSigningDisabled):
        await CoreJupiter().execute_swap("So111", "Mint111", 1000, kp)
    stage = StageJupiter.__new__(StageJupiter)
    with pytest.raises(LiveSigningDisabled):
        stage.sign_jupiter_transaction("AAAA", kp)
    with pytest.raises(LiveSigningDisabled):
        await stage.plan_floor_defense_ladder("Mint111", 3.0, [kp, kp, kp], client=Boom())
    assert called["net"] == 0
