"""Safety invariants for paper-only Solana tooling."""
import json
import os
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


REMOVED_MODES = ["KOTH_PULSE", "FLOOR_DEFENSE"]


@pytest.mark.parametrize("action", REMOVED_MODES)
def test_removed_volume_push_and_price_support_modes_are_not_valid_actions(action):
    """Negative control: the volume-push / price-defense modes were removed."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        VolumeDecision(
            action=action,
            wallet_index=0,
            amount_sol=0.05,
            delay_sec=4,
            mode_tag="test",
            reason="synthetic test decision",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("action", REMOVED_MODES)
async def test_model_output_with_removed_mode_fails_closed_to_wait(action):
    from core.ai_orchestrator import AIOrchestrator

    class FakeResp:
        status_code = 200

        def json(self):
            content = json.dumps({
                "action": action, "wallet_index": 0, "amount_sol": 0.05,
                "delay_sec": 4, "mode_tag": "x", "reason": "y",
            })
            return {"choices": [{"message": {"content": content}}]}

    class FakeClient:
        async def post(self, url, json=None):
            return FakeResp()

    orch = AIOrchestrator()
    decision = await orch.get_volume_decision({"curve_progress_pct": 10.0}, 3, client=FakeClient())
    assert decision.action == "WAIT"
    assert decision.amount_sol == 0
    assert orch.total_fallback_calls == 1


def test_funding_router_and_floor_defense_code_are_removed():
    import importlib

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("core.funding_router")
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("stages.raydium_jupiter_engine")  # also carried plan_floor_defense_ladder
    suite = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assert not os.path.exists(os.path.join(suite, "core", "funding_router.py"))


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
        from dashboard.safety_app import CONTROL_TOKEN
        with TestClient(app) as client:
            client.headers["X-Control-Token"] = CONTROL_TOKEN
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


# ---- H4 (2026-10-11): the live-transaction builders were REMOVED; absence and refusal are asserted ---------------
def test_removed_transaction_builders_do_not_exist():
    import importlib

    from core.jito_client import JitoBundleClient
    from core.key_vault import vault as vault_module
    import core.paper_guard as guard
    import stages.pumpfun_engine as pump

    for name in ("compile_v0_transaction", "build_tip_instruction", "get_bundle_status", "get_random_tip_account"):
        assert not hasattr(JitoBundleClient, name), name
    for name in ("build_pump_buy_instruction", "build_pump_sell_instruction", "assemble_pump_buy_bundle",
                 "assemble_pump_sell_bundle", "get_bonding_curve_pda", "get_associated_token_address", "PumpFunEngine"):
        assert not hasattr(pump, name), name
    assert not hasattr(vault_module, "RentReclaimer")
    assert not hasattr(vault_module.SecurityKeyVault, "build_sweep_all_instructions")
    assert not hasattr(guard, "unsigned_paper_transaction")
    for module in ("core.jupiter_engine", "stages.raydium_jupiter_engine"):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(module)


def test_refuse_live_signing_is_the_single_choke_point():
    from core.paper_guard import LiveSigningDisabled, refuse_live_signing

    with pytest.raises(LiveSigningDisabled, match="LIVE_SIGNING_DISABLED"):
        refuse_live_signing("anything")
