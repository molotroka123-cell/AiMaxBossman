import os
import sys
import asyncio
import time
from typing import Dict, Any, List, Optional

# Ensure root of solana_volume_suite is importable
SUITE_ROOT = os.path.dirname(os.path.abspath(__file__))
if SUITE_ROOT not in sys.path:
    sys.path.insert(0, SUITE_ROOT)

from core.liquidity_gate import LiquidityGate
from core.ai_orchestrator import AIOrchestrator, VolumeDecision
from core.treasury_guard import TreasuryGuard
from core.jito_client import JitoBundleClient


class VolumeOrchestratorLoop:
    """Paper-only market simulation. This runner never loads keys or submits orders."""

    def __init__(
        self,
        vault_path: Optional[str] = None,
        master_password: Optional[str] = None,
        target_token_mint: str = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",  # ci-secret-scan: allow -- public SPL token mint address (identifier, not a key)
        max_allowed_loss_usd: float = 40.0,
        test_mode: bool = False
    ):
        if vault_path is None:
            self.vault_path = os.path.join(SUITE_ROOT, "wallets_encrypted.json")
        else:
            self.vault_path = vault_path
        # This runner is paper-only: it never unlocks or creates a real wallet.
        self.master_password = None
        self.target_token_mint = target_token_mint
        self.max_allowed_loss_usd = max_allowed_loss_usd
        self.test_mode = test_mode

        self.liquidity_gate = LiquidityGate(max_impact_bps=120)
        self.ai_orchestrator = AIOrchestrator()
        if self.test_mode:
            self.ai_orchestrator.timeout = 0.05
        self.treasury_guard = TreasuryGuard(max_allowed_loss_usd=self.max_allowed_loss_usd)
        self.jito_client = JitoBundleClient()

        self.is_running: bool = False
        self.iteration_count: int = 0
        self.event_journal: List[Dict[str, Any]] = []
        self.wallet_balances: Dict[str, float] = {}
        self.cached_keypairs = []
        self.sub_wallet_addresses: List[str] = []

    def log_event(self, event_type: str, message: str, meta: Optional[Dict[str, Any]] = None):
        entry = {
            "timestamp": time.strftime("%H:%M:%S"),
            "epoch": time.time(),
            "type": event_type,
            "message": message,
            "meta": meta or {}
        }
        self.event_journal.insert(0, entry)
        if len(self.event_journal) > 300:
            self.event_journal.pop()

    def initialize_vault_pool(self, count: int = 10):
        """Create virtual paper wallets only; never read, write, or delete a key vault."""
        if type(count) is not int or not 1 <= count <= 100:
            raise ValueError("Paper wallet count must be an integer between 1 and 100")

        self.cached_keypairs = []
        self.sub_wallet_addresses = [f"SIM-WALLET-{idx:03d}" for idx in range(count)]
        self.wallet_balances = {
            addr: self.wallet_balances.get(addr, round(0.42 + (idx % 4) * 0.18, 3))
            for idx, addr in enumerate(self.sub_wallet_addresses)
        }
        self.log_event(
            "PAPER_WALLETS_READY",
            f"Initialized {count} virtual wallets; key vault access is disabled.",
        )
        return list(self.sub_wallet_addresses)

    async def step(self) -> Dict[str, Any]:
        """Executes a single step of the autonomous loop."""
        self.iteration_count += 1

        # Fixed synthetic pool: the paper loop performs no RPC or market-data requests.
        reserves = {
            "model": "CONSTANT_PRODUCT",
            "input_asset": "SOL",
            "reserve_in": int(650.0 * 10**9),
            "reserve_out": int(1_000_000_000 * 10**6),
            "fee_bps": 25,
            "liquidity_usd": 117_000.0
        }

        # 2. Get decision from AI Orchestrator
        market_state = {
            "stage": "PAPER_TRADING_AMM",
            "token_mint": self.target_token_mint,
            "liquidity_usd": reserves.get("liquidity_usd", 120000.0),
            "sol_reserve": reserves.get("reserve_in", 650 * 10**9) / 1e9,
            "active_wallets_count": len(self.sub_wallet_addresses)
        }

        decision: VolumeDecision = await self.ai_orchestrator.get_volume_decision(
            market_state=market_state,
            active_wallet_count=len(self.sub_wallet_addresses)
        )
        # Model output is a paper recommendation only; never trust claimed execution fields.
        decision.confirmed_onchain = False
        decision.tx_signature = None

        # 3. Liquidity Gate Validation (Price Impact <= 1.2%)
        gate_evaluation = self.liquidity_gate.validate_and_slice_order(
            amount_sol=decision.amount_sol,
            pool_reserves=reserves
        )

        # 4. Treasury Guard Check
        if not self.treasury_guard.is_within_budget():
            self.is_running = False
            self.log_event("CIRCUIT_BREAKER", f"Treasury limit reached: {self.treasury_guard.pause_reason}")
            return {
                "iteration": self.iteration_count,
                "status": "CIRCUIT_BREAKER_TRIPPED",
                "decision": decision.model_dump(),
                "gate": gate_evaluation
            }

        # 5. Execute Slices or Direct Order
        if decision.action in {"KOTH_PULSE", "FLOOR_DEFENSE"}:
            decision.confirmed_onchain = False
            decision.tx_signature = None
            self.log_event(
                "POLICY_BLOCKED",
                f"[{decision.action}] Automated volume-push/price-defense actions are disabled.",
                meta={"action": decision.action, "reason": decision.reason},
            )
        elif gate_evaluation["execution_allowed"] and decision.action in ["BUY", "SELL"]:
            slices = gate_evaluation.get("slices_sol", [decision.amount_sol])
            if not self.sub_wallet_addresses:
                self.initialize_vault_pool()
            wallet_addr = self.sub_wallet_addresses[decision.wallet_index % len(self.sub_wallet_addresses)]

            for slice_sol in slices:
                # Record trade friction
                self.treasury_guard.record_trade(
                    volume_sol=slice_sol,
                    dex_type="raydium",
                    jito_tip_lamports=0,
                    network_fee_lamports=0,
                )

                # The paper ledger must never imply an on-chain confirmation.
                decision.confirmed_onchain = False
                decision.tx_signature = None

                # Update wallet balance
                current_bal = self.wallet_balances.get(wallet_addr, 0.5)
                delta = -slice_sol if decision.action == "BUY" else (slice_sol * 0.98)
                self.wallet_balances[wallet_addr] = max(0.01, round(current_bal + delta, 4))

                self.log_event(
                    "PAPER_TRADE_RECORDED",
                    f"Paper {decision.action}: {slice_sol:.4f} SOL | Virtual wallet #{decision.wallet_index} | Impact: {gate_evaluation['estimated_impact_bps']} bps",
                    meta={
                        "action": decision.action,
                        "amount_sol": slice_sol,
                        "wallet_index": decision.wallet_index,
                        "wallet_address": wallet_addr,
                        "impact_bps": gate_evaluation["estimated_impact_bps"],
                        "delay_sec": decision.delay_sec,
                        "reason": decision.reason,
                        "execution": "SIMULATED_ONLY",
                        "confirmed_onchain": False,
                    }
                )
        elif decision.action not in {"WAIT", "MIGRATION_HOLD"}:
            decision.confirmed_onchain = False
            decision.tx_signature = None
            self.log_event(
                "TRADE_HELD",
                f"[{decision.action}] Paper order blocked by the liquidity gate: {gate_evaluation['status']}",
                meta={"reason": decision.reason},
            )
        else:
            self.log_event("TRADE_HELD", f"[{decision.action}] {decision.reason} | Gate: {gate_evaluation['status']}")

        return {
            "iteration": self.iteration_count,
            "status": "COMPLETED",
            "decision": decision.model_dump(),
            "gate": gate_evaluation
        }

    async def run(self, max_iterations: Optional[int] = None):
        """Infinite (or bounded) loop."""
        self.initialize_vault_pool()
        self.is_running = True
        self.log_event("PAPER_RUNNER_START", "Paper simulation started with synthetic market data.")

        try:
            while self.is_running:
                step_result = await self.step()
                if max_iterations and self.iteration_count >= max_iterations:
                    break

                if not self.is_running:
                    break

                delay = 0.05 if self.test_mode else step_result["decision"]["delay_sec"]
                # Clamp delay to keep the local simulation responsive.
                delay = min(delay, 5.0)
                await asyncio.sleep(delay)
        except asyncio.CancelledError:
            self.log_event("RUNNER_CANCEL", "Runner task cancelled.")
        finally:
            self.is_running = False
            self.log_event("PAPER_RUNNER_STOP", "Paper simulation stopped.")

    def stop(self):
        """Kill Switch: Immediately stops loop."""
        self.is_running = False
        self.log_event("KILL_SWITCH", "Emergency STOP triggered by operator.")
